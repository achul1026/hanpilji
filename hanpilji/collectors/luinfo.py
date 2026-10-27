"""국토교통부 토지이용규제정보서비스(행위제한) API → stg.luinfo_raw → core.reg_rule.

    DATA_GO_KR_KEY=... python -m hanpilji.collectors.luinfo [--sigungu 11680] [--zone UQA122] [--act 아파트]

토지이음 행위제한 CSV 대신 쓸 수 있는 길. 시군구 x 용도지역 x 행위마다 한 번 호출하고,
조례까지 반영된 결과라서 core.reg_rule에는 시군구 코드를 붙여 넣음(조례 기준).
응답 모양 · UQ 코드 16개는 실제 응답으로 확인함(2026-10-27). 요청에 type이 없으면 HTTP_ERROR(04)가 옴.
행위 이름은 건물 종류의 세부 이름(아파트 · 휴게음식점 …)으로 물어야 함. 「공동주택」 「근린생활시설」 같은
시설군 이름은 「관련내용 없음」만 옴.
"""
import argparse
import datetime
import json
import re
import sys

import psycopg

from hanpilji import config as config_mod
from hanpilji import datagokr, ingest

SOURCE = "dg_luinfo"
PATH = "arLandUseInfoService/DTarLandUseInfo"


# 건축법 시행령 별표 1의 호 번호 → 시설군 이름. 응답의 근거(LU_REF_LAW_NM1)에서 뽑아 행위 이름 뒤에 붙임
BYEOLPYO1 = {
    1: "단독주택", 2: "공동주택", 3: "제1종근린생활시설", 4: "제2종근린생활시설", 5: "문화및집회시설",
    6: "종교시설", 7: "판매시설", 8: "운수시설", 9: "의료시설", 10: "교육연구시설", 11: "노유자시설",
    12: "수련시설", 13: "운동시설", 14: "업무시설", 15: "숙박시설", 16: "위락시설", 17: "공장",
    18: "창고시설", 19: "위험물저장및처리시설", 20: "자동차관련시설", 21: "동물및식물관련시설",
    22: "자원순환관련시설", 23: "교정시설", 24: "국방·군사시설", 25: "방송통신시설", 26: "발전시설",
    27: "묘지관련시설", 28: "관광휴게시설", 29: "장례시설", 30: "야영장시설",
}
NO_HIT = "관련내용 없음"   # 물어본 이름이 시설 목록에 없을 때 오는 문구


def allowed_value(reg_nm):
    """원본 가능 여부 문구 → 가능 · 조건부 · 불가. 모르는 문구면 None(적재 제외로 남김).
    실제 응답은 「가능」 · 「금지」 두 가지였음(2026-10-27). 조건은 condition_text로 따로 담음."""
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


def _dicts(v):
    return [x for x in datagokr.as_list(v) if isinstance(x, dict)]


def act_label(node_desc, law_ref, act_nm):
    """「휴게음식점」 + 「건축법 시행령 별표 1 제4호아목」 → 「휴게음식점(제2종근린생활시설)」.
    같은 이름이 시설군마다 따로 오므로(1종 · 2종 근생 등) 시설군을 붙여 구분함. 행위가 건축이 아니면 뒤에 붙임."""
    m = re.search(r"별표\s*1\s*제\s*(\d+)\s*호", law_ref or "")
    group = BYEOLPYO1.get(int(m.group(1))) if m else None
    name = node_desc if not group or group == node_desc else f"{node_desc}({group})"
    return name if act_nm in ("", "건축") else f"{name} {act_nm}"


def parse_rules(obj):
    """응답 → [{zone_name, ucode, act_name, reg_nm, allowed, conditions, law_refs}]

    실제 응답(2026-10-27): item마다 actRegList 여러 개(ACT_NM=「건축」, REG_NM=가능 · 금지, QNODE_CONDS/RNUM),
    그 안에 luInfoList 여러 개(NODE_DESC=시설 이름, LU_REF_LAW_NM1=건축법 시행령 별표 1 근거).
    조건 문구(QnodeCond)는 items 아래 item과 나란히 옴. 물어본 이름은 부분 일치로 찾음(「학원」 → 무도학원 · 운전학원 …).
    """
    body = obj.get("body") or {}
    its = body.get("items") or {}
    cond = {}
    for q in _dicts(its.get("QnodeCond") if isinstance(its, dict) else None):
        for n, d in zip(_texts(q.get("RNUM")), _texts(q.get("QNODE_DESC"))):
            cond[n] = d
    out = {}
    for it in datagokr.items(obj):
        for act in _dicts(it.get("actRegList")):
            qc = act.get("QNODE_CONDS")
            nums = _texts(qc.get("RNUM")) if isinstance(qc, dict) else []
            act_nm = (act.get("ACT_NM") or "").strip()
            reg_nm = (act.get("REG_NM") or "").strip()
            for lu in _dicts(act.get("luInfoList")):
                node = (lu.get("NODE_DESC") or "").strip()
                if not node or node == NO_HIT:
                    continue
                law_ref = " ".join((lu.get("LU_REF_LAW_NM1") or "").split())
                r = {
                    "zone_name": (it.get("UNAME") or "").strip(),
                    "ucode": (it.get("UCODE") or "").strip(),
                    "act_name": act_label(node, law_ref, act_nm),
                    "reg_nm": reg_nm,
                    "allowed": allowed_value(reg_nm),
                    "conditions": [cond.get(n, f"조건 {n}") for n in nums],
                    "law_refs": [law_ref] if law_ref else [],
                }
                prev = out.get(r["act_name"])
                if prev and prev["allowed"] != r["allowed"]:
                    # 같은 이름 · 시설군인데 가능 여부가 다름: 합치지 않고 근거를 붙여 따로 둠.
                    # 붙인 이름도 가능 여부가 다른 줄과 겹치면 번호를 더 붙임(서로 다른 결과는 절대 합치지 않음)
                    base = f"{r['act_name']} {law_ref.replace('건축법 시행령 별표 1 ', '') or reg_nm}"
                    r["act_name"], n = base, 2
                    while (prev := out.get(r["act_name"])) and prev["allowed"] != r["allowed"]:
                        r["act_name"], n = f"{base} ({n})", n + 1
                if prev:  # 같은 시설 · 같은 가능 여부가 두 번(근거 목이 다름): 조건과 근거를 합침
                    prev["conditions"] += [c for c in r["conditions"] if c not in prev["conditions"]]
                    prev["law_refs"] += [x for x in r["law_refs"] if x not in prev["law_refs"]]
                    continue
                out[r["act_name"]] = r
    return list(out.values())


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
            (zone_cd, sigungu, r["act_name"], r["allowed"],
             "\n".join(r["conditions"] + ([f"근거: {' · '.join(r['law_refs'])}"] if r["law_refs"] else [])) or None,
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
    ap.add_argument("--refresh", action="store_true", help="최근에 받은 조합도 다시 받음")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    sigungus = args.sigungu or cfg["pilot"]["sigungu"]
    acts = args.act or cfg.get("luinfo", {}).get("acts", [])
    refresh_days = cfg.get("luinfo", {}).get("refresh_days", 30)
    today = datetime.date.today()
    with psycopg.connect(cfg["db"]["dsn"]) as conn:
        zones = args.zone or [r[0] for r in conn.execute(
            "SELECT zone_cd FROM core.dim_zone WHERE code_system = 'uq' ORDER BY zone_cd").fetchall()]
        run_id = ingest.start_run(conn, SOURCE)
        loaded = rejected = failed = 0
        combos = [(sgg, z, act) for sgg in sigungus for z in zones for act in acts]
        finished = False
        try:
            for sgg, z, act in combos:
                key = f"{sgg}:{z}:{act}"
                done = ingest.get_cursor(conn, SOURCE, key)  # 마지막으로 받은 날. 중단돼도 받은 조합은 건너뜀
                if not args.refresh and done and (today - datetime.date.fromisoformat(done)).days < refresh_days:
                    continue
                task = ingest.add_task(conn, run_id, SOURCE, key)
                try:
                    data, obj = fetch(PATH, {"areaCd": sgg, "ucodeList": z, "landUseNm": act, "type": "xml"})
                    ingest.save_raw(cfg["storage"]["raw_dir"], "luinfo", f"{sgg}_{z}_{act}.xml", data)
                    n, r = load(conn, run_id, sgg, z, act, obj, today)
                    ingest.end_task(conn, task, "done")
                    if not r:  # 적재 제외가 있으면 커서를 남기지 않아 다음 실행이 다시 받음
                        ingest.set_cursor(conn, SOURCE, key, today.isoformat())
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
                    if datagokr.is_fatal(e):  # 키 · 한도 문제는 다음 호출도 똑같이 실패하므로 멈춤
                        break
            ingest.finish_run(conn, run_id, "failed" if failed else "done", loaded, rejected)
            finished = True
        finally:
            if not finished:  # 예상 못 한 오류 · 중단에도 실행 기록을 「running」으로 남기지 않음
                conn.rollback()
                ingest.finish_run(conn, run_id, "failed", loaded, rejected, "중단됨")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
