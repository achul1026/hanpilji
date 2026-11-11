-- 부동산 지식 DB 6단계: 부동산학개론 개념 노트(직접 씀, data/concepts/*.toml)
--   core.kb_concept        : 개념 하나 = 한 줄(용어 · 요약 · 풀이 · 공식 · 예시 · 이어 볼 데이터 · 근거 법령)
--   mart.q_concept_search(질문) : 용어 · 요약 · 풀이에서 낱말로 찾음(q_law_search와 같은 방식)
--   mart.q_kb_topic_concepts(단원) : 단원의 개념 노트
-- 수집기: hanpilji/collectors/concepts.py(매주 배치, 바뀐 노트만). 24_kb_topics.sql 다음에 적용

CREATE TABLE IF NOT EXISTS core.kb_concept (
    concept_id   text PRIMARY KEY,                 -- C001 …
    topic_id     text NOT NULL REFERENCES core.kb_topic,
    term         text NOT NULL,
    summary      text NOT NULL,
    body         text NOT NULL,
    formula      text,
    example      text,
    data_refs    text[] NOT NULL DEFAULT '{}',     -- 이어 볼 한필지 함수 · 지표(「q_deal_list」 · 「q_market_series:base_rate」)
    law_refs     text[] NOT NULL DEFAULT '{}',     -- 근거 법령(「감정평가에 관한 규칙 제14조」)
    search_text  text NOT NULL,
    version_id   text NOT NULL,                    -- 노트 내용 sha256 앞 16자(바뀐 것만 asof 갱신)
    source       text NOT NULL,
    asof         date NOT NULL
);
CREATE INDEX IF NOT EXISTS kb_concept_topic_idx ON core.kb_concept (topic_id);
CREATE INDEX IF NOT EXISTS kb_concept_search_trgm ON core.kb_concept USING gin (search_text gin_trgm_ops);

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('kb_concept_note', '한필지 개념 노트(부동산학개론)', '한필지(직접 작성)', 'file', NULL, 'UTF-8', '수시',
  '직접 작성, 법령 근거는 각 노트에 표시', NULL)
ON CONFLICT (source_id) DO NOTHING;

-- 낱말 절반 이상이 들어 있는 노트(대소문자 무시: npv = NPV). 용어에 낱말이 들어 있으면 가산
DROP FUNCTION IF EXISTS mart.q_concept_search(text, int);
CREATE FUNCTION mart.q_concept_search(p_query text, p_limit int DEFAULT 5)
RETURNS TABLE (term text, topic_id text, unit text, summary text, body text, formula text, example text,
               data_refs text[], law_refs text[], score real, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH q AS (
        SELECT array_agg(w) AS words, count(*) AS n
        FROM regexp_split_to_table(btrim(p_query), '\s+') AS w WHERE w <> ''
    ), hit AS (
        SELECT c.*, (m.matched::real / q.n + word_similarity(p_query, c.search_text) / 10
                     + CASE WHEN t.in_term > 0 THEN 0.2 ELSE 0 END)::real AS score
        FROM core.kb_concept c CROSS JOIN q
        CROSS JOIN LATERAL (SELECT count(*) AS matched FROM unnest(q.words) w
                            WHERE strpos(lower(c.search_text), lower(w)) > 0) m
        CROSS JOIN LATERAL (SELECT count(*) AS in_term FROM unnest(q.words) w WHERE strpos(lower(c.term), lower(w)) > 0) t
        WHERE q.n > 0 AND m.matched * 2 >= q.n
    )
    SELECT h.term, h.topic_id, k.unit, h.summary, h.body, h.formula, h.example, h.data_refs, h.law_refs, h.score,
           h.source, h.asof, count(*) OVER ()
    FROM hit h JOIN core.kb_topic k USING (topic_id)
    ORDER BY h.score DESC, h.concept_id
    LIMIT greatest(least(coalesce(p_limit, 5), 30), 1)
$$;

DROP FUNCTION IF EXISTS mart.q_kb_topic_concepts(text);
CREATE FUNCTION mart.q_kb_topic_concepts(p_topic text)
RETURNS TABLE (concept_id text, term text, summary text, formula text, data_refs text[], law_refs text[], asof date)
LANGUAGE sql STABLE AS $$
    SELECT c.concept_id, c.term, c.summary, c.formula, c.data_refs, c.law_refs, c.asof
    FROM core.kb_concept c WHERE c.topic_id = p_topic ORDER BY c.concept_id
$$;

ALTER FUNCTION mart.q_concept_search(text, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_concept_search(text, int) SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_kb_topic_concepts(text) SECURITY DEFINER;
ALTER FUNCTION mart.q_kb_topic_concepts(text) SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_concept_search(text, int) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION mart.q_kb_topic_concepts(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_concept_search(text, int) TO hanpilji_gpt;
GRANT EXECUTE ON FUNCTION mart.q_kb_topic_concepts(text) TO hanpilji_gpt;
