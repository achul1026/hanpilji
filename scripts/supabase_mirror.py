"""Supabase 내용을 로컬 PostgreSQL에 그대로 복사(클라우드 작업 환경용: DB 직접 연결이 막혀 pg_dump를 못 쓸 때).
Supabase에는 조회(SELECT)만 보냄. 받는 쪽은 scripts/init_db.sh --postgis 로 만든 빈 DB.

    createdb hanpilji_mirror
    HANPILJI_DSN=postgresql:///hanpilji_mirror scripts/init_db.sh --postgis --pgvector
    psql -d hanpilji_mirror -c "ALTER TABLE core.dim_building DROP COLUMN geom; ALTER TABLE core.dim_parcel DROP COLUMN geom; ALTER TABLE core.doc_law DROP COLUMN embedding"
    python scripts/supabase_mirror.py [--local postgresql:///hanpilji_mirror] [--verify]

--verify: 줄마다 md5를 떠서 양쪽이 같은지 비교(수집이 도는 중이면 달라짐. 그때는 끝난 뒤 다시).
필요한 환경변수: SUPABASE_ACCESS_TOKEN, HANPILJI_DSN(Supabase 주소, 프로젝트 ref를 읽음)
"""
import argparse
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hanpilji import supabase as s  # noqa: E402

GEO_TYPES = ("geometry", "geography", "vector")


def q(sql):
    return s.query(sql, read_only=True, idempotent=True, retries=5)


def remote_tables():
    return [r["t"] for r in q("""SELECT n.nspname || '.' || c.relname t FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname IN ('ops', 'stg', 'core') AND c.relkind = 'r' ORDER BY 1""")]


def copy_table(conn, t, log=print):
    cols = q(f"""SELECT a.attname c, format_type(a.atttypid, a.atttypmod) ty FROM pg_attribute a
        WHERE a.attrelid = '{t}'::regclass AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum""")
    # 도형은 16진 EWKB 글자로 받음(row_to_json은 GeoJSON으로 바꿔 SRID를 잃음)
    sel = ", ".join(f'"{c["c"]}"::text AS "{c["c"]}"' if c["ty"].startswith(GEO_TYPES) else f'"{c["c"]}"'
                    for c in cols)
    pk = q(f"""SELECT string_agg(quote_ident(a.attname), ',' ORDER BY array_position(i.indkey, a.attnum)) k
        FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = '{t}'::regclass AND i.indisprimary""")[0]["k"] or "ctid"
    n = int(q(f"SELECT count(*) n FROM {t}")[0]["n"])
    sample = int(q(f"SELECT coalesce(sum(pg_column_size(x)), 0) b FROM (SELECT * FROM {t} LIMIT 500) x")[0]["b"])
    per = max(200, min(20000, int(2_500_000 / max(1, sample / 500))))   # 응답 한 번에 약 2.5MB
    got, t0 = 0, time.time()
    for off in range(0, n, per):
        j = q(f"SELECT coalesce(json_agg(row_to_json(x)), '[]')::text j FROM "
              f"(SELECT {sel} FROM {t} ORDER BY {pk} LIMIT {per} OFFSET {off}) x")[0]["j"]
        got += conn.execute(f"INSERT INTO {t} SELECT * FROM json_populate_recordset(null::{t}, %s::json)",
                            (j,)).rowcount
    log(f"{t}: Supabase {n} · 로컬 {got} ({time.time() - t0:.0f}초)")
    return n, got


def verify(conn, tables, log=print):
    conn.execute("SET timezone = 'UTC'")
    bad = []
    for t in tables:
        r = {x["h"] for x in q(f"SET timezone = 'UTC'; SELECT md5(x::text) h FROM {t} x")}
        loc = {x[0] for x in conn.execute(f"SELECT md5(x::text) FROM {t} x")}
        if r != loc:
            bad.append(t)
            log(f"다름 {t}: Supabase에만 {len(r - loc)}줄 · 로컬에만 {len(loc - r)}줄")
    log(f"내용 비교 {len(tables)}개 표, 다른 표 {len(bad)}개")
    return bad


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--local", default="postgresql:///hanpilji_mirror")
    p.add_argument("--verify", action="store_true")
    a = p.parse_args(argv)
    tables = remote_tables()
    with psycopg.connect(a.local, autocommit=True) as conn:
        conn.execute("SET session_replication_role = replica")   # 외래키 검사 없이 넣음(원본이 이미 맞음)
        # 한 번에 비움(표마다 TRUNCATE ... CASCADE 하면 앞서 채운 표가 외래키 따라 같이 비워짐)
        conn.execute("TRUNCATE " + ", ".join(tables))
        short = []
        for t in tables:
            n, got = copy_table(conn, t)
            if n != got:
                short.append(t)
        for r in q("SELECT schemaname || '.' || sequencename s, last_value v FROM pg_sequences "
                   "WHERE schemaname IN ('ops', 'stg', 'core')"):
            if r["v"] is not None:
                conn.execute(f"SELECT setval('{r['s']}', {int(r['v'])}, true)")
        if short:
            sys.exit(f"건수 다른 표: {short}")
        if a.verify and verify(conn, tables):
            sys.exit(1)


if __name__ == "__main__":
    main()
