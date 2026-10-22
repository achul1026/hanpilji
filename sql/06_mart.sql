-- GPT가 부르는 마트. 질문 하나 = 함수 하나, 정렬 · 줄 수를 서버에 고정하고 전체 건수(row_total)를 함께 돌려줌
-- GPT는 SQL을 짜지 않고 함수 이름과 인자만 고름

-- ── 규제: 이 용도지역에 이 건물 가능? ──
CREATE OR REPLACE VIEW mart.zoning_check AS
SELECT z.zone_cd, z.zone_name, r.sigungu_cd, r.act_name, r.allowed, r.condition_text,
       l.law_name, l.article_no, l.article_title, l.effective_date,
       r.source, r.asof
FROM core.reg_rule r
JOIN core.dim_zone z USING (zone_cd)
LEFT JOIN core.doc_law l ON l.article_id = r.article_id;

-- 지자체 조례 규칙이 있으면 그것을, 없으면 전국 기준을 돌려줌. 최대 20줄
CREATE OR REPLACE FUNCTION mart.q_zoning_check(p_zone text, p_act text, p_sigungu text DEFAULT NULL)
RETURNS TABLE (zone_name text, act_name text, allowed text, condition_text text,
               basis text, scope text, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH hit AS (
        SELECT v.*,
               CASE WHEN v.sigungu_cd IS NULL THEN '전국 기준' ELSE '조례(' || v.sigungu_cd || ')' END AS scope,
               row_number() OVER (PARTITION BY v.zone_cd, v.act_name
                                  ORDER BY (v.sigungu_cd IS NULL)) AS pick
        FROM mart.zoning_check v
        WHERE (v.zone_name = p_zone OR v.zone_cd = p_zone)
          AND v.act_name LIKE '%' || p_act || '%'
          AND (v.sigungu_cd IS NULL OR v.sigungu_cd = p_sigungu)
    )
    SELECT h.zone_name, h.act_name, h.allowed, h.condition_text,
           CASE WHEN h.law_name IS NULL THEN NULL
                ELSE h.law_name || ' ' || h.article_no || coalesce('(' || h.article_title || ')', '') END,
           h.scope, h.source, h.asof,
           count(*) OVER ()
    FROM hit h
    WHERE h.pick = 1
    ORDER BY h.act_name
    LIMIT 20
$$;

-- ── 법령: 조문 검색 ──
-- 지금은 pg_trgm 부분 일치. pgvector를 깔면 같은 이름 · 같은 반환 형식으로 의미 검색으로 바꿈
CREATE OR REPLACE VIEW mart.law_search AS
SELECT article_id, law_name, law_kind, article_no, article_title, body, search_text,
       effective_date, source, asof
FROM core.doc_law
WHERE is_current;

CREATE OR REPLACE FUNCTION mart.q_law_search(p_query text, p_law text DEFAULT NULL)
RETURNS TABLE (law_name text, article_no text, article_title text, body text,
               effective_date date, score real, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    -- 질문을 띄어쓰기로 나눠 낱말마다 본문에 있는지 봄. 절반 이상 맞으면 후보
    -- (실측: 「차임 증액 청구」처럼 여러 낱말 질문은 문장 전체 유사도로는 0건이었음)
    WITH q AS (
        SELECT array_agg(w) AS words, count(*) AS n
        FROM regexp_split_to_table(btrim(p_query), '\s+') AS w
        WHERE w <> ''
    ), hit AS (
        SELECT s.*, m.matched,
               (m.matched::real / q.n + word_similarity(p_query, s.search_text) / 10)::real AS score
        FROM mart.law_search s, q,
             LATERAL (SELECT count(*) AS matched FROM unnest(q.words) w
                      WHERE s.search_text LIKE '%' || replace(replace(replace(w, '\', '\\'), '%', '\%'), '_', '\_') || '%') m
        WHERE (p_law IS NULL OR s.law_name = p_law)
          AND m.matched >= greatest(1, ceil(q.n / 2.0))
    )
    SELECT h.law_name, h.article_no, h.article_title, h.body, h.effective_date, h.score,
           h.source, h.asof, count(*) OVER ()
    FROM hit h
    ORDER BY h.score DESC, h.law_name, h.article_no
    LIMIT 10
$$;

-- ── 건물 카드 (0단계: 건축물대장만. 공시가격 · 소유는 데이터가 들어오면 붙임) ──
CREATE OR REPLACE VIEW mart.building_card AS
SELECT b.bld_pk, b.pnu, b.name, b.main_use, b.structure, b.approval_date,
       extract(year FROM age(current_date, b.approval_date))::int AS age_years,
       b.floors_above, b.floors_below, b.total_area, b.bc_rat, b.vl_rat,
       b.has_elevator, b.parking_count, b.is_violation,
       (SELECT count(*) FROM core.dim_unit u WHERE u.bld_pk = b.bld_pk) AS unit_count,
       b.source, b.asof
FROM core.dim_building b;

-- 「준공 20년 넘고 승강기 없는 건물」처럼 조건으로 고르는 질문. 최대 50줄, 오래된 순
CREATE OR REPLACE FUNCTION mart.q_building_filter(
    p_lawd_prefix text, p_min_age int DEFAULT NULL, p_elevator boolean DEFAULT NULL,
    p_violation boolean DEFAULT NULL, p_use text DEFAULT NULL, p_min_parking int DEFAULT NULL)
RETURNS TABLE (bld_pk text, pnu varchar, name text, main_use text, approval_date date,
               age_years int, floors_above int, has_elevator boolean, parking_count int,
               is_violation boolean, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    SELECT c.bld_pk, c.pnu, c.name, c.main_use, c.approval_date, c.age_years, c.floors_above,
           c.has_elevator, c.parking_count, c.is_violation, c.source, c.asof, count(*) OVER ()
    FROM mart.building_card c
    WHERE c.pnu LIKE p_lawd_prefix || '%'
      AND (p_min_age IS NULL OR c.age_years >= p_min_age)
      AND (p_elevator IS NULL OR c.has_elevator = p_elevator)
      AND (p_violation IS NULL OR c.is_violation = p_violation)
      AND (p_use IS NULL OR c.main_use LIKE '%' || p_use || '%')
      AND (p_min_parking IS NULL OR c.parking_count >= p_min_parking)
    ORDER BY c.approval_date NULLS LAST, c.bld_pk
    LIMIT 50
$$;
