"""DB 접속 한 곳. 스키마를 하나로 합친 서버(개발서버 등)도 같은 코드로 쓰게 함.

코드의 SQL은 ops · stg · core · mart 네 스키마 이름을 그대로 씀(Supabase 구성).
환경변수 HANPILJI_DB_SCHEMA=hanpilji 처럼 스키마 이름을 주면, 보내기 직전에
`core.fact_deal` → `hanpilji.fact_deal` 처럼 바꿔서 보냄. 네 스키마의 표 · 뷰 · 함수 이름이
겹치지 않아(2026-11-02 확인) 하나로 합쳐도 됨. 값이 없으면 아무것도 바꾸지 않음.
"""
import os
import re

import psycopg

SCHEMAS = ("ops", "stg", "core", "mart")
# 따옴표 안의 'core.dim_building'(to_regclass 인자 등)도 SQL이라 같이 바꿈. 앞이 글자 · 숫자 · 점이면 건드리지 않음
_REF = re.compile(r"(?<![\w.])(?:ops|stg|core|mart)\.(?=[A-Za-z_\"])")


def target_schema():
    return os.environ.get("HANPILJI_DB_SCHEMA", "").strip() or None


def rewrite(query, schema=None):
    """SQL 문자열 속 네 스키마 이름을 schema로 바꿈. 문자열이 아니면(psycopg.sql 조합 등) 그대로."""
    schema = schema if schema is not None else target_schema()
    if not schema or not isinstance(query, str):
        return query
    return _REF.sub(schema + ".", query)


class _Rewrite:
    def execute(self, query, params=None, **kw):
        return super().execute(rewrite(query), params, **kw)

    def executemany(self, query, params_seq, **kw):
        return super().executemany(rewrite(query), params_seq, **kw)

    def copy(self, statement, params=None, **kw):
        return super().copy(rewrite(statement), params, **kw)


class Cursor(_Rewrite, psycopg.Cursor):
    pass


class ServerCursor(_Rewrite, psycopg.ServerCursor):
    pass


def connect(dsn, **kw):
    """psycopg.connect와 같음. HANPILJI_DB_SCHEMA가 있으면 SQL의 스키마 이름을 바꾸는 커서를 씀."""
    schema = target_schema()
    if schema:
        kw.setdefault("cursor_factory", Cursor)
        # PostGIS · 확장 함수가 public에 있어도 이름만으로 찾게 함
        opt = kw.get("options", "")
        kw["options"] = (opt + f" -c search_path={schema},public").strip()
    conn = psycopg.connect(dsn, **kw)
    if schema:
        conn.server_cursor_factory = ServerCursor
    return conn
