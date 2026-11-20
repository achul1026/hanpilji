"""서울 생활인구(내국인, 행정동, 서울시 · KT) → core.fact_living_pop.

    SEOUL_API_KEY=... python -m hanpilji.collectors.seoul_living_pop [--since 2025-01-01] [--days 14] [--max-calls 500]

Open API SPOP_LOCAL_RESD_DONG(서울 열린데이터광장 OA-14991). 하루 = 행정동 약 424곳 x 24시간 ≈ 10,200줄(호출 11번 안팎).
Open API 공개는 3~4달 늦고, 최근 약 두 달치만 들고 있음(2026-11-20 첫 적재: 2026-07-31까지 62일, 그 전 날은 「아직 없음」).
기준일마다 커서를 남겨 받은 날은 건너뛰고, 최근 --days일 중 빠진 날만 받음(없는 날은 1번씩 물음).
처음 채울 때는 --since로 시작 날을 줌(하루 11번 x 날 수만큼 부름, --max-calls에 닿으면 다음 실행이 이어 받음).
값은 KT 휴대전화 신호 · 공공 자료로 만든 추정치라 소수가 있고, 3명 이하는 원본에서 * 로 가려져 0으로 넣음.
"""
import argparse
import datetime
import os
import sys

from hanpilji import db, config as config_mod
from hanpilji import ingest
from hanpilji.collectors import seoul_api

SOURCE = "seoul_living_pop"
SOURCE_LABEL = "서울 생활인구(서울특별시 · KT)"
SERVICE = "SPOP_LOCAL_RESD_DONG"
AGES = ("F0T9", "F10T14", "F15T19", "F20T24", "F25T29", "F30T34", "F35T39", "F40T44", "F45T49", "F50T54",
        "F55T59", "F60T64", "F65T69", "F70T74")
SEX_AGE = [f"{sex}_{a}_LVPOP_CO" for sex in ("MALE", "FEMALE") for a in AGES]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0   # * (3명 이하) · 빈 값


def normalize(row):
    """원본 한 줄 → (기준일, 시간대, 행정동, 총생활인구, 성 · 연령 28칸) 또는 None."""
    de, tm, adm = str(row.get("STDR_DE_ID") or ""), str(row.get("TMZON_PD_SE") or ""), str(row.get("ADSTRD_CODE_SE") or "")
    if len(de) != 8 or not de.isdigit() or not tm.isdigit() or len(adm) != 8:
        return None
    day = datetime.date(int(de[:4]), int(de[4:6]), int(de[6:]))
    return (day, int(tm), adm, round(_f(row.get("TOT_LVPOP_CO")), 4), [round(_f(row.get(k)), 4) for k in SEX_AGE])


def load_day(conn, day, rows, asof):
    vals = [v for v in (normalize(r) for r in rows) if v and v[0] == day]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM core.fact_living_pop WHERE stdr_de = %s", (day,))
        with cur.copy("COPY core.fact_living_pop (stdr_de, tmzon, adm_cd, tot, by_sex_age, source, asof) FROM STDIN") as cp:
            seen = set()
            for v in vals:
                if v[:3] in seen:
                    continue
                seen.add(v[:3])
                cp.write_row((*v, SOURCE_LABEL, asof))
    return len(seen)


def days_to_fetch(conn, since, days, today):
    start = since or today - datetime.timedelta(days=days)
    end = today - datetime.timedelta(days=3)   # 화면 안내는 4일 뒤 공개(실제 Open API는 더 늦음)
    out, d = [], start
    while d <= end:
        if not ingest.get_cursor(conn, SOURCE, d.isoformat()):
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def main(argv=None, client=None, today=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--since", type=datetime.date.fromisoformat, help="이 날부터 빠진 날을 모두(처음 채울 때)")
    ap.add_argument("--days", type=int, default=180, help="최근 며칠 중 빠진 날(기본 180, 공개가 3~4달 늦음)")
    ap.add_argument("--max-calls", type=int, default=500)
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    today = today or datetime.date.today()
    client = client or seoul_api.Client(os.environ.get("SEOUL_API_KEY"), max_calls=args.max_calls)
    with db.connect(cfg["db"]["dsn"]) as conn:
        run_id = ingest.start_run(conn, SOURCE)
        loaded, status, note, got = 0, "done", None, 0
        try:
            for day in days_to_fetch(conn, args.since, args.days, today):
                rows = client.rows(SERVICE, day.strftime("%Y%m%d"))
                if not rows:
                    print(f"{day}: 아직 없음")
                    continue
                n = load_day(conn, day, rows, today)
                ingest.set_cursor(conn, SOURCE, day.isoformat(), str(n))
                conn.commit()
                loaded += n
                got += 1
        except seoul_api.LimitReached:
            conn.rollback()
            note = f"호출 한도 {args.max_calls}번에 닿아 멈춤. 다음 실행이 이어 받음"
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            status, note = "failed", f"{type(e).__name__}: {e}"
        ingest.finish_run(conn, run_id, status, loaded, 0, note)
        print(f"서울 생활인구: {status} · {got}일 {loaded}줄 · 호출 {client.calls}번" + (f" · {note}" if note else ""))
    return 1 if status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
