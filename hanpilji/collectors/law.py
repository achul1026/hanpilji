"""법제처 국가법령정보 Open API → stg.law_article · stg.law_annex → core.doc_law.

    python -m hanpilji.collectors.law [--config config.toml] [--only 건축법]

법령마다 한 트랜잭션. 실패한 법령은 커서가 그대로라 다음 실행에서 다시 받음.
응답 XML 구조는 2026-10-22 실제 응답(OC=test, 17개 법령)으로 확인함.
"""
import argparse
import datetime
import functools
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET

import psycopg

from hanpilji import config as config_mod
from hanpilji import ingest
from hanpilji.textnorm import search_text

SOURCE = "law_go_kr"
BASE = "http://www.law.go.kr/DRF"
# 법제처는 해외(깃허브 실행기)에서 가끔 응답을 아예 안 줌(2026-10-28 두 번). 한 번에 오래 기다리지 않고,
# 접속 실패가 이만큼 연달아 나면 남은 법령은 다음 실행으로 넘김(커서가 그대로라 그때 받음)
fetch_default = functools.partial(ingest.http_get, tries=2, timeout=15)
MAX_NET_FAILS = 3


def _t(el, tag):
    v = el.findtext(tag)
    return v.strip() if v else None


# ── 응답 파싱 ──

def pick_current(search_xml: bytes, name):
    """검색 결과에서 이름이 정확히 같은 현행 법령 하나 → {mst, law_id, kind}."""
    root = ET.fromstring(search_xml)
    for law in root.iter("law"):
        if _t(law, "법령명한글") != name:
            continue
        if (_t(law, "현행연혁코드") or "현행") != "현행":
            continue
        return {"mst": _t(law, "법령일련번호"), "law_id": _t(law, "법령ID"), "kind": _t(law, "법령구분명")}
    return None


_DELETED = re.compile(r"^제\d+조(의\d+)?\s*삭제\s*(<[^>]*>)?\s*$")


def _article_body(unit):
    """조문내용 + 항 · 호 · 목을 원래 순서대로 이어 붙임."""
    parts = [_t(unit, "조문내용") or ""]
    for hang in unit.findall("항"):
        if _t(hang, "항내용"):
            parts.append(_t(hang, "항내용"))
        for ho in hang.findall("호"):
            if _t(ho, "호내용"):
                parts.append(_t(ho, "호내용"))
            for mok in ho.findall("목"):
                if _t(mok, "목내용"):
                    parts.append(_t(mok, "목내용"))
    return "\n".join(p for p in parts if p)


def parse_law(service_xml: bytes):
    """법령 본문 → (기본정보 dict, 조문 list, 별표 list). 「전문」(장 · 절 제목 줄)은 뺌."""
    root = ET.fromstring(service_xml)
    info = root.find("기본정보")
    if info is None:
        raise ValueError("기본정보가 없는 응답 — 오류 응답이거나 형식이 바뀌었음")
    meta = {
        "law_id": _t(info, "법령ID"),
        "name": _t(info, "법령명_한글"),
        "kind": _t(info, "법종구분"),
        "promulgation_date": _t(info, "공포일자"),
        "effective_date": _t(info, "시행일자"),
    }
    articles = []
    for unit in root.iter("조문단위"):
        if (_t(unit, "조문여부") or "조문") != "조문":
            continue
        no, branch = _t(unit, "조문번호"), _t(unit, "조문가지번호")
        body = _article_body(unit)
        if _DELETED.match(body):  # 「제69조 삭제 <1999.4.30>」 — 내용이 없어 검색에 방해만 됨
            continue
        articles.append({
            "key": unit.get("조문키") or f"{no}-{branch or 0}",
            "no": no,
            "branch": branch,
            "title": _t(unit, "조문제목"),
            "effective_date": _t(unit, "조문시행일자") or meta["effective_date"],
            "body": body,
        })
    annexes = []
    for unit in root.iter("별표단위"):
        annexes.append({
            "key": unit.get("별표키") or f"{_t(unit, '별표번호')}-{_t(unit, '별표가지번호') or 0}",
            "no": _t(unit, "별표번호"),
            "title": _t(unit, "별표제목"),
            "body": _t(unit, "별표내용"),
        })
    return meta, articles, annexes


def article_label(no, branch):
    no = str(int(no)) if no and no.isdigit() else no
    return f"제{no}조" + (f"의{int(branch)}" if branch and branch.isdigit() and int(branch) > 0 else "")


def _date(yyyymmdd):
    return datetime.datetime.strptime(yyyymmdd, "%Y%m%d").date()


# ── 적재 ──

def load_law(conn, run_id, mst, meta, articles, annexes):
    """stg를 이 법령일련번호(mst) 기준으로 갈아 끼우고 core.doc_law에 올림. 반환: 올린 조문 수."""
    conn.execute("DELETE FROM stg.law_article WHERE mst = %s", (mst,))
    conn.execute("DELETE FROM stg.law_annex WHERE mst = %s", (mst,))
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO stg.law_article (run_id, law_id, mst, law_name, law_kind, promulgation_date,
                   effective_date, article_key, article_no, article_branch, article_title, body)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(run_id, meta["law_id"], mst, meta["name"], meta["kind"], meta["promulgation_date"],
              a["effective_date"], a["key"], a["no"], a["branch"], a["title"], a["body"]) for a in articles],
        )
        cur.executemany(
            """INSERT INTO stg.law_annex (run_id, law_id, mst, law_name, annex_key, annex_no, annex_title, body)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            [(run_id, meta["law_id"], mst, meta["name"], x["key"], x["no"], x["title"], x["body"]) for x in annexes],
        )
        eff = _date(meta["effective_date"])
        # 같은 법령의 예전 시행본은 현행에서 내림(지우지 않음 — 과거 시점 질문용)
        cur.execute(
            "UPDATE core.doc_law SET is_current = false WHERE law_id = %s AND effective_date <> %s",
            (meta["law_id"], eff),
        )
        today = datetime.date.today()
        cur.executemany(
            """INSERT INTO core.doc_law (article_id, law_id, law_name, law_kind, article_no, article_title,
                   body, search_text, effective_date, is_current, source, asof)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s)
               ON CONFLICT (article_id) DO UPDATE SET
                   article_title = excluded.article_title, body = excluded.body,
                   search_text = excluded.search_text, is_current = true, asof = excluded.asof""",
            [(f"{meta['law_id']}:{a['no']}-{a['branch'] or 0}:{meta['effective_date']}",
              meta["law_id"], meta["name"], meta["kind"], article_label(a["no"], a["branch"]), a["title"],
              a["body"], search_text(a["body"]), eff, f"법제처 국가법령정보(MST {mst})", today)
             for a in articles],
        )
    return len(articles)


def collect_one(conn, cfg, run_id, name, fetch):
    oc = cfg["law"]["oc"]
    q = urllib.parse.quote(name)
    search_xml = fetch(f"{BASE}/lawSearch.do?OC={oc}&target=law&type=XML&display=100&query={q}")
    found = pick_current(search_xml, name)
    if not found:
        raise LookupError(f"검색 결과에 「{name}」 현행 법령이 없음")
    mst = found["mst"]
    if ingest.get_cursor(conn, SOURCE, f"law:{name}") == mst:
        return "skipped", 0
    body = fetch(f"{BASE}/lawService.do?OC={oc}&target=law&type=XML&MST={mst}")
    path, sha = ingest.save_raw(cfg["storage"]["raw_dir"], "law", f"{name}_{mst}.xml", body)
    if ingest.already_loaded(conn, SOURCE, sha):
        ingest.set_cursor(conn, SOURCE, f"law:{name}", mst)
        conn.commit()
        return "skipped", 0
    task = ingest.add_task(conn, run_id, SOURCE, f"law:{name}", path, sha)
    meta, articles, annexes = parse_law(body)
    if not articles:
        raise ValueError(f"「{name}」 조문 0개 — 응답 형식 확인 필요")
    n = load_law(conn, run_id, mst, meta, articles, annexes)
    ingest.end_task(conn, task, "done")
    ingest.set_cursor(conn, SOURCE, f"law:{name}", mst)
    conn.commit()
    return "done", n


def main(argv=None, fetch=fetch_default):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--only", action="append", help="이 법령만(여러 번 가능)")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    if cfg["law"]["oc"] == "test":
        print("주의: 법제처 테스트 계정(OC=test)으로 요청함. 운영 전에 open.law.go.kr에서 ID를 받아 LAW_OC로 넣을 것", file=sys.stderr)
    names = args.only or cfg["law"]["names"]
    with psycopg.connect(cfg["db"]["dsn"]) as conn:
        run_id = ingest.start_run(conn, SOURCE)
        loaded, failed, net_fails = 0, [], 0
        for i, name in enumerate(names):
            if net_fails >= MAX_NET_FAILS:
                left = names[i:]
                failed += left
                print(f"법제처 접속 실패 {net_fails}번 연속 → 남은 {len(left)}개는 다음 실행에서", file=sys.stderr)
                break
            try:
                status, n = collect_one(conn, cfg, run_id, name, fetch)
                loaded += n
                net_fails = 0
                print(f"{status:8} {n:5}조  {name}")
            except Exception as e:  # 한 법령이 실패해도 나머지는 계속
                conn.rollback()
                task = ingest.add_task(conn, run_id, SOURCE, f"law:{name}", status="failed")
                ingest.end_task(conn, task, "failed", str(e)[:500])
                conn.commit()
                failed.append(name)
                net_fails = net_fails + 1 if isinstance(e, OSError) else 0  # URLError · 시간 초과 · DNS 실패
                print(f"failed       {name}: {e}", file=sys.stderr)
        ingest.finish_run(conn, run_id, "failed" if failed else "done", loaded, len(failed),
                          ("실패: " + ", ".join(failed)) if failed else None)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
