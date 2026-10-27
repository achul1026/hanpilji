-- 4단계(데모): 단지 · 주소 하나로 위치 · 토지 · 건물 · 규제 · 거래를 한 번에 보는 카드

-- 강남구 법정동 코드(현재 쓰는 14개). 행정표준코드관리시스템 법정동코드 목록 기준(말소되지 않은 것만)
-- 실거래가 매매 응답에는 법정동 코드가 없고 이름(umdNm)만 있어서, 이름 → 코드로 바꿀 때 씀
INSERT INTO core.dim_region (lawd_cd, sido, sigungu, emd, source, asof) VALUES
 ('1168000000','서울특별시','강남구',NULL,      '행정표준코드 법정동코드','2026-10-27'),
 ('1168010100','서울특별시','강남구','역삼동',  '행정표준코드 법정동코드','2026-10-27'),
 ('1168010300','서울특별시','강남구','개포동',  '행정표준코드 법정동코드','2026-10-27'),
 ('1168010400','서울특별시','강남구','청담동',  '행정표준코드 법정동코드','2026-10-27'),
 ('1168010500','서울특별시','강남구','삼성동',  '행정표준코드 법정동코드','2026-10-27'),
 ('1168010600','서울특별시','강남구','대치동',  '행정표준코드 법정동코드','2026-10-27'),
 ('1168010700','서울특별시','강남구','신사동',  '행정표준코드 법정동코드','2026-10-27'),
 ('1168010800','서울특별시','강남구','논현동',  '행정표준코드 법정동코드','2026-10-27'),
 ('1168011000','서울특별시','강남구','압구정동','행정표준코드 법정동코드','2026-10-27'),
 ('1168011100','서울특별시','강남구','세곡동',  '행정표준코드 법정동코드','2026-10-27'),
 ('1168011200','서울특별시','강남구','자곡동',  '행정표준코드 법정동코드','2026-10-27'),
 ('1168011300','서울특별시','강남구','율현동',  '행정표준코드 법정동코드','2026-10-27'),
 ('1168011400','서울특별시','강남구','일원동',  '행정표준코드 법정동코드','2026-10-27'),
 ('1168011500','서울특별시','강남구','수서동',  '행정표준코드 법정동코드','2026-10-27'),
 ('1168011800','서울특별시','강남구','도곡동',  '행정표준코드 법정동코드','2026-10-27')
ON CONFLICT (lawd_cd) DO UPDATE SET sido = excluded.sido, sigungu = excluded.sigungu, emd = excluded.emd,
    source = excluded.source, asof = excluded.asof;

-- 단지에 위치를 붙임. 실거래가 응답의 동 · 지번 · 도로명(전월세에만)에서 옴
ALTER TABLE core.dim_complex ADD COLUMN IF NOT EXISTS jibun text;
ALTER TABLE core.dim_complex ADD COLUMN IF NOT EXISTS road_addr text;
ALTER TABLE core.dim_complex ADD COLUMN IF NOT EXISTS build_year smallint;
ALTER TABLE core.dim_complex ADD COLUMN IF NOT EXISTS pnu varchar(25);   -- 법정동 + 지번으로 만든 값. 토지 · 건물 데이터와 잇는 열쇠

-- PNU 19자리 = 법정동 10 + 대장 구분 1(일반 1 · 산 2) + 본번 4 + 부번 4. 「527」 「12-3」 「산 4-1」 꼴만, 「BL」(블록) 등은 NULL
CREATE OR REPLACE FUNCTION core.make_pnu(p_lawd text, p_jibun text) RETURNS varchar
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN p_lawd ~ '^[0-9]{10}$' AND right(p_lawd, 5) <> '00000' AND m IS NOT NULL
                THEN p_lawd || CASE WHEN m[1] IS NULL THEN '1' ELSE '2' END
                     || lpad(m[2], 4, '0') || lpad(coalesce(nullif(m[3], ''), '0'), 4, '0') END
    FROM (SELECT regexp_match(btrim(coalesce(p_jibun, '')), '^(산\s*)?([0-9]{1,4})(?:-([0-9]{1,4}))?$') AS m) x
$$;

-- 이미 적재한 DB도 다시 수집하지 않고 채우도록 stg 원본에서 한 번 채움(여러 번 돌려도 같은 결과)
WITH raw AS (
    SELECT DISTINCT ON (k.complex_id)
           k.complex_id, r.row_data->>'umdNm' AS emd, nullif(btrim(r.row_data->>'jibun'), '') AS jibun,
           r.sigungu_cd
    FROM stg.rtms_raw r
    JOIN LATERAL (SELECT coalesce(d.complex_id, e.complex_id) AS complex_id
                  FROM (SELECT 1) one
                  LEFT JOIN core.fact_deal d ON d.deal_id = r.row_key
                  LEFT JOIN core.fact_rent e ON e.rent_id = r.row_key) k ON k.complex_id IS NOT NULL
    ORDER BY k.complex_id, r.loaded_at DESC
), road AS (
    SELECT DISTINCT ON (e.complex_id) e.complex_id, nullif(btrim(r.row_data->>'roadnm'), '') AS road_addr
    FROM stg.rtms_raw r JOIN core.fact_rent e ON e.rent_id = r.row_key
    WHERE nullif(btrim(r.row_data->>'roadnm'), '') IS NOT NULL
    ORDER BY e.complex_id, e.contract_date DESC
), yr AS (
    SELECT complex_id, max(build_year) AS build_year FROM core.fact_deal GROUP BY 1
), code AS (
    SELECT raw.complex_id, raw.jibun, g.lawd_cd
    FROM raw JOIN core.dim_region g ON left(g.lawd_cd, 5) = raw.sigungu_cd AND g.emd = raw.emd
)
UPDATE core.dim_complex x
SET lawd_cd = coalesce(code.lawd_cd, x.lawd_cd),
    jibun = coalesce(code.jibun, x.jibun),
    pnu = coalesce(core.make_pnu(code.lawd_cd, code.jibun), x.pnu),
    road_addr = coalesce(road.road_addr, x.road_addr),
    build_year = coalesce(yr.build_year, x.build_year)
FROM code LEFT JOIN road USING (complex_id) LEFT JOIN yr USING (complex_id)
WHERE x.complex_id = code.complex_id;

-- ── 단지 카드: 단지명 또는 「동 지번」(예: 도곡동 527) 하나로 다섯 칸을 한 줄에 ──
-- 위치 · 거래는 실거래가에서, 토지 · 건물 · 용도지역은 PNU로 잇는 표에서 가져옴(아직 수집 전이면 NULL과 함께 missing에 적음)
-- 거래 요약은 최근 p_months개월, 해제된 매매 · 월세 제외 기준. 같은 이름의 단지가 여럿이면 최대 5줄
CREATE OR REPLACE FUNCTION mart.q_property_card(p_sigungu text, p_query text, p_months int DEFAULT 12)
RETURNS TABLE (complex_name text, address text, road_addr text, pnu varchar, build_year int,
               area_types numeric[],
               trade_count bigint, trade_per_pyeong bigint, trade_last_date date, trade_last_amount bigint,
               trade_last_area numeric, jeonse_count bigint, jeonse_avg bigint, jeonse_ratio numeric, wolse_count bigint,
               land_category text, land_area_m2 numeric, land_price_per_m2 bigint, land_price_year int,
               zones text, zone_rule_count bigint,
               bld_main_use text, bld_approval_date date, bld_floors_above int, bld_households int,
               bld_parking int, bld_elevator boolean, bld_violation boolean,
               missing text[], period_from date, period_to date, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH q AS (
        SELECT regexp_replace(replace(replace(p_query, ' ', ''), '번지', ''), '\([^()]*\)$', '') AS k
    ), win AS (
        SELECT (current_date - make_interval(months => p_months))::date AS d_from, current_date AS d_to
    ), c AS (
        SELECT x.*, g.sido, g.sigungu AS sgg_name, g.emd, count(*) OVER () AS n_match   -- LIMIT 앞의 전체 건수
        FROM core.dim_complex x LEFT JOIN core.dim_region g ON g.lawd_cd = x.lawd_cd, q
        WHERE left(x.lawd_cd, 5) = p_sigungu
          AND (regexp_replace(replace(x.name, ' ', ''), '\([^()]*\)$', '') LIKE '%' || q.k || '%'
               OR (g.emd IS NOT NULL AND x.jibun IS NOT NULL AND q.k LIKE '%' || g.emd || x.jibun))
        ORDER BY (SELECT count(*) FROM core.fact_deal d WHERE d.complex_id = x.complex_id) DESC, x.name
        LIMIT 5
    ), t AS (
        SELECT d.complex_id, count(*) AS n, (sum(d.amount) / sum(d.area) * 3.305785)::bigint AS per_py,
               max(d.contract_date) AS last_date,
               (array_agg(d.amount ORDER BY d.contract_date DESC, d.amount DESC))[1] AS last_amt,
               (array_agg(d.area ORDER BY d.contract_date DESC, d.amount DESC))[1] AS last_area, max(d.asof) AS asof
        FROM core.fact_deal d JOIN c USING (complex_id), win
        WHERE NOT d.is_cancelled AND d.contract_date BETWEEN win.d_from AND win.d_to GROUP BY 1
    ), j AS (
        SELECT e.complex_id, count(*) FILTER (WHERE e.monthly_rent = 0) AS jn,
               avg(e.deposit) FILTER (WHERE e.monthly_rent = 0)::bigint AS j_avg,
               count(*) FILTER (WHERE e.monthly_rent > 0) AS wn, max(e.asof) AS asof
        FROM core.fact_rent e JOIN c USING (complex_id), win
        WHERE e.contract_date BETWEEN win.d_from AND win.d_to GROUP BY 1
    ), types AS (
        SELECT d.complex_id, array_agg(DISTINCT round(d.area)) AS a
        FROM (SELECT complex_id, area FROM core.fact_deal UNION ALL SELECT complex_id, area FROM core.fact_rent) d
        JOIN c USING (complex_id) WHERE d.area > 0 GROUP BY 1
    ), avg_t AS (
        SELECT d.complex_id, avg(d.amount)::numeric AS a FROM core.fact_deal d JOIN c USING (complex_id), win
        WHERE NOT d.is_cancelled AND d.contract_date BETWEEN win.d_from AND win.d_to GROUP BY 1
    ), land AS (
        SELECT p.pnu, p.land_category, p.area_m2,
               (SELECT lp.price_per_m2 FROM core.fact_land_price lp WHERE lp.pnu = p.pnu ORDER BY lp.base_year DESC LIMIT 1)::bigint AS price,
               (SELECT lp.base_year FROM core.fact_land_price lp WHERE lp.pnu = p.pnu ORDER BY lp.base_year DESC LIMIT 1)::int AS price_year,
               (SELECT string_agg(z.zone_name, ', ' ORDER BY z.zone_cd) FROM core.bridge_parcel_zone bz
                JOIN core.dim_zone z USING (zone_cd) WHERE bz.pnu = p.pnu) AS zones,
               (SELECT count(*) FROM core.bridge_parcel_zone bz JOIN core.reg_rule r USING (zone_cd)
                WHERE bz.pnu = p.pnu AND (r.sigungu_cd IS NULL OR r.sigungu_cd = p_sigungu)) AS rules
        FROM core.dim_parcel p JOIN c USING (pnu)
    ), bld AS (
        SELECT DISTINCT ON (b.pnu) b.pnu, b.main_use, b.approval_date, b.floors_above, b.parking_count,
               b.has_elevator, b.is_violation
        FROM core.dim_building b JOIN c USING (pnu)
        ORDER BY b.pnu, b.total_area DESC NULLS LAST
    )
    SELECT c.name,
           concat_ws(' ', c.sido, c.sgg_name, c.emd, c.jibun),
           c.road_addr, c.pnu, c.build_year::int,
           types.a,
           coalesce(t.n, 0), t.per_py, t.last_date, t.last_amt, t.last_area,
           coalesce(j.jn, 0), j.j_avg,
           CASE WHEN avg_t.a > 0 AND j.j_avg IS NOT NULL THEN round(j.j_avg / avg_t.a * 100, 1) END,
           coalesce(j.wn, 0),
           land.land_category, land.area_m2, land.price, land.price_year, land.zones, land.rules,
           bld.main_use, bld.approval_date, bld.floors_above, c.households, bld.parking_count,
           bld.has_elevator, bld.is_violation,
           array_remove(ARRAY[
               CASE WHEN c.pnu IS NULL THEN '지번(PNU)' END,
               CASE WHEN land.pnu IS NULL THEN '토지(지목 · 면적 · 공시지가)' END,
               CASE WHEN land.zones IS NULL THEN '용도지역 · 규제' END,
               CASE WHEN bld.pnu IS NULL THEN '건축물대장(용도 · 층수 · 주차 · 위반)' END], NULL),
           win.d_from, win.d_to,
           '국토교통부 아파트 매매 · 전월세 실거래가' || CASE WHEN land.pnu IS NOT NULL THEN ' · 토지' ELSE '' END
               || CASE WHEN bld.pnu IS NOT NULL THEN ' · 건축물대장' ELSE '' END,
           greatest(t.asof, j.asof, c.asof),
           c.n_match
    FROM c LEFT JOIN t USING (complex_id) LEFT JOIN j USING (complex_id) LEFT JOIN types USING (complex_id)
         LEFT JOIN avg_t USING (complex_id) LEFT JOIN land ON land.pnu = c.pnu LEFT JOIN bld ON bld.pnu = c.pnu, win
    ORDER BY coalesce(t.n, 0) + coalesce(j.jn, 0) DESC, c.name
$$;
ALTER FUNCTION mart.q_property_card(text, text, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_property_card(text, text, int) SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_property_card(text, text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_property_card(text, text, int) TO hanpilji_gpt;
