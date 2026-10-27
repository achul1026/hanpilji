"""국토교통부 아파트 매매 · 전월세 실거래가 API → stg.rtms_raw → core.fact_deal · core.fact_rent.

    DATA_GO_KR_KEY=... python -m hanpilji.collectors.rtms [--months 36] [--only trade|rent] [--sigungu 11680]

시군구 x 계약월 x 종류마다 한 트랜잭션. 끝난 달은 커서에 남겨 다시 받지 않되,
최근 몇 달(config [rtms] refetch_months)은 신고 · 해제가 늦게 붙으므로 매번 다시 받음.
응답 항목 이름은 포털 명세(swagger, 2026-10-23 확인) 기준. 실제 응답으로는 아직 확인 못 함(미확인).
"""
import argparse
import datetime
import hashlib
import json
import sys

import psycopg

from hanpilji import config as config_mod
from hanpilji import datagokr, ingest

KINDS = {
    "trade": {"source": "dg_apt_trade", "path": "RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade", "label": "아파트"},
    "rent": {"source": "dg_apt_rent", "path": "RTMSDataSvcAptRent/getRTMSDataSvcAptRent", "label": "아파트"},
}
SOURCE_LABEL = {"trade": "국토교통부 아파트 매매 실거래가", "rent": "국토교통부 아파트 전월세 실거래가"}
PAGE_ROWS = 1000


# ── 값 다듬기 ──

def _s(row, k):
    v = row.get(k)
    return v.strip() if isinstance(v, str) else ("" if v is None else str(v))


def _int(v):
    """「82,500」 「 -1」 → 정수. 빈칸이면 None."""
    v = (v or "").replace(",", "").strip()
    return int(v) if v.lstrip("-").isdigit() else None


def _area(v):
    try:
        return round(float((v or "").replace(",", "")), 2)
    except ValueError:
        return None


def contract_date(row):
    return datetime.date(int(_s(row, "dealYear")), int(_s(row, "dealMonth")), int(_s(row, "dealDay")))


def complex_key(row):
    """같은 단지면 매매 · 전월세가 같은 값이 되도록 시군구 · 법정동 · 지번 · 단지명으로 만듦."""
    parts = [_s(row, "sggCd"), _s(row, "umdNm"), _s(row, "jibun"), _s(row, "aptNm").replace(" ", "")]
    return "rtms:" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def row_key(kind, row):
    """재수집해도 같은 값. 해제 여부처럼 나중에 바뀌는 항목은 넣지 않음."""
    keys = ["sggCd", "umdNm", "jibun", "aptNm", "aptDong", "dealYear", "dealMonth", "dealDay", "excluUseAr", "floor"]
    keys += ["dealAmount"] if kind == "trade" else ["deposit", "monthlyRent", "contractTerm", "contractType"]
    return hashlib.sha1("|".join(_s(row, k) for k in keys).encode()).hexdigest()


def lawd_cd(row, sigungu):
    """법정동 10자리. 응답에 umdCd가 있으면 쓰고, 없으면 시군구 단위(뒤 5자리 0)로 둠."""
    umd = _s(row, "umdCd")
    return f"{sigungu}{umd}" if len(umd) == 5 and umd.isdigit() else f"{sigungu}00000"


def to_deal(row, sigungu):
    """매매 한 건 → fact_deal 값. 못 쓰는 줄이면 (None, 이유)."""
    amount, area = _int(_s(row, "dealAmount")), _area(_s(row, "excluUseAr"))
    try:
        d = contract_date(row)
    except ValueError:
        return None, "계약일 형식 오류"
    if not amount or amount <= 0:
        return None, "거래금액 없음"
    if not area or area <= 0:
        return None, "전용면적 없음"
    return {
        "deal_id": row_key("trade", row),
        "lawd_cd": lawd_cd(row, sigungu),
        "complex_id": complex_key(row),
        "contract_date": d,
        "amount": amount * 10_000,                     # 만원 → 원
        "area": area,
        "floor": _int(_s(row, "floor")),
        "build_year": _int(_s(row, "buildYear")),
        "is_cancelled": _s(row, "cdealType") not in ("", "N"),   # 해제되면 「O」
    }, None


def to_rent(row, sigungu):
    deposit, monthly, area = _int(_s(row, "deposit")), _int(_s(row, "monthlyRent")) or 0, _area(_s(row, "excluUseAr"))
    try:
        d = contract_date(row)
    except ValueError:
        return None, "계약일 형식 오류"
    if deposit is None or deposit < 0:
        return None, "보증금 없음"
    if not area or area <= 0:
        return None, "전용면적 없음"
    return {
        "rent_id": row_key("rent", row),
        "lawd_cd": lawd_cd(row, sigungu),
        "complex_id": complex_key(row),
        "contract_date": d,
        "area": area,
        "floor": _int(_s(row, "floor")),
        "deposit": deposit * 10_000,
        "monthly_rent": monthly * 10_000,
        "contract_kind": _s(row, "contractType") or None,
        "renewal": (_s(row, "useRRRight") == "사용") if _s(row, "useRRRight") else None,
    }, None


# ── 적재 ──

def _ensure_dims(cur, lawd, row, asof):
    cur.execute(
        """INSERT INTO core.dim_region (lawd_cd, emd, source, asof) VALUES (%s, %s, %s, %s)
           ON CONFLICT (lawd_cd) DO NOTHING""",
        (lawd, _s(row, "umdNm") if not lawd.endswith("00000") else None, "국토교통부 실거래가 응답", asof),
    )
    cur.execute(
        """INSERT INTO core.dim_complex (complex_id, name, lawd_cd, source, asof) VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (complex_id) DO UPDATE SET asof = excluded.asof""",
        (complex_key(row), _s(row, "aptNm") or "(이름 없음)", lawd, "국토교통부 실거래가(시군구 · 동 · 지번 · 단지명)", asof),
    )


def load_month(conn, run_id, kind, sigungu, ym, rows, asof):
    """한 달 치를 stg에 갈아 끼우고 core에 올림. 반환: (적재, 제외)."""
    src = KINDS[kind]["source"]
    label = KINDS[kind]["label"]
    loaded = rejected = 0
    seen, keys = {}, []
    with conn.cursor() as cur:
        cur.execute("DELETE FROM stg.rtms_raw WHERE source_id = %s AND sigungu_cd = %s AND deal_ym = %s",
                    (src, sigungu, ym))
        for row in rows:
            # 모든 항목이 같은 거래가 한 달에 여러 건이면 「-1」 「-2」를 붙여 따로 셈(같은 응답이면 같은 번호)
            base = row_key(kind, row)
            n = seen.get(base, 0)
            seen[base] = n + 1
            key = base if n == 0 else f"{base}-{n}"
            cur.execute(
                """INSERT INTO stg.rtms_raw (run_id, source_id, sigungu_cd, deal_ym, row_key, row_data)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (source_id, row_key) DO UPDATE SET run_id = excluded.run_id, row_data = excluded.row_data""",
                (run_id, src, sigungu, ym, key, json.dumps(row, ensure_ascii=False)),
            )
            v, why = (to_deal if kind == "trade" else to_rent)(row, sigungu)
            if v is None:
                ingest.reject(conn, run_id, src, "core.fact_deal" if kind == "trade" else "core.fact_rent", why, row)
                rejected += 1
                continue
            v["deal_id" if kind == "trade" else "rent_id"] = key
            keys.append(key)
            _ensure_dims(cur, v["lawd_cd"], row, asof)
            if kind == "trade":
                cur.execute(
                    """INSERT INTO core.fact_deal (deal_id, lawd_cd, complex_id, deal_type, contract_date, amount, area,
                           floor, build_year, is_cancelled, match_method, source, asof)
                       VALUES (%(deal_id)s, %(lawd_cd)s, %(complex_id)s, %(t)s, %(contract_date)s, %(amount)s, %(area)s,
                               %(floor)s, %(build_year)s, %(is_cancelled)s, '시군구·동·지번·단지명', %(src)s, %(asof)s)
                       ON CONFLICT (deal_id) DO UPDATE SET lawd_cd = excluded.lawd_cd, complex_id = excluded.complex_id,
                           build_year = excluded.build_year, is_cancelled = excluded.is_cancelled, asof = excluded.asof""",
                    {**v, "t": label, "src": SOURCE_LABEL["trade"], "asof": asof},
                )
            else:
                cur.execute(
                    """INSERT INTO core.fact_rent (rent_id, lawd_cd, complex_id, rent_type, contract_date, area, floor,
                           deposit, monthly_rent, contract_kind, renewal, source, asof)
                       VALUES (%(rent_id)s, %(lawd_cd)s, %(complex_id)s, %(t)s, %(contract_date)s, %(area)s, %(floor)s,
                               %(deposit)s, %(monthly_rent)s, %(contract_kind)s, %(renewal)s, %(src)s, %(asof)s)
                       ON CONFLICT (rent_id) DO UPDATE SET lawd_cd = excluded.lawd_cd, complex_id = excluded.complex_id,
                           renewal = excluded.renewal, asof = excluded.asof""",
                    {**v, "t": label, "src": SOURCE_LABEL["rent"], "asof": asof},
                )
            loaded += 1
        # 이번 응답에 없는 그 달 거래(정정 · 삭제된 신고)는 core에서도 뺌
        y, m = int(ym[:4]), int(ym[4:])
        first = datetime.date(y, m, 1)
        nxt = datetime.date(y + (m == 12), m % 12 + 1, 1)
        if kind == "trade":
            cur.execute(
                """DELETE FROM core.fact_deal WHERE left(lawd_cd, 5) = %s AND contract_date >= %s AND contract_date < %s
                   AND source = %s AND NOT (deal_id = ANY(%s))""",
                (sigungu, first, nxt, SOURCE_LABEL[kind], keys),
            )
        else:
            cur.execute(
                """DELETE FROM core.fact_rent WHERE left(lawd_cd, 5) = %s AND contract_date >= %s AND contract_date < %s
                   AND source = %s AND NOT (rent_id = ANY(%s))""",
                (sigungu, first, nxt, SOURCE_LABEL[kind], keys),
            )
    return loaded, rejected


def fetch_month(kind, sigungu, ym, raw_dir, fetch=datagokr.fetch):
    """한 달 치를 쪽마다 받아 원본을 보관하고 항목 list를 돌려줌."""
    rows, page = [], 1
    while True:
        data, obj = fetch(KINDS[kind]["path"],
                          {"LAWD_CD": sigungu, "DEAL_YMD": ym, "pageNo": page, "numOfRows": PAGE_ROWS})
        ingest.save_raw(raw_dir, f"rtms_{kind}", f"{sigungu}_{ym}_p{page}.xml", data)
        got = datagokr.items(obj)
        rows += got
        total = int(str((obj.get("body") or {}).get("totalCount") or 0) or 0)
        if not got or len(rows) >= total:
            return rows
        page += 1


def months_back(n, today=None):
    """이번 달부터 거꾸로 n개월 YYYYMM."""
    today = today or datetime.date.today()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def main(argv=None, fetch=datagokr.fetch):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--months", type=int, help="몇 개월 치(기본: config [rtms] months)")
    ap.add_argument("--only", choices=list(KINDS), action="append")
    ap.add_argument("--sigungu", action="append", help="시군구 5자리(기본: config [pilot])")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    rcfg = cfg.get("rtms", {})
    months = months_back(args.months or rcfg.get("months", 36))
    refetch = set(months[: rcfg.get("refetch_months", 3)])
    kinds = args.only or list(KINDS)
    sigungus = args.sigungu or cfg["pilot"]["sigungu"]
    today = datetime.date.today()
    failed_any = stop = False
    with psycopg.connect(cfg["db"]["dsn"]) as conn:
        for kind in kinds:
            if stop:
                break
            src = KINDS[kind]["source"]
            run_id = ingest.start_run(conn, src)
            total_loaded = total_rejected = failed = 0
            finished = False
            try:
                for sgg in sigungus:
                    for ym in months:
                        ckey = f"{sgg}:{ym}"
                        if ym not in refetch and ingest.get_cursor(conn, src, ckey) == "done":
                            continue
                        task = ingest.add_task(conn, run_id, src, ckey)
                        try:
                            rows = fetch_month(kind, sgg, ym, cfg["storage"]["raw_dir"], fetch)
                            n, r = load_month(conn, run_id, kind, sgg, ym, rows, today)
                            ingest.end_task(conn, task, "done")
                            ingest.set_cursor(conn, src, ckey, "done")
                            conn.commit()
                            total_loaded += n
                            total_rejected += r
                            print(f"{kind:5} {sgg} {ym}  {n:5}건  제외 {r}")
                        except Exception as e:  # 한 달이 실패해도 나머지는 계속(커서는 그대로)
                            conn.rollback()
                            t = ingest.add_task(conn, run_id, src, ckey, status="failed")
                            ingest.end_task(conn, t, "failed", str(e)[:500])
                            conn.commit()
                            failed += 1
                            print(f"failed {kind} {sgg} {ym}: {e}", file=sys.stderr)
                            if datagokr.is_fatal(e):  # 키 · 한도 문제는 다음 호출도 똑같이 실패하므로 전부 멈춤
                                stop = True
                                break
                    if stop:
                        break
                ingest.finish_run(conn, run_id, "failed" if failed else "done", total_loaded, total_rejected)
                finished = True
            finally:
                if not finished:  # 예상 못 한 오류 · 중단(Ctrl+C)에도 실행 기록을 「running」으로 남기지 않음
                    conn.rollback()
                    ingest.finish_run(conn, run_id, "failed", total_loaded, total_rejected, "중단됨")
            failed_any = failed_any or failed > 0
    return 1 if failed_any else 0


if __name__ == "__main__":
    sys.exit(main())
