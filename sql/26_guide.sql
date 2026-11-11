-- 부동산 지식 DB 5단계: 공공 해설자료
--   core.doc_guide       : 자료 하나 = 한 줄. 두 종류
--       행정규칙 : 정부 고시 · 훈령 · 지침(법제처 행정규칙 API, target=admrul). 매주 바뀌었는지 보고 다시 받음
--       해설서   : 정부가 낸 해설 책자 PDF(국토부 · 법무부 · 국세청 …). 사람이 받아 올린 PDF를 쪽 단위로 넣음
--   core.doc_guide_part  : 자료를 자른 조각. 행정규칙은 조문 하나, 해설서는 PDF 한 쪽이 한 줄(쪽 번호를 답에 적음)
--   core.kb_topic_guide  : 시험 단원(core.kb_topic)마다 이어 둔 자료
--   mart.q_guide_search(질문)   : 조각에서 낱말로 찾음(q_law_search · q_prec_search와 같은 방식)
--   mart.q_kb_topic_guides(단원): 단원에 이어 둔 자료
-- 행정규칙은 저작권법 제7조(고시 · 훈령)라 그대로 인용 가능. 해설서는 자료마다 공공누리 유형을 license에 적고 출처를 표시함
-- 수집기: hanpilji/collectors/admrul.py(매주, config.toml [admrul]) · hanpilji/collectors/guide.py(해설서, 손으로)
-- 25_prec.sql 다음에 적용

CREATE TABLE IF NOT EXISTS core.doc_guide (
    doc_id        text PRIMARY KEY,                -- 행정규칙: 'admrul:' || 행정규칙ID, 해설서: 'pdf:' || 파일 이름(확장자 뺌)
    kind          text NOT NULL CHECK (kind IN ('행정규칙', '해설서')),
    title         text NOT NULL,
    issuer        text,                            -- 소관부처 · 펴낸 곳
    rule_kind     text,                            -- 고시 · 훈령 · 예규 · 공고 (해설서는 NULL)
    issued_no     text,                            -- 발령번호 · 판(예: 2023-522, 2025년판)
    issued_on     date,                            -- 발령일자 · 펴낸 날
    effective_on  date,                            -- 시행일자
    version_id    text,                            -- 행정규칙일련번호(바뀌면 새로 받음) · PDF sha256 앞 16자
    license       text,                            -- 이용 조건(공공누리 제1유형 …)
    url           text,                            -- 원문을 볼 수 있는 주소
    part_count    int NOT NULL DEFAULT 0,
    source        text NOT NULL,
    asof          date NOT NULL
);

CREATE TABLE IF NOT EXISTS core.doc_guide_part (
    doc_id       text NOT NULL REFERENCES core.doc_guide ON DELETE CASCADE,
    part_no      int  NOT NULL,                    -- 순서(1부터)
    label        text NOT NULL,                    -- 「제3조」 · 「12쪽」 · 「부칙」 · 「별표 1」
    heading      text,                             -- 조문 제목 · 쪽 머리의 장 · 절 제목
    page         int,                              -- 해설서의 PDF 쪽 번호(행정규칙은 NULL)
    body         text NOT NULL,
    search_text  text NOT NULL,
    PRIMARY KEY (doc_id, part_no)
);
CREATE INDEX IF NOT EXISTS doc_guide_part_trgm ON core.doc_guide_part USING gin (search_text gin_trgm_ops);

CREATE TABLE IF NOT EXISTS core.kb_topic_guide (
    topic_id  text NOT NULL REFERENCES core.kb_topic ON DELETE CASCADE,
    doc_id    text NOT NULL REFERENCES core.doc_guide ON DELETE CASCADE,
    asof      date NOT NULL,
    PRIMARY KEY (topic_id, doc_id)
);
CREATE INDEX IF NOT EXISTS kb_topic_guide_doc_idx ON core.kb_topic_guide (doc_id);

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('law_go_kr_admrul', '법제처 국가법령정보 Open API(행정규칙)', '법제처 · 각 부처', 'api', NULL, 'UTF-8', '매주',
  '고시 · 훈령은 저작권 보호 대상 아님(저작권법 제7조), 출처 표시', 'https://open.law.go.kr'),
 ('gov_guide_pdf', '정부 해설 책자(PDF)', '국토교통부 · 법무부 · 국세청 등', 'file', NULL, 'UTF-8', '매년',
  '자료마다 공공누리 유형(자료 목록의 이용 조건), 출처 표시', NULL)
ON CONFLICT (source_id) DO NOTHING;

-- 낱말 절반 이상이 들어 있는 조각. 점수 = 맞은 낱말 비율 + 문장 유사도/10. 같은 점수면 행정규칙 → 해설서, 순서대로
DROP FUNCTION IF EXISTS mart.q_guide_search(text, int);
CREATE FUNCTION mart.q_guide_search(p_query text, p_limit int DEFAULT 10)
RETURNS TABLE (title text, kind text, issuer text, label text, heading text, page int, body text,
               score real, license text, url text, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH q AS (
        SELECT array_agg(w) AS words, count(*) AS n
        FROM regexp_split_to_table(btrim(p_query), '\s+') AS w WHERE w <> ''
    ), hit AS (
        SELECT p.doc_id, p.part_no, p.label, p.heading, p.page, p.body,
               (m.matched::real / q.n + word_similarity(p_query, p.search_text) / 10)::real AS score
        FROM core.doc_guide_part p CROSS JOIN q
        CROSS JOIN LATERAL (SELECT count(*) AS matched FROM unnest(q.words) w
                            WHERE strpos(p.search_text, w) > 0) m
        WHERE q.n > 0 AND m.matched * 2 >= q.n
    )
    SELECT d.title, d.kind, d.issuer, h.label, h.heading, h.page, h.body, h.score, d.license, d.url,
           d.source, d.asof, count(*) OVER ()
    FROM hit h JOIN core.doc_guide d USING (doc_id)
    ORDER BY h.score DESC, d.kind DESC, h.doc_id, h.part_no
    LIMIT greatest(least(coalesce(p_limit, 10), 50), 1)
$$;

DROP FUNCTION IF EXISTS mart.q_kb_topic_guides(text);
CREATE FUNCTION mart.q_kb_topic_guides(p_topic text)
RETURNS TABLE (title text, kind text, issuer text, rule_kind text, issued_no text, effective_on date,
               part_count int, license text, url text, source text, asof date)
LANGUAGE sql STABLE AS $$
    SELECT d.title, d.kind, d.issuer, d.rule_kind, d.issued_no, d.effective_on, d.part_count, d.license, d.url,
           d.source, d.asof
    FROM core.kb_topic_guide t JOIN core.doc_guide d USING (doc_id)
    WHERE t.topic_id = p_topic
    ORDER BY d.kind DESC, d.title
$$;

ALTER FUNCTION mart.q_guide_search(text, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_guide_search(text, int) SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_kb_topic_guides(text) SECURITY DEFINER;
ALTER FUNCTION mart.q_kb_topic_guides(text) SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_guide_search(text, int) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION mart.q_kb_topic_guides(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_guide_search(text, int) TO hanpilji_gpt;
GRANT EXECUTE ON FUNCTION mart.q_kb_topic_guides(text) TO hanpilji_gpt;
