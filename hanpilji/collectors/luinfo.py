"""국토교통부 토지이용규제정보서비스(행위제한) API → stg.luinfo_raw → core.reg_rule.

    DATA_GO_KR_KEY=... python -m hanpilji.collectors.luinfo [--sigungu 11680] [--zone UQA122] [--act 공동주택]

토지이음 행위제한 CSV 대신 쓸 수 있는 길. 시군구 x 용도지역 x 행위마다 한 번 호출하고,
조례까지 반영된 결과라서 core.reg_rule에는 시군구 코드를 붙여 넣음(조례 기준).
응답 항목 이름은 포털 명세(swagger, 2026-10-23 확인) 기준. 실제 응답으로는 아직 확인 못 함(미확인).
"""
import argparse
import datetime
import json
import sys

import psycopg

from hanpilji import config as config_mod
from hanpilji import datagokr, ingest

SOURCE = "dg_luinfo"
PATH = "arLandUseInfoService/DTarLandUseInfo"


def allowed_value(reg_nm):
    """원본 가능 여부 문구 → 가능 · 조건부 · 불가. 모르는 문구면 None(적재 제외로 남김)."""
    v = (reg_nm or "").replace(" ", "")
    if not v:
        return None
    if "불가" in v or "금지" in v:
        return "불가"
    if "조건" in v or "예외" in v or "허가" in v or "심의" in v:
        return "조건부"
    if "가능" in v or "허용" in v:
        return "가능"
    return None


def _texts(v):
    return [t for t in (x.strip() if isinstance(x, str) else "" for x in datagokr.as_list(v)) if t]


def parse_rules(obj):
    """응답 → [{zone_name, ucode, act_name, reg_nm, allowed, conditions}]"""
    body = obj.get("body") or {}
    its = body.get("items") or {}
    # 조건 · 예외 문구: 번호 → 내용
    cond = {}
    for q in datagokr.as_list(its.get("QnodeCond") if isinstance(its, dict) else None):
        nums, descs = _texts(q.get("RNUM")), _texts(q.get("QNODE_DESC"))
        for n, d in zip(nums, descs):
            cond[n] = d
    out = []
    for it in datagokr.items(obj):
        for act in datagokr.as_list(it.get("actRegList")):
            nums = _texts((act.get("QNODE_CONDS") or {}).get("RNUM") if isinstance(act.get("QNODE_CONDS"), dict) else None)
            out.append({
                "zone_name": (it.get("UNAME") or "").strip(),
                "ucode": (it.get("UCODE") or "").strip(),
                "act_name": (act.get("ACT_NM") or "").strip(),
                "reg_nm": (act.get("REG_NM") or "").strip(),
                "allowed": allowed_value(act.get("REG_NM")),
                "conditions": [cond.get(n, f"조건 {n}") for n in nums],
            })
    return out


def load(conn, run_id, sigungu, ucode, act_query, obj, asof):
    """반환: (적재, 제외)"""
    conn.execute(
        """INSERT INTO stg.luinfo_raw (run_id, sigungu_cd, ucode, act_query, row_data) VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (sigungu_cd, ucode, act_query) DO UPDATE SET run_id = excluded.run_id, row_data = excluded.row_data,
               loaded_at = now()""",
        (run_id, sigungu, ucode, act_query, json.dumps(obj, ensure_ascii=False)),
    )
    zones = {r[0] for r in conn.execute("SELECT zone_cd FROM core.dim_zone").fetchall()}
    loaded = rejected = 0
    for r in parse_rules(obj):
        zone_cd = r["ucode"] if r["ucode"] in zones else ucode
        if not r["act_name"] or not r["allowed"]:
            ingest.reject(conn, run_id, SOURCE, "core.reg_rule",
                          "행위 이름 빈칸" if not r["act_name"] else f"가능 여부 값 모름: {r['reg_nm']}", r)
            rejected += 1
            continue
        conn.execute(
            """INSERT INTO core.reg_rule (zone_cd, sigungu_cd, act_name, allowed, condition_text, source, asof)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (zone_cd, coalesce(sigungu_cd, ''), act_name)
               DO UPDATE SET allowed = excluded.allowed, condition_text = excluded.condition_text,
                             source = excluded.source, asof = excluded.asof""",
            (zone_cd, sigungu, r["act_name"], r["allowed"], "\n".join(r["conditions"]) or None,
             "국토교통부 토지이용규제정보서비스(행위제한)", asof),
        )
        loaded += 1
    return loaded, rejected


def main(argv=None, fetch=datagokr.fetch):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--sigungu", action="append")
    ap.add_argument("--zone", action="append", help="UQ 코드(기본: dim_zone의 uq 코드 전부)")
    ap.add_argument("--act", action="append", help="행위 이름(기본: config [luinfo] acts)")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    sigungus = args.sigungu or cfg["pilot"]["sigungu"]
    acts = args.act or cfg.get("luinfo", {}).get("acts", [])
    today = datetime.date.today()
    with psycopg.connect(cfg["db"]["dsn"]) as conn:
        zones = args.zone or [r[0] for r in conn.execute(
            "SELECT zone_cd FROM core.dim_zone WHERE code_system = 'uq' ORDER BY zone_cd").fetchall()]
        run_id = ingest.start_run(conn, SOURCE)
        loaded = rejected = failed = 0
        stop = False
        for sgg in sigungus:
            for z in zones:
                for act in acts:
                    key = f"{sgg}:{z}:{act}"
                    task = ingest.add_task(conn, run_id, SOURCE, key)
                    try:
                        data, obj = fetch(PATH, {"areaCd": sgg, "ucodeList": z, "landUseNm": act})
                        ingest.save_raw(cfg["storage"]["raw_dir"], "luinfo", f"{sgg}_{z}_{act}.xml", data)
                        n, r = load(conn, run_id, sgg, z, act, obj, today)
                        ingest.end_task(conn, task, "done")
                        conn.commit()
                        loaded, rejected = loaded + n, rejected + r
                        print(f"{sgg} {z} {act:12} {n}건 제외 {r}")
                    except Exception as e:  # 한 조합이 실패해도 나머지는 계속
                        conn.rollback()
                        t = ingest.add_task(conn, run_id, SOURCE, key, status="failed")
                        ingest.end_task(conn, t, "failed", str(e)[:500])
                        conn.commit()
                        failed += 1
                        print(f"failed {key}: {e}", file=sys.stderr)
                        if isinstance(e, datagokr.ApiError) and ("KEY" in str(e).upper() or "LIMIT" in str(e).upper()):
                            stop = True
                    if stop:
                        break
                if stop:
                    break
            if stop:
                break
        ingest.finish_run(conn, run_id, "failed" if failed else "done", loaded, rejected)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
