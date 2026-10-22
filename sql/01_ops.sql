-- 수집 기록 테이블 5개 (공통 모듈, 도메인과 무관)

CREATE TABLE IF NOT EXISTS ops.ingest_source (
    source_id      text PRIMARY KEY,              -- 예) law_go_kr, eum_act_rule
    name           text NOT NULL,                 -- 사람이 읽는 이름
    operator       text,                          -- 운영 기관
    method         text NOT NULL CHECK (method IN ('file', 'api')),
    daily_limit    integer,                       -- 하루 한도(모르면 비움)
    encoding       text,                          -- 원본 한글 저장 방식(UTF-8, CP949 …)
    cycle          text,                          -- 수집 주기(매월, 반년 …)
    license        text,                          -- 상업 이용 조건
    url            text,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.ingest_run (
    run_id         bigserial PRIMARY KEY,
    source_id      text NOT NULL REFERENCES ops.ingest_source,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    status         text NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'done', 'failed')),
    rows_loaded    integer NOT NULL DEFAULT 0,
    rows_rejected  integer NOT NULL DEFAULT 0,
    note           text
);

-- 요청 하나 또는 파일 하나. 같은 원본(해시)이 다시 오면 새로 적재하지 않음
CREATE TABLE IF NOT EXISTS ops.ingest_task (
    task_id        bigserial PRIMARY KEY,
    run_id         bigint NOT NULL REFERENCES ops.ingest_run,
    source_id      text NOT NULL REFERENCES ops.ingest_source,
    task_key       text NOT NULL,                 -- 예) law:건축법, file:행위제한_202610.csv
    raw_path       text,                          -- 원본 보관 경로
    raw_sha256     text,
    status         text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'running', 'done', 'failed', 'skipped')),
    attempts       integer NOT NULL DEFAULT 0,
    error          text,
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ingest_task_key_idx ON ops.ingest_task (source_id, task_key);
CREATE INDEX IF NOT EXISTS ingest_task_sha_idx ON ops.ingest_task (raw_sha256);

-- 수집처마다 어디까지 받았는지. 실패하면 커서를 움직이지 않음
CREATE TABLE IF NOT EXISTS ops.ingest_cursor (
    source_id      text NOT NULL REFERENCES ops.ingest_source,
    cursor_key     text NOT NULL,                 -- 예) law:건축법, rt_apt:11680
    cursor_value   text NOT NULL,                 -- 예) 시행일 20260101, 계약월 202609
    last_success   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, cursor_key)
);

-- 형식이 틀려 넣지 못한 줄과 이유
CREATE TABLE IF NOT EXISTS ops.reject_estate (
    reject_id      bigserial PRIMARY KEY,
    run_id         bigint REFERENCES ops.ingest_run,
    source_id      text NOT NULL,
    target_table   text NOT NULL,
    reason         text NOT NULL,
    raw_row        jsonb,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- 매일 볼 것: 마지막 성공이 주기보다 오래된 수집처
CREATE OR REPLACE VIEW ops.v_source_freshness AS
SELECT s.source_id, s.name, s.cycle,
       max(r.finished_at) FILTER (WHERE r.status = 'done') AS last_success,
       now() - max(r.finished_at) FILTER (WHERE r.status = 'done') AS age
FROM ops.ingest_source s
LEFT JOIN ops.ingest_run r USING (source_id)
GROUP BY s.source_id, s.name, s.cycle;
