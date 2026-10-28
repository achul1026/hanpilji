"""시장 · 거시 지표 → stg.market_raw → core.dim_stat · core.fact_market.

    ECOS_KEY=... RONE_KEY=... KOSIS_KEY=... python -m hanpilji.collectors.market [--only ecos] [--stat base_rate] [--months 120]
    python -m hanpilji.collectors.market --find rone 매매가격지수     # 통계표 찾기(표 번호 확인용, DB 안 씀)
    python -m hanpilji.collectors.market --items 121Y006             # ECOS 통계표의 항목 코드 보기

세 곳:
  한국은행 ECOS(ecos.bok.or.kr): 기준금리 · 주택담보대출 금리 · 국고채 3년 · 소비자물가. 키 ECOS_KEY
  한국부동산원 R-ONE(www.reb.or.kr): 월간 아파트 매매 · 전세 가격지수(전국 · 서울 · 강남구). 키 RONE_KEY
  KOSIS(kosis.kr): 주민등록 인구 · 세대(강남구 · 서울 · 전국). 키 KOSIS_KEY
키가 없는 곳은 건너뛰고 나머지만 받음. 지표 하나가 실패해도 나머지는 계속.

표 번호 · 항목 코드 · 응답 모양은 2026-10-28 첫 실제 수집에서 확인함(tests/fixtures/*_real.json).
번호가 바뀌면 --find · --items로 찾아 SERIES나 config.toml [market]에서 고칠 것.
R-ONE은 표 이름으로 표 번호를 찾음(이름이 딱 하나만 맞아야 받음, config.toml [market.rone]에 정하면 그 번호).
KOSIS는 한 번에 40,000셀까지라 지역 코드(objL1)를 정해서 받음.
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse

from hanpilji import ingest

REGION = {"전국": "0000000000", "서울": "1100000000", "서울특별시": "1100000000", "강남구": "1168000000"}
KEYS = {"ecos": "ECOS_KEY", "rone": "RONE_KEY", "kosis": "KOSIS_KEY"}
PAGE = 1000
# R-ONE 지역 분류 번호(CLS_ID). 2026-10-28 매매 · 전세 아파트 표에서 확인(두 표가 같은 번호를 씀)
RONE_CLS = {"전국": 500001, "서울": 500008, "강남구": 530038}

# 지표 목록. id는 한필지 이름(fact_market.stat_id), regions는 받을 지역 이름(응답의 지역 이름과 맞춤)
SERIES = [
    {"id": "base_rate", "src": "ecos", "name": "한국은행 기준금리", "unit": "%", "cycle": "M",
     "ecos": {"stat": "722Y001", "item": "0101000"}, "note": "월말 기준. 차이는 %p로 읽음"},
    {"id": "mortgage_rate", "src": "ecos", "name": "예금은행 주택담보대출 금리(신규취급액 기준)", "unit": "%", "cycle": "M",
     "ecos": {"stat": "121Y006", "item": "BECBLA0302"}, "note": "그달 새로 나간 대출의 가중평균 금리(연 %)"},
    {"id": "ktb3y_rate", "src": "ecos", "name": "국고채 3년 금리", "unit": "%", "cycle": "M",
     "ecos": {"stat": "721Y001", "item": "5020000"}, "note": "월평균"},
    {"id": "cpi", "src": "ecos", "name": "소비자물가지수", "unit": "2020=100", "cycle": "M",
     "ecos": {"stat": "901Y009", "item": "0"}, "note": "물가 상승률은 전년 같은 달 대비(yoy_pct)로 읽음"},
    {"id": "apt_sale_idx", "src": "rone", "name": "아파트 매매가격지수(월간)", "unit": "지수", "cycle": "M",
     "rone": {"table": r"^\(월\) 매매가격지수_아파트$", "cycle": "MM", "cls": RONE_CLS},
     "regions": ["전국", "서울", "강남구"], "note": "한국부동산원 전국주택가격동향조사. 기준 시점 지수=100"},
    {"id": "apt_jeonse_idx", "src": "rone", "name": "아파트 전세가격지수(월간)", "unit": "지수", "cycle": "M",
     "rone": {"table": r"^\(월\) 전세가격지수_아파트$", "cycle": "MM", "cls": RONE_CLS},
     "regions": ["전국", "서울", "강남구"], "note": "한국부동산원 전국주택가격동향조사. 기준 시점 지수=100"},
    {"id": "population", "src": "kosis", "name": "주민등록 인구", "unit": "명", "cycle": "M",
     "kosis": {"org": "101", "tbl": "DT_1B040A3", "itm": "T20", "objL1": "00 11 11680"},
     "regions": ["전국", "서울특별시", "강남구"],
     "note": "행정안전부 주민등록인구현황(월말)"},
    {"id": "households", "src": "kosis", "name": "주민등록 세대 수", "unit": "세대", "cycle": "M",
     "kosis": {"org": "101", "tbl": "DT_1B040B3", "itm": "T1", "objL1": "00 11 11680"},
     "regions": ["전국", "서울특별시", "강남구"],
     "note": "행정안전부 주민등록세대현황(월말)"},
]
BY_ID = {s["id"]: s for s in SERIES}


class ApiError(RuntimeError):
    """기관이 준 오류 응답."""


class Fatal(ApiError):
    """키가 틀렸거나 막힘 — 그 기관의 다음 호출도 똑같이 실패하므로 그 기관은 멈춤."""


def api_key(src):
    key = os.environ.get(KEYS[src], "").strip()
    if not key:
        raise Fatal(f"환경변수 {KEYS[src]} 가 없음")
    return key


def _json(data):
    text = data.decode("utf-8-sig").strip()
    try:
        return json.loads(text)
    except ValueError:
        raise ApiError(f"JSON이 아닌 응답: {text[:200]}") from None


# ───────────── 기관별 주소 · 응답 풀기 ─────────────

def ecos_url(key, s, start, end, p_from, p_to):
    e = s["ecos"]
    parts = ["StatisticSearch", key, "json", "kr", str(start), str(end), e["stat"], s["cycle"], p_from, p_to, e["item"]]
    return "https://ecos.bok.or.kr/api/" + "/".join(urllib.parse.quote(x, safe="") for x in parts)


def ecos_parse(data, service="StatisticSearch"):
    """→ (줄 목록, 전체 건수). 「해당하는 데이터가 없습니다」(INFO-200)는 빈 결과."""
    obj = _json(data)
    if isinstance(obj, dict) and "RESULT" in obj:
        code, msg = obj["RESULT"].get("CODE", ""), obj["RESULT"].get("MESSAGE", "")
        if code == "INFO-200":
            return [], 0
        raise (Fatal if code in ("INFO-100", "ERROR-300", "ERROR-301", "ERROR-602") else ApiError)(f"ECOS {code} {msg}")
    body = obj.get(service) if isinstance(obj, dict) else None
    if not isinstance(body, dict):
        raise ApiError(f"ECOS 응답 모양이 다름: {str(obj)[:200]}")
    return body.get("row") or [], int(body.get("list_total_count") or 0)


def ecos_points(rows, s):
    for r in rows:
        v = _num(r.get("DATA_VALUE"))
        if v is not None:
            yield "전국", str(r["TIME"]), v, r.get("UNIT_NAME") or s["unit"]


def rone_url(key, service, page, **params):
    q = {"KEY": key, "Type": "json", "pIndex": page, "pSize": PAGE, **params}
    return f"https://www.reb.or.kr/r-one/openapi/{service}.do?{urllib.parse.urlencode(q)}"


def rone_parse(data, service="SttsApiTblData"):
    """→ (줄 목록, 전체 건수). 서울 열린데이터와 같은 모양: {서비스: [{head: [...]}, {row: [...]}]}"""
    obj = _json(data)
    if isinstance(obj, dict) and "RESULT" in obj:   # 오류는 서비스 이름 없이 RESULT만 옴
        code, msg = obj["RESULT"].get("CODE", ""), obj["RESULT"].get("MESSAGE", "")
        if code == "INFO-200":
            return [], 0
        raise (Fatal if code in ("INFO-100", "ERROR-290", "ERROR-300") else ApiError)(f"R-ONE {code} {msg}")
    parts = obj.get(service) if isinstance(obj, dict) else None
    if not isinstance(parts, list):
        raise ApiError(f"R-ONE 응답 모양이 다름: {str(obj)[:200]}")
    total, rows = 0, []
    for p in parts:
        for h in p.get("head", []):
            if "list_total_count" in h:
                total = int(h["list_total_count"])
            res = h.get("RESULT")
            if res and res.get("CODE") not in ("INFO-000", None):
                raise ApiError(f"R-ONE {res.get('CODE')} {res.get('MESSAGE', '')}")
        rows += p.get("row", [])
    return rows, total


def rone_points(rows, s):
    want = set(s.get("regions", []))
    for r in rows:
        # 지역 이름은 CLS_NM(예: 강남구), 묶음 이름은 CLS_FULLNM(예: 서울>동남권>강남구)에 옴
        name = (r.get("CLS_NM") or str(r.get("CLS_FULLNM", "")).split(">")[-1]).strip()
        v = _num(r.get("DTA_VAL"))
        if name in want and v is not None:
            yield name, str(r["WRTTIME_IDTFR_ID"])[:6], v, r.get("UI_NM") or s["unit"]


def kosis_url(key, s, p_from, p_to):
    k = s["kosis"]
    q = {"method": "getList", "apiKey": key, "itmId": k["itm"] + " ", "objL1": k.get("objL1", "ALL") + " ", "format": "json",
         "jsonVD": "Y", "prdSe": s["cycle"], "startPrdDe": p_from, "endPrdDe": p_to, "orgId": k["org"], "tblId": k["tbl"]}
    return f"https://kosis.kr/openapi/Param/statisticsParameterData.do?{urllib.parse.urlencode(q)}"


def kosis_parse(data):
    """→ (줄 목록, 건수). 오류는 {"err": "20", "errMsg": ...}. 30(결과 없음)은 빈 결과"""
    obj = _json(data)
    if isinstance(obj, dict):
        code, msg = str(obj.get("err", "")), obj.get("errMsg", "")
        if code == "30":
            return [], 0
        raise (Fatal if code in ("10", "11") else ApiError)(f"KOSIS {code} {msg}".strip())
    return obj, len(obj)


def kosis_points(rows, s):
    want = set(s.get("regions", []))
    for r in rows:
        v = _num(r.get("DT"))
        if r.get("C1_NM", "").strip() in want and v is not None:
            yield r["C1_NM"].strip(), str(r["PRD_DE"]), v, r.get("UNIT_NM") or s["unit"]


def _num(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except ValueError:
        return None


# ───────────── 받기 ─────────────

class Fetcher:
    """원본을 raw/에 남기고 (응답 바이트)를 돌려줌. fetch는 시험에서 바꿔 끼움."""

    def __init__(self, fetch, raw_dir):
        self.fetch, self.raw_dir, self.calls = fetch, raw_dir, 0

    def get(self, src, name, url):
        data = self.fetch(url)
        self.calls += 1
        ingest.save_raw(self.raw_dir, src, name, data)
        return data


def fetch(url):
    return ingest.http_get(url)


def months_back(n, today):
    y, m = today.year, today.month - n + 1
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y}{m:02d}", f"{today.year}{today.month:02d}"


def rone_table(f, key, s, override=None):
    """R-ONE 표 번호: config에 있으면 그것, 없으면 표 목록에서 이름(정규식)이 맞고 주기가 같은 표 하나."""
    if override:
        return override
    rows, page = [], 1
    while True:
        got, total = rone_parse(f.get("rone", f"tables_p{page}.json", rone_url(key, "SttsApiTbl", page)), "SttsApiTbl")
        rows += got
        if not got or len(rows) >= total:
            break
        page += 1
    pat = re.compile(s["rone"]["table"])
    hit = [r for r in rows if pat.search(r.get("STATBL_NM", "")) and r.get("DTACYCLE_CD", s["rone"]["cycle"]) == s["rone"]["cycle"]]
    if len(hit) != 1:
        names = ", ".join(f"{r.get('STATBL_ID')} {r.get('STATBL_NM')}" for r in hit[:8]) or "없음"
        raise ApiError(f"R-ONE 표 이름이 {len(hit)}개 맞음({names}) — config.toml [market.rone]에 {s['id']} = \"표 번호\"로 정할 것")
    return hit[0]["STATBL_ID"]


def collect(f, s, months, today, overrides):
    """지표 하나를 받아 (요청 키, 원본 줄 목록, 점 목록[(지역, 기간, 값, 단위)])를 돌려줌."""
    key = api_key(s["src"])
    p_from, p_to = months_back(months, today)
    raws, points = [], []
    if s["src"] == "ecos":
        start = 1
        while True:
            rows, total = ecos_parse(f.get("ecos", f"{s['id']}_{start}.json", ecos_url(key, s, start, start + PAGE - 1, p_from, p_to)))
            raws += rows
            if not rows or start + PAGE - 1 >= total:
                break
            start += PAGE
        points = list(ecos_points(raws, s))
    elif s["src"] == "rone":
        tbl = rone_table(f, key, s, overrides.get(s["id"]))
        for region in s["regions"]:   # 지역(CLS_ID) · 기간을 걸어 받음. 전 지역을 받으면 120개월에 27쪽
            page = 1
            while True:
                rows, total = rone_parse(f.get("rone", f"{s['id']}_{s['rone']['cls'][region]}_p{page}.json",
                                               rone_url(key, "SttsApiTblData", page, STATBL_ID=tbl, DTACYCLE_CD=s["rone"]["cycle"],
                                                        CLS_ID=s["rone"]["cls"][region], START_WRTTIME=p_from, END_WRTTIME=p_to)))
                raws += rows
                if not rows or page * PAGE >= total:
                    break
                page += 1
        points = [p for p in rone_points(raws, s) if p_from <= p[1] <= p_to]
    else:
        rows, _ = kosis_parse(f.get("kosis", f"{s['id']}.json", kosis_url(key, s, p_from, p_to)))
        raws, points = rows, list(kosis_points(rows, s))
    return raws, points


def load(conn, s, raws, points, today):
    """원본 보관 → dim_stat 덮어쓰기 → fact_market 넣거나 고침. 같은 지역 · 기간이 두 번 오면 처음 것."""
    ref = {"ecos": lambda: f"한국은행 ECOS {s['ecos']['stat']}", "rone": lambda: "한국부동산원 R-ONE 전국주택가격동향조사",
           "kosis": lambda: f"KOSIS {s['kosis']['tbl']}"}[s["src"]]()
    conn.execute(
        """INSERT INTO stg.market_raw (source_id, req_key, row_data) VALUES (%s, %s, %s)
           ON CONFLICT (source_id, req_key) DO UPDATE SET row_data = excluded.row_data, loaded_at = now()""",
        (s["src"], s["id"], json.dumps(raws, ensure_ascii=False)))
    conn.execute(
        """INSERT INTO core.dim_stat (stat_id, name, unit, cycle, source_id, source, note, asof)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (stat_id) DO UPDATE SET name = excluded.name, unit = excluded.unit, cycle = excluded.cycle,
               source_id = excluded.source_id, source = excluded.source, note = excluded.note, asof = excluded.asof""",
        (s["id"], s["name"], s["unit"], s["cycle"], s["src"], ref, s.get("note"), today))
    seen, n = set(), 0
    for region, period, value, unit in points:
        k = (REGION[region], period)
        if k in seen:
            continue
        seen.add(k)
        conn.execute(
            """INSERT INTO core.fact_market (lawd_cd, stat_id, period, value, unit, source, asof)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (lawd_cd, stat_id, period) DO UPDATE SET value = excluded.value, unit = excluded.unit,
                   source = excluded.source, asof = excluded.asof""",
            (k[0], s["id"], period, value, unit, ref, today))
        n += 1
    return n


def find(src, word, fetch=fetch):
    """통계표 이름으로 찾기(표 번호를 확인할 때). DB를 쓰지 않음."""
    key = api_key(src)
    get = lambda url: fetch(url)
    if src == "ecos":
        rows, _ = ecos_parse(get(f"https://ecos.bok.or.kr/api/StatisticTableList/{key}/json/kr/1/10000"), "StatisticTableList")
        hit = [f"{r.get('STAT_CODE')}  {r.get('CYCLE') or '-'}  {r.get('STAT_NAME')}" for r in rows if word in r.get("STAT_NAME", "")]
    elif src == "rone":
        rows, page = [], 1
        while True:
            got, total = rone_parse(get(rone_url(key, "SttsApiTbl", page)), "SttsApiTbl")
            rows += got
            if not got or len(rows) >= total:
                break
            page += 1
        hit = [f"{r.get('STATBL_ID')}  {r.get('DTACYCLE_CD')}  {r.get('STATBL_NM')}" for r in rows if word in r.get("STATBL_NM", "")]
    else:
        q = {"method": "getList", "apiKey": key, "searchNm": word, "format": "json", "jsonVD": "Y"}
        rows, _ = kosis_parse(get(f"https://kosis.kr/openapi/statisticsSearch.do?{urllib.parse.urlencode(q)}"))
        hit = [f"{r.get('ORG_ID')}  {r.get('TBL_ID')}  {r.get('TBL_NM')}" for r in rows]
    print("\n".join(hit) or "없음")
    return 0


def items(stat, fetch=fetch):
    rows, _ = ecos_parse(fetch(f"https://ecos.bok.or.kr/api/StatisticItemList/{api_key('ecos')}/json/kr/1/1000/{stat}"),
                         "StatisticItemList")
    print("\n".join(f"{r.get('ITEM_CODE')}  {r.get('CYCLE')}  {r.get('ITEM_NAME')}" for r in rows) or "없음")
    return 0


def main(argv=None, fetch=fetch, today=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--only", choices=sorted(KEYS), action="append", help="이 기관만")
    ap.add_argument("--stat", action="append", choices=sorted(BY_ID), help="이 지표만")
    ap.add_argument("--months", type=int, help="이번 달부터 거꾸로 몇 개월(기본 config [market] months)")
    ap.add_argument("--find", nargs=2, metavar=("SRC", "WORD"), help="통계표 이름 찾기")
    ap.add_argument("--items", metavar="STAT", help="ECOS 통계표의 항목 코드")
    args = ap.parse_args(argv)
    if args.find:
        return find(args.find[0], args.find[1], fetch)
    if args.items:
        return items(args.items, fetch)

    import psycopg
    from hanpilji import config as config_mod
    cfg = config_mod.load(args.config)
    mcfg = cfg.get("market", {})
    months = args.months or mcfg.get("months", 120)
    today = today or datetime.date.today()
    series = [s for s in SERIES if (not args.stat or s["id"] in args.stat) and (not args.only or s["src"] in args.only)]
    nokey = sorted({s["src"] for s in series if not os.environ.get(KEYS[s["src"]], "").strip()})
    for src in nokey:
        print(f"건너뜀 {src}: 환경변수 {KEYS[src]} 없음", file=sys.stderr)
    series = [s for s in series if s["src"] not in nokey]
    f = Fetcher(fetch, cfg["storage"]["raw_dir"])
    failed, stopped = 0, set()
    with psycopg.connect(cfg["db"]["dsn"]) as conn:
        runs = {}
        for s in series:
            src = s["src"]
            if src in stopped:
                continue
            if src not in runs:
                runs[src] = [ingest.start_run(conn, src), 0, 0]
            task = ingest.add_task(conn, runs[src][0], src, s["id"])
            try:
                raws, points = collect(f, s, months, today, mcfg.get(src, {}))
                n = load(conn, s, raws, points, today)
                ingest.end_task(conn, task, "done")
                conn.commit()
                runs[src][1] += n
                periods = sorted({p[1] for p in points})
                print(f"{s['id']:16} {n:5}줄  {periods[0] if periods else '-'}~{periods[-1] if periods else '-'}  (호출 {f.calls})")
                if not points:
                    print(f"  주의: {s['id']} 받은 값이 0개 — 지역 이름 · 항목 코드를 --find/--items로 확인", file=sys.stderr)
            except Exception as e:   # 지표 하나가 실패해도 나머지는 계속
                conn.rollback()
                t = ingest.add_task(conn, runs[src][0], src, s["id"], status="failed")
                ingest.end_task(conn, t, "failed", str(e)[:500])
                conn.commit()
                failed += 1
                runs[src][2] += 1
                print(f"failed {s['id']}: {e}", file=sys.stderr)
                if isinstance(e, Fatal):
                    stopped.add(src)
        for src, (run_id, loaded, bad) in runs.items():
            ingest.finish_run(conn, run_id, "failed" if bad else "done", loaded,
                              note=f"실패 {bad}개" + (" · 키 오류로 멈춤" if src in stopped else "") if bad else None)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
