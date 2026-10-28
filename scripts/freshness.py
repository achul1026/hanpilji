"""수집처별 마지막 성공 시각을 마크다운 표로 찍음(깃허브 실행 요약용). DB는 HANPILJI_DSN."""
import os

import psycopg

with psycopg.connect(os.environ["HANPILJI_DSN"]) as conn:
    rows = conn.execute(
        """SELECT f.source_id, f.name, f.cycle, f.last_success, r.status, r.rows_loaded, r.note
           FROM ops.v_source_freshness f
           LEFT JOIN LATERAL (SELECT status, rows_loaded, note FROM ops.ingest_run
                              WHERE source_id = f.source_id ORDER BY run_id DESC LIMIT 1) r ON true
           ORDER BY f.last_success DESC NULLS LAST""").fetchall()
print("| 수집처 | 이름 | 주기 | 마지막 성공(UTC) | 최근 실행 | 넣은 줄 | 메모 |")
print("|---|---|---|---|---|---|---|")
for sid, name, cycle, last, status, n, note in rows:
    when = last.strftime("%Y-%m-%d %H:%M") if last else "-"
    print(f"| {sid} | {name} | {cycle or '-'} | {when} | {status or '-'} | {n if n is not None else '-'} | {note or ''} |")
