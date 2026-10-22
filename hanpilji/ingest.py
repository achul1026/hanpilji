"""수집 공통 모듈 — 실행 기록 · 원본 보관(해시 중복 차단) · 커서 · 적재 제외."""
import datetime
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def start_run(conn, source_id):
    row = conn.execute(
        "INSERT INTO ops.ingest_run (source_id) VALUES (%s) RETURNING run_id", (source_id,)
    ).fetchone()
    conn.commit()
    return row[0]


def finish_run(conn, run_id, status, loaded=0, rejected=0, note=None):
    conn.execute(
        """UPDATE ops.ingest_run
           SET finished_at = now(), status = %s, rows_loaded = %s, rows_rejected = %s, note = %s
           WHERE run_id = %s""",
        (status, loaded, rejected, note, run_id),
    )
    conn.commit()


def save_raw(raw_dir, site, name, data: bytes):
    """raw/{사이트}/{받은 날}/{이름} 에 저장하고 (경로, sha256)을 돌려줌. 같은 이름이 있으면 덮지 않음."""
    sha = hashlib.sha256(data).hexdigest()
    folder = Path(raw_dir) / site / datetime.date.today().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        path = folder / f"{path.stem}.{sha[:8]}{path.suffix}"
    if not path.exists():
        path.write_bytes(data)
    return str(path), sha


def already_loaded(conn, source_id, sha):
    """같은 원본이 이미 적재 완료됐으면 True — 재실행해도 중복이 쌓이지 않게 함."""
    return conn.execute(
        "SELECT 1 FROM ops.ingest_task WHERE source_id = %s AND raw_sha256 = %s AND status = 'done' LIMIT 1",
        (source_id, sha),
    ).fetchone() is not None


def add_task(conn, run_id, source_id, task_key, raw_path=None, sha=None, status="running"):
    row = conn.execute(
        """INSERT INTO ops.ingest_task (run_id, source_id, task_key, raw_path, raw_sha256, status, attempts)
           VALUES (%s, %s, %s, %s, %s, %s, 1) RETURNING task_id""",
        (run_id, source_id, task_key, raw_path, sha, status),
    ).fetchone()
    return row[0]


def end_task(conn, task_id, status, error=None):
    conn.execute(
        "UPDATE ops.ingest_task SET status = %s, error = %s, updated_at = now() WHERE task_id = %s",
        (status, error, task_id),
    )


def get_cursor(conn, source_id, key):
    row = conn.execute(
        "SELECT cursor_value FROM ops.ingest_cursor WHERE source_id = %s AND cursor_key = %s",
        (source_id, key),
    ).fetchone()
    return row[0] if row else None


def set_cursor(conn, source_id, key, value):
    """성공했을 때만 부름. 실패하면 커서가 그대로 남아 다음 실행이 같은 자리에서 다시 시작함."""
    conn.execute(
        """INSERT INTO ops.ingest_cursor (source_id, cursor_key, cursor_value) VALUES (%s, %s, %s)
           ON CONFLICT (source_id, cursor_key)
           DO UPDATE SET cursor_value = excluded.cursor_value, last_success = now()""",
        (source_id, key, value),
    )


def reject(conn, run_id, source_id, table, reason, row):
    conn.execute(
        """INSERT INTO ops.reject_estate (run_id, source_id, target_table, reason, raw_row)
           VALUES (%s, %s, %s, %s, %s)""",
        (run_id, source_id, table, reason, json.dumps(row, ensure_ascii=False)),
    )


def http_get(url, tries=4, timeout=30):
    """재시도(2 · 4 · 8초)를 넣은 GET. 끝내 실패하면 마지막 예외를 그대로 올림."""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hanpilji-collector/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError):
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))
