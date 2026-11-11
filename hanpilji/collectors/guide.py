"""정부 해설 책자(쪽마다 글) → core.doc_guide · core.doc_guide_part · core.kb_topic_guide.

    python -m hanpilji.collectors.guide [--config config.toml] [--dir data/guides]

data/guides/catalog.toml의 [[doc]]마다 <file>.jsonl(scripts/guide_extract.py가 만든 쪽 단위 글)을 읽어 넣음.
JSONL 내용이 지난번과 같으면(sha256) 건너뜀. 목록에서 빠진 해설서는 DB에서도 지움(행정규칙은 건드리지 않음).
표준 라이브러리만 씀(서버에 PDF 도구를 두지 않음).
"""
import argparse
import datetime
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

from hanpilji import db, config as config_mod
from hanpilji import ingest
from hanpilji.textnorm import search_text

SOURCE = "gov_guide_pdf"
KST = datetime.timezone(datetime.timedelta(hours=9))
DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "guides"
REQUIRED = ("file", "title", "issuer", "license")


def catalog(folder):
    """catalog.toml → [doc dict]. 빠진 칸 · 틀린 이름 · 겹친 file이 있으면 ValueError."""
    with open(Path(folder) / "catalog.toml", "rb") as f:
        docs = tomllib.load(f).get("doc", [])
    seen, bad = set(), []
    for d in docs:
        miss = [k for k in REQUIRED if not str(d.get(k) or "").strip()]
        name = str(d.get("file") or "")
        if miss:
            bad.append(f"{name or '?'}: {', '.join(miss)} 없음")
        elif not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            bad.append(f"{name}: file은 영문 · 숫자 · _ · - 만")
        elif name in seen:
            bad.append(f"{name}: 두 번 적힘")
        seen.add(name)
        if d.get("issued_on") and not isinstance(d["issued_on"], datetime.date):
            bad.append(f"{name}: issued_on은 따옴표 없이 YYYY-MM-DD")
    if bad:
        raise ValueError("; ".join(bad))
    return docs


def read_pages(path):
    raw = Path(path).read_bytes()
    pages = []
    for n, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        r = json.loads(line)
        if not str(r.get("text") or "").strip() or not (isinstance(r.get("page"), int) or r.get("label")):
            raise ValueError(f"{Path(path).name} {n}번째 줄: text와 page(숫자) 또는 label이 없음")
        pages.append(r)
    if not pages:
        raise ValueError(f"{Path(path).name}: 쪽이 하나도 없음")
    return pages, hashlib.sha256(raw).hexdigest()[:16]


def load_doc(conn, d, folder, today, valid_topics):
    """해설서 하나. 반환: 넣은 쪽 수(바뀐 게 없으면 0)."""
    doc_id = f"pdf:{d['file']}"
    pages, version = read_pages(Path(folder) / f"{d['file']}.jsonl")
    topics = [str(t) for t in d.get("topics", [])]
    unknown = [t for t in topics if t not in valid_topics]
    if unknown:
        raise LookupError(f"단원 {unknown}가 core.kb_topic에 없음")
    row = conn.execute("SELECT version_id FROM core.doc_guide WHERE doc_id = %s", (doc_id,)).fetchone()
    changed = not row or row[0] != version
    conn.execute(
        """INSERT INTO core.doc_guide (doc_id, kind, title, issuer, issued_no, issued_on, version_id, license, url,
                                       part_count, source, asof)
           VALUES (%s, '해설서', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (doc_id) DO UPDATE SET title = excluded.title, issuer = excluded.issuer,
               issued_no = excluded.issued_no, issued_on = excluded.issued_on, version_id = excluded.version_id,
               license = excluded.license, url = excluded.url, part_count = excluded.part_count,
               source = excluded.source, asof = CASE WHEN core.doc_guide.version_id = excluded.version_id
                                                     THEN core.doc_guide.asof ELSE excluded.asof END""",
        (doc_id, d["title"], d["issuer"], d.get("edition"), d.get("issued_on"), version, d["license"], d.get("url"),
         len(pages), f"{d['issuer']} 「{d['title']}」" + (f" {d['edition']}" if d.get("edition") else ""), today))
    if changed:
        conn.execute("DELETE FROM core.doc_guide_part WHERE doc_id = %s", (doc_id,))
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO core.doc_guide_part (doc_id, part_no, label, heading, page, body, search_text)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                [(doc_id, i, p.get("label") or f"{p.get('printed') or p['page']}쪽", p.get("heading"), p.get("page"),
                  p["text"].strip(),
                  search_text(f"{d['title']}\n{p['text'].strip()}")) for i, p in enumerate(pages, start=1)])
    conn.execute("DELETE FROM core.kb_topic_guide WHERE doc_id = %s AND NOT (topic_id = ANY(%s))", (doc_id, topics))
    for t in topics:
        conn.execute("""INSERT INTO core.kb_topic_guide (topic_id, doc_id, asof) VALUES (%s, %s, %s)
                        ON CONFLICT (topic_id, doc_id) DO NOTHING""", (t, doc_id, today))
    return len(pages) if changed else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    try:
        docs = catalog(args.dir)
    except (ValueError, OSError, tomllib.TOMLDecodeError) as e:
        print(f"해설서 목록 오류: {e}", file=sys.stderr)
        return 1
    today = datetime.datetime.now(KST).date()
    with db.connect(cfg["db"]["dsn"]) as conn:
        valid = {r[0] for r in conn.execute("SELECT topic_id FROM core.kb_topic").fetchall()}
        run_id = ingest.start_run(conn, SOURCE)
        loaded, failed = 0, []
        for d in docs:
            try:
                n = load_doc(conn, d, args.dir, today, valid)
                conn.commit()
                loaded += n
                print(f"{'done ' if n else 'same '} {n:4}쪽  {d['title']}")
            except Exception as e:
                conn.rollback()
                failed.append(d["file"])
                print(f"failed  {d['file']}: {e}", file=sys.stderr)
        if not failed:   # 목록에서 뺀 해설서는 지움(실패가 있으면 지우지 않음)
            gone = conn.execute("""DELETE FROM core.doc_guide WHERE kind = '해설서' AND NOT (doc_id = ANY(%s))
                                   RETURNING title""", ([f"pdf:{d['file']}" for d in docs],)).fetchall()
            for (t,) in gone:
                print(f"removed  {t}")
            conn.commit()
        ingest.finish_run(conn, run_id, "failed" if failed else "done", loaded, len(failed),
                          ("실패: " + ", ".join(failed))[:500] if failed else None)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
