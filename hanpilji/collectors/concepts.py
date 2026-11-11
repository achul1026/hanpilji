"""개념 노트(data/concepts/*.toml) → core.kb_concept.

    python -m hanpilji.collectors.concepts [--config config.toml] [--dir data/concepts]

노트는 저장소에 직접 쓴 글(학원 교재 문장을 옮기지 않음). 내용이 바뀐 노트만 asof를 새로 하고,
파일에서 지운 노트는 DB에서도 지움. 표준 라이브러리만 씀.
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

SOURCE = "kb_concept_note"
KST = datetime.timezone(datetime.timedelta(hours=9))
DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "concepts"
REQUIRED = ("id", "topic", "term", "summary", "body")
_DATA_REF = re.compile(r"^q_[a-z_]+(:[a-z0-9_]+)?$")


def load_notes(folder):
    """notes_*.toml 전부 → [note]. 빠진 칸 · 겹친 id · 틀린 data 꼴이면 ValueError."""
    notes, bad, seen = [], [], set()
    files = sorted(Path(folder).glob("notes_*.toml"))
    if not files:
        raise ValueError(f"{folder}에 notes_*.toml이 없음")
    for f in files:
        with open(f, "rb") as fh:
            for n in tomllib.load(fh).get("note", []):
                nid = str(n.get("id") or "?")
                miss = [k for k in REQUIRED if not str(n.get(k) or "").strip()]
                if miss:
                    bad.append(f"{f.name} {nid}: {', '.join(miss)} 없음")
                elif not re.fullmatch(r"C\d{3,}", nid):
                    bad.append(f"{f.name} {nid}: id는 C001 꼴")
                elif nid in seen:
                    bad.append(f"{nid}: 두 번 적힘")
                for r in n.get("data", []):
                    if not _DATA_REF.match(str(r)):
                        bad.append(f"{nid}: data 「{r}」는 q_함수 또는 q_market_series:지표 꼴")
                seen.add(nid)
                notes.append(n)
    if bad:
        raise ValueError("; ".join(bad))
    return notes


def version(n):
    keys = ("topic", "term", "summary", "body", "formula", "example", "data", "laws")
    return hashlib.sha256(json.dumps({k: n.get(k) for k in keys}, ensure_ascii=False, sort_keys=True)
                          .encode()).hexdigest()[:16]


def check_refs(conn, notes):
    """data에 적힌 함수 · 지표가 DB에 있는지. 없는 것 목록."""
    funcs = {r[0] for r in conn.execute(
        """SELECT p.proname FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace
           WHERE p.proname LIKE 'q\\_%%' AND s.nspname IN ('mart', current_schema(), 'hanpilji')""").fetchall()}
    stats = {r[0] for r in conn.execute("SELECT stat_id FROM core.dim_stat").fetchall()}
    missing = []
    for n in notes:
        for r in n.get("data", []):
            fn, _, stat = str(r).partition(":")
            if fn not in funcs or (stat and stat not in stats):
                missing.append(f"{n['id']}:{r}")
    return missing


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    try:
        notes = load_notes(args.dir)
    except (ValueError, OSError, tomllib.TOMLDecodeError) as e:
        print(f"개념 노트 오류: {e}", file=sys.stderr)
        return 1
    today = datetime.datetime.now(KST).date()
    with db.connect(cfg["db"]["dsn"]) as conn:
        run_id = ingest.start_run(conn, SOURCE)
        valid = {r[0] for r in conn.execute("SELECT topic_id FROM core.kb_topic").fetchall()}
        bad_topic = [f"{n['id']}:{n['topic']}" for n in notes if n["topic"] not in valid]
        missing = check_refs(conn, notes)
        if bad_topic:
            print(f"failed  없는 단원: {', '.join(bad_topic)}", file=sys.stderr)
            ingest.finish_run(conn, run_id, "failed", 0, len(bad_topic), ("없는 단원: " + ", ".join(bad_topic))[:500])
            return 1
        old = dict(conn.execute("SELECT concept_id, version_id FROM core.kb_concept").fetchall())
        changed = 0
        for n in notes:
            v = version(n)
            if old.get(n["id"]) == v:
                continue
            text = "\n".join(str(x) for x in (n["term"], n["summary"], n["body"], n.get("formula"), n.get("example")) if x)
            conn.execute(
                """INSERT INTO core.kb_concept (concept_id, topic_id, term, summary, body, formula, example, data_refs,
                                                law_refs, search_text, version_id, source, asof)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (concept_id) DO UPDATE SET topic_id = excluded.topic_id, term = excluded.term,
                       summary = excluded.summary, body = excluded.body, formula = excluded.formula,
                       example = excluded.example, data_refs = excluded.data_refs, law_refs = excluded.law_refs,
                       search_text = excluded.search_text, version_id = excluded.version_id, asof = excluded.asof""",
                (n["id"], n["topic"], n["term"].strip(), n["summary"].strip(), n["body"].strip(),
                 (n.get("formula") or "").strip() or None, (n.get("example") or "").strip() or None,
                 list(n.get("data", [])), list(n.get("laws", [])), search_text(text), v,
                 "한필지 개념 노트(직접 작성)", today))
            changed += 1
        gone = conn.execute("DELETE FROM core.kb_concept WHERE NOT (concept_id = ANY(%s)) RETURNING concept_id",
                            ([n["id"] for n in notes],)).fetchall()
        conn.commit()
        note = f"없는 데이터 연결: {', '.join(missing)}"[:500] if missing else None
        if missing:
            print(f"주의  {note}", file=sys.stderr)
        print(f"done  노트 {len(notes)}개 중 바뀐 {changed}개 · 지운 {len(gone)}개")
        ingest.finish_run(conn, run_id, "done", changed, len(missing), note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
