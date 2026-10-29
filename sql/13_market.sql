-- 지표 축: 시장 · 거시 지표(한국은행 ECOS · 한국부동산원 R-ONE · KOSIS)와 이미 가진 데이터로 만든 추세 분석
-- 지표 값은 core.fact_market(지역 x 지표 x 기준월), 어떤 지표인지는 core.dim_stat에 둠. 수집기는 hanpilji/collectors/market.py
-- 키 없이 되는 분석(실거래 월별 추세 · 공시지가 연도별 추세 · 공시지가 대비 실거래가)은 fact_deal · fact_rent · fact_land_price로 셈

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('ecos', '한국은행 경제통계시스템(ECOS) Open API', '한국은행', 'api', NULL, 'UTF-8', '매월',
  '출처 표시', 'https://ecos.bok.or.kr/api/'),
 ('rone', '한국부동산원 부동산통계정보(R-ONE) Open API', '한국부동산원', 'api', NULL, 'UTF-8', '매주 · 매월',
  '출처 표시', 'https://www.reb.or.kr/r-one/'),
 ('kosis', '국가통계포털(KOSIS) Open API', '통계청', 'api', NULL, 'UTF-8', '매월',
  '출처 표시', 'https://kosis.kr/openapi/')
ON CONFLICT (source_id) DO NOTHING;

-- 전국 · 시도 단위 지표를 담으려고 넣는 지역 행(법정동 코드 체계: 시도 2자리 + 0)
INSERT INTO core.dim_region (lawd_cd, sido, sigungu, emd, source, asof) VALUES
 ('0000000000', '전국', NULL, NULL, '한필지 지표용', '2026-10-28'),
 ('1100000000', '서울특별시', NULL, NULL, '행정표준코드 법정동코드', '2026-10-28')
ON CONFLICT (lawd_cd) DO NOTHING;

-- 지표 목록. 수집기가 돌 때마다 자기 목록(market.SERIES)으로 덮어씀
CREATE TABLE IF NOT EXISTS core.dim_stat (
    stat_id        text PRIMARY KEY,              -- 한필지 이름. 예) base_rate, apt_sale_idx
    name           text NOT NULL,                 -- 예) 한국은행 기준금리
    unit           text,                          -- % · 지수 · 명 · 호 …
    cycle          text NOT NULL,                 -- M(월) · Q(분기) · A(연)
    source_id      text NOT NULL REFERENCES ops.ingest_source,
    source         text NOT NULL,                 -- 답에 적을 출처. 예) 한국은행 ECOS 722Y001
    note           text,                          -- 읽을 때 조심할 점
    asof           date NOT NULL
);

-- 지표 원본 응답 줄. 요청 하나(지표 x 쪽) = 한 줄
CREATE TABLE IF NOT EXISTS stg.market_raw (
    source_id      text NOT NULL,
    req_key        text NOT NULL,                 -- 예) base_rate:p1
    row_data       jsonb NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, req_key)
);

-- 단지의 필지: 실거래가 대표 필지 + 단지 건물이 선 필지(q_property_card의 cp와 같은 규칙)
CREATE OR REPLACE VIEW core.v_complex_parcel AS
    SELECT x.complex_id, x.pnu FROM core.dim_complex x WHERE x.pnu IS NOT NULL
    UNION SELECT cb.complex_id, b.pnu FROM core.bridge_complex_building cb
          JOIN core.dim_building b USING (bld_pk) WHERE b.pnu IS NOT NULL
    UNION SELECT x.complex_id, b.pnu FROM core.dim_complex x JOIN core.dim_building b ON b.pnu = x.pnu
    UNION SELECT cb.complex_id, pb.pnu FROM core.bridge_complex_building cb
          JOIN core.bridge_parcel_building pb USING (bld_pk);

-- 단지 찾기(이름 일부 또는 「동 지번」). q_property_card와 같은 규칙, 거래가 많은 단지가 먼저
CREATE OR REPLACE FUNCTION core.find_complex(p_sigungu text, p_query text)
RETURNS TABLE (complex_id text, name text, n_match bigint)
LANGUAGE sql STABLE AS $$
    WITH q AS (
        SELECT regexp_replace(replace(replace(p_query, ' ', ''), '번지', ''), '\([^()]*\)$', '') AS k
    )
    SELECT x.complex_id, x.name, count(*) OVER ()
    FROM core.dim_complex x LEFT JOIN core.dim_region g ON g.lawd_cd = x.lawd_cd, q
    WHERE left(x.lawd_cd, 5) = p_sigungu
      AND (regexp_replace(replace(x.name, ' ', ''), '\([^()]*\)$', '') LIKE '%' || q.k || '%'
           OR (g.emd IS NOT NULL AND x.jibun IS NOT NULL AND q.k LIKE '%' || g.emd || replace(x.jibun, ' ', '')))
    ORDER BY (SELECT count(*) FROM core.fact_deal d WHERE d.complex_id = x.complex_id) DESC, x.name
$$;

-- ── 실거래 월별 추세: 구 · 동 · 단지의 월별 매매 · 전세 ㎡당 중위 단가와 전월 · 전년 같은 달 대비 ──
-- 평균 대신 중위값: 비싼 단지가 몰린 달에 값이 튀지 않게. 단가는 전용면적 ㎡당
-- 실거래 신고 기한이 계약 후 30일이라 「달 끝 + 30일」이 기준일보다 뒤인 달은 아직 덜 들어온 달(partial = true)
-- 범위: p_name(단지) > p_emd(동) > 구 전체. 0건인 달도 줄을 냄(값은 비움)
-- 동 · 구 전체는 아파트만(오피스텔 단가를 섞으면 중위값이 흔들림). 단지를 주면 그 단지의 종류(아파트 · 오피스텔) 그대로
DROP FUNCTION IF EXISTS mart.q_price_trend(text, int, text, text, numeric, numeric);
CREATE FUNCTION mart.q_price_trend(p_sigungu text, p_months int DEFAULT 24, p_emd text DEFAULT NULL,
                                   p_name text DEFAULT NULL, p_area numeric DEFAULT NULL, p_area_tol numeric DEFAULT 3)
RETURNS TABLE (month text, trade_count bigint, trade_median_per_m2 bigint, trade_median_per_pyeong bigint,
               trade_mom_pct numeric, trade_yoy_pct numeric,
               jeonse_count bigint, jeonse_median_per_m2 bigint, jeonse_yoy_pct numeric,
               partial boolean, scope text, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH cx AS (
        SELECT f.complex_id, f.name, x.kind FROM core.find_complex(p_sigungu, p_name) f
        JOIN core.dim_complex x USING (complex_id) WHERE p_name IS NOT NULL LIMIT 1
    ), reg AS (
        SELECT lawd_cd FROM core.dim_region WHERE p_emd IS NOT NULL AND left(lawd_cd, 5) = p_sigungu
          AND emd = replace(p_emd, ' ', '')
    ), dc AS (   -- 실거래 줄의 법정동 코드는 구 단위라 동은 단지 위치(dim_complex)로 가림
        SELECT complex_id FROM core.dim_complex WHERE lawd_cd IN (SELECT lawd_cd FROM reg)
    ), win AS (   -- 전년 같은 달을 셀 수 있게 12개월 더 읽음
        SELECT date_trunc('month', current_date)::date - make_interval(months => greatest(1, least(p_months, 36)) - 1) AS m_from
    ), mons AS (
        SELECT generate_series(win.m_from - interval '12 months', date_trunc('month', current_date), interval '1 month')::date AS m
        FROM win
    ), d AS (
        SELECT date_trunc('month', d.contract_date)::date AS m, d.amount / d.area AS u, d.asof
        FROM core.fact_deal d, win
        WHERE NOT d.is_cancelled AND left(d.lawd_cd, 5) = p_sigungu
          AND d.contract_date >= win.m_from - interval '12 months'
          AND (p_name IS NULL OR d.complex_id = (SELECT complex_id FROM cx)) AND (p_name IS NOT NULL OR d.deal_type = '아파트')
          AND (p_emd IS NULL OR d.complex_id IN (SELECT complex_id FROM dc))
          AND (p_area IS NULL OR abs(d.area - p_area) <= p_area_tol)
    ), r AS (
        SELECT date_trunc('month', e.contract_date)::date AS m, e.deposit / e.area AS u, e.asof
        FROM core.fact_rent e, win
        WHERE e.monthly_rent = 0 AND e.area > 0 AND e.deposit > 0 AND left(e.lawd_cd, 5) = p_sigungu
          AND e.contract_date >= win.m_from - interval '12 months'
          AND (p_name IS NULL OR e.complex_id = (SELECT complex_id FROM cx)) AND (p_name IS NOT NULL OR e.rent_type = '아파트')
          AND (p_emd IS NULL OR e.complex_id IN (SELECT complex_id FROM dc))
          AND (p_area IS NULL OR abs(e.area - p_area) <= p_area_tol)
    ), dm AS (
        SELECT m, count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY u) AS med, max(asof) AS asof FROM d GROUP BY m
    ), rm AS (
        SELECT m, count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY u) AS med, max(asof) AS asof FROM r GROUP BY m
    ), s AS (
        SELECT mons.m, coalesce(dm.n, 0) AS tn, dm.med AS tmed, coalesce(rm.n, 0) AS jn, rm.med AS jmed,
               lag(dm.med) OVER (ORDER BY mons.m) AS t_prev, lag(dm.med, 12) OVER (ORDER BY mons.m) AS t_ly,
               lag(rm.med, 12) OVER (ORDER BY mons.m) AS j_ly
        FROM mons LEFT JOIN dm USING (m) LEFT JOIN rm USING (m)
    ), asof AS (
        SELECT greatest((SELECT max(asof) FROM dm), (SELECT max(asof) FROM rm)) AS a
    )
    SELECT to_char(s.m, 'YYYY-MM'), s.tn, s.tmed::bigint, (s.tmed * 3.305785)::bigint,
           round(((s.tmed / nullif(s.t_prev, 0) - 1) * 100)::numeric, 1),
           round(((s.tmed / nullif(s.t_ly, 0) - 1) * 100)::numeric, 1),
           s.jn, s.jmed::bigint, round(((s.jmed / nullif(s.j_ly, 0) - 1) * 100)::numeric, 1),
           (s.m + interval '1 month' + interval '30 days')::date > coalesce(asof.a, current_date),
           concat_ws(' ', (SELECT sigungu FROM core.dim_region WHERE lawd_cd = p_sigungu || '00000'),
                     CASE WHEN p_name IS NOT NULL THEN coalesce((SELECT name FROM cx), p_name || '(못 찾음)') END,
                     CASE WHEN p_name IS NULL AND p_emd IS NOT NULL THEN
                          CASE WHEN EXISTS (SELECT 1 FROM reg) THEN p_emd ELSE p_emd || '(못 찾음)' END END,
                     CASE WHEN p_area IS NOT NULL THEN '전용 ' || p_area || '㎡ ±' || p_area_tol END),
           '국토교통부 ' || coalesce((SELECT kind FROM cx), '아파트') || ' 매매 · 전월세 실거래가', asof.a, count(*) OVER ()
    FROM s, win, asof
    WHERE s.m >= win.m_from
    ORDER BY s.m
$$;

-- ── 공시지가 연도별 추세: 구 · 동 · 단지 필지의 개별공시지가(원/㎡) ──
-- 상승률은 두 해 모두 값이 있는 같은 필지끼리 비교한 상승률의 중위값(필지가 늘고 줄어도 값이 튀지 않음)
-- index_100은 첫 해를 100으로 둔 누적 지수(해마다 상승률을 곱함)
-- 범위: p_query가 동 이름(예: 대치동)이면 그 동, 아니면 단지(이름 일부 · 「동 지번」, 가장 거래가 많은 한 곳), 비우면 구 전체
DROP FUNCTION IF EXISTS mart.q_land_price_trend(text, text, int);
CREATE FUNCTION mart.q_land_price_trend(p_sigungu text, p_query text DEFAULT NULL, p_from_year int DEFAULT NULL)
RETURNS TABLE (base_year int, parcel_count bigint, median_per_m2 bigint, area_weighted_per_m2 bigint,
               yoy_pct numeric, matched_parcels bigint, index_100 numeric,
               scope text, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH reg AS (
        SELECT lawd_cd, emd FROM core.dim_region
        WHERE p_query IS NOT NULL AND left(lawd_cd, 5) = p_sigungu AND emd = replace(p_query, ' ', '')
    ), cx AS (
        SELECT f.complex_id, f.name FROM core.find_complex(p_sigungu, p_query) f
        WHERE p_query IS NOT NULL AND NOT EXISTS (SELECT 1 FROM reg) LIMIT 1
    ), parcels AS (
        SELECT p.pnu, p.area_m2 FROM core.dim_parcel p
        WHERE left(p.pnu, 5) = p_sigungu
          AND (p_query IS NULL
               OR p.lawd_cd IN (SELECT lawd_cd FROM reg)
               OR p.pnu IN (SELECT v.pnu FROM core.v_complex_parcel v JOIN cx USING (complex_id)))
    ), lp AS (
        SELECT l.pnu, l.base_year::int AS y, l.price_per_m2::numeric AS v, parcels.area_m2, l.source, l.asof
        FROM core.fact_land_price l JOIN parcels USING (pnu)
        WHERE NOT l.is_standard AND (p_from_year IS NULL OR l.base_year >= p_from_year - 1)
    ), g AS (
        SELECT a.y, (percentile_cont(0.5) WITHIN GROUP (ORDER BY a.v / b.v - 1) * 100)::numeric AS g, count(*) AS n
        FROM lp a JOIN lp b ON b.pnu = a.pnu AND b.y = a.y - 1
        GROUP BY a.y
    ), yr AS (
        SELECT lp.y, count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY lp.v) AS med,
               sum(lp.v * lp.area_m2) / nullif(sum(lp.area_m2) FILTER (WHERE lp.v IS NOT NULL), 0) AS wavg,
               string_agg(DISTINCT lp.source, ' · ') AS src, max(lp.asof) AS asof
        FROM lp WHERE p_from_year IS NULL OR lp.y >= p_from_year GROUP BY lp.y
    ), y0 AS (
        SELECT min(y) AS y0 FROM yr
    ), x AS (
        SELECT yr.*, g.g, g.n AS gn, yr.y > y0.y0 AS later,
               -- 첫 해는 100. 그 뒤는 상승률을 곱해 감(중간에 비교할 필지가 없는 해는 그대로 둠)
               100 * exp(sum(CASE WHEN yr.y > y0.y0 THEN ln(1 + coalesce(g.g, 0) / 100) ELSE 0 END)
                         OVER (ORDER BY yr.y)) AS idx
        FROM yr LEFT JOIN g USING (y), y0
    )
    SELECT x.y, x.n, x.med::bigint, x.wavg::bigint,
           CASE WHEN x.later THEN round(x.g, 1) END,
           CASE WHEN x.later THEN coalesce(x.gn, 0) END,
           round(x.idx, 1),
           concat_ws(' ', (SELECT sigungu FROM core.dim_region WHERE lawd_cd = p_sigungu || '00000'),
                     (SELECT emd FROM reg LIMIT 1), (SELECT name FROM cx),
                     CASE WHEN p_query IS NOT NULL AND NOT EXISTS (SELECT 1 FROM reg) AND NOT EXISTS (SELECT 1 FROM cx)
                          THEN p_query || '(못 찾음)' END),
           x.src || ' 개별공시지가', x.asof, count(*) OVER ()
    FROM x
    ORDER BY x.y
$$;

-- ── 공시지가 대비 실거래가: 단지마다 매매 ㎡당 중위 단가(전용면적) ÷ 개별공시지가(토지 ㎡당) ──
-- 배수는 「땅값 공시 대비 아파트 거래 단가가 몇 배인가」를 비교하는 지표일 뿐 시세 반영률(현실화율)이 아님
--   (공시지가는 토지 ㎡당, 실거래 단가는 전용면적 ㎡당이라 단위 면적이 다름. 용적률이 높을수록 배수가 작아짐)
-- 공시지가는 단지 필지 전부의 최신 해 값을 토지 면적으로 가중 평균. land_yoy_pct는 같은 필지끼리 전년 대비
-- trade_yoy_pct는 최근 12개월 대 그 앞 12개월의 매매 ㎡당 중위 단가(두 기간 모두 3건 이상일 때)
-- p_query가 있으면 그 단지들(최대 5), 없으면 매매가 p_min_count건 이상인 단지의 배수 순위(최대 20)
DROP FUNCTION IF EXISTS mart.q_price_vs_official(text, text, int, int, text, int);
CREATE FUNCTION mart.q_price_vs_official(p_sigungu text, p_query text DEFAULT NULL, p_months int DEFAULT 12,
                                         p_limit int DEFAULT 10, p_order text DEFAULT 'desc', p_min_count int DEFAULT 3)
RETURNS TABLE (rank bigint, complex_name text, build_year int, trade_count bigint, trade_median_per_m2 bigint,
               land_price_per_m2 bigint, land_price_year int, price_multiple numeric,
               land_yoy_pct numeric, trade_yoy_pct numeric, vl_rat numeric,
               period_from date, period_to date, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH win AS (
        SELECT (current_date - make_interval(months => greatest(1, least(p_months, 36))))::date AS d_from, current_date AS d_to
    ), c AS (
        SELECT x.complex_id, x.name, x.build_year, x.vl_rat, x.kind FROM core.dim_complex x
        WHERE left(x.lawd_cd, 5) = p_sigungu
          AND (p_query IS NOT NULL OR x.kind = '아파트')   -- 순위는 아파트끼리만. 단지를 주면 오피스텔도
          AND (p_query IS NULL OR x.complex_id IN (SELECT f.complex_id FROM core.find_complex(p_sigungu, p_query) f LIMIT 5))
    ), t AS (
        SELECT d.complex_id, count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY d.amount / d.area) AS med,
               max(d.asof) AS asof
        FROM core.fact_deal d JOIN c USING (complex_id), win
        WHERE NOT d.is_cancelled AND d.contract_date BETWEEN win.d_from AND win.d_to
        GROUP BY 1
    ), ty AS (   -- 최근 12개월 대 그 앞 12개월
        SELECT d.complex_id,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d.amount / d.area)
                   FILTER (WHERE d.contract_date > current_date - interval '12 months') AS now_med,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d.amount / d.area)
                   FILTER (WHERE d.contract_date <= current_date - interval '12 months') AS ly_med,
               count(*) FILTER (WHERE d.contract_date > current_date - interval '12 months') AS now_n,
               count(*) FILTER (WHERE d.contract_date <= current_date - interval '12 months') AS ly_n
        FROM core.fact_deal d JOIN c USING (complex_id)
        WHERE NOT d.is_cancelled AND d.contract_date > current_date - interval '24 months'
        GROUP BY 1
    ), cp AS (
        SELECT v.complex_id, v.pnu, p.area_m2 FROM core.v_complex_parcel v JOIN c USING (complex_id)
        JOIN core.dim_parcel p USING (pnu)
    ), ly AS (   -- 단지마다 공시지가가 있는 가장 최근 해
        SELECT cp.complex_id, max(l.base_year)::int AS y
        FROM cp JOIN core.fact_land_price l USING (pnu) WHERE NOT l.is_standard GROUP BY 1
    ), lp AS (
        SELECT cp.complex_id, ly.y,
               coalesce(sum(a.price_per_m2::numeric * cp.area_m2) / nullif(sum(cp.area_m2), 0), avg(a.price_per_m2)) AS v,
               (percentile_cont(0.5) WITHIN GROUP (ORDER BY a.price_per_m2::numeric / b.price_per_m2 - 1)
                    FILTER (WHERE b.price_per_m2 IS NOT NULL)) * 100 AS g,
               max(a.asof) AS asof
        FROM cp JOIN ly USING (complex_id)
        JOIN core.fact_land_price a ON a.pnu = cp.pnu AND a.base_year = ly.y AND NOT a.is_standard
        LEFT JOIN core.fact_land_price b ON b.pnu = cp.pnu AND b.base_year = ly.y - 1 AND NOT b.is_standard
        GROUP BY 1, 2
    ), m AS (
        SELECT c.*, t.n, t.med, lp.v, lp.y, lp.g, round((t.med / lp.v)::numeric, 2) AS mult,
               CASE WHEN ty.now_n >= p_min_count AND ty.ly_n >= p_min_count
                    THEN round(((ty.now_med / ty.ly_med - 1) * 100)::numeric, 1) END AS tg,
               greatest(t.asof, lp.asof) AS asof
        FROM c LEFT JOIN t USING (complex_id) LEFT JOIN lp USING (complex_id) LEFT JOIN ty USING (complex_id)
        WHERE p_query IS NOT NULL OR (t.n >= p_min_count AND lp.v IS NOT NULL)
    )
    SELECT rank() OVER (ORDER BY m.mult * CASE WHEN p_order = 'asc' THEN -1 ELSE 1 END DESC NULLS LAST),
           m.name, m.build_year::int, coalesce(m.n, 0), m.med::bigint, m.v::bigint, m.y, m.mult,
           round(m.g::numeric, 1), m.tg, m.vl_rat, win.d_from, win.d_to,
           '국토교통부 ' || m.kind || ' 매매 실거래가 · V-World 토지특성 개별공시지가', m.asof, count(*) OVER ()
    FROM m, win
    ORDER BY m.mult * CASE WHEN p_order = 'asc' THEN -1 ELSE 1 END DESC NULLS LAST, m.name
    LIMIT CASE WHEN p_query IS NOT NULL THEN 5 ELSE greatest(1, least(coalesce(p_limit, 10), 20)) END
$$;

-- ── 시장 · 거시 지표: 금리 · 가격지수 · 인구 등(core.fact_market) ──
-- p_stat을 비우면 지표마다 가장 최근 값 한 줄씩(무엇이 있는지 볼 때). 주면 그 지표의 월별 값
-- change는 전월(전기) 대비 차이, change_pct는 변화율. 금리(%)는 차이를 %p로 읽고, 지수 · 건수는 변화율로 읽음
-- p_region: 전국 · 서울 · 강남구 또는 법정동 코드 10자리. 비우면 그 지표가 가진 지역 전부
DROP FUNCTION IF EXISTS mart.q_market_series(text, text, int);
CREATE FUNCTION mart.q_market_series(p_stat text DEFAULT NULL, p_region text DEFAULT NULL, p_months int DEFAULT 24)
RETURNS TABLE (stat_id text, stat_name text, region text, period text, value numeric, unit text,
               change numeric, change_pct numeric, yoy_change numeric, yoy_pct numeric, note text,
               source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH reg AS (
        SELECT lawd_cd FROM core.dim_region
        WHERE p_region IS NOT NULL AND (lawd_cd = p_region
              OR (right(lawd_cd, 8) = '00000000' AND replace(p_region, '특별시', '') = replace(sido, '특별시', ''))
              OR (right(lawd_cd, 5) = '00000' AND right(lawd_cd, 8) <> '00000000' AND sigungu = replace(p_region, ' ', '')))
    ), f AS (
        SELECT m.stat_id, m.lawd_cd, m.period, m.value, m.asof, s.name, coalesce(m.unit, s.unit) AS u,
               s.note, s.source AS src, s.cycle,
               lag(m.value) OVER w AS prev, lag(m.value, CASE s.cycle WHEN 'Q' THEN 4 WHEN 'A' THEN 1 ELSE 12 END) OVER w AS ly,
               row_number() OVER (PARTITION BY m.stat_id, m.lawd_cd ORDER BY m.period DESC) AS rn
        FROM core.fact_market m JOIN core.dim_stat s USING (stat_id)
        WHERE (p_stat IS NULL OR m.stat_id = p_stat)
          AND (p_region IS NULL OR m.lawd_cd IN (SELECT lawd_cd FROM reg))
        WINDOW w AS (PARTITION BY m.stat_id, m.lawd_cd ORDER BY m.period)
    ), pick AS (
        SELECT f.* FROM f
        WHERE (p_stat IS NULL AND f.rn = 1)
           OR (p_stat IS NOT NULL AND f.rn <= CASE f.cycle WHEN 'Q' THEN greatest(1, p_months / 3)
                                                        WHEN 'A' THEN greatest(1, p_months / 12)
                                                        ELSE greatest(1, least(p_months, 120)) END)
    )
    SELECT p.stat_id, p.name, coalesce(g.sigungu, g.sido), p.period, p.value, p.u,
           p.value - p.prev, round((p.value / nullif(p.prev, 0) - 1) * 100, 2),
           p.value - p.ly, round((p.value / nullif(p.ly, 0) - 1) * 100, 2), p.note,
           p.src, p.asof, count(*) OVER ()
    FROM pick p JOIN core.dim_region g USING (lawd_cd)
    ORDER BY p.stat_id, p.lawd_cd, p.period
$$;

ALTER FUNCTION mart.q_price_trend(text, int, text, text, numeric, numeric) SECURITY DEFINER;
ALTER FUNCTION mart.q_price_trend(text, int, text, text, numeric, numeric) SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_land_price_trend(text, text, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_land_price_trend(text, text, int) SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_price_vs_official(text, text, int, int, text, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_price_vs_official(text, text, int, int, text, int) SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_market_series(text, text, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_market_series(text, text, int) SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_price_trend(text, int, text, text, numeric, numeric),
    mart.q_land_price_trend(text, text, int), mart.q_price_vs_official(text, text, int, int, text, int),
    mart.q_market_series(text, text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_price_trend(text, int, text, text, numeric, numeric),
    mart.q_land_price_trend(text, text, int), mart.q_price_vs_official(text, text, int, int, text, int),
    mart.q_market_series(text, text, int) TO hanpilji_gpt;
