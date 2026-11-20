"""서울시 상권분석서비스(서울신용보증재단, 서울 열린데이터광장) → core.dim_trdar · dim_adm_dong · fact_trdar_stat · geo_trdar · geo_adm_dong.

    SEOUL_API_KEY=... python -m hanpilji.collectors.seoul_trdar [--only relm|stat] [--dataset trdar_sales …]
                       [--since 20211] [--max-calls 3000] [--refresh-last 2]

  relm : 상권 1,650곳 · 행정동 425곳 목록(Open API) + 경계(SHP 파일, 인증키 없음, PostGIS 있는 DB만)
  stat : 분기 지표(아래 DATASETS). 분기로 나눠 부를 수 있는 것은 분기마다, 아닌 것은 통째로 받음
이용허락범위 제한 없음(2026-11-20 화면 확인). 갱신은 분기(분기 끝나고 2~3달 뒤).
2026-07-03부터 2021년 이후 자료만 제공(표준단위구역으로 바뀜)이라 기본 시작 분기는 20211.
추정매출은 카드 결제를 바탕으로 한 추정치이고 상권 · 행정동 합계(매장 한 곳의 매출이 아님).

다시 돌리면: 다 받은 분기는 건너뛰고(커서), 받은 분기 중 가장 최근 --refresh-last개는 고쳐졌을 수 있어 다시 받음.
하루 호출 수는 --max-calls까지. 닿으면 거기서 멈추고 다음 실행이 이어 받음.
"""
import argparse
import datetime
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from psycopg.types.json import Jsonb

from hanpilji import db, config as config_mod
from hanpilji import ingest
from hanpilji.collectors import seoul_api

SOURCE = "seoul_trdar"
SOURCE_LABEL = "서울시 상권분석서비스(서울신용보증재단)"

# 키 → (Open API 서비스, 단위, 분기로 나눠 부를 수 있는지, 열린데이터광장 번호)
DATASETS = {
    "trdar_sales": ("VwsmTrdarSelngQq", "상권", True, "OA-15572"),        # 추정매출
    "trdar_store": ("VwsmTrdarStorQq", "상권", True, "OA-15577"),         # 점포(업종별 점포 · 개업 · 폐업 · 프랜차이즈)
    "trdar_flpop": ("VwsmTrdarFlpopQq", "상권", True, "OA-15568"),        # 길단위인구
    "trdar_change": ("VwsmTrdarIxQq", "상권", True, "OA-15576"),          # 상권변화지표(다이나믹 · 확장 · 정체 · 축소)
    "trdar_consume": ("trdarNcmCnsmp", "상권", True, "OA-21278"),         # 소득소비
    "trdar_repop": ("VwsmTrdarRepopQq", "상권", False, "OA-15584"),       # 상주인구
    "trdar_wrcpop": ("VwsmTrdarWrcPopltnQq", "상권", False, "OA-15569"),  # 직장인구
    "trdar_fclty": ("VwsmTrdarFcltyQq", "상권", False, "OA-15580"),       # 집객시설
    "trdar_apt": ("InfoTrdarAptQq", "상권", False, "OA-15566"),           # 아파트(단지 · 면적 · 가격대)
    "dong_sales": ("VwsmAdstrdSelngW", "행정동", True, "OA-22175"),
    "dong_store": ("VwsmAdstrdStorW", "행정동", True, "OA-22172"),
    "dong_flpop": ("VwsmAdstrdFlpopW", "행정동", False, "OA-22178"),
    "dong_change": ("VwsmAdstrdIxQq", "행정동", False, "OA-15575"),
    "dong_consume": ("VwsmAdstrdNcmCnsmpW", "행정동", False, "OA-22166"),
    "dong_repop": ("VwsmAdstrdRepopW", "행정동", False, "OA-22183"),
    "dong_wrcpop": ("VwsmAdstrdWrcPopltnW", "행정동", False, "OA-22184"),
    "dong_fclty": ("VwsmAdstrdFcltyW", "행정동", False, "OA-22169"),
    "dong_apt": ("VwsmAdstrdAptW", "행정동", False, "OA-22163"),
}
# 줄을 가르는 칸(지표가 아님). 나머지 칸은 모두 metrics(jsonb)로
KEY_FIELDS = {"STDR_YYQU_CD", "TRDAR_SE_CD", "TRDAR_SE_CD_NM", "TRDAR_CD", "TRDAR_CD_NM",
              "ADSTRD_CD", "ADSTRD_CD_NM", "SVC_INDUTY_CD", "SVC_INDUTY_CD_NM"}
RELM_TRDAR, RELM_DONG = "TbgisTrdarRelm", "TbgisAdstrdRelmW"
SHP_FILES = {"trdar": ("OA-15560", "TRDAR_CD"), "dong": ("OA-22160", "ADSTRD_CD")}


def quarters(since, today):
    """since(20211) ~ 오늘이 든 분기까지 [20211, 20212, …]."""
    y, q = divmod(since, 10)
    end = today.year * 10 + (today.month - 1) // 3 + 1
    out = []
    while y * 10 + q <= end:
        out.append(y * 10 + q)
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def _num(v):
    """숫자 칸은 숫자로(1.5E8 같은 글자도), 아니면 글자 그대로."""
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else v
    if isinstance(v, str):
        s = v.strip()
        try:
            f = float(s)
        except ValueError:
            return s
        return int(f) if f.is_integer() and "e" not in s.lower() and "." not in s else f
    return v


def stat_row(dataset, row):
    """원본 한 줄 → (dataset, yyqu, 단위, 지역 코드, 지역 이름, 업종 코드, 업종 이름, metrics) 또는 None."""
    level = DATASETS[dataset][1]
    area_cd = row.get("TRDAR_CD") if level == "상권" else row.get("ADSTRD_CD")
    yyqu = str(row.get("STDR_YYQU_CD") or "")
    if not area_cd or not yyqu.isdigit():
        return None
    area_nm = row.get("TRDAR_CD_NM") if level == "상권" else row.get("ADSTRD_CD_NM")
    metrics = {k: _num(v) for k, v in row.items() if k not in KEY_FIELDS and v is not None}
    return (dataset, int(yyqu), level, str(area_cd), area_nm, row.get("SVC_INDUTY_CD") or "",
            row.get("SVC_INDUTY_CD_NM"), metrics)


def load_stat(conn, dataset, rows, asof, yyqu=None):
    """한 묶음(한 분기 또는 통째)을 넣음. 같은 분기에서 이번에 없는 줄은 지움(고친 원본 반영) → 넣은 줄 수."""
    vals = [v for v in (stat_row(dataset, r) for r in rows) if v]
    with conn.cursor() as cur:
        cur.execute("""CREATE TEMP TABLE IF NOT EXISTS trdar_in (dataset text, yyqu int, area_level text, area_cd text,
                           area_nm text, induty_cd text, induty_nm text, metrics jsonb) ON COMMIT DROP""")
        cur.execute("TRUNCATE trdar_in")
        with cur.copy("COPY trdar_in FROM STDIN") as cp:
            for v in vals:
                cp.write_row((*v[:7], Jsonb(v[7])))
        cur.execute("""INSERT INTO core.fact_trdar_stat (dataset, yyqu, area_level, area_cd, area_nm, induty_cd, induty_nm,
                                                         metrics, source, asof)
                       SELECT DISTINCT ON (dataset, yyqu, area_cd, induty_cd)
                              dataset, yyqu, area_level, area_cd, area_nm, induty_cd, induty_nm, metrics, %s, %s
                       FROM trdar_in
                       ON CONFLICT (dataset, yyqu, area_cd, induty_cd) DO UPDATE
                           SET area_nm = excluded.area_nm, induty_nm = excluded.induty_nm, metrics = excluded.metrics,
                               asof = excluded.asof""", (SOURCE_LABEL, asof))
        qs = [yyqu] if yyqu is not None else [r[0] for r in cur.execute("SELECT DISTINCT yyqu FROM trdar_in").fetchall()]
        for q in qs:
            cur.execute("""DELETE FROM core.fact_trdar_stat f WHERE f.dataset = %s AND f.yyqu = %s
                           AND NOT EXISTS (SELECT 1 FROM trdar_in i WHERE i.dataset = f.dataset AND i.yyqu = f.yyqu
                                           AND i.area_cd = f.area_cd AND i.induty_cd = f.induty_cd)""", (dataset, q))
    return len(vals)


def load_relm(conn, trdar_rows, dong_rows, asof):
    n = 0
    for r in trdar_rows:
        x, y = float(r["XCNTS_VALUE"]), float(r["YDNTS_VALUE"])
        lon, lat = seoul_api.tm_to_lonlat(x, y)
        conn.execute("""INSERT INTO core.dim_trdar (trdar_cd, trdar_nm, trdar_se_cd, trdar_se_nm, sigungu_cd, sigungu_nm,
                                                    adm_cd, adm_nm, x_5181, y_5181, lon, lat, area_m2, source, asof)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (trdar_cd) DO UPDATE SET trdar_nm = excluded.trdar_nm, trdar_se_cd = excluded.trdar_se_cd,
                            trdar_se_nm = excluded.trdar_se_nm, sigungu_cd = excluded.sigungu_cd, sigungu_nm = excluded.sigungu_nm,
                            adm_cd = excluded.adm_cd, adm_nm = excluded.adm_nm, x_5181 = excluded.x_5181, y_5181 = excluded.y_5181,
                            lon = excluded.lon, lat = excluded.lat, area_m2 = excluded.area_m2, asof = excluded.asof""",
                     (str(r["TRDAR_CD"]), r["TRDAR_CD_NM"], r.get("TRDAR_SE_CD"), r.get("TRDAR_SE_CD_NM"), r.get("SIGNGU_CD"),
                      r.get("SIGNGU_CD_NM"), r.get("ADSTRD_CD"), r.get("ADSTRD_CD_NM"), x, y, round(lon, 7), round(lat, 7),
                      _num(r.get("RELM_AR")), SOURCE_LABEL, asof))
        n += 1
    for r in dong_rows:
        x, y = float(r["XCNTS_VALUE"]), float(r["YDNTS_VALUE"])
        lon, lat = seoul_api.tm_to_lonlat(x, y)
        conn.execute("""INSERT INTO core.dim_adm_dong (adm_cd, adm_nm, x_5181, y_5181, lon, lat, area_m2, source, asof)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (adm_cd) DO UPDATE SET adm_nm = excluded.adm_nm, x_5181 = excluded.x_5181,
                            y_5181 = excluded.y_5181, lon = excluded.lon, lat = excluded.lat, area_m2 = excluded.area_m2,
                            asof = excluded.asof""",
                     (str(r["ADSTRD_CD"]), r["ADSTRD_NM"], x, y, round(lon, 7), round(lat, 7), _num(r.get("RELM_AR")),
                      SOURCE_LABEL, asof))
        n += 1
    return n


def shapes_from_zip(path, key_field):
    """영역 SHP zip → [(코드, WKT(5186))]. 파일 이름이 깨진 한글이라 확장자로 찾음."""
    with zipfile.ZipFile(path) as zf:
        names = {Path(i.filename).suffix.lower(): i for i in zf.infolist()}
        if ".shp" not in names or ".dbf" not in names:
            raise RuntimeError("압축 안에 .shp · .dbf가 없음")
        enc = "utf-8"
        if ".cpg" in names:
            enc = zf.read(names[".cpg"]).decode("ascii", "replace").strip().lower() or "utf-8"
            enc = "cp949" if enc in ("euc-kr", "cp949", "ks_c_5601-1987") else enc
        recs = seoul_api.read_dbf(zf.read(names[".dbf"]), enc)
        shapes = seoul_api.read_shp_polygons(zf.read(names[".shp"]))
    if len(recs) != len(shapes):
        raise RuntimeError(f"DBF {len(recs)}줄 · SHP {len(shapes)}개로 수가 다름")
    out = []
    for rec, rings in zip(recs, shapes):
        wkt = seoul_api.rings_to_wkt(rings, dy=seoul_api.FN_5186 - seoul_api.FN_5181)
        if rec.get(key_field) and wkt:
            out.append((rec[key_field], wkt))
    return out


def load_shapes(conn, table, key_col, shapes, asof):
    with conn.cursor() as cur:
        for code, wkt in shapes:
            cur.execute(f"""INSERT INTO core.{table} ({key_col}, geom, source, asof)
                            VALUES (%s, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromText(%s, 5186)), 3)), %s, %s)
                            ON CONFLICT ({key_col}) DO UPDATE SET geom = excluded.geom, asof = excluded.asof""",
                        (code, wkt, SOURCE_LABEL, asof))
    return len(shapes)


def fetch_shapes(kind, tmpdir, lister=seoul_api.list_files, fetch=seoul_api.download):
    inf, key = SHP_FILES[kind]
    inf_seq, files = lister(inf)
    if not files:
        raise RuntimeError(f"{inf} 파일 목록이 비어 있음")
    seq, name = files[0]
    path = f"{tmpdir}/{kind}.zip"
    fetch(inf, inf_seq, seq, path)
    if not zipfile.is_zipfile(path):
        raise RuntimeError(f"{inf} zip이 아닌 응답")
    return shapes_from_zip(path, key)


def has_geo(conn):
    return conn.execute("SELECT to_regclass('core.geo_trdar') IS NOT NULL").fetchone()[0]


def run_stat(conn, client, run_id, datasets, qs, refresh_last, asof, log=print):
    loaded = 0
    for ds in datasets:
        service, _, by_q, _ = DATASETS[ds]
        if not by_q:
            rows = client.rows(service)
            loaded += load_stat(conn, ds, rows, asof)
            conn.commit()
            log(f"{ds}: {len(rows)}줄(통째)")
            continue
        # 받은 분기 중 가장 최근 refresh_last개는 고쳐졌을 수 있어 다시 받음(공개가 2~3달 늦어 달력상 최근 분기는 보통 비어 있음)
        done = [q for q in qs if ingest.get_cursor(conn, SOURCE, f"{ds}:{q}")]
        recent = set(done[-refresh_last:]) if refresh_last else set()
        for q in qs:
            if q in done and q not in recent:
                continue
            rows = client.rows(service, q)
            if not rows:
                log(f"{ds} {q}: 자료 없음")
                continue
            loaded += load_stat(conn, ds, rows, asof, yyqu=q)
            ingest.set_cursor(conn, SOURCE, f"{ds}:{q}", str(len(rows)))
            conn.commit()
            log(f"{ds} {q}: {len(rows)}줄")
    return loaded


def main(argv=None, client=None, today=None, lister=seoul_api.list_files, fetch=seoul_api.download):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--only", choices=("relm", "stat"), action="append")
    ap.add_argument("--dataset", action="append", choices=sorted(DATASETS), help="이 지표만(여러 번 줄 수 있음)")
    ap.add_argument("--since", type=int, default=20211, help="시작 분기(기본 20211)")
    ap.add_argument("--max-calls", type=int, default=3000, help="이번 실행에서 부를 최대 횟수(기본 3,000)")
    ap.add_argument("--refresh-last", type=int, default=2, help="다시 받을 최근 분기 수(기본 2)")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    today = today or datetime.date.today()
    parts = args.only or ["relm", "stat"]
    client = client or seoul_api.Client(os.environ.get("SEOUL_API_KEY"), max_calls=args.max_calls)
    with db.connect(cfg["db"]["dsn"]) as conn:
        run_id = ingest.start_run(conn, SOURCE)
        loaded, status, note = 0, "done", None
        try:
            if "relm" in parts:
                loaded += load_relm(conn, client.rows(RELM_TRDAR), client.rows(RELM_DONG), today)
                conn.commit()
                if has_geo(conn):
                    with tempfile.TemporaryDirectory() as tmp:
                        loaded += load_shapes(conn, "geo_trdar", "trdar_cd", fetch_shapes("trdar", tmp, lister, fetch), today)
                        loaded += load_shapes(conn, "geo_adm_dong", "adm_cd", fetch_shapes("dong", tmp, lister, fetch), today)
                    conn.commit()
                else:
                    print("PostGIS 표(geo_trdar)가 없어 경계는 건너뜀(sql/31_trade_area_geo.sql)")
            if "stat" in parts:
                datasets = args.dataset or list(DATASETS)
                loaded += run_stat(conn, client, run_id, datasets, quarters(args.since, today), args.refresh_last, today)
        except seoul_api.LimitReached as e:
            conn.rollback()
            status, note = "done", f"호출 한도 {args.max_calls}번에 닿아 멈춤({e}). 다음 실행이 이어 받음"
        except Exception as e:  # noqa: BLE001 — 실행 기록에 남기고 실패로 끝냄
            conn.rollback()
            status, note = "failed", f"{type(e).__name__}: {e}"
        ingest.finish_run(conn, run_id, status, loaded, 0, note)
        print(f"서울 상권분석: {status} · {loaded}줄 · 호출 {client.calls}번" + (f" · {note}" if note else ""))
    return 1 if status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
