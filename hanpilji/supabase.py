"""Supabase를 웹(HTTPS)으로만 쓰기. Management API의 SQL 실행 주소를 씀.

클라우드 작업 환경은 프록시가 HTTPS만 통과시켜 DB 직접 연결(5432)이 안 됨. 그래서
  - 복원: 백업(pg_dump)을 로컬 PostgreSQL에 풀고, 스키마 → 데이터(JSON 묶음) → 제약 · 색인 → 권한 순으로 보냄
  - 조회: psycopg 연결처럼 쓰는 얇은 연결(SupabaseConn)로 GPT · 단지 카드가 읽음
필요한 환경변수: SUPABASE_ACCESS_TOKEN(개인 액세스 토큰), HANPILJI_DSN(프로젝트 ref를 사용자명 postgres.<ref>에서 읽음)

    python -m hanpilji.supabase restore [백업 파일]     # 덮어쓰기 복원 + 건수 비교
    python -m hanpilji.supabase counts                 # 테이블별 건수만 비교
"""
import argparse
import datetime
import decimal
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

API = "https://api.supabase.com/v1/projects/{ref}/database/query"
SCHEMAS = ("ops", "stg", "core", "mart")
LOCAL_DSN = "postgresql:///hanpilji_restore?host=/var/run/postgresql"
CHUNK = 900_000    # 요청 본문 상한이 1MB와 4MB 사이라 넉넉히 아래로 잡음
GPT_ROLE = "hanpilji_gpt"


class ApiError(RuntimeError):
    pass


def enabled():
    return bool(os.environ.get("SUPABASE_ACCESS_TOKEN"))


def project_ref(dsn=None):
    dsn = dsn or os.environ.get("HANPILJI_DSN", "")
    m = re.search(r"postgres\.([a-z0-9]{20})\b", dsn) or re.search(r"db\.([a-z0-9]{20})\.supabase\.co", dsn)
    if not m:
        raise ApiError("HANPILJI_DSN에서 Supabase 프로젝트 ref를 찾지 못함(postgres.<ref> 사용자명이 필요)")
    return m.group(1)


def query(sql, read_only=False, ref=None, retries=3):
    """SQL 한 덩어리(여러 문장 가능)를 실행하고 마지막 문장의 결과를 dict 목록으로 돌려줌."""
    body = json.dumps({"query": sql, "read_only": read_only}).encode()
    req = urllib.request.Request(
        API.format(ref=ref or project_ref()), data=body, method="POST",
        headers={"Authorization": "Bearer " + os.environ["SUPABASE_ACCESS_TOKEN"],
                 "Content-Type": "application/json", "User-Agent": "hanpilji"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read() or b"[]") or []
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:500]
            if e.code in (429, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt * 3)
                continue
            raise ApiError(f"HTTP {e.code}: {msg}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(2 ** attempt * 3)
                continue
            raise ApiError(f"접속 실패: {e}") from None


def literal(v):
    """파라미터를 SQL 리터럴로. 도구 인자는 문자열 · 숫자 · 참거짓 · None뿐."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float, decimal.Decimal)):
        if isinstance(v, float) and v != v:
            raise ValueError("NaN")
        return repr(int(v)) if isinstance(v, int) else str(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        v = v.isoformat()
    if isinstance(v, str):
        if "\x00" in v:
            raise ValueError("문자열에 NUL")
        return "'" + v.replace("'", "''") + "'"
    raise TypeError(f"지원하지 않는 인자 형식: {type(v).__name__}")


def bind(sql, params=()):
    """psycopg처럼 %s 자리를 채움(%%는 %로)."""
    params = list(params or ())
    parts = re.split(r"(%%|%s)", sql)
    out = []
    for p in parts:
        if p == "%%":
            out.append("%")
        elif p == "%s":
            if not params:
                raise ValueError("인자가 모자람")
            out.append(literal(params.pop(0)))
        else:
            out.append(p)
    if params:
        raise ValueError("인자가 남음")
    return "".join(out)


class _Col:
    def __init__(self, name):
        self.name = name


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
        self.description = [_Col(k) for k in rows[0]] if rows else []

    def fetchall(self):
        return [tuple(r.values()) for r in self._rows]

    def fetchone(self):
        return tuple(self._rows[0].values()) if self._rows else None


class SupabaseConn:
    """GPT 도구가 쓰는 만큼만 psycopg 연결을 흉내 냄. 읽기 전용 트랜잭션 · GPT 계정 권한 · 5초 제한으로 실행."""

    def __init__(self, ref=None, role=GPT_ROLE):
        self.ref = ref or project_ref()
        self.role = role

    def execute(self, sql, params=()):
        # API의 read_only 옵션은 supabase_read_only_user(모든 스키마를 읽음)로 돌아 GPT 계정으로 좁힐 수 없음.
        # 그래서 요청 한 번을 읽기 전용 트랜잭션으로 열고 그 안에서만 GPT 계정으로 바꿈(다음 요청엔 남지 않음)
        prefix = ("SET TRANSACTION READ ONLY; SET LOCAL standard_conforming_strings = on; "
                  "SET LOCAL statement_timeout = 5000; ")
        if self.role:
            prefix += f"SET LOCAL ROLE {self.role}; "
        # 결과를 줄마다 JSON으로 받아야 numeric이 문자열이 아닌 숫자로 옴(열 순서도 그대로)
        wrapped = f"SELECT row_to_json(t) AS r FROM ({bind(sql, params)}) t"
        return _Cursor([row["r"] for row in query(prefix + wrapped, ref=self.ref)])

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ---------------------------------------------------------------- 복원

def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw).stdout


def _local(sql, dsn=LOCAL_DSN):
    return _run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-qAt", "-c", sql])


def unpack_local(dump):
    """백업을 로컬 임시 DB(hanpilji_restore)에 풂. 원본 DB와 겹치지 않게 따로 만듦."""
    base = LOCAL_DSN.replace("/hanpilji_restore", "/postgres")
    _local("DROP DATABASE IF EXISTS hanpilji_restore", base)
    _local("CREATE DATABASE hanpilji_restore", base)
    subprocess.run(["pg_restore", "--no-owner", "--no-acl", "-d", LOCAL_DSN, dump], check=True)


def _section(dump, section, listfile=None):
    cmd = ["pg_restore", "--no-owner", "-f", "-"]
    cmd += ["-L", listfile] if listfile else ["--no-acl", f"--section={section}"]
    sql = _run(cmd + [dump])
    # psql 전용 줄(\restrict 등)과 search_path를 비우는 줄은 API에서 못 쓰거나 이후 문장을 깨뜨림
    # 확장은 Supabase가 관리하므로 만들기 · 설명 달기는 따로 함
    keep = [l for l in sql.splitlines() if not l.startswith(("\\", "CREATE EXTENSION", "COMMENT ON EXTENSION"))
            and "pg_catalog.set_config('search_path', '', false)" not in l]
    return "\n".join(keep)


def _acl_sql(dump):
    """권한(GRANT)만 뽑음. 기본 권한(DEFAULT ACL)은 원본 소유자 이름이 박혀 있어 빼고 따로 다시 줌."""
    items = [l for l in _run(["pg_restore", "-l", dump]).splitlines()
             if " ACL " in l and "DEFAULT ACL" not in l and not l.startswith(";")]
    with tempfile.NamedTemporaryFile("w", suffix=".list", delete=False) as f:
        f.write("\n".join(items) + "\n")
    try:
        return _section(dump, None, f.name)
    finally:
        os.unlink(f.name)


def _tables():
    rows = _local("SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                  f"WHERE c.relkind IN ('r', 'p') AND n.nspname IN ({', '.join(repr(s) for s in SCHEMAS)}) ORDER BY 1")
    return rows.split()


def _send_table(table, log):
    """한 테이블을 JSON 묶음으로 보냄. 요청이 너무 크다고 하면 묶음을 반으로 줄여 다시 보냄."""
    import psycopg
    sent = 0
    with psycopg.connect(LOCAL_DSN) as conn, conn.cursor(name="dump") as cur:
        cur.itersize = 2000
        cur.execute(f"SELECT row_to_json(t)::text FROM {table} t")
        batch, size = [], 0

        def flush(rows):
            if not rows:
                return 0
            payload = "[" + ",".join(rows) + "]"
            sql = (f"INSERT INTO {table} SELECT * FROM json_populate_recordset(NULL::{table}, "
                   + literal(payload) + ")")
            try:
                query("SET standard_conforming_strings = on; " + sql)
            except ApiError as e:
                if "413" not in str(e) or len(rows) == 1:
                    raise
                half = len(rows) // 2
                return flush(rows[:half]) + flush(rows[half:])
            return len(rows)

        for (row,) in cur:
            batch.append(row)
            size += len(row.encode()) + 1
            if size >= CHUNK:
                sent += flush(batch)
                log(f"  {table}: {sent:,}")
                batch, size = [], 0
        sent += flush(batch)
    return sent


def counts_local():
    out = {}
    for t in _tables():
        out[t] = int(_local(f"SELECT count(*) FROM {t}"))
    return out


def counts_remote(tables):
    sql = " UNION ALL ".join(f"SELECT '{t}' AS t, count(*) AS n FROM {t}" for t in tables)
    return {r["t"]: int(r["n"]) for r in query(sql)}


def restore(dump, log=print):
    t0 = time.time()
    log(f"== 로컬에 풀기: {dump}")
    unpack_local(dump)
    pre = _section(dump, "pre-data")

    log("== Supabase: 기존 스키마 지우고 GPT 계정 · 확장 준비")
    query(f"DROP SCHEMA IF EXISTS {', '.join(SCHEMAS)} CASCADE; "
          f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{GPT_ROLE}') "
          f"THEN CREATE ROLE {GPT_ROLE} NOLOGIN; END IF; END $$; "
          f"GRANT {GPT_ROLE} TO current_user; "
          "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;")
    log("== 스키마 · 테이블 · 함수")
    query(pre)

    tables = _tables()
    total = 0
    for t in tables:
        n = _send_table(t, log)
        total += n
        log(f"{t}: {n:,}줄")

    finish(dump, log)
    log(f"== 끝: {len(tables)}개 테이블 {total:,}줄, {time.time() - t0:.0f}초")
    return compare(tables, log)


def finish(dump, log=print):
    """데이터를 다 보낸 뒤 할 일: 제약 · 색인 · 시퀀스 · 권한. 로컬에 백업이 풀려 있어야 함."""
    post, acl = _section(dump, "post-data"), _acl_sql(dump)
    log("== 제약 · 색인 · 시퀀스")
    query(post)
    seqs = _local("SELECT format('SELECT setval(%L, %s, %s);', schemaname || '.' || sequencename, "
                  "coalesce(last_value, 1), (last_value IS NOT NULL)::text) FROM pg_sequences "
                  f"WHERE schemaname IN ({', '.join(repr(s) for s in SCHEMAS)})")
    if seqs.strip():
        query(seqs)

    log("== 권한")
    query(acl + "\nALTER DEFAULT PRIVILEGES IN SCHEMA mart GRANT SELECT ON TABLES TO hanpilji_gpt;"
          "\nALTER DEFAULT PRIVILEGES IN SCHEMA mart GRANT EXECUTE ON FUNCTIONS TO hanpilji_gpt;"
          "\nALTER DEFAULT PRIVILEGES IN SCHEMA mart REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;"
          "\nANALYZE;")


def compare(tables=None, log=print):
    tables = tables or _tables()
    local, remote = counts_local(), counts_remote(tables)
    bad = [t for t in tables if local.get(t) != remote.get(t)]
    for t in tables:
        mark = "  " if t not in bad else "!!"
        log(f"{mark} {t:32} 백업 {local.get(t, 0):>8,}  Supabase {remote.get(t, 0):>8,}")
    log("건수 모두 같음" if not bad else f"건수 다름: {', '.join(bad)}")
    return not bad


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m hanpilji.supabase")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("restore", help="백업을 Supabase에 덮어쓰기 복원")
    r.add_argument("dump", nargs="?")
    c = sub.add_parser("counts", help="로컬에 푼 백업과 Supabase 건수 비교")
    c.add_argument("dump", nargs="?", help="주면 먼저 로컬에 풂")
    a = p.parse_args(argv)
    if not enabled():
        print("SUPABASE_ACCESS_TOKEN이 없음", file=sys.stderr)
        return 2
    dump = a.dump or _latest_dump()
    if a.cmd == "restore":
        return 0 if restore(dump) else 1
    if a.dump:
        unpack_local(dump)
    return 0 if compare() else 1


def _latest_dump():
    d = "/mnt/project-files/hanpilji-data/"
    files = sorted((os.path.join(d, f) for f in os.listdir(d) if f.startswith("hanpilji_") and f.endswith(".dump")),
                   key=os.path.getmtime)
    if not files:
        raise SystemExit(f"백업 파일이 없음: {d}")
    return files[-1]


if __name__ == "__main__":
    sys.exit(main())
