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

-- 단지 카드(mart.q_property_card)는 18_property_card.sql에 있음(16_policy.sql의 토지거래허가구역 표를 읽어서 그 뒤에 만듦)
