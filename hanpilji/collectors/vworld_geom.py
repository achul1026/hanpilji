"""V-World 2D 데이터(연속지적도 · 도로명주소 건물) → core.geo_parcel · core.geo_building.

V-World는 해외 IP(클라우드)의 연결을 끊어서 받기는 국내 PC에서 함:
    python scripts/vworld_fetch_geom_gangnam.py                     # 국내 PC: vworld_geom_11680.zip 생김
    python -m hanpilji.collectors.vworld_geom --from-zip vworld_geom_11680.zip [--sigungu 11680]   # 적재

zip 안: parcel/{칸}.json · building/{칸}.json · pnu/{pnu}.json, 파일마다 {"layer", "filter", "features": [GeoJSON]}.
도형은 경위도(EPSG:4326)로 받아 DB에는 5186(Korea 2000 중부원점, m)으로 바꿔 넣음.
필지는 그 구(시군구 5자리)의 PNU만, 건물은 그 구 필지에 걸친 것만 남기고 가장 많이 겹치는 필지에 이음.
여러 칸에 걸친 도형은 여러 번 오므로 PNU · 건물관리번호로 하나만 남김. 다시 돌리면 덮어씀.
"""
import argparse
import datetime
import json
import re
import sys
import zipfile
from pathlib import Path

from hanpilji import ingest

SOURCE = "vworld_geom"
PARCEL_LABEL = "V-World 연속지적도"
BUILDING_LABEL = "V-World 도로명주소 건물"
BATCH = 500
# 건물 속성 이름 후보(V-World 문서마다 대소문자 · 이름이 조금씩 달라 소문자로 맞춰 찾음)
BLD_ID = ("bd_mgt_sn", "bdmgtsn", "buld_mnnm_sn")
BLD_NAME = ("buld_nm", "bld_nm", "bdnm")
BLD_NAME_DC = ("buld_nm_dc", "bld_nm_dc")
BLD_FLOORS = ("gro_flo_co", "grnd_flr", "ground_floor")
BLD_UNDER = ("und_flo_co", "ugrnd_flr", "under_floor")


def _props(f):
    return {str(k).lower(): v for k, v in (f.get("properties") or {}).items()}


def _pick(p, keys):
    for k in keys:
        v = p.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def split_jibun(s):
    """V-World jibun 「527대」 「산12-3임」 → ("527", "대") · ("산12-3", "임"). 지목이 없으면 (번지, None)."""
    s = (s or "").strip()
    m = re.fullmatch(r"(산?\s*\d+(?:-\d+)?)\s*([^\d\s-]*)", s)
    if not m:
        return (s or None), None
    return m.group(1).replace(" ", ""), (m.group(2) or None)


def read_files(src):
    """zip 또는 폴더 → (종류, features) 목록. 종류는 parcel · building · pnu(필지번호로 받은 필지)."""
    out = []
    if Path(src).is_dir():
        items = [(p.relative_to(src).parts[0], p.read_bytes()) for p in sorted(Path(src).rglob("*.json"))]
    else:
        with zipfile.ZipFile(src) as z:
            items = [(n.split("/")[0], z.read(n)) for n in sorted(z.namelist()) if n.endswith(".json")]
    for kind, data in items:
        if kind not in ("parcel", "building", "pnu"):
            continue
        out.append((kind, json.loads(data.decode("utf-8-sig")).get("features") or []))
    return out


def collect(files, sigungu):
    """features를 필지 · 건물로 모으고 중복을 뺌. 반환: (필지 dict pnu → (jibun, jimok, geometry), 건물 dict id → row)."""
    parcels, buildings = {}, {}
    for kind, feats in files:
        for f in feats:
            g = f.get("geometry")
            if not g or g.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            p = _props(f)
            if kind in ("parcel", "pnu"):
                pnu = str(p.get("pnu") or "")
                if not re.fullmatch(r"\d{19}", pnu) or not pnu.startswith(sigungu):
                    continue
                jibun, jimok = split_jibun(p.get("jibun"))
                parcels[pnu] = (jibun, jimok, g)
            else:
                bid = _pick(p, BLD_ID) or str(f.get("id") or "")
                if not bid:
                    continue
                name = " ".join(x for x in (_pick(p, BLD_NAME), _pick(p, BLD_NAME_DC)) if x) or None
                buildings[bid] = {"name": name, "above": _int(_pick(p, BLD_FLOORS)),
                                  "below": _int(_pick(p, BLD_UNDER)), "props": p, "geom": g}
    return parcels, buildings


GEOM = "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 5186)), 3))"


def load(conn, parcels, buildings, sigungu, asof, log=print):
    """반환: (필지 수, 건물 수)."""
    with conn.cursor() as cur:
        cur.execute("SET LOCAL search_path = public, extensions")
        rows = [(pnu, j, m, json.dumps(g), PARCEL_LABEL, asof) for pnu, (j, m, g) in sorted(parcels.items())]
        for i in range(0, len(rows), BATCH):
            cur.executemany(
                f"""INSERT INTO core.geo_parcel (pnu, jibun, jimok, geom, source, asof)
                    VALUES (%s, %s, %s, {GEOM}, %s, %s)
                    ON CONFLICT (pnu) DO UPDATE SET jibun = excluded.jibun, jimok = excluded.jimok,
                        geom = excluded.geom, source = excluded.source, asof = excluded.asof""", rows[i:i + BATCH])
        log(f"필지 경계 {len(rows):,}개")

        # 건물은 임시 표에 다 올린 뒤, 이 구 필지에 걸친 것만 남기고 가장 많이 겹치는 필지를 붙여 넣음
        cur.execute("""CREATE TEMP TABLE tmp_bld (bld_id text PRIMARY KEY, name text, floors_above int,
                       floors_below int, props jsonb, geom geometry(MultiPolygon, 5186)) ON COMMIT DROP""")
        rows = [(k, b["name"], b["above"], b["below"], json.dumps(b["props"], ensure_ascii=False), json.dumps(b["geom"]))
                for k, b in sorted(buildings.items())]
        for i in range(0, len(rows), BATCH):
            cur.executemany(f"INSERT INTO tmp_bld VALUES (%s, %s, %s, %s, %s, {GEOM})", rows[i:i + BATCH])
        cur.execute(
            """INSERT INTO core.geo_building (bld_id, pnu, name, floors_above, floors_below, props, geom, source, asof)
               SELECT t.bld_id, best.pnu, t.name, t.floors_above, t.floors_below, t.props, t.geom, %s, %s
               FROM tmp_bld t
               CROSS JOIN LATERAL (
                   SELECT g.pnu FROM core.geo_parcel g
                   WHERE left(g.pnu, 5) = %s AND g.geom && t.geom AND ST_Intersects(g.geom, t.geom)
                   ORDER BY ST_Area(ST_Intersection(g.geom, t.geom)) DESC, g.pnu LIMIT 1
               ) best
               WHERE NOT ST_IsEmpty(t.geom)
               ON CONFLICT (bld_id) DO UPDATE SET pnu = excluded.pnu, name = excluded.name,
                   floors_above = excluded.floors_above, floors_below = excluded.floors_below,
                   props = excluded.props, geom = excluded.geom, source = excluded.source, asof = excluded.asof""",
            (BUILDING_LABEL, asof, sigungu))
        nb = cur.rowcount
        log(f"건물 윤곽 {nb:,}개 (받은 {len(rows):,}개 중 이 구 필지에 걸친 것)")
    return len(parcels), nb


def coverage(conn, sigungu):
    """한필지 필지(core.dim_parcel) 중 경계가 있는 수 · 전체 수."""
    return conn.execute(
        """SELECT count(g.pnu), count(*) FROM core.dim_parcel p LEFT JOIN core.geo_parcel g USING (pnu)
           WHERE left(p.pnu, 5) = %s""", (sigungu,)).fetchone()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--sigungu", default="11680", help="시군구 5자리(이 구의 필지 · 건물만 남김)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-zip", metavar="ZIP", help="vworld_fetch_geom_gangnam.py가 만든 zip")
    src.add_argument("--from-dir", metavar="DIR", help="그 zip을 푼 폴더")
    args = ap.parse_args(argv)

    import psycopg
    from hanpilji import config as config_mod
    cfg = config_mod.load(args.config)
    parcels, buildings = collect(read_files(args.from_zip or args.from_dir), args.sigungu)
    print(f"읽음: 필지 {len(parcels):,}개 · 건물 {len(buildings):,}개")
    today = datetime.date.today()
    with psycopg.connect(cfg["db"]["dsn"]) as conn:
        run_id = ingest.start_run(conn, SOURCE)
        try:
            n_p, n_b = load(conn, parcels, buildings, args.sigungu, today)
            conn.commit()
        except Exception as e:
            conn.rollback()
            ingest.finish_run(conn, run_id, "failed", note=str(e)[:500])
            raise
        have, total = coverage(conn, args.sigungu)
        note = f"필지 {n_p} · 건물 {n_b} · 한필지 필지 {have}/{total}개에 경계"
        ingest.finish_run(conn, run_id, "done", loaded=n_p + n_b, note=note)
        print(note)
        if have < total:
            miss = [r[0] for r in conn.execute(
                """SELECT p.pnu FROM core.dim_parcel p LEFT JOIN core.geo_parcel g USING (pnu)
                   WHERE left(p.pnu, 5) = %s AND g.pnu IS NULL ORDER BY 1 LIMIT 20""", (args.sigungu,))]
            print("경계 없는 필지(앞 20개): " + ", ".join(miss), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
