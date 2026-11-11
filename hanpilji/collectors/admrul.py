"""법제처 국가법령정보 행정규칙(target=admrul) → core.doc_guide · core.doc_guide_part · core.kb_topic_guide.

    python -m hanpilji.collectors.admrul [--config config.toml]

config.toml [admrul] rules 「단원:행정규칙 이름」마다 이름으로 검색해 현행본을 찾고,
행정규칙일련번호가 지난번과 다를 때만 본문을 다시 받음(고시 · 지침은 자주 안 바뀜). 법제처 DRF는 LAW_OC + 등록 IP(개발서버).
응답 모양은 2026-11-11 서버에서 받은 실제 응답(감정평가 실무기준)으로 확인:
  목록 AdmRulSearch/admrul(행정규칙일련번호 · 행정규칙명 · 행정규칙종류 · 발령일자 · 발령번호 · 소관부처명 · 현행연혁구분 ·
       행정규칙ID · 시행일자)
  본문 AdmRulService/행정규칙기본정보(같은 칸 + 현행여부 · 조문형식여부) + 조문내용(CDATA 여러 개 = 조 · 절 하나씩) +
       부칙(부칙내용 …) + 첨부파일. 부칙 · 첨부(별표 hwp · pdf)는 넣지 않음
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
from hanpilji.collectors.prec import NotVerified, _check, _date, _t
from hanpilji.textnorm import search_text

SOURCE = "law_go_kr_admrul"
BASE = "https://www.law.go.kr/DRF"
fetch_default = functools.partial(ingest.http_get, tries=2, timeout=30)
MAX_NET_FAILS = 3
MAX_PART = 3000          # 이보다 긴 절은 줄 경계에서 나눔(검색 · 답에 쓰기 좋게)
KST = datetime.timezone(datetime.timedelta(hours=9))


def search_url(oc, name):
    q = {"OC": oc, "target": "admrul", "type": "XML", "query": name, "display": 100}
    return f"{BASE}/lawSearch.do?" + urllib.parse.urlencode(q)


def body_url(oc, seq):
    return f"{BASE}/lawService.do?" + urllib.parse.urlencode({"OC": oc, "target": "admrul", "type": "XML", "ID": seq})


def norm(name):
    """이름 비교용: 띄어쓰기 · 가운뎃점(· ㆍ) · 쉼표를 뺌."""
    return re.sub(r"[\s·ㆍ‧・,]", "", name or "")


def parse_search(xml_bytes):
    _check(xml_bytes)
    out = []
    for a in ET.fromstring(xml_bytes).iter("admrul"):
        seq = _t(a, "행정규칙일련번호") or ""
        if not seq.isdigit():
            continue
        out.append({"seq": seq, "rule_id": _t(a, "행정규칙ID"), "title": _t(a, "행정규칙명"),
                    "rule_kind": _t(a, "행정규칙종류"), "issued_on": _date(_t(a, "발령일자")),
                    "issued_no": _t(a, "발령번호"), "issuer": _t(a, "소관부처명"),
                    "current": _t(a, "현행연혁구분") == "현행", "effective_on": _date(_t(a, "시행일자"))})
    return out


def pick(rows, name):
    """검색 결과에서 이름이 같은 현행본. 없으면 None."""
    want = norm(name)
    hits = [r for r in rows if r["current"] and norm(r["title"]) == want]
    return max(hits, key=lambda r: (r["effective_on"] or datetime.date.min, r["seq"])) if hits else None


def tidy(text):
    t = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    t = re.sub(r"<img[^>]*>", "", t, flags=re.I)
    t = html.unescape(t)
    t = "\n".join(re.sub(r"[ \t ]+", " ", ln).strip() for ln in t.splitlines())
    return re.sub(r"\n{2,}", "\n", t).strip()


def split_long(text, size=MAX_PART):
    """size보다 긴 글을 줄 경계에서 나눔."""
    if len(text) <= size:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > size:   # 줄 하나가 너무 길면 그 줄을 자름
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:size])
            line = line[size:]
        if cur and len(cur) + 1 + len(line) > size:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


_ARTICLE = re.compile(r"^(제\d+조(?:의\d+)?)\s*(?:\(([^)]{1,60})\))?")


def label_of(text):
    """조각 이름: 「제3조」(제목 「정의」) 꼴이면 그대로, 아니면 첫 줄 앞 40자(예: 「610 토지의 감정평가」)."""
    first = text.split("\n", 1)[0].strip()
    m = _ARTICLE.match(first)
    if m:
        return m.group(1), m.group(2)
    return first[:40], None


def parse_body(xml_bytes):
    _check(xml_bytes)
    root = ET.fromstring(xml_bytes)
    if root.tag != "AdmRulService":
        raise ValueError(f"행정규칙 본문이 아닌 응답: <{root.tag}>")
    info = root.find("행정규칙기본정보")
    if info is None:
        raise ValueError("행정규칙기본정보가 없는 응답")
    rec = {"seq": _t(info, "행정규칙일련번호"), "rule_id": _t(info, "행정규칙ID"), "title": _t(info, "행정규칙명"),
           "rule_kind": _t(info, "행정규칙종류"), "issued_on": _date(_t(info, "발령일자")),
           "issued_no": _t(info, "발령번호"), "issuer": _t(info, "소관부처명"),
           "effective_on": _date(_t(info, "시행일자"))}
    if not (rec["seq"] or "").isdigit() or not (rec["rule_id"] or "").isdigit() or not rec["title"]:
        raise ValueError("행정규칙일련번호 · 행정규칙ID(숫자) · 이름이 없는 응답")
    # 조문내용 속 CDATA 하나 = 조 · 절 하나. ElementTree는 CDATA 경계를 지우므로 원문에서 나눔
    raw = xml_bytes.decode("utf-8", "replace")
    m = re.search(r"<조문내용>(.*?)</조문내용>", raw, re.S)
    chunks = re.findall(r"<!\[CDATA\[(.*?)\]\]>", m.group(1), re.S) if m else []
    if m and not chunks:   # CDATA 없이 글만 온 경우
        chunks = [html.unescape(m.group(1))]
    parts = []
    for c in chunks:
        text = tidy(c)
        if not text:
            continue
        label, heading = label_of(text)
        pieces = split_long(text)
        for i, piece in enumerate(pieces):
            parts.append({"label": label if len(pieces) == 1 else f"{label} ({i + 1}/{len(pieces)})",
                          "heading": heading, "body": piece})
    if not parts:
        raise ValueError(f"{rec['title']}: 조문내용이 비어 있음")
    rec["parts"] = parts
    return rec


def rules(cfg):
    """config [admrul] rules 「단원:이름」 → [(단원, 이름)]. 비었거나 꼴이 틀리면 ValueError."""
    items = cfg.get("admrul", {}).get("rules")
    if not items:
        raise ValueError("config.toml [admrul] rules가 비어 있음")
    out, bad = [], []
    for item in items:
        tid, _, name = str(item).partition(":")
        if not (tid.strip() and name.strip()):
            bad.append(item)
        else:
            out.append((tid.strip(), name.strip()))
    if bad:
        raise ValueError(f"config.toml [admrul] rules에 「단원:이름」 꼴이 아닌 줄: {bad}")
    return out


def save(conn, rec, today):
    doc_id = f"admrul:{rec['rule_id']}"
    url = f"https://www.law.go.kr/행정규칙/{urllib.parse.quote(rec['title'])}"
    conn.execute(
        """INSERT INTO core.doc_guide (doc_id, kind, title, issuer, rule_kind, issued_no, issued_on, effective_on,
                                       version_id, license, url, part_count, source, asof)
           VALUES (%s, '행정규칙', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (doc_id) DO UPDATE SET title = excluded.title, issuer = excluded.issuer,
               rule_kind = excluded.rule_kind, issued_no = excluded.issued_no, issued_on = excluded.issued_on,
               effective_on = excluded.effective_on, version_id = excluded.version_id, url = excluded.url,
               part_count = excluded.part_count, source = excluded.source, asof = excluded.asof""",
        (doc_id, rec["title"], rec["issuer"], rec["rule_kind"], rec["issued_no"], rec["issued_on"],
         rec["effective_on"], rec["seq"], "저작권법 제7조(고시 · 훈령), 출처 표시", url, len(rec["parts"]),
         f"법제처 국가법령정보 행정규칙 「{rec['title']}」({rec['issuer'] or ''} {rec['rule_kind'] or ''} "
         f"제{rec['issued_no'] or '-'}호)", today))
    conn.execute("DELETE FROM core.doc_guide_part WHERE doc_id = %s", (doc_id,))
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO core.doc_guide_part (doc_id, part_no, label, heading, page, body, search_text)
               VALUES (%s, %s, %s, %s, NULL, %s, %s)""",
            [(doc_id, i, p["label"], p["heading"], p["body"], search_text(f"{rec['title']}\n{p['body']}"))
             for i, p in enumerate(rec["parts"], start=1)])
    return doc_id


def collect_rule(conn, cfg, tid, name, fetch, today):
    """한 행정규칙. 반환 (doc_id, 새로 넣은 조각 수 — 바뀐 게 없으면 0)."""
    oc = cfg["law"]["oc"]
    rows = parse_search(fetch(search_url(oc, name)))
    hit = pick(rows, name)
    if not hit:
        near = ", ".join(sorted({r["title"] for r in rows if r["current"]})[:5]) or "없음"
        raise LookupError(f"검색 결과에 「{name}」 현행본 없음 (비슷한 현행: {near})")
    doc_id = f"admrul:{hit['rule_id']}"
    row = conn.execute("SELECT version_id FROM core.doc_guide WHERE doc_id = %s", (doc_id,)).fetchone()
    n = 0
    if not row or row[0] != hit["seq"]:
        body = fetch(body_url(oc, hit["seq"]))
        ingest.save_raw(cfg["storage"]["raw_dir"], "admrul", f"{hit['seq']}.xml", body)
        rec = parse_body(body)
        save(conn, rec, today)
        n = len(rec["parts"])
    conn.execute("""INSERT INTO core.kb_topic_guide (topic_id, doc_id, asof) VALUES (%s, %s, %s)
                    ON CONFLICT (topic_id, doc_id) DO UPDATE SET asof = excluded.asof""", (tid, doc_id, today))
    return doc_id, n


def main(argv=None, fetch=fetch_default):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    try:
        todo = rules(cfg)
    except ValueError as e:
        print(f"행정규칙 설정 오류: {e}", file=sys.stderr)
        return 1
    today = datetime.datetime.now(KST).date()
    with db.connect(cfg["db"]["dsn"]) as conn:
        valid = {r[0] for r in conn.execute("SELECT topic_id FROM core.kb_topic").fetchall()}
        run_id = ingest.start_run(conn, SOURCE)
        loaded, failed, net_fails, linked = 0, [], 0, set()
        for i, (tid, name) in enumerate(todo):
            key = f"{tid}:{name}"
            if net_fails >= MAX_NET_FAILS:
                failed += [f"{t}:{n}" for t, n in todo[i:]]
                print(f"법제처 접속 실패 {net_fails}번 연속 → 남은 {len(todo) - i}개는 다음 실행에서", file=sys.stderr)
                break
            try:
                if tid not in valid:
                    raise LookupError(f"단원 {tid}가 core.kb_topic에 없음(sql/24_kb_topics.sql 확인)")
                doc_id, n = collect_rule(conn, cfg, tid, name, fetch, today)
                conn.commit()
                linked.add((tid, doc_id))
                loaded += n
                net_fails = 0
                print(f"{'done ' if n else 'same '} {n:4}조각  {key}")
            except NotVerified as e:
                conn.rollback()
                failed += [f"{t}:{n}" for t, n in todo[i:]]
                print(f"법제처 사용자 검증 실패(LAW_OC · 서버 IP 확인): {e}", file=sys.stderr)
                break
            except Exception as e:
                conn.rollback()
                failed.append(key)
                net_fails = net_fails + 1 if isinstance(e, OSError) else 0
                print(f"failed  {key}: {e}", file=sys.stderr)
        if not failed:   # 설정에서 뺀 단원 연결 · 행정규칙은 지움(실패가 있으면 지우지 않음)
            conn.execute("""DELETE FROM core.kb_topic_guide t USING core.doc_guide d
                            WHERE t.doc_id = d.doc_id AND d.kind = '행정규칙'
                              AND NOT ((t.topic_id || '|' || t.doc_id) = ANY(%s))""",
                         ([f"{t}|{d}" for t, d in linked],))
            gone = conn.execute("""DELETE FROM core.doc_guide WHERE kind = '행정규칙' AND NOT (doc_id = ANY(%s))
                                   RETURNING title""", (sorted({d for _, d in linked}),)).fetchall()
            for (t,) in gone:
                print(f"removed  {t}")
            conn.commit()
        ingest.finish_run(conn, run_id, "failed" if failed else "done", loaded, len(failed),
                          ("실패: " + ", ".join(failed))[:500] if failed else None)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
