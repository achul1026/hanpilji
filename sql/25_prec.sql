-- 부동산 지식 DB 4단계: 대법원 판례(법제처 국가법령정보 판례 API, target=prec)
--   core.doc_prec       : 판례 하나 = 한 줄(사건번호 · 선고일 · 판시사항 · 판결요지 · 참조조문 · 판례 전문)
--   core.kb_topic_prec  : 시험 단원(core.kb_topic)마다 주제어로 찾은 판례
--   mart.q_prec_search(질문)      : 판시사항 · 판결요지 · 사건명에서 낱말로 찾음(법령 검색 q_law_search와 같은 방식)
--   mart.q_kb_topic_precs(단원)   : 단원에 연결된 판례(최근 선고 순)
-- 판례는 저작권법 제7조(보호받지 못하는 저작물)의 판결이라 그대로 저장 · 인용 가능. 답에는 사건번호 · 선고일을 적음
-- 수집기: hanpilji/collectors/prec.py(매주, config.toml [prec]). 24_kb_topics.sql 다음에 적용

CREATE TABLE IF NOT EXISTS core.doc_prec (
    prec_id        text PRIMARY KEY,               -- 법제처 판례일련번호
    case_no        text NOT NULL,                  -- 사건번호(예: 2025다210305)
    case_name      text,                           -- 사건명
    court          text,                           -- 법원명(대법원)
    decided_on     date,                           -- 선고일자
    case_type      text,                           -- 사건종류명(민사 · 형사 · 일반행정 …)
    judgment_type  text,                           -- 판결유형(판결 · 결정)
    holding        text,                           -- 판시사항
    summary        text,                           -- 판결요지
    ref_articles   text,                           -- 참조조문
    ref_cases      text,                           -- 참조판례
    body           text,                           -- 판례내용(전문)
    search_text    text,                           -- 사건명 + 판시사항 + 판결요지(검색용)
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE INDEX IF NOT EXISTS doc_prec_decided_idx ON core.doc_prec (decided_on DESC);
CREATE INDEX IF NOT EXISTS doc_prec_search_trgm ON core.doc_prec USING gin (search_text gin_trgm_ops);

CREATE TABLE IF NOT EXISTS core.kb_topic_prec (
    topic_id   text NOT NULL REFERENCES core.kb_topic ON DELETE CASCADE,
    prec_id    text NOT NULL REFERENCES core.doc_prec ON DELETE CASCADE,
    query      text NOT NULL,                      -- 찾을 때 쓴 주제어
    asof       date NOT NULL,
    PRIMARY KEY (topic_id, prec_id)
);
CREATE INDEX IF NOT EXISTS kb_topic_prec_prec_idx ON core.kb_topic_prec (prec_id);

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('law_go_kr_prec', '법제처 국가법령정보 Open API(판례)', '법제처 · 대법원', 'api', NULL, 'UTF-8', '매주',
  '판결은 저작권 보호 대상 아님(저작권법 제7조), 출처 표시', 'https://open.law.go.kr')
ON CONFLICT (source_id) DO NOTHING;

-- 낱말 절반 이상이 들어 있는 판례. 점수 = 맞은 낱말 비율 + 문장 유사도/10, 같으면 최근 선고 순
DROP FUNCTION IF EXISTS mart.q_prec_search(text, int);
CREATE FUNCTION mart.q_prec_search(p_query text, p_limit int DEFAULT 10)
RETURNS TABLE (case_no text, court text, decided_on date, case_name text, holding text, summary text,
               ref_articles text, score real, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH q AS (
        SELECT array_agg(w) AS words, count(*) AS n
        FROM regexp_split_to_table(btrim(p_query), '\s+') AS w WHERE w <> ''
    ), hit AS (
        SELECT p.*, (m.matched::real / q.n + word_similarity(p_query, p.search_text) / 10)::real AS score
        FROM core.doc_prec p CROSS JOIN q
        CROSS JOIN LATERAL (SELECT count(*) AS matched FROM unnest(q.words) w
                            WHERE strpos(p.search_text, w) > 0) m
        WHERE q.n > 0 AND m.matched * 2 >= q.n
    )
    SELECT h.case_no, h.court, h.decided_on, h.case_name, h.holding, h.summary, h.ref_articles, h.score,
           h.source, h.asof, count(*) OVER ()
    FROM hit h ORDER BY h.score DESC, h.decided_on DESC NULLS LAST, h.prec_id
    LIMIT greatest(least(coalesce(p_limit, 10), 50), 1)
$$;

DROP FUNCTION IF EXISTS mart.q_kb_topic_precs(text);
CREATE FUNCTION mart.q_kb_topic_precs(p_topic text)
RETURNS TABLE (case_no text, court text, decided_on date, case_name text, holding text, summary text,
               ref_articles text, query text, source text, asof date)
LANGUAGE sql STABLE AS $$
    SELECT p.case_no, p.court, p.decided_on, p.case_name, p.holding, p.summary, p.ref_articles,
           string_agg(DISTINCT t.query, ' · '), p.source, p.asof
    FROM core.kb_topic_prec t JOIN core.doc_prec p USING (prec_id)
    WHERE t.topic_id = p_topic
    GROUP BY p.prec_id
    ORDER BY p.decided_on DESC NULLS LAST, p.prec_id
$$;

ALTER FUNCTION mart.q_prec_search(text, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_prec_search(text, int) SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_kb_topic_precs(text) SECURITY DEFINER;
ALTER FUNCTION mart.q_kb_topic_precs(text) SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_prec_search(text, int) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION mart.q_kb_topic_precs(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_prec_search(text, int) TO hanpilji_gpt;
GRANT EXECUTE ON FUNCTION mart.q_kb_topic_precs(text) TO hanpilji_gpt;
