#!/usr/bin/env bash
# 빈 PostgreSQL DB에 스키마 · 테이블 · 마트 · 권한 · 기준값을 올림. 여러 번 돌려도 됨
#   HANPILJI_DSN=postgresql://user@host/hanpilji scripts/init_db.sh [--postgis] [--pgvector]
set -euo pipefail
DSN="${HANPILJI_DSN:-postgresql://localhost/hanpilji}"
cd "$(dirname "$0")/../sql"
files=(00_schemas.sql 01_ops.sql 02_core.sql)
for a in "$@"; do
  case "$a" in
    --postgis)  files+=(03_core_postgis.sql) ;;
    --pgvector) files+=(04_core_pgvector.sql) ;;
  esac
done
files+=(05_stg_regulation.sql 06_mart.sql 07_roles.sql 08_seed.sql 09_stage1.sql 10_property.sql)
for f in "${files[@]}"; do
  echo "== $f"
  PGOPTIONS="--client-min-messages=warning" psql "$DSN" -v ON_ERROR_STOP=1 -q -f "$f"
done
