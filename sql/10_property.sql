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
-- 건축물대장 총괄표제부(단지 전체 한 장)에서 옴. 총괄표제부가 없는 작은 단지는 비우고 카드가 동별 표제부를 합침
ALTER TABLE core.dim_complex ADD COLUMN IF NOT EXISTS site_area_m2 numeric(14, 2);   -- 대지면적
ALTER TABLE core.dim_complex ADD COLUMN IF NOT EXISTS parking_total integer;          -- 총 주차 대수
ALTER TABLE core.dim_complex ADD COLUMN IF NOT EXISTS bc_rat numeric(7, 2);           -- 건폐율 %
ALTER TABLE core.dim_complex ADD COLUMN IF NOT EXISTS vl_rat numeric(8, 2);           -- 용적률 %
-- 건축물대장 표제부(동마다 한 장)에서 옴
ALTER TABLE core.dim_building ADD COLUMN IF NOT EXISTS is_main boolean;               -- 주건축물(부속건축물이면 false)
ALTER TABLE core.dim_building ADD COLUMN IF NOT EXISTS households integer;            -- 세대 수
ALTER TABLE core.dim_building ADD COLUMN IF NOT EXISTS site_area numeric(14, 2);      -- 대지면적(집합건물 동은 0으로 와서 비움)

-- 블록 단지(택지지구에서 지번이 아직 안 매겨진 단지. 실거래가 지번 「BL」)의 용도지역 · 지구.
-- 필지(PNU)가 없어 단지에 바로 붙임. 블록 대장(「자곡동 블록」)의 지역지구에서 옴(collectors/bld.py)
CREATE TABLE IF NOT EXISTS core.bridge_complex_zone (
    complex_id     text NOT NULL REFERENCES core.dim_complex,
    zone_cd        text NOT NULL REFERENCES core.dim_zone,
    source         text NOT NULL,
    asof           date NOT NULL,
    PRIMARY KEY (complex_id, zone_cd)
);

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
-- 위치 · 거래는 실거래가에서, 토지 · 용도지역 · 건물은 단지에 걸린 필지 · 건물 전부에서 모음
--   필지 = 실거래가 대표 지번(PNU) + 단지 건물(bridge_complex_building)이 선 필지(bridge_parcel_building · dim_building.pnu)
--   용도지역은 「접함」을 빼고 「포함」 · 「저촉」만. 공시지가는 대표 필지의 최신 해 개별공시지가를 먼저 씀
-- 전세가율은 면적이 섞이지 않게 매매 · 전세가 모두 있는 전용면적 중 매매가 가장 많은 타입 하나로 셈(jeonse_ratio_area)
-- 거래 요약은 최근 p_months개월, 해제된 매매 제외. recent_deals는 최근 매매 5건(같은 스냅숏에서 읽음)
-- 토지 면적은 토지대장 면적 합이 없으면 건축물대장 대지면적(총괄표제부 → 동별 표제부)을 쓰고 land_area_kind에 출처를 적음
-- 세대 · 주차 · 건폐율 · 용적률도 총괄표제부(dim_complex)가 먼저, 없으면 동별 표제부를 합침. 동 수는 주건축물만
-- 아직 수집 전이라 비어 있는 칸은 missing에 적음. 같은 이름의 단지가 여럿이면 최대 5줄
DROP FUNCTION IF EXISTS mart.q_property_card(text, text, int);
CREATE FUNCTION mart.q_property_card(p_sigungu text, p_query text, p_months int DEFAULT 12)
RETURNS TABLE (complex_name text, address text, road_addr text, pnu varchar, build_year int,
               area_types numeric[],
               trade_count bigint, trade_per_pyeong bigint, trade_last_date date, trade_last_amount bigint,
               trade_last_area numeric, jeonse_count bigint, jeonse_avg bigint,
               jeonse_ratio numeric, jeonse_ratio_area numeric, wolse_count bigint, recent_deals jsonb,
               land_parcel_count bigint, land_category text, land_area_m2 numeric, land_area_kind text,
               land_price_per_m2 bigint, land_price_year int, zones text, zone_rule_count bigint,
               bld_count bigint, bld_main_use text, bld_approval_date date, bld_floors_above int, bld_households int,
               bld_parking bigint, bld_elevator boolean, bld_violation boolean, bld_bc_rat numeric, bld_vl_rat numeric,
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
               OR (g.emd IS NOT NULL AND x.jibun IS NOT NULL AND q.k LIKE '%' || g.emd || replace(x.jibun, ' ', '')))
        ORDER BY (SELECT count(*) FROM core.fact_deal d WHERE d.complex_id = x.complex_id) DESC, x.name
        LIMIT 5
    ), td AS (
        SELECT d.* FROM core.fact_deal d JOIN c USING (complex_id), win
        WHERE NOT d.is_cancelled AND d.contract_date BETWEEN win.d_from AND win.d_to
    ), jd AS (
        SELECT e.* FROM core.fact_rent e JOIN c USING (complex_id), win
        WHERE e.contract_date BETWEEN win.d_from AND win.d_to
    ), t AS (
        SELECT complex_id, count(*) AS n, (sum(amount) / sum(area) * 3.305785)::bigint AS per_py,
               max(contract_date) AS last_date,
               (array_agg(amount ORDER BY contract_date DESC, amount DESC))[1] AS last_amt,
               (array_agg(area ORDER BY contract_date DESC, amount DESC))[1] AS last_area, max(asof) AS asof,
               (jsonb_agg(jsonb_build_object('contract_date', contract_date, 'area_m2', area, 'floor', floor,
                                             'amount', amount) ORDER BY contract_date DESC, amount DESC)) AS deals
        FROM td GROUP BY 1
    ), j AS (
        SELECT complex_id, count(*) FILTER (WHERE monthly_rent = 0) AS jn,
               avg(deposit) FILTER (WHERE monthly_rent = 0)::bigint AS j_avg,
               count(*) FILTER (WHERE monthly_rent > 0) AS wn, max(asof) AS asof
        FROM jd GROUP BY 1
    ), ratio AS (   -- 같은 전용면적 타입끼리만 비교
        SELECT DISTINCT ON (a.complex_id) a.complex_id, a.area_m2,
               round(b.dep / a.amt * 100, 1) AS r
        FROM (SELECT complex_id, round(area) AS area_m2, count(*) AS n, avg(amount) AS amt FROM td GROUP BY 1, 2) a
        JOIN (SELECT complex_id, round(area) AS area_m2, avg(deposit) AS dep FROM jd
              WHERE monthly_rent = 0 AND area > 0 GROUP BY 1, 2) b USING (complex_id, area_m2)
        ORDER BY a.complex_id, a.n DESC, a.area_m2
    ), types AS (
        SELECT d.complex_id, array_agg(DISTINCT round(d.area)) AS a
        FROM (SELECT complex_id, area FROM core.fact_deal UNION ALL SELECT complex_id, area FROM core.fact_rent) d
        JOIN c USING (complex_id) WHERE d.area > 0 GROUP BY 1
    ), cb AS (   -- 단지의 건물: 연결표에 있는 것 + 대표 필지에 선 것
        SELECT c.complex_id, b.* FROM c JOIN core.bridge_complex_building x USING (complex_id)
        JOIN core.dim_building b USING (bld_pk)
        UNION
        SELECT c.complex_id, b.* FROM c JOIN core.dim_building b ON b.pnu = c.pnu
    ), cp AS (   -- 단지의 필지: 대표 필지 + 건물이 선 필지
        SELECT c.complex_id, c.pnu FROM c WHERE c.pnu IS NOT NULL
        UNION SELECT complex_id, pnu FROM cb WHERE pnu IS NOT NULL
        UNION SELECT cb.complex_id, pb.pnu FROM cb JOIN core.bridge_parcel_building pb USING (bld_pk)
    ), land AS (
        SELECT cp.complex_id, count(*) AS n, string_agg(DISTINCT p.land_category, ', ') AS cat,
               sum(p.area_m2) AS area, max(p.asof) AS asof,
               string_agg(DISTINCT p.source, ' · ') FILTER (WHERE p.land_category IS NOT NULL) AS src
        FROM cp JOIN core.dim_parcel p USING (pnu) GROUP BY 1
    ), price AS (
        SELECT DISTINCT ON (cp.complex_id) cp.complex_id, lp.price_per_m2 AS price, lp.base_year::int AS yr, lp.asof
        FROM cp JOIN c USING (complex_id) JOIN core.fact_land_price lp ON lp.pnu = cp.pnu
        ORDER BY cp.complex_id, (cp.pnu = c.pnu) DESC, lp.base_year DESC, lp.is_standard, lp.pnu
    ), pz AS (   -- 필지의 용도지역. V-World 토지이용계획이 있는 필지는 그것만 씀(건축물대장 지역지구는 「일반주거지역」처럼 뭉뚱그려 올 때가 많음)
        SELECT cp.complex_id, bz.zone_cd, bz.source, bz.asof
        FROM cp JOIN core.bridge_parcel_zone bz USING (pnu)
        WHERE bz.overlap <> '접함'
          AND (bz.source = 'V-World 토지이용계획' OR NOT EXISTS (
                   SELECT 1 FROM core.bridge_parcel_zone v WHERE v.pnu = bz.pnu AND v.source = 'V-World 토지이용계획'))
        UNION ALL   -- 필지가 없는 블록 단지는 단지에 바로 붙은 용도지역
        SELECT cz.complex_id, cz.zone_cd, cz.source, cz.asof FROM c JOIN core.bridge_complex_zone cz USING (complex_id)
    ), zone AS (
        SELECT pz.complex_id, string_agg(DISTINCT z.zone_name, ', ') AS zones, max(pz.asof) AS asof,
               string_agg(DISTINCT pz.source, ' · ') AS src,
               (SELECT count(DISTINCT r.rule_id) FROM pz pz2 JOIN core.reg_rule r USING (zone_cd)
                WHERE pz2.complex_id = pz.complex_id
                  AND (r.sigungu_cd IS NULL OR r.sigungu_cd = p_sigungu)) AS rules
        FROM pz JOIN core.dim_zone z USING (zone_cd) GROUP BY 1
    ), bld AS (   -- 동 수 · 용도 · 세대는 주건축물만. 주차는 부속건축물(지하주차장 등)까지 합침
        SELECT complex_id, count(*) FILTER (WHERE is_main IS NOT FALSE) AS n,
               (array_agg(main_use ORDER BY is_main IS NOT FALSE DESC, total_area DESC NULLS LAST))[1] AS main_use,
               min(approval_date) AS approval, max(floors_above) AS floors, sum(parking_count) AS parking,
               sum(households) FILTER (WHERE is_main IS NOT FALSE) AS households, max(site_area) AS site_area,
               max(bc_rat) AS bc_rat, max(vl_rat) AS vl_rat, string_agg(DISTINCT source, ' · ') AS src,
               -- 승강기는 주건축물만 봄. 한 동이라도 모름(대장에 0대로 적힌 6층 이상)이면 「없음」이라 하지 않음
               CASE WHEN bool_or(has_elevator) FILTER (WHERE is_main IS NOT FALSE) THEN true
                    WHEN count(*) FILTER (WHERE is_main IS NOT FALSE AND has_elevator IS NULL) > 0 THEN NULL
                    ELSE bool_or(has_elevator) FILTER (WHERE is_main IS NOT FALSE) END AS elevator,
               bool_or(is_violation) AS violation, max(asof) AS asof
        FROM cb GROUP BY 1
    )
    SELECT c.name,
           concat_ws(' ', c.sido, c.sgg_name, c.emd, c.jibun),
           c.road_addr, c.pnu, c.build_year::int,
           types.a,
           coalesce(t.n, 0), t.per_py, t.last_date, t.last_amt, t.last_area,
           coalesce(j.jn, 0), j.j_avg, ratio.r, ratio.area_m2, coalesce(j.wn, 0),
           coalesce((SELECT jsonb_agg(e) FROM (SELECT e FROM jsonb_array_elements(t.deals) e LIMIT 5) s), '[]'::jsonb),
           land.n, land.cat, coalesce(land.area, c.site_area_m2, bld.site_area),
           CASE WHEN land.area IS NOT NULL THEN '토지대장 면적 합'
                WHEN coalesce(c.site_area_m2, bld.site_area) IS NOT NULL THEN '건축물대장 대지면적' END,
           price.price, price.yr, zone.zones, zone.rules,
           bld.n, bld.main_use, bld.approval, bld.floors, coalesce(c.households, bld.households),
           -- 동별 주차 합이 0이면 「0대」가 아니라 대장에 안 적힌 것(은마 31동 모두 0, 2026-10-27 실측)으로 보고 비움
           coalesce(c.parking_total, nullif(bld.parking, 0)), bld.elevator, bld.violation,
           coalesce(c.bc_rat, bld.bc_rat), coalesce(c.vl_rat, bld.vl_rat),
           -- 토지 칸은 지목(토지대장)과 면적을 따로 셈: 건축물대장 대지면적만 있어도 면적은 채움
           array_remove(ARRAY[
               CASE WHEN c.pnu IS NULL THEN '지번(PNU)' END,
               CASE WHEN land.cat IS NULL THEN '토지 지목' END,
               CASE WHEN coalesce(land.area, c.site_area_m2, bld.site_area) IS NULL THEN '토지 면적' END,
               CASE WHEN price.price IS NULL THEN '공시지가' END,
               CASE WHEN zone.zones IS NULL THEN '용도지역 · 규제' END,
               CASE WHEN bld.n IS NULL THEN '건축물대장(용도 · 층수 · 주차 · 위반)' END], NULL),
           win.d_from, win.d_to,
           concat_ws(' · ', '국토교통부 ' || c.kind || ' 매매 · 전월세 실거래가', land.src,
                     CASE WHEN price.price IS NOT NULL THEN '공시지가' END, zone.src, bld.src),
           greatest(t.asof, j.asof, c.asof, land.asof, price.asof, zone.asof, bld.asof),
           c.n_match
    FROM c LEFT JOIN t USING (complex_id) LEFT JOIN j USING (complex_id) LEFT JOIN ratio USING (complex_id)
         LEFT JOIN types USING (complex_id) LEFT JOIN land USING (complex_id) LEFT JOIN price USING (complex_id)
         LEFT JOIN zone USING (complex_id) LEFT JOIN bld USING (complex_id), win
    ORDER BY coalesce(t.n, 0) + coalesce(j.jn, 0) DESC, c.name
$$;
ALTER FUNCTION mart.q_property_card(text, text, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_property_card(text, text, int) SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_property_card(text, text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_property_card(text, text, int) TO hanpilji_gpt;
