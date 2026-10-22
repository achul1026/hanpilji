-- 규제 축 스테이징 (0단계)

-- 법제처 조문을 파싱한 그대로. 한 줄 = 한 조문(조문여부=조문인 것만)
CREATE TABLE IF NOT EXISTS stg.law_article (
    run_id         bigint NOT NULL REFERENCES ops.ingest_run,
    law_id         text NOT NULL,
    mst            text NOT NULL,                 -- 법령일련번호(개정마다 바뀜)
    law_name       text NOT NULL,
    law_kind       text,
    promulgation_date text,                       -- 원본 형식 YYYYMMDD 그대로
    effective_date text NOT NULL,
    article_key    text NOT NULL,                 -- 원본 조문키
    article_no     text NOT NULL,                 -- 조문번호
    article_branch text,                          -- 조문가지번호(「의2」)
    article_title  text,
    body           text NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (mst, article_key)
);

-- 법령 별표(국토계획법 시행령 별표 2~22 = 용도지역별 건축할 수 있는 건축물)
CREATE TABLE IF NOT EXISTS stg.law_annex (
    run_id         bigint NOT NULL REFERENCES ops.ingest_run,
    law_id         text NOT NULL,
    mst            text NOT NULL,
    law_name       text NOT NULL,
    annex_key      text NOT NULL,
    annex_no       text,
    annex_title    text,
    body           text,                          -- 별표내용(글로 들어 있음)
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (mst, annex_key)
);

-- 토지이음 행위제한 CSV. 컬럼 구성이 아직 미확인이라 한 줄을 jsonb로 통째로 받음
-- 헤더를 확인하면 collectors/eum_rule.py 의 COLUMN_MAP만 채워 core.reg_rule로 옮김
CREATE TABLE IF NOT EXISTS stg.eum_rule_raw (
    run_id         bigint NOT NULL REFERENCES ops.ingest_run,
    file_sha256    text NOT NULL,
    line_no        integer NOT NULL,
    row_data       jsonb NOT NULL,
    PRIMARY KEY (file_sha256, line_no)
);
