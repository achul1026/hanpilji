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

-- ── 시세: 단지 x 전용면적별 매매 · 전세 (거래단가 · 거래가격 · 전세단가 · 전세시세) ──
-- 해제된 거래는 뺌. 전세 = 월세 0원인 계약. 면적은 ±p_area_tol ㎡ 안을 같은 타입으로 봄. 최대 20줄
CREATE OR REPLACE FUNCTION mart.q_complex_price(
    p_sigungu text, p_name text, p_area numeric DEFAULT NULL, p_months int DEFAULT 12,
    p_area_tol numeric DEFAULT 3)
RETURNS TABLE (complex_name text, emd text, area_m2 numeric,
               trade_count bigint, trade_avg bigint, trade_per_m2 bigint, trade_per_pyeong bigint,
               trade_last_date date, trade_last_amount bigint,
               jeonse_count bigint, jeonse_avg bigint, jeonse_per_m2 bigint, jeonse_ratio numeric,
               period_from date, period_to date, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH c AS (
        SELECT x.complex_id, x.name, r.emd, x.kind
        FROM core.dim_complex x
        LEFT JOIN core.dim_region r ON r.lawd_cd = x.lawd_cd
        WHERE left(x.lawd_cd, 5) = p_sigungu
          -- 끝 괄호는 빼고 비교(매매 「강남브리즈힐(토지임대부아파트)」 · 전월세 「강남브리즈힐」이 같은 단지)
          AND regexp_replace(replace(x.name, ' ', ''), '\([^()]*\)$', '')
              LIKE '%' || regexp_replace(replace(p_name, ' ', ''), '\([^()]*\)$', '') || '%'
    ), win AS (
        SELECT (current_date - make_interval(months => p_months))::date AS d_from, current_date AS d_to
    ), t AS (
        SELECT d.complex_id, round(d.area) AS area_m2, d.amount, d.area, d.contract_date, d.asof
        FROM core.fact_deal d JOIN c USING (complex_id), win
        WHERE NOT d.is_cancelled AND d.contract_date >= win.d_from
          AND (p_area IS NULL OR abs(d.area - p_area) <= p_area_tol)
    ), j AS (
        SELECT e.complex_id, round(e.area) AS area_m2, e.deposit, e.area, e.asof
        FROM core.fact_rent e JOIN c USING (complex_id), win
        WHERE e.monthly_rent = 0 AND e.area > 0 AND e.contract_date >= win.d_from
          AND (p_area IS NULL OR abs(e.area - p_area) <= p_area_tol)
    ), tg AS (
        SELECT complex_id, area_m2, count(*) AS n, avg(amount)::bigint AS avg_amt,
               (sum(amount) / sum(area))::bigint AS per_m2,
               max(contract_date) AS last_date,
               (array_agg(amount ORDER BY contract_date DESC))[1] AS last_amt, max(asof) AS asof
        FROM t GROUP BY 1, 2
    ), jg AS (
        SELECT complex_id, area_m2, count(*) AS n, avg(deposit)::bigint AS avg_dep,
               (sum(deposit) / sum(area))::bigint AS per_m2, max(asof) AS asof
        FROM j GROUP BY 1, 2
    ), g AS (
        SELECT coalesce(tg.complex_id, jg.complex_id) AS complex_id,
               coalesce(tg.area_m2, jg.area_m2) AS area_m2, tg.n AS tn, tg.avg_amt, tg.per_m2 AS t_m2,
               tg.last_date, tg.last_amt, jg.n AS jn, jg.avg_dep, jg.per_m2 AS j_m2,
               greatest(tg.asof, jg.asof) AS asof
        FROM tg FULL JOIN jg ON jg.complex_id = tg.complex_id AND jg.area_m2 = tg.area_m2
    )
    SELECT c.name, c.emd, g.area_m2,
           coalesce(g.tn, 0), g.avg_amt, g.t_m2, (g.t_m2 * 3.305785)::bigint, g.last_date, g.last_amt,
           coalesce(g.jn, 0), g.avg_dep, g.j_m2,
           CASE WHEN g.avg_amt > 0 AND g.avg_dep IS NOT NULL THEN round(g.avg_dep::numeric / g.avg_amt * 100, 1) END,
           win.d_from, win.d_to,
           '국토교통부 ' || c.kind || ' 매매 · 전월세 실거래가', g.asof,
           count(*) OVER ()
    FROM g JOIN c USING (complex_id), win
    ORDER BY c.name, g.area_m2
    LIMIT 20
$$;

-- ── 거래 목록: 한 단지의 거래를 한 건씩 (최고가 · 최저가 · 층 · 최근 거래) ──
-- kind: 매매 · 전세(월세 0원) · 월세 · 전월세. order: recent(최근순) · price_desc(비싼 순) · price_asc(싼 순)
-- 금액은 매매가 또는 보증금, 월세는 따로(월세를 값 순으로 보면 월세액 순). 해제된 매매는 뺌. 최대 30줄
-- 이름 일부가 여러 단지에 걸리면 이름이 똑같은 단지만 씀. 똑같은 게 없으면 걸린 단지를 모두 보이되 줄마다 단지명을 붙임
CREATE OR REPLACE FUNCTION mart.q_deal_list(
    p_sigungu text, p_name text, p_kind text DEFAULT '매매', p_area numeric DEFAULT NULL,
    p_months int DEFAULT 12, p_order text DEFAULT 'recent', p_area_tol numeric DEFAULT 3)
RETURNS TABLE (complex_name text, kind text, contract_date date, area_m2 numeric, floor int,
               amount bigint, monthly_rent bigint, contract_kind text,
               period_from date, period_to date, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH hit AS (
        SELECT x.complex_id, x.name,
               regexp_replace(replace(x.name, ' ', ''), '\([^()]*\)$', '')
                   = regexp_replace(replace(p_name, ' ', ''), '\([^()]*\)$', '') AS exact
        FROM core.dim_complex x
        WHERE left(x.lawd_cd, 5) = p_sigungu
          AND regexp_replace(replace(x.name, ' ', ''), '\([^()]*\)$', '')
              LIKE '%' || regexp_replace(replace(p_name, ' ', ''), '\([^()]*\)$', '') || '%'
    ), c AS (
        SELECT complex_id, name FROM hit WHERE exact OR NOT EXISTS (SELECT 1 FROM hit WHERE exact)
    ), win AS (
        SELECT (current_date - make_interval(months => p_months))::date AS d_from, current_date AS d_to
    ), d AS (
        SELECT c.name, '매매'::text AS kind, t.contract_date, t.area, t.floor, t.amount, NULL::bigint AS monthly,
               NULL::text AS ckind, t.source AS src, t.asof
        FROM core.fact_deal t JOIN c USING (complex_id)
        WHERE p_kind = '매매' AND NOT t.is_cancelled
        UNION ALL
        SELECT c.name, CASE WHEN e.monthly_rent = 0 THEN '전세' ELSE '월세' END, e.contract_date, e.area, e.floor,
               e.deposit, e.monthly_rent, e.contract_kind, e.source, e.asof
        FROM core.fact_rent e JOIN c USING (complex_id)
        WHERE (p_kind = '전월세' OR (p_kind = '전세' AND e.monthly_rent = 0) OR (p_kind = '월세' AND e.monthly_rent > 0))
    )
    SELECT d.name, d.kind, d.contract_date, d.area, d.floor, d.amount, d.monthly, d.ckind,
           win.d_from, win.d_to, d.src, d.asof, count(*) OVER ()
    FROM d, win
    WHERE d.contract_date BETWEEN win.d_from AND win.d_to
      AND (p_area IS NULL OR abs(d.area - p_area) <= p_area_tol)
    ORDER BY CASE WHEN p_order = 'price_desc' THEN -coalesce(nullif(d.monthly, 0), d.amount)
                  WHEN p_order = 'price_asc' THEN coalesce(nullif(d.monthly, 0), d.amount) END NULLS LAST,
             d.contract_date DESC, d.amount DESC
    LIMIT 30
$$;

-- ── 단지 순위: 시군구 안 단지를 거래 건수 · 단가로 줄 세움 ──
-- metric: trade_count · jeonse_count · wolse_count · trade_per_m2 · jeonse_per_m2 · jeonse_ratio. p_order: desc(큰 값부터) · asc
-- 단가 · 전세가율 순위는 그 거래가 p_min_count건 이상인 단지만(건수가 적으면 평균이 흔들림)
-- build_year는 매매 신고의 건축년도(단지의 모든 매매 중 가장 늦은 값). p_min_build_year로 신축만 고를 수 있음
CREATE OR REPLACE FUNCTION mart.q_complex_rank(
    p_sigungu text, p_metric text DEFAULT 'trade_count', p_months int DEFAULT 12,
    p_area numeric DEFAULT NULL, p_min_build_year int DEFAULT NULL, p_limit int DEFAULT 10,
    p_order text DEFAULT 'desc', p_min_count int DEFAULT 3, p_area_tol numeric DEFAULT 3)
RETURNS TABLE (rank bigint, complex_name text, build_year int,
               trade_count bigint, trade_avg bigint, trade_per_m2 bigint, trade_per_pyeong bigint,
               jeonse_count bigint, jeonse_avg bigint, jeonse_per_m2 bigint, jeonse_ratio numeric,
               wolse_count bigint, period_from date, period_to date, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH win AS (
        SELECT (current_date - make_interval(months => p_months))::date AS d_from, current_date AS d_to
    ), c AS (
        SELECT x.complex_id, x.name, (SELECT max(b.build_year) FROM core.fact_deal b
                                      WHERE b.complex_id = x.complex_id)::int AS build_year
        FROM core.dim_complex x
        WHERE left(x.lawd_cd, 5) = p_sigungu AND x.kind = '아파트'   -- 순위는 아파트끼리만(오피스텔은 단가 · 전세가율이 달라 섞지 않음)
    ), t AS (
        SELECT d.complex_id, count(*) AS n, avg(d.amount)::bigint AS avg_amt,
               (sum(d.amount) / sum(d.area))::bigint AS per_m2, max(d.asof) AS asof
        FROM core.fact_deal d, win
        WHERE NOT d.is_cancelled AND d.contract_date BETWEEN win.d_from AND win.d_to AND left(d.lawd_cd, 5) = p_sigungu
          AND d.deal_type = '아파트' AND (p_area IS NULL OR abs(d.area - p_area) <= p_area_tol)
        GROUP BY 1
    ), r AS (
        SELECT e.complex_id,
               count(*) FILTER (WHERE e.monthly_rent = 0) AS jn,
               avg(e.deposit) FILTER (WHERE e.monthly_rent = 0)::bigint AS j_avg,
               (sum(e.deposit) FILTER (WHERE e.monthly_rent = 0 AND e.area > 0)
                / nullif(sum(e.area) FILTER (WHERE e.monthly_rent = 0 AND e.area > 0), 0))::bigint AS j_m2,
               count(*) FILTER (WHERE e.monthly_rent > 0) AS wn, max(e.asof) AS asof
        FROM core.fact_rent e, win
        WHERE e.contract_date BETWEEN win.d_from AND win.d_to AND left(e.lawd_cd, 5) = p_sigungu
          AND e.rent_type = '아파트' AND (p_area IS NULL OR abs(e.area - p_area) <= p_area_tol)
        GROUP BY 1
    ), g AS (
        SELECT c.name, c.build_year, coalesce(t.n, 0) AS tn, t.avg_amt, t.per_m2,
               coalesce(r.jn, 0) AS jn, r.j_avg, r.j_m2, coalesce(r.wn, 0) AS wn,
               CASE WHEN t.avg_amt > 0 AND r.j_avg IS NOT NULL THEN round(r.j_avg::numeric / t.avg_amt * 100, 1) END AS ratio,
               greatest(t.asof, r.asof) AS asof
        FROM c LEFT JOIN t USING (complex_id) LEFT JOIN r USING (complex_id)
        WHERE (p_min_build_year IS NULL OR c.build_year >= p_min_build_year)
    ), m AS (
        SELECT g.*, CASE p_metric
                        WHEN 'trade_count'   THEN nullif(g.tn, 0)::numeric
                        WHEN 'jeonse_count'  THEN nullif(g.jn, 0)::numeric
                        WHEN 'wolse_count'   THEN nullif(g.wn, 0)::numeric
                        WHEN 'trade_per_m2'  THEN CASE WHEN g.tn >= p_min_count THEN g.per_m2 END
                        WHEN 'jeonse_per_m2' THEN CASE WHEN g.jn >= p_min_count THEN g.j_m2 END
                        WHEN 'jeonse_ratio'  THEN CASE WHEN g.tn >= p_min_count AND g.jn >= p_min_count THEN g.ratio END
                    END * CASE WHEN p_order = 'asc' THEN -1 ELSE 1 END AS v
        FROM g
    )
    SELECT rank() OVER (ORDER BY m.v DESC), m.name, m.build_year,
           m.tn, m.avg_amt, m.per_m2, (m.per_m2 * 3.305785)::bigint,
           m.jn, m.j_avg, m.j_m2, m.ratio, m.wn,
           win.d_from, win.d_to, '국토교통부 아파트 매매 · 전월세 실거래가', m.asof, count(*) OVER ()
    FROM m, win
    WHERE m.v IS NOT NULL
    ORDER BY m.v DESC, m.name
    LIMIT greatest(1, least(coalesce(p_limit, 10), 20))
$$;
