#!/usr/bin/env bash
# 서버 cron용 수집 배치(스키마 하나로 합친 DB). GitHub Actions(.github/workflows/refresh.yml)와 같은 순서 · 같은 수집기를 돎
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
# 셸에서 물려받은 값은 버리고 .env에 적힌 값만 씀
unset HANPILJI_DSN HANPILJI_GPT_DSN HANPILJI_DB_SCHEMA HANPILJI_DB_VIA HANPILJI_BATCH_SKIP
set -a; . "$ENV_FILE"; set +a
[ -n "${HANPILJI_DSN:-}" ] || { echo "$ENV_FILE 에 HANPILJI_DSN 없음" >> "$LOG"; exit 2; }
# 서버는 스키마 하나(hanpilji) 구성이라 꼭 있어야 함. 없으면 core. · ops. 그대로 보내 전부 실패함
[ -n "${HANPILJI_DB_SCHEMA:-}" ] || { echo "$ENV_FILE 에 HANPILJI_DB_SCHEMA 없음" >> "$LOG"; exit 2; }
cd "$ROOT"
export PYTHONUNBUFFERED=1
failed=()

# .env의 HANPILJI_BATCH_SKIP=apt,onbid 처럼 쉼표로 적은 작업은 건너뜀(공백으로 띄우려면 따옴표 필수: 없으면 뒤 낱말이 명령으로 실행됨)
# (다른 서비스가 같은 데이터를 이미 모으고 있어 그쪽 표를 읽을 때).
# bld는 총괄표제부 · 표제부 · 지역지구 · 부속지번을 함께 받으므로, 네 가지를 모두 다른 곳에서 읽을 때만 넣음
KEYS="apt offi rh sh nrg onbid law market bld vworld luinfo house_price land_price freshness"
SKIP=" ${HANPILJI_BATCH_SKIP:-} "
SKIP="${SKIP//,/ }"
for k in $SKIP; do
  [[ " $KEYS " == *" $k "* ]] || echo "!! HANPILJI_BATCH_SKIP의 '$k'는 없는 키라 무시함 (쓸 수 있는 키: $KEYS)" >> "$LOG"
done

# 공공데이터포털 하루 한도(자정에 다시 참)는 계정 · API마다 하나라, 같은 계정을 쓰는 GitHub Actions(강남 매일, 04:17) ·
# 다른 서비스(5대 도메인 부동산 월간 배치, 관리자가 손으로 돌림, 매월 3일은 피함) 몫을 남김.
# 실거래는 종류(API)마다, 건축물대장은 API 묶음 전체로 하루 이 수까지만 부름. 개발계정 한도 10,000 중 1,000을 남기는 값.
# 평소 실거래는 최근 3개월만 다시 받아 API마다 하루 75~150번이라 상한에 닿는 건 처음 채울 때뿐
DG_MAX="${HANPILJI_DG_MAX_CALLS:-9000}"

step() {   # step <키> <이름> <제한 분> <명령…>
  local key="$1" name="$2" mins="$3"; shift 3
  if [[ "$SKIP" == *" $key "* ]]; then
    echo "-- 건너뜀 $name (HANPILJI_BATCH_SKIP에 $key)" >> "$LOG"
    return 0
  fi
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
    # 실거래는 모두 서울 25개 구(5대 도메인과는 엮지 않음, 2026-11-04).
    # 처음엔 36개월을 채우느라 하루 한도에 걸리는 API는 멈추고, 다음 날 이어 받음(2~3일)
    RTMS=("$PY" -m hanpilji.collectors.rtms --sigungu seoul --max-calls "$DG_MAX")
    step apt "실거래(아파트, 서울)" 120 "${RTMS[@]}" --only trade --only rent
    step offi "실거래(오피스텔, 서울)" 120 "${RTMS[@]}" --only offi_trade --only offi_rent
    step rh "실거래(연립다세대, 서울)" 120 "${RTMS[@]}" --only rh_trade --only rh_rent
    step sh "실거래(단독다가구 매매 · 전월세, 서울)" 120 "${RTMS[@]}" --only sh_trade --only sh_rent
    step nrg "실거래(상업업무용 매매, 서울)" 120 "${RTMS[@]}" --only nrg_trade
    # 공매는 서울 25개 구 × 재산유형 5개(구마다 보통 10번 안팎 호출)
    step onbid "공매(온비드, 서울)" 60 "$PY" -m hanpilji.collectors.onbid --sigungu seoul --max-calls 2000
    # 건축물대장(서울 아파트 · 오피스텔 단지 필지). 필지마다 4번 호출이라 서울은 수만 번 → 매일 9,000번씩 이어 받고,
    # 받은 지 30일(config [bld] refresh_days) 지난 필지만 다시 받음. 실거래 단계 뒤라 새 단지도 그날 붙음
    step bld "건축물대장(서울)" 240 "$PY" -m hanpilji.collectors.bld --sigungu seoul --max-calls "$DG_MAX"
    # V-World 토지특성(지목 · 토지대장 면적 · 공시지가 이력) · 토지이용계획(용도지역 · 지구). V-World는 해외 IP를 막아
    # 클라우드에서는 못 받고 국내 IP인 서버에서만 받음. 서울 단지 필지만(필지마다 2번 안팎), 받은 지 30일 지난 필지만 다시.
    # 공공데이터포털 한도와 따로라 건축물대장과 겹치지 않음. .env에 VWORLD_KEY가 없으면 건너뜀
    if [ -n "${VWORLD_KEY:-}" ]; then
      step vworld "토지특성 · 토지이용계획(V-World, 서울 단지 필지)" 180 \
        "$PY" -m hanpilji.collectors.vworld --sigungu seoul --complex-only --max-calls "${HANPILJI_VWORLD_MAX_CALLS:-9000}"
    else
      echo "-- 건너뜀 V-World (.env에 VWORLD_KEY 없음)" >> "$LOG"
    fi
    ;;
  weekly)
    # 법제처 본문은 open.law.go.kr에서 받은 ID(.env의 LAW_OC)와 그 ID에 등록한 이 서버의 IP가 있어야 받음.
    # 테스트 계정(test)은 2026-11-01부터 막힘(2026-11-04 확인). ID가 test면 수집기가 로그에 「주의」를 남기고,
    # 개정만 감지해 본문은 「본문 대기」로 둠
    step law "법령" 20 "$PY" -m hanpilji.collectors.law
    step market "시장 지표" 20 "$PY" -m hanpilji.collectors.market --months 12
    ;;
  monthly)
    # 행위제한은 강남구로 받고 서울 다른 구에 복사(서울은 자치구 조례 없이 서울시 도시계획조례 하나라 구마다 답이 같음,
    # 2026-11-11 강남 · 마포 · 노원 비교). 구마다 받으면 576번 × 25개 구라 하루 한도(1,000)로 보름이 걸림
    step luinfo "행위제한(서울)" 60 "$PY" -m hanpilji.collectors.luinfo --refresh --sigungu 11680 --copy-to seoul
    step house_price "주택 공시가격(서울)" 60 "$PY" -m hanpilji.collectors.house_price --sigungu seoul
    # 서울 개별공시지가(서울 열린데이터광장 파일). 새 해 파일이 올라왔거나 고쳐졌을 때만 받음
    step land_price "개별공시지가(서울)" 30 "$PY" -m hanpilji.collectors.seoul_land_price
    ;;
  *) echo "daily|weekly|monthly 중 하나" >&2; exit 2 ;;
esac

step freshness "수집처별 마지막 성공" 5 "$PY" scripts/freshness.py
# 로그는 60일만 둠
find "$LOG_DIR" -name '*.log' -mtime +60 ! -name skipped.log -delete 2>/dev/null

if [ ${#failed[@]} -gt 0 ]; then
  echo "== 실패한 단계: ${failed[*]}" >> "$LOG"
  exit 1
fi
