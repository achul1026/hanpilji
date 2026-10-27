#!/usr/bin/env bash
# 백업(pg_dump)에서 DB를 되살림. 새 세션에서 다시 수집하지 않고 몇 분 안에 데이터가 돌아옴. 여러 번 돌려도 됨
#   HANPILJI_DSN=postgresql://user@host/hanpilji scripts/restore_db.sh [백업 파일]
# 파일을 안 주면 프로젝트 공유 폴더(/mnt/project-files/hanpilji-data/)의 가장 최근 백업을 씀
# 백업 만들기: pg_dump -Fc -Z 6 -d "$HANPILJI_DSN" -f /mnt/project-files/hanpilji-data/hanpilji_YYYYMMDD.dump
set -euo pipefail
DSN="${HANPILJI_DSN:-postgresql://localhost/hanpilji}"
FILE="${1:-$(ls -t /mnt/project-files/hanpilji-data/hanpilji_*.dump 2>/dev/null | head -1)}"
[ -n "$FILE" ] && [ -f "$FILE" ] || { echo "백업 파일이 없음: ${FILE:-/mnt/project-files/hanpilji-data/}" >&2; exit 1; }
echo "== $FILE → $DSN"
# GPT용 계정이 있어야 권한(GRANT)이 같이 돌아옴. 비밀번호는 따로 정함(07_roles.sql 참고)
psql "$DSN" -v ON_ERROR_STOP=1 -qc "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hanpilji_gpt')
    THEN CREATE ROLE hanpilji_gpt LOGIN; END IF; END \$\$;"
pg_restore --no-owner --clean --if-exists -d "$DSN" "$FILE"
psql "$DSN" -tAc "SELECT '매매 ' || (SELECT count(*) FROM core.fact_deal) || '건 · 전월세 ' || (SELECT count(*) FROM core.fact_rent)
    || '건 · 단지 ' || (SELECT count(*) FROM core.dim_complex) || '개 · 건물 ' || (SELECT count(*) FROM core.dim_building)
    || '동 · 행위제한 ' || (SELECT count(*) FROM core.reg_rule) || '건 · 법령 조문 ' || (SELECT count(*) FROM core.doc_law) || '개'"
