"""한국자산관리공사 차세대 온비드 부동산 물건목록 API → stg.auction_raw → core.fact_auction.

    DATA_GO_KR_KEY=... python -m hanpilji.collectors.onbid [--sigungu 11680] [--div 0007] [--max-calls 200]

시범 구(config [pilot] sigungu)의 시도 · 시군구 이름으로 입찰 진행 · 예정인 공매 물건을 재산유형마다 끝 쪽까지 받음.
응답 한 줄 = 물건의 한 회차(유찰되면 회차가 늘고 최저입찰가가 내려감). 같은 물건 · 회차는 고쳐 씀.
지번 PNU(ltnoPnu)와 물건명에서 뽑은 「동 지번」을 두면 mart.q_auction_discount가 그것으로 단지를 찾아 실거래와 비교함.

인증키는 공공데이터포털 키(DATA_GO_KR_KEY) 하나지만 이 API를 따로 활용신청해야 함
(「한국자산관리공사_차세대 온비드 부동산 물건목록 조회서비스」, 자동 승인). 신청 전에는 「등록되지 않은 서비스키」(30).
항목 이름은 실제 응답으로 확인함(2026-10-28 강남구: 압류재산 0007 · 기타일반재산 0005만 있고 나머지 유형은 결과 없음).
알아 둘 것: 주소 칸이 따로 없어 물건명(「서울특별시 강남구 도곡동 467-6 대림아크로텔 제17층 제1714호」)이 주소임.
앞으로 열릴 회차까지 한꺼번에 오고(물건 12개에 101줄), 결과 없음은 {"result": {"resultCode": "03"}}로 옴.
숫자 칸(금액 · 면적 · 공매조건번호)은 숫자로, 최저입찰가는 문자로 옴. 개발계정 하루 1,000회.
법원경매(courtauction.go.kr)는 공식 API가 없어 받지 않음(크롤링은 하지 않기로 함).
"""
import argparse
import datetime
import json
import re
import sys

from hanpilji import config as config_mod
from hanpilji import datagokr, ingest

SOURCE = "onbid"
SOURCE_LABEL = "한국자산관리공사 온비드 공매"
BASE = "https://apis.data.go.kr/B010003"
PATH = "OnbidRlstListSrvc2/getRlstCltrList2"
PAGE_ROWS = 500
NODATA = "03"

# 응답 항목 이름(앞에 있는 것부터, 비어 있으면 다음). 실제 응답 기준
FIELDS = {
    "cltr_mng_no": ("cltrMngNo",),
    "pbct_cdtn_no": ("pbctCdtnNo",),
    "name": ("onbidCltrNm",),
    "address": (),                       # 주소 칸이 없음. 물건명을 씀
    "pnu": ("ltnoPnu",),
    "sido": ("lctnSdnm",),
    "sigungu": ("lctnSggnm",),
    "emd": ("lctnEmdNm",),
    "usage": ("cltrUsgSclsCtgrNm", "cltrUsgMclsCtgrNm"),
    "prpt_div": ("prptDivNm",),
    "dsps_method": ("dspsMthodNm",),
    "org": ("rqstOrgNm", "orgNm"),       # 체납 세무서 · 기관(orgNm은 늘 한국자산관리공사)
    "area_m2": ("bldSqms", "landSqms"),
    "appraisal": ("apslEvlAmt",),
    "min_bid": ("lowstBidPrcIndctCont",),
    "fail_count": ("usbdNft",),
    "round_no": ("pbctNsq",),
    "status": ("pbctStatNm",),
    "bid_begin": ("cltrBidBgngDt",),
    "bid_end": ("cltrBidEndDt",),
}
STATUS = {"0001": "입찰준비중", "0002": "입찰진행중", "0003": "입찰마감", "0006": "개찰중", "0009": "수의계약가능",
          "0010": "낙찰", "0011": "유찰", "0012": "취소"}

# 「강남구 대치동 316 은마아파트」 → (대치동, 316). 산 지번 · 「번지」도 받음
JIBUN = re.compile(r"(\S+?(?:동|가|리|읍|면))\s+(산\s*)?(\d+)(?:번지)?(?:\s*-\s*(\d+))?(?:번지)?(?=\s|,|$)")


def _first(row, keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return None


def _num(v):
    if v in (None, ""):
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v))
    return float(m.group().replace(",", "")) if m else None


def _ts(v):
    """「202611101000」 「2026-11-10 10:00」 「20261110」 → datetime. 못 읽으면 None."""
    d = re.sub(r"\D", "", v or "")
    if len(d) < 8:
        return None
    d = (d + "0000")[:12]
    try:
        return datetime.datetime.strptime(d, "%Y%m%d%H%M")
    except ValueError:
        return None


def split_jibun(*texts):
    """주소 · 물건명에서 (법정동, 지번)을 뽑음. 「316」 「12-3」 「산 4」 꼴. 못 찾으면 (None, None)."""
    for t in texts:
        for m in JIBUN.finditer(t or ""):
            emd = m.group(1)
            if emd.endswith(("시", "구", "군")):
                continue
            jibun = ("산" if m.group(2) else "") + m.group(3) + (f"-{m.group(4)}" if m.group(4) else "")
            return emd, jibun
    return None, None


def normalize(row):
    """응답 한 줄 → fact_auction 한 줄(dict). 물건 번호가 없으면 None."""
    out = {k: _first(row, keys) for k, keys in FIELDS.items()}
    if not out["cltr_mng_no"]:
        return None
    out["pbct_cdtn_no"] = out["pbct_cdtn_no"] or out["round_no"] or "0"
    out["area_m2"] = _num(out["area_m2"])
    for k in ("appraisal", "min_bid"):
        v = _num(out[k])
        out[k] = int(round(v)) if v is not None else None
    for k in ("fail_count", "round_no"):
        v = _num(out[k])
        out[k] = int(v) if v is not None else None
    if out["status"] in STATUS:   # 이름 대신 코드가 올 때
        out["status"] = STATUS[out["status"]]
    elif not out["status"]:
        out["status"] = STATUS.get(row.get("pbctStatCd") or "")
    out["bid_begin"], out["bid_end"] = _ts(out["bid_begin"]), _ts(out["bid_end"])
    if not out["address"]:
        out["address"] = out["name"] or " ".join(x for x in (out["sido"], out["sigungu"], out["emd"]) if x) or None
    if out["pnu"] and not re.fullmatch(r"\d{19}", out["pnu"]):
        out["pnu"] = None
    emd, jibun = split_jibun(out["address"], out["name"])
    if out["emd"] and emd and emd != out["emd"]:   # 동 항목과 주소가 다르면 지번을 믿지 않음(단지를 잘못 잇지 않게)
        jibun = None
    out["emd"] = out["emd"] or emd
    out["jibun"] = jibun
    return out


# ───────────── 받기 ─────────────

class Fetcher:
    """호출 수를 세고 원본을 raw/onbid/에 남김. fetch는 시험에서 바꿔 끼움."""

    def __init__(self, http_get, raw_dir, max_calls=None):
        self.http_get, self.raw_dir, self.max_calls, self.calls = http_get, raw_dir, max_calls, 0

    def page(self, sido, sigungu, div, page):
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise Budget()
        params = {"pageNo": page, "numOfRows": PAGE_ROWS, "resultType": "json", "pvctTrgtYn": "N",
                  "prptDivCd": div, "lctnSdnm": sido, "lctnSggnm": sigungu}
        self.calls += 1
        try:
            data, obj = datagokr.fetch(PATH, params, http_get=self.http_get, base=BASE)
        except datagokr.ApiError as e:
            if str(e).startswith(NODATA):   # 결과 없음
                return b"", []
            raise
        ingest.save_raw(self.raw_dir, SOURCE, f"{sigungu}_{div}_p{page}.json", data)
        return data, datagokr.items(obj)


class Budget(Exception):
    """--max-calls에 닿음."""


def collect(f, sido, sigungu, divs):
    """재산유형마다 끝 쪽까지 쪽 하나씩 (요청 키, 원본 줄 목록)을 내줌. 받은 쪽은 바로 적재되어 도중에 멈춰도 남음."""
    for div in divs:
        page = 1
        while True:
            _, rows = f.page(sido, sigungu, div, page)
            if rows:
                yield f"{sido} {sigungu}:{div}:p{page}", rows
            if len(rows) < PAGE_ROWS:
                break
            page += 1


def load(conn, req_key, rows, today):
    """원본 보관 → fact_auction 넣거나 고침. 넣은 줄 수와 버린 줄 수."""
    conn.execute(
        """INSERT INTO stg.auction_raw (source_id, req_key, row_data) VALUES (%s, %s, %s)
           ON CONFLICT (source_id, req_key) DO UPDATE SET row_data = excluded.row_data, loaded_at = now()""",
        (SOURCE, req_key, json.dumps(rows, ensure_ascii=False)))
    n = bad = 0
    for row in rows:
        r = normalize(row)
        if r is None:
            bad += 1
            continue
        conn.execute(
            """INSERT INTO core.fact_auction (cltr_mng_no, pbct_cdtn_no, kind, sigungu_cd, sido, sigungu, emd, jibun, pnu,
                   address, name, usage, prpt_div, dsps_method, org, area_m2, appraisal, min_bid, fail_count,
                   round_no, status, bid_begin, bid_end, source, asof)
               VALUES (%(cltr_mng_no)s, %(pbct_cdtn_no)s, '공매',
                   (SELECT left(lawd_cd, 5) FROM core.dim_region
                     WHERE sigungu = %(sigungu)s AND emd IS NULL AND (%(sido)s::text IS NULL OR sido = %(sido)s) LIMIT 1),
                   %(sido)s, %(sigungu)s, %(emd)s, %(jibun)s, %(pnu)s, %(address)s, %(name)s, %(usage)s, %(prpt_div)s,
                   %(dsps_method)s, %(org)s, %(area_m2)s, %(appraisal)s, %(min_bid)s, %(fail_count)s, %(round_no)s,
                   %(status)s, %(bid_begin)s, %(bid_end)s, %(source)s, %(asof)s)
               ON CONFLICT (cltr_mng_no, pbct_cdtn_no) DO UPDATE SET sigungu_cd = excluded.sigungu_cd,
                   sido = excluded.sido, sigungu = excluded.sigungu, emd = excluded.emd, jibun = excluded.jibun, pnu = excluded.pnu,
                   address = excluded.address, name = excluded.name, usage = excluded.usage,
                   prpt_div = excluded.prpt_div, dsps_method = excluded.dsps_method, org = excluded.org,
                   area_m2 = excluded.area_m2, appraisal = excluded.appraisal, min_bid = excluded.min_bid,
                   fail_count = excluded.fail_count, round_no = excluded.round_no, status = excluded.status,
                   bid_begin = excluded.bid_begin, bid_end = excluded.bid_end, source = excluded.source,
                   asof = excluded.asof""",
            {**r, "source": SOURCE_LABEL, "asof": today})
        n += 1
    return n, bad


def region_names(conn, sigungu_cd):
    row = conn.execute("SELECT sido, sigungu FROM core.dim_region WHERE lawd_cd = %s", (sigungu_cd + "00000",)).fetchone()
    if not row or not row[1]:
        raise SystemExit(f"core.dim_region에 시군구 {sigungu_cd}가 없음")
    return row


def main(argv=None, http_get=ingest.http_get, today=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--sigungu", action="append", help="시군구 코드 5자리(기본 config [pilot] sigungu)")
    ap.add_argument("--div", action="append", help="재산유형 코드(기본 config [onbid] prpt_divs)")
    ap.add_argument("--max-calls", type=int, help="이번 실행의 최대 호출 수")
    args = ap.parse_args(argv)

    import psycopg
    cfg = config_mod.load(args.config)
    ocfg = cfg.get("onbid", {})
    divs = args.div or ocfg.get("prpt_divs", ["0007", "0005", "0010", "0002", "0008"])
    today = today or datetime.date.today()
    f = Fetcher(http_get, cfg["storage"]["raw_dir"], args.max_calls)
    with psycopg.connect(cfg["db"]["dsn"]) as conn:
        run_id = ingest.start_run(conn, SOURCE)
        loaded = rejected = 0
        status, note = "done", None
        try:
            for sgg in args.sigungu or cfg["pilot"]["sigungu"]:
                sido, name = region_names(conn, sgg)
                for req_key, rows in collect(f, sido, name, divs):
                    task = ingest.add_task(conn, run_id, SOURCE, req_key)
                    n, bad = load(conn, req_key, rows, today)
                    ingest.end_task(conn, task, "done")
                    conn.commit()
                    loaded, rejected = loaded + n, rejected + bad
                print(f"{sido} {name}: 회차 {loaded}줄 (버림 {rejected}, 호출 {f.calls})")
        except Budget:
            note = f"--max-calls {args.max_calls}에 닿아 멈춤"
            print(note, file=sys.stderr)
        except Exception as e:
            conn.rollback()
            status, note = "failed", str(e)[:500]
            print(f"failed: {e}", file=sys.stderr)
            if datagokr.is_fatal(e):
                print("  공공데이터포털에서 「한국자산관리공사_차세대 온비드 부동산 물건목록 조회서비스」를 활용신청했는지 확인",
                      file=sys.stderr)
        ingest.finish_run(conn, run_id, status, loaded, rejected, note)
    return 0 if status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
