"""V-World 국가중점데이터 API → stg.vworld_raw → core.dim_parcel(지목 · 면적) · fact_land_char · fact_land_price ·
bridge_parcel_zone(토지이용계획).

    VWORLD_KEY=... python -m hanpilji.collectors.vworld [--sigungu 11680] [--pnu 1168011800105270000] [--max-calls 900]

공공데이터포털의 토지특성 · 개별공시지가 · 토지이용계획 API는 폐기되고 V-World로만 나옴(2026-10-27 확인).
필지(core.dim_parcel)마다 기능 2개를 부름:
  토지특성(getLandCharacteristics): 해마다 한 줄. 지목 · 토지대장 면적 · 개별공시지가 · 용도지역 · 이용상황 · 지형 · 도로
  토지이용계획(getLandUseAttr): 그 필지에 걸린 지역 · 지구 · 구역 전부와 포함 · 저촉 · 접함
필지 하나를 다 받으면 커서에 날짜를 남기고, 다시 돌리면 남은 필지부터 이어 받음(bld와 같은 방식).
인증키는 환경변수 VWORLD_KEY(파일에 쓰지 않음). 키를 받을 때 적은 서비스 URL은 VWORLD_DOMAIN(기본 http://localhost).
응답 항목 이름은 V-World 개발자센터 문서 기준이고, 실제 응답으로 아직 확인하지 못함(클라우드에서 api.vworld.kr이 막혀 있음).
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse

import psycopg

from hanpilji import config as config_mod
from hanpilji import ingest

SOURCE = "vworld_ned"
LAND_LABEL = "V-World 토지특성"
ZONE_LABEL = "V-World 토지이용계획"
BASE = "https://api.vworld.kr/ned/data"
OPS = ("getLandCharacteristics", "getLandUseAttr")
PAGE_ROWS = 1000
OVERLAP = {"1": "포함", "2": "저촉", "3": "접함"}


class ApiError(RuntimeError):
    """V-World 오류 응답(키 · 도메인 · 한도 등)."""


class Budget(Exception):
    """--max-calls에 닿음. 받던 필지는 커서를 남기지 않아 다음에 처음부터 다시 받음."""


def is_fatal(e):
    """다음 호출도 똑같이 실패할 오류(키 · 도메인 · 한도)면 True — 수집을 멈춰야 함."""
    if isinstance(e, ApiError):
        return bool(re.search(r"KEY|DOMAIN|LIMIT|EXCEED|인증키|도메인|한도|초과", str(e), re.I))
    return isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 429)


def api_key():
    key = os.environ.get("VWORLD_KEY", "").strip()
    if not key:
        raise ApiError("환경변수 VWORLD_KEY 가 없음 — V-World 인증키를 넣어야 함")
    return key


def build_url(op, params, key=None):
    q = {"key": key or api_key(), "domain": os.environ.get("VWORLD_DOMAIN", "http://localhost"),
         "format": "json", **params}
    return f"{BASE}/{op}?{urllib.parse.urlencode(q)}"


def parse(data: bytes):
    """응답 → (줄 목록, 전체 건수). 응답 맨 위 이름이 기능마다 달라서(landCharacteristicss · landUses …)
    「field」나 「totalCount」가 있는 첫 묶음을 씀. 오류면 ApiError."""
    text = data.decode("utf-8-sig").strip()
    try:
        obj = json.loads(text)
    except ValueError:
        raise ApiError(f"JSON이 아닌 응답: {text[:200]}") from None
    resp = obj.get("response") if isinstance(obj, dict) else None
    if isinstance(resp, dict) and str(resp.get("status", "")).upper() == "ERROR":   # 게이트웨이 오류 모양
        err = resp.get("error") or {}
        raise ApiError(f"{err.get('code', '?')} {err.get('text', '')}".strip())
    for v in (obj.values() if isinstance(obj, dict) else ()):
        if isinstance(v, dict) and ("field" in v or "totalCount" in v):
            rows = v.get("field") or []
            rows = rows if isinstance(rows, list) else [rows]
            try:
                total = int(str(v.get("totalCount") or len(rows)))
            except ValueError:
                total = len(rows)
            return rows, total
        if isinstance(v, dict) and v.get("resultCode"):   # 묶음은 있는데 줄 대신 결과 코드만 옴
            raise ApiError(f"{v['resultCode']} {v.get('resultMsg', '')}".strip())
    raise ApiError(f"알 수 없는 응답: {text[:200]}")


def fetch(op, params, http_get=ingest.http_get):
    """한 번 호출해서 (원본 bytes, 줄 목록, 전체 건수)를 돌려줌. 원본은 호출한 쪽이 보관."""
    try:
        data = http_get(build_url(op, params))
    except urllib.error.HTTPError as e:
        try:
            parse(e.read())   # 오류 JSON이면 여기서 ApiError
        except ApiError:
            raise
        except Exception:
            pass
        raise
    rows, total = parse(data)
    return data, rows, total


# ── 값 다듬기 ──

def _s(row, k):
    v = row.get(k)
    return v.strip() if isinstance(v, str) else ("" if v is None else str(v))


def _num(v):
    try:
        x = float(str(v or "").replace(",", ""))
    except ValueError:
        return None
    return x if x > 0 else None


def to_char(row):
    """토지특성 한 줄 → 값. 기준연도가 없으면 None."""
    year = _s(row, "stdrYear")
    if not re.fullmatch(r"\d{4}", year):
        return None
    price = _num(_s(row, "pblntfPclnd"))
    return {
        "year": int(year),
        "category": _s(row, "lndcgrCodeNm") or None,
        "area": _num(_s(row, "lndpclAr")),
        "price": int(price) if price else None,
        "zone1": _s(row, "prposArea1") or None,
        "zone2": _s(row, "prposArea2") or None,
        "land_use": _s(row, "ladUseSittnNm") or None,
        "height": _s(row, "tpgrphHgCodeNm") or None,
        "shape": _s(row, "tpgrphFrmCodeNm") or None,
        "road": _s(row, "roadSideCodeNm") or None,
    }


def pick_zones(rows):
    """토지이용계획 줄 → [(코드, 이름, 포함 · 저촉 · 접함)]. 같은 코드는 가장 강한 관계(포함 > 저촉 > 접함) 하나만.
    「도시지역」 「일반주거지역」처럼 더 구체적인 이름(제3종일반주거지역)이 같이 있으면 상위 이름은 뺌(bld와 같은 규칙)."""
    rank = {"포함": 0, "저촉": 1, "접함": 2}
    out = {}
    for r in rows:
        code, name = _s(r, "prposAreaDstrcCode"), _s(r, "prposAreaDstrcCodeNm").replace(" ", "")
        overlap = OVERLAP.get(_s(r, "cnflcAt")) or (_s(r, "cnflcAtNm") if _s(r, "cnflcAtNm") in rank else "포함")
        if not code or not name or name.endswith(("미지정", "미분류")):
            continue
        if code not in out or rank[overlap] < rank[out[code][2]]:
            out[code] = (code, name, overlap)
    names = {v[1] for v in out.values()}
    broad = {"도시지역", "관리지역"} if len(names) > 1 else set()
    broad |= {n for n in names if any(o != n and o.endswith(n) for o in names)}
    return [v for v in out.values() if v[1] not in broad]


# ── 받기 ──

class Caller:
    """호출 수를 셈. 한도(--max-calls)에 닿으면 Budget."""

    def __init__(self, fetch, raw_dir, max_calls=None):
        self.fetch, self.raw_dir, self.max_calls, self.calls = fetch, raw_dir, max_calls, 0

    def all_rows(self, op, pnu):
        rows, page = [], 1
        while True:
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise Budget()
            self.calls += 1
            data, got, total = self.fetch(op, {"pnu": pnu, "numOfRows": PAGE_ROWS, "pageNo": page})
            ingest.save_raw(self.raw_dir, "vworld", f"{pnu}_{op}_p{page}.json", data)
            rows += got
            if not got or len(rows) >= total:
                return rows
            page += 1


# ── 적재 ──

def load(conn, pnu, got, asof):
    """필지 하나의 응답 2개를 올림. 반환: 올린 줄 수(토지특성 해 + 용도지역)."""
    n = 0
    with conn.cursor() as cur:
        for op, rows in got.items():
            cur.execute(
                """INSERT INTO stg.vworld_raw (pnu, op, row_data) VALUES (%s, %s, %s)
                   ON CONFLICT (pnu, op) DO UPDATE SET row_data = excluded.row_data, loaded_at = now()""",
                (pnu, op, json.dumps(rows, ensure_ascii=False)))

        chars = sorted((c for c in map(to_char, got.get("getLandCharacteristics", [])) if c),
                       key=lambda c: c["year"])
        zone_codes = {r[0] for r in cur.execute("SELECT zone_cd FROM core.dim_zone WHERE code_system = 'land_char'")}
        for c in chars:
            cur.execute(
                """INSERT INTO core.fact_land_char (pnu, base_year, land_use, terrain_height, terrain_shape, road_side,
                       zone_cd1, zone_cd2, source, asof) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (pnu, base_year) DO UPDATE SET land_use = excluded.land_use,
                       terrain_height = excluded.terrain_height, terrain_shape = excluded.terrain_shape,
                       road_side = excluded.road_side, zone_cd1 = excluded.zone_cd1, zone_cd2 = excluded.zone_cd2,
                       source = excluded.source, asof = excluded.asof""",
                (pnu, c["year"], c["land_use"], c["height"], c["shape"], c["road"],
                 c["zone1"] if c["zone1"] in zone_codes else None, c["zone2"] if c["zone2"] in zone_codes else None,
                 LAND_LABEL, asof))
            if c["price"]:
                cur.execute(
                    """INSERT INTO core.fact_land_price (pnu, base_year, is_standard, price_per_m2, source, asof)
                       VALUES (%s, %s, false, %s, %s, %s)
                       ON CONFLICT (pnu, base_year, is_standard) DO UPDATE SET price_per_m2 = excluded.price_per_m2,
                           source = excluded.source, asof = excluded.asof""",
                    (pnu, c["year"], c["price"], LAND_LABEL, asof))
            n += 1
        # 지목 · 면적은 가장 최근 해 값. 토지특성이 없는(받은 줄이 0인) 필지는 그대로 둠
        latest = next((c for c in reversed(chars) if c["category"] or c["area"]), None)
        if latest:
            cur.execute("""UPDATE core.dim_parcel SET land_category = %s, area_m2 = %s, source = %s, asof = %s
                           WHERE pnu = %s""", (latest["category"], latest["area"], LAND_LABEL, asof, pnu))

        # 토지이용계획: 이 필지의 V-World 줄을 지우고 다시 넣음. 코드가 UQ 코드표에 있으면 그것을, 없으면 새로 둠
        cur.execute("DELETE FROM core.bridge_parcel_zone WHERE pnu = %s AND source = %s", (pnu, ZONE_LABEL))
        for code, name, overlap in pick_zones(got.get("getLandUseAttr", [])):
            cur.execute(
                """INSERT INTO core.dim_zone (zone_cd, code_system, zone_name, source, asof)
                   VALUES (%s, 'vworld_lup', %s, %s, %s) ON CONFLICT (zone_cd) DO NOTHING""",
                (code, name, ZONE_LABEL, asof))
            cur.execute(
                """INSERT INTO core.bridge_parcel_zone (pnu, zone_cd, overlap, source, asof)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (pnu, zone_cd) DO UPDATE SET overlap = excluded.overlap, source = excluded.source,
                       asof = excluded.asof""",
                (pnu, code, overlap, ZONE_LABEL, asof))
            n += 1
    return n


def main(argv=None, fetch=fetch):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--sigungu", action="append", help="시군구 5자리(기본: config [pilot])")
    ap.add_argument("--pnu", action="append", help="이 필지만(기본: 그 구의 필지 전부)")
    ap.add_argument("--max-calls", type=int, help="이번 실행의 최대 호출 수(하루 한도를 나눠 쓸 때)")
    ap.add_argument("--refresh", action="store_true", help="최근에 받은 필지도 다시 받음")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    vcfg = cfg.get("vworld", {})
    refresh_days = vcfg.get("refresh_days", 30)
    max_calls = args.max_calls if args.max_calls is not None else vcfg.get("max_calls")
    sigungus = args.sigungu or cfg["pilot"]["sigungu"]
    today = datetime.date.today()
    caller = Caller(fetch, cfg["storage"]["raw_dir"], max_calls)
    with psycopg.connect(cfg["db"]["dsn"]) as conn:
        pnus = args.pnu or [r[0] for r in conn.execute(
            "SELECT pnu FROM core.dim_parcel WHERE left(pnu, 5) = ANY(%s) ORDER BY pnu", (sigungus,)).fetchall()]
        run_id = ingest.start_run(conn, SOURCE)
        loaded = failed = done = skipped = 0
        note, finished = None, False
        try:
            for pnu in pnus:
                last = ingest.get_cursor(conn, SOURCE, pnu)
                if not args.refresh and last and (today - datetime.date.fromisoformat(last)).days < refresh_days:
                    skipped += 1
                    continue
                task = ingest.add_task(conn, run_id, SOURCE, pnu)
                try:
                    got = {op: caller.all_rows(op, pnu) for op in OPS}
                    n = load(conn, pnu, got, today)
                    ingest.end_task(conn, task, "done")
                    ingest.set_cursor(conn, SOURCE, pnu, today.isoformat())
                    conn.commit()
                    loaded, done = loaded + n, done + 1
                    print(f"{pnu}  {n:3}줄  (호출 {caller.calls})")
                except Budget:
                    conn.rollback()
                    note = f"호출 한도 {max_calls}회에 닿아 멈춤 — 다시 돌리면 이어 받음"
                    print(note, file=sys.stderr)
                    break
                except Exception as e:  # 한 필지가 실패해도 나머지는 계속(커서는 그대로)
                    conn.rollback()
                    t = ingest.add_task(conn, run_id, SOURCE, pnu, status="failed")
                    ingest.end_task(conn, t, "failed", str(e)[:500])
                    conn.commit()
                    failed += 1
                    print(f"failed {pnu}: {e}", file=sys.stderr)
                    if is_fatal(e):  # 키 · 도메인 · 하루 한도 문제는 다음 호출도 똑같이 실패하므로 멈춤
                        note = f"멈춤: {e}"[:500]
                        break
            left = len(pnus) - done - skipped
            print(f"필지 {len(pnus)}개 중 이번에 {done}개 · 이미 받음 {skipped}개 · 남음 {left}개 · 호출 {caller.calls}회")
            ingest.finish_run(conn, run_id, "failed" if failed else "done", loaded, 0, note)
            finished = True
        finally:
            if not finished:  # 예상 못 한 오류 · 중단에도 실행 기록을 「running」으로 남기지 않음
                conn.rollback()
                ingest.finish_run(conn, run_id, "failed", loaded, 0, "중단됨")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
