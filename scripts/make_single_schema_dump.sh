#!/usr/bin/env bash
# 네 스키마(ops · stg · core · mart) DB를 복사해 스키마 하나로 합친 뒤 백업 파일을 만듦. 원본 DB는 건드리지 않음
#   scripts/make_single_schema_dump.sh <원본 DB 이름> <백업 파일> [스키마 이름, 기본 hanpilji]
# 원본은 같은 PostgreSQL 안의 DB(예: scripts/supabase_mirror.py로 채운 hanpilji_mirror). 접속은 PG* 환경변수로
set -euo pipefail
SRC="$1"; OUT="$2"; TARGET="${3:-hanpilji}"
WORK="${SRC}_single_tmp"
cd "$(dirname "$0")/.."
dropdb --if-exists "$WORK"
createdb -T "$SRC" "$WORK"
psql -d "$WORK" -v ON_ERROR_STOP=1 -q -v target="$TARGET" -f sql/single_schema/to_single_schema.sql >/dev/null
# 확장(PostGIS · pg_trgm)은 받는 쪽에서 미리 만들어 둠. 계정 · 권한은 받는 쪽 grants.sql로
pg_dump -Fc -Z 6 -n "$TARGET" --no-owner --no-acl -d "$WORK" -f "$OUT"
dropdb "$WORK"
echo "만듦: $OUT ($(du -h "$OUT" | cut -f1))"
