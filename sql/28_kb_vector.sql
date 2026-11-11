-- 부동산 지식 DB 7단계: 의미 검색(pgvector + bge-m3)
--   core.kb_chunk : 지식 DB의 조각마다 한 줄(법령 조문 · 판례 요지 · 행정규칙 · 해설서 쪽 · 개념 노트) + 1024차원 벡터
--   mart.q_kb_search(질문, 질문 벡터) : 글자 검색 순위와 의미 검색 순위를 섞어(RRF) 찾음. 벡터를 안 주면 글자 검색만
-- pgvector는 공용 DB의 public 스키마에 이미 있음(0.8.7, 2026-11-11 확인) → CREATE EXTENSION 하지 않음
-- 벡터는 서버 배치(hanpilji/collectors/embed.py, bge-m3 dense, CLS + 정규화)가 채움. 내용이 바뀐 조각만 다시 만듦
-- 25_prec · 26_guide · 27_concepts 다음에 적용

CREATE TABLE IF NOT EXISTS core.kb_chunk (
    chunk_id      text PRIMARY KEY,                -- law:<article_id> · prec:<prec_id> · guide:<doc_id>#<part_no> · concept:<concept_id>
    kind          text NOT NULL CHECK (kind IN ('법령', '판례', '행정규칙', '해설서', '개념')),
    title         text NOT NULL,                   -- 법령명 · 사건번호 · 자료 이름 · 용어
    label         text,                            -- 제3조 · 선고일 · 12쪽 · 단원
    body          text NOT NULL,                   -- 벡터를 만든 글(앞부분)
    search_text   text NOT NULL,
    content_hash  text NOT NULL,                   -- body md5. 바뀌면 벡터를 지우고 다시 만듦
    embedding     public.vector(1024),
    model         text,                            -- 벡터를 만든 모델 · 버전
    embedded_at   timestamptz
);
-- 출처 · 기준일(답 끝 근거 도장용). 원본 표에서 그대로 옮김. 바뀌어도 벡터는 다시 만들지 않음
ALTER TABLE core.kb_chunk ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE core.kb_chunk ADD COLUMN IF NOT EXISTS asof date;
CREATE INDEX IF NOT EXISTS kb_chunk_trgm ON core.kb_chunk USING gin (search_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS kb_chunk_kind_idx ON core.kb_chunk (kind);
CREATE INDEX IF NOT EXISTS kb_chunk_hnsw ON core.kb_chunk USING hnsw (embedding public.vector_cosine_ops);

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('kb_embed', '지식 DB 의미 검색 벡터(bge-m3)', '한필지(서버에서 계산)', 'file', NULL, 'UTF-8', '매주',
  '모델 BAAI/bge-m3(MIT), 서버 안에서만 계산 · 밖으로 보내지 않음', 'https://huggingface.co/BAAI/bge-m3')
ON CONFLICT (source_id) DO NOTHING;

-- 원본 표들 → kb_chunk. 바뀐 조각은 벡터를 비우고, 원본에서 사라진 조각은 지움. 반환: (넣거나 바뀐 수, 지운 수)
DROP FUNCTION IF EXISTS mart.kb_chunk_refresh();
CREATE FUNCTION mart.kb_chunk_refresh(OUT changed bigint, OUT removed bigint)
LANGUAGE plpgsql AS $$
BEGIN
    DROP TABLE IF EXISTS _src;
    CREATE TEMP TABLE _src (chunk_id text PRIMARY KEY, kind text, title text, label text, body text,
                            source text, asof date) ON COMMIT DROP;
    INSERT INTO _src
    SELECT 'law:' || w.article_id, '법령', w.law_name, w.article_no || coalesce('(' || w.article_title || ')', ''),
           left(w.law_name || ' ' || w.article_no || coalesce(' ' || w.article_title, '') || E'\n' || w.body, 2000),
           w.source, w.asof
    FROM core.doc_law w WHERE w.is_current;
    IF to_regclass('core.doc_prec') IS NOT NULL THEN
        EXECUTE $q$INSERT INTO _src
            SELECT 'prec:' || p.prec_id, '판례', p.case_no, coalesce(p.decided_on::text, ''),
                   left(concat_ws(E'\n', p.court || ' ' || p.case_no || ' ' || coalesce(p.case_name, ''),
                                  p.holding, p.summary), 2000),
                   p.source, p.asof
            FROM core.doc_prec p$q$;
    END IF;
    IF to_regclass('core.doc_guide_part') IS NOT NULL THEN
        EXECUTE $q$INSERT INTO _src
            SELECT 'guide:' || g.doc_id || '#' || p.part_no, CASE d.kind WHEN '행정규칙' THEN '행정규칙' ELSE '해설서' END,
                   d.title, p.label, left(d.title || ' ' || p.label || E'\n' || p.body, 2000),
                   d.source, d.asof
            FROM core.doc_guide_part p JOIN core.doc_guide d USING (doc_id)
            CROSS JOIN LATERAL (SELECT p.doc_id) g$q$;
    END IF;
    IF to_regclass('core.kb_concept') IS NOT NULL THEN
        EXECUTE $q$INSERT INTO _src
            SELECT 'concept:' || c.concept_id, '개념', c.term, c.topic_id,
                   left(concat_ws(E'\n', c.term, c.summary, c.body, c.formula), 2000),
                   c.source, c.asof
            FROM core.kb_concept c$q$;
    END IF;

    -- 글이 바뀐 조각만 벡터를 비움(출처 · 기준일만 바뀌면 벡터는 그대로). changed = 새로 넣거나 고친 조각 수
    WITH up AS (
        INSERT INTO core.kb_chunk (chunk_id, kind, title, label, body, search_text, content_hash, source, asof)
        SELECT s.chunk_id, s.kind, s.title, s.label, s.body, s.body, md5(s.body), s.source, s.asof FROM _src s
        ON CONFLICT (chunk_id) DO UPDATE SET kind = excluded.kind, title = excluded.title, label = excluded.label,
            body = excluded.body, search_text = excluded.search_text, content_hash = excluded.content_hash,
            source = excluded.source, asof = excluded.asof,
            embedding = CASE WHEN core.kb_chunk.content_hash = excluded.content_hash THEN core.kb_chunk.embedding END,
            model = CASE WHEN core.kb_chunk.content_hash = excluded.content_hash THEN core.kb_chunk.model END,
            embedded_at = CASE WHEN core.kb_chunk.content_hash = excluded.content_hash THEN core.kb_chunk.embedded_at END
        WHERE core.kb_chunk.content_hash <> excluded.content_hash OR core.kb_chunk.title <> excluded.title
              OR core.kb_chunk.label IS DISTINCT FROM excluded.label
              OR core.kb_chunk.source IS DISTINCT FROM excluded.source OR core.kb_chunk.asof IS DISTINCT FROM excluded.asof
        RETURNING 1
    ) SELECT count(*) INTO changed FROM up;
    WITH del AS (
        DELETE FROM core.kb_chunk k WHERE NOT EXISTS (SELECT 1 FROM _src s WHERE s.chunk_id = k.chunk_id) RETURNING 1
    ) SELECT count(*) INTO removed FROM del;
END $$;

-- 글자 순위(맞은 낱말 비율)와 의미 순위(코사인 거리)를 RRF(1/(60+순위))로 섞음. p_kinds로 종류를 좁힐 수 있음
-- 벡터를 주면 글자 쪽 가중을 절반으로(흔한 낱말 「있으면」 등으로 걸린 엉뚱한 조각이 위로 오지 않게)
-- 같은 글이 두 번 실린 조각(같은 사건이 판례 번호 둘로 실린 경우 등)은 점수 높은 하나만
DROP FUNCTION IF EXISTS mart.q_kb_search(text, public.vector, int, text[]);
CREATE FUNCTION mart.q_kb_search(p_query text, p_embedding public.vector DEFAULT NULL, p_limit int DEFAULT 8,
                                 p_kinds text[] DEFAULT NULL)
RETURNS TABLE (kind text, title text, label text, body text, text_rank int, vector_rank int, similarity real,
               score real, chunk_id text, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH q AS (
        SELECT array_agg(lower(w)) AS words, count(*) AS n
        FROM regexp_split_to_table(btrim(coalesce(p_query, '')), '\s+') AS w WHERE w <> ''
    ), txt AS (
        SELECT k.chunk_id, row_number() OVER (ORDER BY m.matched::real / q.n + word_similarity(p_query, k.search_text) / 10 DESC,
                                                       k.chunk_id)::int AS rnk
        FROM core.kb_chunk k CROSS JOIN q
        CROSS JOIN LATERAL (SELECT count(*) AS matched FROM unnest(q.words) w WHERE strpos(lower(k.search_text), w) > 0) m
        WHERE q.n > 0 AND m.matched * 2 >= q.n AND (p_kinds IS NULL OR k.kind = ANY(p_kinds))
        ORDER BY rnk LIMIT 50
    ), vec AS (
        SELECT k.chunk_id, (1 - (k.embedding OPERATOR(public.<=>) p_embedding))::real AS sim,
               row_number() OVER (ORDER BY k.embedding OPERATOR(public.<=>) p_embedding, k.chunk_id)::int AS rnk
        FROM core.kb_chunk k
        WHERE p_embedding IS NOT NULL AND k.embedding IS NOT NULL AND (p_kinds IS NULL OR k.kind = ANY(p_kinds))
        ORDER BY k.embedding OPERATOR(public.<=>) p_embedding LIMIT 50
    ), mix AS (
        SELECT coalesce(t.chunk_id, v.chunk_id) AS chunk_id, t.rnk AS t_rnk, v.rnk AS v_rnk, v.sim,
               (coalesce(1.0 / (60 + t.rnk), 0) * CASE WHEN p_embedding IS NULL THEN 1 ELSE 0.5 END
                + coalesce(1.0 / (60 + v.rnk), 0))::real AS score
        FROM txt t FULL JOIN vec v USING (chunk_id)
    ), one AS (
        SELECT DISTINCT ON (k.content_hash) k.kind, k.title, k.label, k.body, m.t_rnk, m.v_rnk, m.sim, m.score,
               k.chunk_id, k.source, k.asof
        FROM mix m JOIN core.kb_chunk k USING (chunk_id)
        ORDER BY k.content_hash, m.score DESC, k.chunk_id
    )
    SELECT o.kind, o.title, o.label, o.body, o.t_rnk, o.v_rnk, o.sim, o.score, o.chunk_id, o.source, o.asof,
           count(*) OVER ()
    FROM one o
    ORDER BY o.score DESC, o.chunk_id
    LIMIT greatest(least(coalesce(p_limit, 8), 30), 1)
$$;

ALTER FUNCTION mart.q_kb_search(text, public.vector, int, text[]) SECURITY DEFINER;
ALTER FUNCTION mart.q_kb_search(text, public.vector, int, text[]) SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_kb_search(text, public.vector, int, text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_kb_search(text, public.vector, int, text[]) TO hanpilji_gpt;
REVOKE EXECUTE ON FUNCTION mart.kb_chunk_refresh() FROM PUBLIC;
