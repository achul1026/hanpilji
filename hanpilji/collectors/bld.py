"""건축HUB 건축물대장정보 API → stg.bld_raw → core.dim_building · dim_parcel · bridge_* · dim_complex.

    DATA_GO_KR_KEY=... python -m hanpilji.collectors.bld [--sigungu 11680] [--pnu 1168011800105270000] [--max-calls 900]

실거래가로 만든 단지 PNU(core.dim_complex.pnu)마다 기능 4개를 부름:
  총괄표제부(단지 한 장: 대지면적 · 세대 · 주차 · 건폐율 · 용적률), 표제부(동마다 한 장),
  지역지구(그 필지의 용도지역 · 지구), 부속지번(단지가 걸친 다른 필지).
필지 하나를 다 받으면 커서에 날짜를 남김. 하루 한도에 걸리거나 --max-calls에 닿으면 멈추고,
다시 돌리면 남은 필지부터 이어 받음(config [bld] refresh_days 안에 받은 필지는 건너뜀).
응답 항목은 실제 응답으로 확인함(강남구, 2026-10-27). 알아 둘 것:
- 총괄표제부는 큰 단지에만 있음. 작은 단지는 표제부만 와서 카드가 동별 값을 합침.
- 집합건물 동의 표제부는 대지면적 · 건폐율 · 용적률이 0으로 옴(값 없음으로 봄).
- 지역지구 코드는 「1023」 「UQA123」처럼 두 체계가 섞여 오고, 「도시지역」 「일반주거지역」 같은 상위 이름도 같이 옴.
  이름으로 UQ 코드(core.dim_zone)에 맞추고, 더 구체적인 이름이 있으면 상위 이름은 뺌.
- 위반건축물 여부는 이 API에 없음(is_violation은 비워 둠).
"""
import argparse
import datetime
import json
import re
import sys

import psycopg

from hanpilji import config as config_mod
from hanpilji import datagokr, ingest

SOURCE = "dg_bld_hub"
SOURCE_LABEL = "건축HUB 건축물대장"
ZONE_LABEL = "건축물대장 지역지구"
OPS = ("getBrRecapTitleInfo", "getBrTitleInfo", "getBrJijiguInfo", "getBrAtchJibunInfo")
PAGE_ROWS = 100


class Budget(Exception):
    """--max-calls에 닿음. 받던 필지는 커서를 남기지 않아 다음에 처음부터 다시 받음."""


# ── 값 다듬기 ──

def _s(row, k):
    v = row.get(k)
    return v.strip() if isinstance(v, str) else ""


def _num(v, zero_is_none=False):
    try:
        x = float((v or "").replace(",", ""))
    except ValueError:
        return None
    return None if zero_is_none and x == 0 else x


def _int(v, zero_is_none=False):
    x = _num(v, zero_is_none)
    return None if x is None else int(x)


def _date(v):
    v = (v or "").strip()
    try:
        return datetime.date(int(v[:4]), int(v[4:6]), int(v[6:8])) if re.fullmatch(r"\d{8}", v) else None
    except ValueError:  # 「20020100」처럼 날짜가 빈 값
        return None


def params(pnu):
    """PNU 19자리 → 요청 인자. 대장 구분은 PNU 1(일반) · 2(산)이고 API는 0 · 1."""
    return {"sigunguCd": pnu[:5], "bjdongCd": pnu[5:10], "platGbCd": str(int(pnu[10]) - 1),
            "bun": pnu[11:15], "ji": pnu[15:19]}


def item_pnu(row, prefix=""):
    """응답의 시군구 · 법정동 · 대지구분 · 번 · 지 → PNU. 하나라도 형식이 틀리면 None.
    prefix=「atch」면 부속지번 항목(atchSigunguCd · atchBun …)에서 읽음."""
    keys = ("sigunguCd", "bjdongCd", "platGbCd", "bun", "ji")
    sgg, bjd, gb, bun, ji = (_s(row, prefix + (k[0].upper() + k[1:] if prefix else k)) for k in keys)
    if not (re.fullmatch(r"\d{5}", sgg) and re.fullmatch(r"\d{5}", bjd) and gb in ("0", "1")
            and bun.isdigit() and ji.isdigit()):
        return None
    return f"{sgg}{bjd}{int(gb) + 1}{int(bun):04d}{int(ji):04d}"


def has_elevator(count, floors):
    """승강기 대수 → 있음 · 없음 · 모름. 6층 이상은 승강기를 둬야 하는데(건축법 제64조) 대장에 0대로 적힌
    오래된 아파트가 많아서(일원동 우성7차 14층 등, 2026-10-27 실측) 그때는 「없음」이 아니라 모름(None)으로 둠."""
    if count > 0:
        return True
    return None if (floors or 0) >= 6 else False


def to_building(row):
    """표제부 한 장 → dim_building 값."""
    elev = (_int(_s(row, "rideUseElvtCnt")) or 0) + (_int(_s(row, "emgenUseElvtCnt")) or 0)
    park = sum(_int(_s(row, k)) or 0 for k in ("indrAutoUtcnt", "oudrAutoUtcnt", "indrMechUtcnt", "oudrMechUtcnt"))
    name = " ".join(x for x in (_s(row, "bldNm"), _s(row, "dongNm")) if x) or None
    return {
        "bld_pk": _s(row, "mgmBldrgstPk"),
        "pnu": item_pnu(row),
        "name": name,
        "main_use": _s(row, "mainPurpsCdNm") or _s(row, "etcPurps") or None,
        "structure": _s(row, "strctCdNm") or _s(row, "etcStrct") or None,
        "approval_date": _date(_s(row, "useAprDay")),
        "floors_above": _int(_s(row, "grndFlrCnt")),
        "floors_below": _int(_s(row, "ugrndFlrCnt")),
        "total_area": _num(_s(row, "totArea"), True),
        "bc_rat": _num(_s(row, "bcRat"), True),
        "vl_rat": _num(_s(row, "vlRat"), True),
        "has_elevator": has_elevator(elev, _int(_s(row, "grndFlrCnt"))),
        "parking_count": park,
        "is_main": _s(row, "mainAtchGbCd") != "1",
        "households": _int(_s(row, "hhldCnt")),
        "site_area": _num(_s(row, "platArea"), True),
        "dong_name": _s(row, "dongNm") or None,
    }


def to_recap(rows):
    """총괄표제부 → 단지 값. 여러 장이면 연면적이 가장 큰 것. 없으면 None."""
    rows = [r for r in rows if _s(r, "mgmBldrgstPk")]
    if not rows:
        return None
    r = max(rows, key=lambda x: _num(_s(x, "totArea")) or 0)
    return {"pk": _s(r, "mgmBldrgstPk"), "site_area": _num(_s(r, "platArea"), True),
            "households": _int(_s(r, "hhldCnt"), True), "dong_count": _int(_s(r, "mainBldCnt"), True),
            "parking": _int(_s(r, "totPkngCnt"), True), "bc_rat": _num(_s(r, "bcRat"), True),
            "vl_rat": _num(_s(r, "vlRat"), True)}


def zone_name(row):
    """지역지구 이름. 「3종일반주거지역」처럼 「제」가 빠진 기타 이름도 맞춤."""
    for v in (_s(row, "jijiguCdNm"), _s(row, "etcJijigu")):
        v = v.replace(" ", "")
        if v:
            return re.sub(r"^(\d)종", r"제\1종", v)
    return None


def pick_zones(rows):
    """지역지구 item → [(코드, 이름, 구분)]. 같은 이름은 한 번만, 더 구체적인 이름이 있으면 상위 이름(도시지역 ·
    일반주거지역 등)은 뺌. 예: [도시지역, 일반주거지역, 제3종일반주거지역] → [제3종일반주거지역]."""
    out = {}
    for r in rows:
        name = zone_name(r)
        if name and name not in out:
            out[name] = (_s(r, "jijiguCd"), name, _s(r, "jijiguGbCdNm"))
    names = set(out)
    broad = {"도시지역", "관리지역"} if len(names) > 1 else set()
    broad |= {n for n in names if any(o != n and o.endswith(n) for o in names)}   # 일반주거지역 ⊂ 제3종일반주거지역
    return [v for n, v in out.items() if n not in broad]


# ── 받기 ──

class Caller:
    """호출 수를 셈. 한도(--max-calls)에 닿으면 Budget."""

    def __init__(self, fetch, raw_dir, max_calls=None):
        self.fetch, self.raw_dir, self.max_calls, self.calls = fetch, raw_dir, max_calls, 0

    def all_items(self, op, pnu):
        rows, page = [], 1
        while True:
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise Budget()
            self.calls += 1
            data, obj = self.fetch(f"BldRgstHubService/{op}", {**params(pnu), "numOfRows": PAGE_ROWS, "pageNo": page})
            ingest.save_raw(self.raw_dir, "bld_hub", f"{pnu}_{op}_p{page}.xml", data)
            got = datagokr.items(obj)
            rows += got
            total = int(str((obj.get("body") or {}).get("totalCount") or 0) or 0)
            if not got or len(rows) >= total:
                return rows
            page += 1


# ── 적재 ──

def _ensure_parcel(cur, pnu, asof):
    """필지 자리를 만듦(지목 · 면적은 토지대장 몫이라 비움). 법정동 코드표에 없는 동이면 코드만 넣음."""
    cur.execute("""INSERT INTO core.dim_region (lawd_cd, source, asof) VALUES (%s, %s, %s)
                   ON CONFLICT (lawd_cd) DO NOTHING""", (pnu[:10], SOURCE_LABEL, asof))
    jibun = ("산 " if pnu[10] == "2" else "") + str(int(pnu[11:15])) + (f"-{int(pnu[15:19])}" if int(pnu[15:19]) else "")
    cur.execute("""INSERT INTO core.dim_parcel (pnu, lawd_cd, jibun, ledger_type, source, asof)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (pnu) DO NOTHING""",
                (pnu, pnu[:10], jibun, "임야" if pnu[10] == "2" else "토지", SOURCE_LABEL, asof))


def load(conn, run_id, pnu, got, asof):
    """필지 하나의 응답 4개를 올림. 반환: (건물 수, 제외 수)."""
    loaded = rejected = 0
    with conn.cursor() as cur:
        for op, rows in got.items():
            cur.execute(
                """INSERT INTO stg.bld_raw (run_id, pnu, op, row_data) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (pnu, op) DO UPDATE SET run_id = excluded.run_id, row_data = excluded.row_data,
                       loaded_at = now()""",
                (run_id, pnu, op, json.dumps(rows, ensure_ascii=False)))
        _ensure_parcel(cur, pnu, asof)
        complexes = [r[0] for r in cur.execute("SELECT complex_id FROM core.dim_complex WHERE pnu = %s", (pnu,))]

        # 표제부: 동마다 건물 하나
        blds = []
        for row in got.get("getBrTitleInfo", []):
            b = to_building(row)
            if not b["bld_pk"] or not b["pnu"]:
                ingest.reject(conn, run_id, SOURCE, "core.dim_building", "건물 번호 · 지번 없음", row)
                rejected += 1
                continue
            _ensure_parcel(cur, b["pnu"], asof)
            cur.execute(
                """INSERT INTO core.dim_building (bld_pk, pnu, name, main_use, structure, approval_date, floors_above,
                       floors_below, total_area, bc_rat, vl_rat, has_elevator, parking_count, is_main, households,
                       site_area, source, asof)
                   VALUES (%(bld_pk)s, %(pnu)s, %(name)s, %(main_use)s, %(structure)s, %(approval_date)s,
                       %(floors_above)s, %(floors_below)s, %(total_area)s, %(bc_rat)s, %(vl_rat)s, %(has_elevator)s,
                       %(parking_count)s, %(is_main)s, %(households)s, %(site_area)s, %(src)s, %(asof)s)
                   ON CONFLICT (bld_pk) DO UPDATE SET pnu = excluded.pnu, name = excluded.name,
                       main_use = excluded.main_use, structure = excluded.structure,
                       approval_date = excluded.approval_date, floors_above = excluded.floors_above,
                       floors_below = excluded.floors_below, total_area = excluded.total_area,
                       bc_rat = excluded.bc_rat, vl_rat = excluded.vl_rat, has_elevator = excluded.has_elevator,
                       parking_count = excluded.parking_count, is_main = excluded.is_main,
                       households = excluded.households, site_area = excluded.site_area,
                       source = excluded.source, asof = excluded.asof""",
                {**b, "src": SOURCE_LABEL, "asof": asof})
            cur.execute(
                """INSERT INTO core.bridge_parcel_building (pnu, bld_pk, is_main_lot, source, asof)
                   VALUES (%s, %s, true, %s, %s)
                   ON CONFLICT (pnu, bld_pk) DO UPDATE SET is_main_lot = true, asof = excluded.asof""",
                (b["pnu"], b["bld_pk"], SOURCE_LABEL, asof))
            for cid in complexes:
                cur.execute(
                    """INSERT INTO core.bridge_complex_building (complex_id, bld_pk, dong_name, source, asof)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (complex_id, bld_pk) DO UPDATE SET dong_name = excluded.dong_name,
                           asof = excluded.asof""",
                    (cid, b["bld_pk"], b["dong_name"], SOURCE_LABEL, asof))
            blds.append(b["bld_pk"])
            loaded += 1

        # 부속지번: 단지가 걸친 다른 필지. 대장 번호가 동(표제부)이면 그 동에, 총괄표제부면 그 필지의 동 전부에 붙임
        for row in got.get("getBrAtchJibunInfo", []):
            apnu, pk = item_pnu(row, "atch"), _s(row, "mgmBldrgstPk")
            if not apnu:
                ingest.reject(conn, run_id, SOURCE, "core.bridge_parcel_building", "부속지번 형식 오류", row)
                rejected += 1
                continue
            _ensure_parcel(cur, apnu, asof)
            for bk in ([pk] if pk in blds else blds):
                cur.execute(
                    """INSERT INTO core.bridge_parcel_building (pnu, bld_pk, is_main_lot, source, asof)
                       VALUES (%s, %s, false, %s, %s) ON CONFLICT (pnu, bld_pk) DO NOTHING""",
                    (apnu, bk, SOURCE_LABEL, asof))

        # 총괄표제부: 단지 전체 값. 없으면 비움(예전 값이 남지 않게)
        rc = to_recap(got.get("getBrRecapTitleInfo", [])) or {}
        cur.execute(
            """UPDATE core.dim_complex SET site_area_m2 = %s, parking_total = %s, bc_rat = %s, vl_rat = %s,
                   households = coalesce(%s, households), dong_count = coalesce(%s, dong_count)
               WHERE pnu = %s""",
            (rc.get("site_area"), rc.get("parking"), rc.get("bc_rat"), rc.get("vl_rat"),
             rc.get("households"), rc.get("dong_count"), pnu))

        # 지역지구: 이 필지의 용도지역 · 지구. 이름으로 UQ 코드에 맞추고, 없는 이름은 건축물대장 코드로 새로 둠
        cur.execute("DELETE FROM core.bridge_parcel_zone WHERE pnu = %s AND source = %s", (pnu, ZONE_LABEL))
        for code, name, kind in pick_zones(got.get("getBrJijiguInfo", [])):
            hit = cur.execute("SELECT zone_cd FROM core.dim_zone WHERE code_system = 'uq' AND zone_name = %s",
                              (name,)).fetchone()
            zone_cd = hit[0] if hit else f"BLD{code or name}"
            if not hit:
                cur.execute(
                    """INSERT INTO core.dim_zone (zone_cd, code_system, zone_name, law_basis, source, asof)
                       VALUES (%s, 'bld_jijigu', %s, %s, %s, %s) ON CONFLICT (zone_cd) DO NOTHING""",
                    (zone_cd, name, kind or None, ZONE_LABEL, asof))
            cur.execute(
                """INSERT INTO core.bridge_parcel_zone (pnu, zone_cd, overlap, source, asof)
                   VALUES (%s, %s, '포함', %s, %s)
                   ON CONFLICT (pnu, zone_cd) DO UPDATE SET source = excluded.source, asof = excluded.asof""",
                (pnu, zone_cd, ZONE_LABEL, asof))
    return loaded, rejected


def main(argv=None, fetch=datagokr.fetch):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--sigungu", action="append", help="시군구 5자리(기본: config [pilot])")
    ap.add_argument("--pnu", action="append", help="이 필지만(기본: 단지 PNU 전부)")
    ap.add_argument("--max-calls", type=int, help="이번 실행의 최대 호출 수(하루 한도를 나눠 쓸 때)")
    ap.add_argument("--refresh", action="store_true", help="최근에 받은 필지도 다시 받음")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    bcfg = cfg.get("bld", {})
    refresh_days = bcfg.get("refresh_days", 30)
    max_calls = args.max_calls if args.max_calls is not None else bcfg.get("max_calls")
    sigungus = args.sigungu or cfg["pilot"]["sigungu"]
    today = datetime.date.today()
    caller = Caller(fetch, cfg["storage"]["raw_dir"], max_calls)
    with psycopg.connect(cfg["db"]["dsn"]) as conn:
        pnus = args.pnu or [r[0] for r in conn.execute(
            """SELECT DISTINCT pnu FROM core.dim_complex WHERE pnu IS NOT NULL AND left(pnu, 5) = ANY(%s)
               ORDER BY pnu""", (sigungus,)).fetchall()]
        run_id = ingest.start_run(conn, SOURCE)
        loaded = rejected = failed = done = skipped = 0
        note, finished = None, False
        try:
            for pnu in pnus:
                last = ingest.get_cursor(conn, SOURCE, pnu)
                if not args.refresh and last and (today - datetime.date.fromisoformat(last)).days < refresh_days:
                    skipped += 1
                    continue
                task = ingest.add_task(conn, run_id, SOURCE, pnu)
                try:
                    got = {op: caller.all_items(op, pnu) for op in OPS}
                    n, r = load(conn, run_id, pnu, got, today)
                    ingest.end_task(conn, task, "done")
                    if r:  # 적재 제외가 있으면 커서를 지워 다음 실행이 다시 받음
                        ingest.clear_cursor(conn, SOURCE, pnu)
                    else:
                        ingest.set_cursor(conn, SOURCE, pnu, today.isoformat())
                    conn.commit()
                    loaded, rejected, done = loaded + n, rejected + r, done + 1
                    print(f"{pnu}  건물 {n:3}동  제외 {r}  (호출 {caller.calls})")
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
                    if datagokr.is_fatal(e):  # 키 · 하루 한도 문제는 다음 호출도 똑같이 실패하므로 멈춤
                        note = f"멈춤: {e}"[:500]
                        break
            left = len(pnus) - done - skipped
            print(f"필지 {len(pnus)}개 중 이번에 {done}개 · 이미 받음 {skipped}개 · 남음 {left}개 · 호출 {caller.calls}회")
            ingest.finish_run(conn, run_id, "failed" if failed else "done", loaded, rejected, note)
            finished = True
        finally:
            if not finished:  # 예상 못 한 오류 · 중단에도 실행 기록을 「running」으로 남기지 않음
                conn.rollback()
                ingest.finish_run(conn, run_id, "failed", loaded, rejected, "중단됨")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
