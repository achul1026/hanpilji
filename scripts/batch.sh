#!/usr/bin/env bash
# 서버 cron용 수집 배치. GitHub Actions(.github/workflows/refresh.yml)와 같은 순서 · 같은 수집기를 돎
#   scripts/batch.sh daily|weekly|monthly
# 설정은 저장소 옆 .env(권한 600)에서 읽음: HANPILJI_DSN · HANPILJI_DB_SCHEMA · DATA_GO_KR_KEY · ECOS_KEY · RONE_KEY · KOSIS_KEY · LAW_OC · VWORLD_KEY …
# 한 번에 하나만 돎(flock). 단계 하나가 실패해도 다음 단계는 돌고, 끝에 실패가 있으면 종료 코드 1
set -uo pipefail
WHAT="${1:?daily|weekly|monthly}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${HANPILJI_ENV_FILE:-$ROOT/../.env}"
LOG_DIR="${HANPILJI_LOG_DIR:-$ROOT/../logs}"
PY="${HANPILJI_PYTHON:-$ROOT/../venv/bin/python}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y%m%d_%H%M)_$WHAT.log"

exec 9>"$LOG_DIR/.batch.lock"
if ! flock -n 9; then
  echo "$(date '+%F %T') 다른 배치가 도는 중이라 건너뜀: $WHAT" >> "$LOG_DIR/skipped.log"
  exit 0
fi

# 설정 파일이 없으면 멈춤(없이 돌면 셸에 남은 다른 DB 주소로 쓸 수 있음)
[ -r "$ENV_FILE" ] || { echo "설정 파일 없음: $ENV_FILE" >> "$LOG"; exit 2; }
set -a; . "$ENV_FILE"; set +a
[ -n "${HANPILJI_DSN:-}" ] || { echo "HANPILJI_DSN 없음" >> "$LOG"; exit 2; }
cd "$ROOT"
export PYTHONUNBUFFERED=1
failed=()

step() {   # step <이름> <제한 분> <명령…>
  local name="$1" mins="$2"; shift 2
  echo "== $(date '+%F %T') $name" >> "$LOG"
  if timeout "${mins}m" "$@" >> "$LOG" 2>&1; then
    echo "-- 성공 $name" >> "$LOG"
  else
    echo "-- 실패 $name (종료 코드 $?)" >> "$LOG"
    failed+=("$name")
  fi
}

case "$WHAT" in
  daily)
    step "실거래(아파트)" 30 "$PY" -m hanpilji.collectors.rtms --only trade --only rent
    step "실거래(오피스텔)" 30 "$PY" -m hanpilji.collectors.rtms --only offi_trade --only offi_rent
    step "실거래(연립다세대)" 30 "$PY" -m hanpilji.collectors.rtms --only rh_trade --only rh_rent
    step "공매(온비드)" 30 "$PY" -m hanpilji.collectors.onbid
    ;;
  weekly)
    step "법령" 20 "$PY" -m hanpilji.collectors.law
    step "시장 지표" 20 "$PY" -m hanpilji.collectors.market --months 12
    ;;
  monthly)
    step "건축물대장" 240 "$PY" -m hanpilji.collectors.bld --refresh
    step "행위제한" 60 "$PY" -m hanpilji.collectors.luinfo --refresh
    step "주택 공시가격" 30 "$PY" -m hanpilji.collectors.house_price
    ;;
  *) echo "daily|weekly|monthly 중 하나" >&2; exit 2 ;;
esac

step "수집처별 마지막 성공" 5 "$PY" scripts/freshness.py
# 로그는 60일만 둠
find "$LOG_DIR" -name '*.log' -mtime +60 ! -name skipped.log -delete 2>/dev/null

if [ ${#failed[@]} -gt 0 ]; then
  echo "== 실패한 단계: ${failed[*]}" >> "$LOG"
  exit 1
fi
