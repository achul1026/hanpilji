"""법제처 국가법령정보 판례(target=prec) → core.doc_prec · core.kb_topic_prec.

    python -m hanpilji.collectors.prec [--config config.toml] [--only M12]

시험 단원(core.kb_topic)마다 config.toml [prec] topics의 주제어로 본문 검색(search=2, 최근 선고 순)을 하고,
대법원 판례만 단원마다 max_per_topic건까지 연결함. 이미 받은 판례는 본문을 다시 받지 않음(판결은 바뀌지 않음).
법제처 DRF는 OC(LAW_OC)와 그 OC에 등록한 IP에서만 응답함(개발서버). 응답 모양은 2026-11-11 서버에서 받은 실제 응답으로 확인:
  목록 PrecSearch/prec(판례일련번호 · 사건명 · 사건번호 · 선고일자 2026.02.26 · 법원명 · 데이터출처명 …)
  본문 PrecService(판례정보일련번호 · 선고일자 20260226 · 판시사항 · 판결요지 · 참조조문 · 참조판례 · 판례내용, 글 속 <br/>)
"""
import argparse
import datetime
import functools
import html
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET

from hanpilji import db, config as config_mod
from hanpilji import ingest
from hanpilji.textnorm import search_text

SOURCE = "law_go_kr_prec"
BASE = "https://www.law.go.kr/DRF"   # https로도 같은 응답(2026-11-11 확인). OC가 주소에 들어가므로 암호화
COURT = "대법원"
fetch_default = functools.partial(ingest.http_get, tries=2, timeout=20)
MAX_NET_FAILS = 3
KST = datetime.timezone(datetime.timedelta(hours=9))


class NotVerified(RuntimeError):
    """법제처가 「사용자 정보 검증에 실패」로 답함(LAW_OC가 없거나 이 IP가 등록 안 됨). 다음 실행에서 다시."""


def search_url(oc, query, page=1):
    q = {"OC": oc, "target": "prec", "type": "XML", "search": 2, "query": query, "display": 100, "page": page,
         "sort": "ddes"}
    return f"{BASE}/lawSearch.do?" + urllib.parse.urlencode(q)


def body_url(oc, prec_id):
    return f"{BASE}/lawService.do?" + urllib.parse.urlencode({"OC": oc, "target": "prec", "type": "XML", "ID": prec_id})


def _check(xml_bytes):
    head = xml_bytes[:400].decode("utf-8", "replace")
    if "<Response>" in head or "검증에 실패" in head:
        why = re.search(r"<result>(.*?)</result>", xml_bytes.decode("utf-8", "replace"), re.S)
        raise NotVerified(why.group(1).strip() if why else head[:120])


def _t(el, tag):
    v = el.findtext(tag)
    return v.strip() if v and v.strip() else None


def clean(text):
    """판시사항 · 판결요지 속 <br/> → 줄바꿈, 다른 태그는 지움, 빈 줄 정리."""
    if not text:
        return None
    t = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    t = "\n".join(line.strip() for line in t.splitlines())
    return re.sub(r"\n{2,}", "\n", t).strip() or None


def _date(v):
    d = re.sub(r"\D", "", v or "")
    try:
        return datetime.datetime.strptime(d, "%Y%m%d").date() if len(d) == 8 else None
    except ValueError:
        return None


def parse_search(xml_bytes):
    """목록 → ([{prec_id, case_no, case_name, court, decided_on}] 대법원만, 이 쪽의 전체 건수)."""
    _check(xml_bytes)
    out, every = [], list(ET.fromstring(xml_bytes).iter("prec"))
    for p in every:
        court = _t(p, "법원명") or _t(p, "데이터출처명")
        if court != COURT or not (_t(p, "판례일련번호") or "").isdigit():   # 일련번호는 숫자만(파일 이름에 씀)
            continue
        out.append({"prec_id": _t(p, "판례일련번호"), "case_no": _t(p, "사건번호"), "case_name": _t(p, "사건명"),
                    "court": court, "decided_on": _date(_t(p, "선고일자"))})
    return out, len(every)


def parse_body(xml_bytes):
    _check(xml_bytes)
    root = ET.fromstring(xml_bytes)
    if root.tag != "PrecService":
        raise ValueError(f"판례 본문이 아닌 응답: <{root.tag}>")
    g = lambda tag: clean(_t(root, tag))
    rec = {"prec_id": _t(root, "판례정보일련번호"), "case_no": _t(root, "사건번호"), "case_name": g("사건명"),
           "court": _t(root, "법원명"), "decided_on": _date(_t(root, "선고일자")), "case_type": _t(root, "사건종류명"),
           "judgment_type": _t(root, "판결유형"), "holding": g("판시사항"), "summary": g("판결요지"),
           "ref_articles": g("참조조문"), "ref_cases": g("참조판례"), "body": g("판례내용")}
    if not (rec["prec_id"] or "").isdigit() or not rec["case_no"]:
        raise ValueError("판례일련번호(숫자) · 사건번호가 없는 응답")
    return rec


def topics(cfg, only=None):
    """config [prec] topics 「단원:주제어」 → [(단원, 주제어)]. 비었거나 꼴이 틀린 줄이 있으면 ValueError."""
    items = cfg.get("prec", {}).get("topics")
    if not items:
        raise ValueError("config.toml [prec] topics가 비어 있음")
    out, bad = [], []
    for item in items:
        tid, _, q = str(item).partition(":")
        if not (tid.strip() and q.strip()):
            bad.append(item)
        elif not only or tid.strip() in only:
            out.append((tid.strip(), q.strip()))
    if bad:
        raise ValueError(f"config.toml [prec] topics에 「단원:주제어」 꼴이 아닌 줄: {bad}")
    return out


def known(conn, prec_id):
    return conn.execute("SELECT 1 FROM core.doc_prec WHERE prec_id = %s", (prec_id,)).fetchone() is not None


def save(conn, rec, today):
    conn.execute(
        """INSERT INTO core.doc_prec (prec_id, case_no, case_name, court, decided_on, case_type, judgment_type,
               holding, summary, ref_articles, ref_cases, body, search_text, source, asof)
           VALUES (%(prec_id)s, %(case_no)s, %(case_name)s, %(court)s, %(decided_on)s, %(case_type)s,
                   %(judgment_type)s, %(holding)s, %(summary)s, %(ref_articles)s, %(ref_cases)s, %(body)s,
                   %(search_text)s, %(source)s, %(asof)s)
           ON CONFLICT (prec_id) DO UPDATE SET case_name = excluded.case_name, holding = excluded.holding,
               summary = excluded.summary, ref_articles = excluded.ref_articles, ref_cases = excluded.ref_cases,
               body = excluded.body, search_text = excluded.search_text, asof = excluded.asof""",
        {**rec, "asof": today, "source": f"법제처 국가법령정보 판례(일련번호 {rec['prec_id']})",
         "search_text": search_text("\n".join(x for x in (rec["case_name"], rec["holding"], rec["summary"]) if x))})


def collect_topic(conn, cfg, tid, query, fetch, limit, pages=3):
    """한 단원 · 주제어: 대법원 판례를 limit건까지 연결. 반환 (새로 받은 판례 수, 연결 수)."""
    oc, today = cfg["law"]["oc"], datetime.datetime.now(KST).date()
    found, seen = [], set()
    for page in range(1, pages + 1):
        rows, n_all = parse_search(fetch(search_url(oc, query, page)))
        for r in rows:
            if r["prec_id"] not in seen:
                seen.add(r["prec_id"])
                found.append(r)
        if len(found) >= limit or n_all < 100:   # 다 모았거나 마지막 쪽
            break
    new = 0
    for r in found[:limit]:
        if not known(conn, r["prec_id"]):
            body = fetch(body_url(oc, r["prec_id"]))
            ingest.save_raw(cfg["storage"]["raw_dir"], "prec", f"{r['prec_id']}.xml", body)
            save(conn, parse_body(body), today)
            new += 1
        conn.execute("""INSERT INTO core.kb_topic_prec (topic_id, prec_id, query, asof) VALUES (%s, %s, %s, %s)
                        ON CONFLICT (topic_id, prec_id, query) DO UPDATE SET asof = excluded.asof""",
                     (tid, r["prec_id"], query, today))
    # 이번 검색에서 상위 limit건 밖으로 밀려난 판례는 이 주제어 연결에서 뺌(판례 자체는 남김)
    conn.execute("DELETE FROM core.kb_topic_prec WHERE topic_id = %s AND query = %s AND NOT (prec_id = ANY(%s))",
                 (tid, query, [r["prec_id"] for r in found[:limit]]))
    return new, min(len(found), limit)


def main(argv=None, fetch=fetch_default):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--only", action="append", help="이 단원만(예: M12, 여러 번 가능)")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    limit = int(cfg.get("prec", {}).get("max_per_topic", 15))
    try:
        todo = topics(cfg, args.only)
    except ValueError as e:   # 설정이 틀리면 조용히 「성공 0건」으로 끝나지 않게
        print(f"판례 설정 오류: {e}", file=sys.stderr)
        return 1
    with db.connect(cfg["db"]["dsn"]) as conn:
        valid = {r[0] for r in conn.execute("SELECT topic_id FROM core.kb_topic").fetchall()}
        run_id = ingest.start_run(conn, SOURCE)
        loaded, failed, net_fails = 0, [], 0
        for i, (tid, query) in enumerate(todo):
            key = f"{tid}:{query}"
            if net_fails >= MAX_NET_FAILS:
                failed += [f"{t}:{q}" for t, q in todo[i:]]
                print(f"법제처 접속 실패 {net_fails}번 연속 → 남은 {len(todo) - i}개는 다음 실행에서", file=sys.stderr)
                break
            try:
                if tid not in valid:
                    raise LookupError(f"단원 {tid}가 core.kb_topic에 없음(sql/24_kb_topics.sql 확인)")
                new, linked = collect_topic(conn, cfg, tid, query, fetch, limit)
                conn.commit()
                loaded += new
                net_fails = 0
                print(f"done  새 판례 {new:3}건 · 연결 {linked:3}건  {key}")
            except NotVerified as e:   # OC · IP 문제는 주제어마다 같음 → 바로 멈춤
                conn.rollback()
                failed += [f"{t}:{q}" for t, q in todo[i:]]
                print(f"법제처 사용자 검증 실패(LAW_OC · 서버 IP 확인): {e}", file=sys.stderr)
                break
            except Exception as e:
                conn.rollback()
                failed.append(key)
                net_fails = net_fails + 1 if isinstance(e, OSError) else 0
                print(f"failed  {key}: {e}", file=sys.stderr)
        ingest.finish_run(conn, run_id, "failed" if failed else "done", loaded, len(failed),
                          ("실패: " + ", ".join(failed))[:500] if failed else None)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
