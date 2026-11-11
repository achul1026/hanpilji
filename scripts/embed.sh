#!/usr/bin/env bash
# 지식 DB 의미 검색 벡터(bge-m3) 만들기. 서버 호스트에서 cron으로 돎(배치 컨테이너 안이 아님)
#   scripts/embed.sh [--search "질문"]
# 같은 서버의 다른 서비스를 지키려고 상한을 건 별도 컨테이너에서만 돎:
#   메모리 HANPILJI_EMBED_MEM(기본 5g, 스왑 추가 사용 없음) · CPU HANPILJI_EMBED_CPUS(기본 2)
#   상한을 넘으면 이 컨테이너만 꺼지고 다른 컨테이너에는 영향 없음
# 수집 배치(batch.sh)와 같은 잠금 파일을 써서 겹쳐 돌지 않음. 모델 · venv-embed가 없으면 아무것도 안 함
set -uo pipefail
B="${HANPILJI_BATCH_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
MEM="${HANPILJI_EMBED_MEM:-5g}"
CPUS="${HANPILJI_EMBED_CPUS:-2}"
LOG_DIR="$B/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y%m%d_%H%M)_embed.log"

# venv-embed는 컨테이너 안에서 만들어 bin/python 링크가 호스트에서는 끊겨 보임 → pyvenv.cfg로 확인
[ -f "$B/venv-embed/pyvenv.cfg" ] || { echo "venv-embed 없음: 건너뜀" >> "$LOG"; exit 0; }
ls -d "$B"/models/bge-m3-* >/dev/null 2>&1 || { echo "모델 폴더 없음: 건너뜀" >> "$LOG"; exit 0; }

exec 9>"$LOG_DIR/.batch.lock"
flock -w 3600 9 || { echo "$(date '+%F %T') 다른 배치가 1시간 넘게 돌아서 건너뜀: embed" >> "$LOG_DIR/skipped.log"; exit 0; }

ARGS=("$@"); [ ${#ARGS[@]} -gt 0 ] || ARGS=(--max-minutes 55)
echo "$(date '+%F %T') 시작 (메모리 $MEM · CPU $CPUS)" >> "$LOG"
docker run --rm --network host --memory "$MEM" --memory-swap "$MEM" --cpus "$CPUS" \
  -e HANPILJI_EMBED_THREADS="${CPUS%.*}" -e PYTHONUNBUFFERED=1 \
  -v "$B:/work" -w /work/app python:3.12-slim \
  sh -c 'set -a; . /work/.env; set +a; exec /work/venv-embed/bin/python -m hanpilji.collectors.embed "$@"' embed "${ARGS[@]}" \
  >> "$LOG" 2>&1
rc=$?
[ $rc -eq 137 ] && echo "메모리 상한($MEM)에 걸려 멈춤. 이 작업만 꺼졌고 다른 서비스는 그대로임" >> "$LOG"
echo "$(date '+%F %T') 끝 (종료 코드 $rc)" >> "$LOG"
exit $rc
