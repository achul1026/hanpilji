"""지식 DB 조각 → core.kb_chunk 벡터(bge-m3). 서버의 venv-embed 파이썬으로 돎.

    python -m hanpilji.collectors.embed [--config config.toml] [--max-minutes 50] [--batch 8]
    python -m hanpilji.collectors.embed --search "주택 임차인 대항력은 언제 생기나"     (결과 눈으로 확인)

1) mart.kb_chunk_refresh(): 법령 · 판례 · 행정규칙 · 해설서 · 개념 노트 → kb_chunk(바뀐 조각은 벡터를 비움)
2) 벡터가 빈 조각을 batch개씩 만들어 넣고 바로 commit(끊겨도 다음 실행이 이어 함). max-minutes가 지나면 멈춤
"""
import argparse
import sys
import time

from hanpilji import db, config as config_mod
from hanpilji import ingest
from hanpilji.embedding import MODEL_NAME, Embedder, to_literal

SOURCE = "kb_embed"


def run(conn, embedder, batch=8, max_minutes=50, log=print):
    """반환 (바뀐 조각, 지운 조각, 새로 만든 벡터, 남은 조각)."""
    changed, removed = conn.execute("SELECT changed, removed FROM mart.kb_chunk_refresh()").fetchone()
    conn.commit()
    # 모델이 바뀌면 예전 벡터를 다시 만듦
    conn.execute("UPDATE core.kb_chunk SET embedding = NULL WHERE model IS DISTINCT FROM %s AND embedding IS NOT NULL",
                  (MODEL_NAME,))
    conn.commit()
    left = conn.execute("SELECT count(*) FROM core.kb_chunk WHERE embedding IS NULL").fetchone()[0]
    log(f"조각 바뀜 {changed} · 지움 {removed} · 벡터 만들 조각 {left}")
    done, start = 0, time.monotonic()
    while True:
        if time.monotonic() - start > max_minutes * 60:
            break
        rows = conn.execute("""SELECT chunk_id, body FROM core.kb_chunk WHERE embedding IS NULL
                               ORDER BY chunk_id LIMIT %s""", (batch,)).fetchall()
        if not rows:
            break
        vecs = embedder.encode([b for _, b in rows])
        with conn.cursor() as cur:
            cur.executemany("""UPDATE core.kb_chunk SET embedding = %s::public.vector, model = %s, embedded_at = now()
                               WHERE chunk_id = %s""",
                            [(to_literal(v), MODEL_NAME, cid) for (cid, _), v in zip(rows, vecs)])
        conn.commit()
        done += len(rows)
        if done % 400 < batch:
            log(f"  {done}/{left}  ({time.monotonic() - start:.0f}초)")
    remain = conn.execute("SELECT count(*) FROM core.kb_chunk WHERE embedding IS NULL").fetchone()[0]
    return changed, removed, done, remain


def search(conn, embedder, query, limit=8):
    vec = to_literal(embedder.encode([query])[0])
    return conn.execute("""SELECT kind, title, label, text_rank, vector_rank, round(similarity::numeric, 3), left(body, 80)
                           FROM mart.q_kb_search(%s, %s::public.vector, %s)""", (query, vec, limit)).fetchall()


def main(argv=None, embedder=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--max-minutes", type=float, default=50)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--search", help="질문 하나로 섞은 검색 결과를 보여 줌(DB를 바꾸지 않음)")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    try:
        embedder = embedder or Embedder()
    except (ImportError, FileNotFoundError) as e:
        print(f"임베딩 모델을 못 엶(venv-embed · models 폴더 확인): {e}", file=sys.stderr)
        return 1
    with db.connect(cfg["db"]["dsn"]) as conn:
        if args.search:
            for r in search(conn, embedder, args.search):
                print(" | ".join("" if x is None else str(x) for x in r))
            return 0
        run_id = ingest.start_run(conn, SOURCE)
        try:
            changed, removed, done, remain = run(conn, embedder, args.batch, args.max_minutes)
        except Exception as e:
            conn.rollback()
            ingest.finish_run(conn, run_id, "failed", 0, 0, str(e)[:500])
            print(f"failed  {e}", file=sys.stderr)
            return 1
        note = f"남은 조각 {remain}개는 다음 실행에서" if remain else None
        print(f"done  벡터 {done}개 · 남은 {remain}개")
        ingest.finish_run(conn, run_id, "done", done, 0, note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
