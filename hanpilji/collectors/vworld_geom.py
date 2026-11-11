"""V-World 2D 데이터(연속지적도 · 도로명주소 건물) → core.geo_parcel · core.geo_building.

서버(국내 IP)는 바로 받아 넣음. 서울을 약 500m 칸으로 나눠 칸마다 받고 커서를 남겨, 끊기면 다음에 이어 받음:
    VWORLD_KEY=... python -m hanpilji.collectors.vworld_geom --fetch-seoul [--max-calls 5000]
V-World는 해외 IP(클라우드)의 연결을 끊어서, 클라우드에서는 받기를 국내 PC에서 함:
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
import os
import re
import sys
import urllib.error
import urllib.parse
import zipfile
from pathlib import Path

from hanpilji import db, ingest
from hanpilji.collectors import vworld

SOURCE = "vworld_geom"
PARCEL_LABEL = "V-World 연속지적도"
BUILDING_LABEL = "V-World 도로명주소 건물"
BATCH = 500
DATA_URL = "https://api.vworld.kr/req/data"
LAYERS = {"parcel": "LP_PA_CBND_BUBUN", "building": "LT_C_SPBD"}
PAGE_SIZE = 1000
# 서울을 넉넉히 덮는 경위도 범위와 칸 크기(경도 0.0055° ≈ 490m, 위도 0.0045° ≈ 500m). 강남 받을 때와 같은 칸 크기.
# 서울 밖 칸은 필지가 경기도라 버려지고, 그런 칸은 건물을 부르지 않음
SEOUL_BBOX = (126.760, 37.410, 127.190, 37.720)
STEP_X, STEP_Y = 0.0055, 0.0045
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
    """반환: (필지 수, 건물 수). sigungu는 PNU 앞자리(구 5자리, 서울 전체는 「11」)."""
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
                   WHERE left(g.pnu, length(%s)) = %s AND g.geom && t.geom AND ST_Intersects(g.geom, t.geom)
                   ORDER BY ST_Area(ST_Intersection(g.geom, t.geom)) DESC, g.pnu LIMIT 1
               ) best
               WHERE NOT ST_IsEmpty(t.geom)
               ON CONFLICT (bld_id) DO UPDATE SET pnu = excluded.pnu, name = excluded.name,
                   floors_above = excluded.floors_above, floors_below = excluded.floors_below,
                   props = excluded.props, geom = excluded.geom, source = excluded.source, asof = excluded.asof""",
            (BUILDING_LABEL, asof, sigungu, sigungu))
        nb = cur.rowcount
        log(f"건물 윤곽 {nb:,}개 (받은 {len(rows):,}개 중 이 구 필지에 걸친 것)")
    return len(parcels), nb


# ── 서버에서 바로 받기 ──
def parse_data(data):
    """2D 데이터 API 응답 → (도형 목록, 전체 건수). 결과 없음(NOT_FOUND)은 빈 목록. 오류면 vworld.ApiError."""
    text = data.decode("utf-8-sig").strip()
    try:
        resp = json.loads(text)["response"]
    except (ValueError, KeyError, TypeError):
        raise vworld.ApiError(f"알 수 없는 응답: {text[:200]}") from None
    status = str(resp.get("status", "")).upper()
    if status == "NOT_FOUND":
        return [], 0
    if status != "OK":
        err = resp.get("error") or {}
        raise vworld.ApiError(f"{err.get('code', status)} {err.get('text', '')}".strip())
    feats = ((resp.get("result") or {}).get("featureCollection") or {}).get("features") or []
    try:
        total = int(str((resp.get("record") or {}).get("total") or len(feats)))
    except ValueError:
        total = len(feats)
    return feats, total


def tiles(bbox=SEOUL_BBOX):
    x0, y0, x1, y1 = bbox
    nx, ny = round((x1 - x0) / STEP_X + 0.4999), round((y1 - y0) / STEP_Y + 0.4999)
    for j in range(ny):
        for i in range(nx):
            a, b = x0 + i * STEP_X, y0 + j * STEP_Y
            yield f"{j:02d}_{i:02d}", (round(a, 6), round(b, 6), round(a + STEP_X, 6), round(b + STEP_Y, 6))


class BoxCaller:
    """칸 하나의 한 층을 끝 쪽까지 받음. max_calls에 닿으면 vworld.Budget."""
    def __init__(self, http_get, max_calls=None):
        self.http_get, self.max_calls, self.calls = http_get, max_calls, 0
        self.key = vworld.api_key()
        self.domain = os.environ.get("VWORLD_DOMAIN", "http://localhost")

    def box(self, layer, box):
        feats, page = [], 1
        while True:
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise vworld.Budget()
            q = urllib.parse.urlencode({
                "service": "data", "request": "GetFeature", "version": "2.0", "data": layer,
                "key": self.key, "domain": self.domain, "format": "json", "crs": "EPSG:4326",
                "geometry": "true", "attribute": "true", "size": PAGE_SIZE, "page": page,
                "geomFilter": "BOX({},{},{},{})".format(*box)})
            self.calls += 1
            try:
                data = self.http_get(f"{DATA_URL}?{q}")
            except urllib.error.HTTPError as e:
                if e.code >= 500 or vworld.is_fatal(e):   # 인증 · 한도(401 · 403 · 429)나 재시도 뒤에도 서버 오류면 오늘은 멈춤
                    raise vworld.ApiError(f"HTTP {e.code} — 인증 · 한도 · 서버 오류라 멈춤") from None
                data = e.read()
            except Exception as e:   # 연결 오류 문구에 키가 섞여 나오지 않게
                raise RuntimeError(str(e).replace(self.key, "***")) from None
            part, total = parse_data(data)
            feats += part
            if not part or len(feats) >= total:
                return feats
            page += 1


def fetch_seoul(conn, http_get=ingest.http_get, max_calls=None, refresh_days=180, today=None, log=print):
    """서울 칸마다 필지 · 건물을 받아 바로 넣음 → (넣은 칸, 건너뛴 칸, 실패 칸, 호출 수, 메모)."""
    today = today or datetime.date.today()
    caller = BoxCaller(http_get, max_calls)
    grid = list(tiles())
    done = skipped = failed = 0
    note = None
    for k, (tid, box) in enumerate(grid, 1):
        key = f"seoul:{tid}"
        last = ingest.get_cursor(conn, SOURCE, key)
        if last and (today - datetime.date.fromisoformat(last)).days < refresh_days:
            skipped += 1
            continue
        try:
            files = [("parcel", caller.box(LAYERS["parcel"], box))]
            parcels, _ = collect(files, "11")
            if parcels:   # 서울 필지가 없는 칸(경기도 · 산 · 강 밖)은 건물을 부르지 않음
                files.append(("building", caller.box(LAYERS["building"], box)))
            parcels, buildings = collect(files, "11")
            n_p, n_b = load(conn, parcels, buildings, "11", today, log=lambda *_: None)
            ingest.set_cursor(conn, SOURCE, key, today.isoformat())
            conn.commit()
            done += 1
            if n_p:
                log(f"[{k}/{len(grid)}] 칸 {tid} 필지 {n_p:,} · 건물 {n_b:,} (호출 {caller.calls})")
        except vworld.Budget:
            conn.rollback()
            note = f"호출 한도 {max_calls}회에 닿아 멈춤 — 다시 돌리면 이어 받음"
            log(note)
            break
        except Exception as e:
            conn.rollback()
            failed += 1
            log(f"실패 칸 {tid}: {e}")
            if vworld.is_fatal(e):
                note = f"멈춤: {e}"[:500]
                break
    left = len(grid) - done - skipped
    log(f"칸 {len(grid)}개 중 이번에 {done}개 · 이미 받음 {skipped}개 · 남음 {left}개 · 호출 {caller.calls}회")
    return done, skipped, failed, caller.calls, note


def coverage(conn, sigungu):
    """한필지 필지(core.dim_parcel) 중 경계가 있는 수 · 전체 수."""
    return conn.execute(
        """SELECT count(g.pnu), count(*) FROM core.dim_parcel p LEFT JOIN core.geo_parcel g USING (pnu)
           WHERE left(p.pnu, 5) = %s""", (sigungu,)).fetchone()


def main(argv=None, http_get=ingest.http_get):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--sigungu", default="11680", help="시군구 5자리(이 구의 필지 · 건물만 남김)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-zip", metavar="ZIP", help="vworld_fetch_geom_gangnam.py가 만든 zip")
    src.add_argument("--from-dir", metavar="DIR", help="그 zip을 푼 폴더")
    src.add_argument("--fetch-seoul", action="store_true", help="서울을 칸마다 V-World에서 바로 받아 넣음(국내 IP에서만)")
    ap.add_argument("--max-calls", type=int, help="--fetch-seoul 한 번에 쓸 최대 호출 수")
    args = ap.parse_args(argv)

    from hanpilji import config as config_mod
    cfg = config_mod.load(args.config)
    if args.fetch_seoul:
        refresh_days = cfg.get("vworld_geom", {}).get("refresh_days", 180)
        vworld.api_key()   # 키가 없으면 수집 기록을 남기기 전에 멈춤
        with db.connect(cfg["db"]["dsn"]) as conn:
            run_id = ingest.start_run(conn, SOURCE)
            try:
                done, skipped, failed, calls, note = fetch_seoul(conn, http_get=http_get, max_calls=args.max_calls,
                                                                 refresh_days=refresh_days)
            except Exception as e:
                conn.rollback()
                ingest.finish_run(conn, run_id, "failed", note=str(e)[:500])
                conn.commit()
                raise
            ingest.finish_run(conn, run_id, "failed" if failed else "done", loaded=done,
                              note=note or f"칸 {done}개 · 호출 {calls}회")
        return 1 if failed else 0
    parcels, buildings = collect(read_files(args.from_zip or args.from_dir), args.sigungu)
    print(f"읽음: 필지 {len(parcels):,}개 · 건물 {len(buildings):,}개")
    today = datetime.date.today()
    with db.connect(cfg["db"]["dsn"]) as conn:
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
