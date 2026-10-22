"""토지이음 행위제한 CSV → stg.eum_rule_raw → core.reg_rule.

    python -m hanpilji.collectors.eum_rule 내려받은파일.csv [--config config.toml]

파일은 사람이 토지이음에서 내려받음(로그인 없이 이용 목적만 고름, 매월).
컬럼 구성은 아직 확인하지 못했음. 그래서 1단계는 줄을 통째로 stg에 넣고 헤더를 출력함.
헤더를 보고 COLUMN_MAP과 ALLOWED_MAP을 채우면 2단계(core.reg_rule로 올리기)가 돌아감.
"""
import argparse
import csv
import datetime
import io
import json
import sys
from pathlib import Path

import psycopg

from hanpilji import config as config_mod
from hanpilji import ingest

SOURCE = "eum_act_rule"

# 원본 컬럼명 → reg_rule 컬럼. 실제 헤더 확인 후 채움(None이면 2단계를 건너뜀)
COLUMN_MAP = {
    "zone_name": None,       # 용도지역 이름 컬럼
    "act_name": None,        # 건축물 종류 · 행위 컬럼
    "allowed": None,         # 가능 여부 컬럼
    "condition_text": None,  # 조건 · 비고 컬럼(없으면 None)
    "sigungu_cd": None,      # 조례 지자체 코드 컬럼(없으면 None → 전국 기준)
}
# 원본 가능 여부 값 → 가능 · 조건부 · 불가
ALLOWED_MAP = {}


def decode(data: bytes):
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError("UTF-8도 CP949도 아님")


def stage(conn, run_id, data: bytes, sha):
    text, enc = decode(data)
    reader = csv.DictReader(io.StringIO(text))
    rows = [(run_id, sha, i, json.dumps(r, ensure_ascii=False)) for i, r in enumerate(reader, start=2)]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO stg.eum_rule_raw (run_id, file_sha256, line_no, row_data) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            rows,
        )
    return reader.fieldnames or [], len(rows), enc


def promote(conn, run_id, sha, asof):
    """stg → core.reg_rule. 용도지역 이름이 dim_zone에 없거나 가능 여부 값을 모르면 적재 제외."""
    if not all(COLUMN_MAP[k] for k in ("zone_name", "act_name", "allowed")):
        return None
    zones = dict(conn.execute("SELECT zone_name, zone_cd FROM core.dim_zone").fetchall())
    loaded = rejected = 0
    for line_no, row in conn.execute(
        "SELECT line_no, row_data FROM stg.eum_rule_raw WHERE file_sha256 = %s ORDER BY line_no", (sha,)
    ).fetchall():
        get = lambda k: (row.get(COLUMN_MAP[k]) or "").strip() if COLUMN_MAP[k] else None
        zone_cd = zones.get(get("zone_name"))
        allowed = ALLOWED_MAP.get(get("allowed"))
        if not zone_cd or not allowed or not get("act_name"):
            reason = "용도지역 이름이 dim_zone에 없음" if not zone_cd else "가능 여부 값 모름" if not allowed else "행위 이름 빈칸"
            ingest.reject(conn, run_id, SOURCE, "core.reg_rule", f"{reason} (줄 {line_no})", row)
            rejected += 1
            continue
        conn.execute(
            """INSERT INTO core.reg_rule (zone_cd, sigungu_cd, act_name, allowed, condition_text, source, asof)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (zone_cd, coalesce(sigungu_cd, ''), act_name)
               DO UPDATE SET allowed = excluded.allowed, condition_text = excluded.condition_text,
                             source = excluded.source, asof = excluded.asof""",
            (zone_cd, get("sigungu_cd") or None, get("act_name"), allowed, get("condition_text") or None,
             "토지이음 행위제한정보", asof),
        )
        loaded += 1
    return loaded, rejected


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--asof", help="자료 기준일 YYYY-MM-DD(기본: 오늘)")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    data = Path(args.csv_path).read_bytes()
    asof = datetime.date.fromisoformat(args.asof) if args.asof else datetime.date.today()
    with psycopg.connect(cfg["db"]["dsn"]) as conn:
        run_id = ingest.start_run(conn, SOURCE)
        path, sha = ingest.save_raw(cfg["storage"]["raw_dir"], "eum", Path(args.csv_path).name, data)
        if ingest.already_loaded(conn, SOURCE, sha):
            ingest.finish_run(conn, run_id, "done", note="같은 파일을 이미 적재함 — 건너뜀")
            print("같은 파일을 이미 적재함 — 건너뜀")
            return 0
        task = ingest.add_task(conn, run_id, SOURCE, f"file:{Path(args.csv_path).name}", path, sha)
        try:
            header, n, enc = stage(conn, run_id, data, sha)
            result = promote(conn, run_id, sha, asof)
            ingest.end_task(conn, task, "done")
            conn.commit()
        except Exception as e:
            conn.rollback()
            ingest.finish_run(conn, run_id, "failed", note=str(e)[:500])
            raise
        print(f"stg 적재 {n}줄 (인코딩 {enc})")
        print("헤더:", " | ".join(header))
        if result is None:
            ingest.finish_run(conn, run_id, "done", n, 0, "COLUMN_MAP 미설정 — stg까지만 적재")
            print("COLUMN_MAP이 비어 있어 core.reg_rule로는 올리지 않음. 위 헤더를 보고 채울 것")
        else:
            ingest.finish_run(conn, run_id, "done", result[0], result[1])
            print(f"core.reg_rule 적재 {result[0]}줄 · 적재 제외 {result[1]}줄")
    return 0


if __name__ == "__main__":
    sys.exit(main())
