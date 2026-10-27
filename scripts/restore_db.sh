#!/usr/bin/env bash
# pg_dump -Fc 덤프를 HANPILJI_DSN의 DB(Supabase 포함)에 복원하고 테이블별 건수를 보여 줌
#   HANPILJI_DSN=postgresql://... scripts/restore_db.sh <덤프 파일> [--clean]
#   --clean : 같은 이름의 객체를 먼저 지우고 다시 만듦(최종 덤프로 덮어쓸 때)
set -euo pipefail
DSN="${HANPILJI_DSN:-postgresql://localhost/hanpilji}"
DUMP="${1:?덤프 파일 경로를 주세요}"
shift
opts=(--no-owner --no-comments --single-transaction --exit-on-error)
for a in "$@"; do
  case "$a" in
    --clean) opts+=(--clean --if-exists) ;;
  esac
done

# 덤프의 권한(GRANT)이 가리키는 읽기 전용 계정을 먼저 만듦. Supabase는 관리 계정이 원본과 달라 소유자는 옮기지 않음
psql "$DSN" -v ON_ERROR_STOP=1 -q <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hanpilji_gpt') THEN
        CREATE ROLE hanpilji_gpt LOGIN;
    END IF;
END $$;
SQL

# 기본 권한(DEFAULT ACL)은 원본 DB 계정 이름이 박혀 있어 빼고, 복원한 계정 기준으로 다시 줌
list="$(mktemp)"
trap 'rm -f "$list"' EXIT
pg_restore -l "$DUMP" | grep -v ' DEFAULT ACL ' > "$list"

echo "== pg_restore $(basename "$DUMP")"
pg_restore "${opts[@]}" -L "$list" -d "$DSN" "$DUMP"
psql "$DSN" -v ON_ERROR_STOP=1 -q <<'SQL'
ALTER DEFAULT PRIVILEGES IN SCHEMA mart GRANT SELECT ON TABLES TO hanpilji_gpt;
ALTER DEFAULT PRIVILEGES IN SCHEMA mart GRANT EXECUTE ON FUNCTIONS TO hanpilji_gpt;
ALTER DEFAULT PRIVILEGES IN SCHEMA mart REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
SQL

echo "== 건수"
psql "$DSN" -At -F $'\t' <<'SQL'
SELECT n.nspname || '.' || c.relname,
       (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', n.nspname, c.relname), false, true, '')))[1]::text::bigint
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname IN ('ops', 'stg', 'core', 'mart')
ORDER BY 1;
SQL
