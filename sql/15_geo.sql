-- 지적도(연속지적도 필지 경계) · 건물 윤곽 → 웹 지도 블록. PostGIS가 있어야 함(init_db.sh --postgis)
-- V-World 2D 데이터 API는 해외 IP를 막아 국내 PC에서 받음: scripts/vworld_fetch_geom_gangnam.py → zip
--   → python -m hanpilji.collectors.vworld_geom --from-zip vworld_geom_11680.zip
-- 연속지적도는 지적도 · 임야도를 이어 붙인 참고용 도면이라 측량 · 경계 확정에 쓰는 법적 도면이 아님
-- Supabase는 PostGIS가 extensions 스키마에 있어 그 스키마도 찾게 함(로컬에는 없어도 됨)
SET search_path = public, extensions;
CREATE EXTENSION IF NOT EXISTS postgis;

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('vworld_geom', 'V-World 2D 데이터(연속지적도 · 도로명주소 건물)', '국토교통부 공간정보정책관', 'api', NULL,
  'UTF-8', '일 단위(연속지적도) · 수시(건물)', '출처 표시(개발키는 개발 · 시험용, 서비스 공개 전 운영키 필요)',
  'https://www.vworld.kr/dev/v4dv_2ddataguide2_s001.do')
ON CONFLICT (source_id) DO NOTHING;

-- 필지 경계. 한필지 필지(core.dim_parcel)만이 아니라 받은 구역의 이웃 필지도 둠(지도에 둘레 선을 그리려고)
CREATE TABLE IF NOT EXISTS core.geo_parcel (
    pnu            varchar(25) PRIMARY KEY,
    jibun          text,                          -- 번지(예: 527, 산12-3)
    jimok          text,                          -- 지목 한 글자(V-World jibun 끝 글자. 예: 대 · 도 · 임)
    geom           geometry(MultiPolygon, 5186) NOT NULL,   -- Korea 2000 중부원점(m). 받을 때는 경위도
    source         text NOT NULL,
    asof           date NOT NULL,
    CHECK (pnu ~ '^[0-9]{19,}$')
);
CREATE INDEX IF NOT EXISTS geo_parcel_geom_idx ON core.geo_parcel USING gist (geom);

-- 건물 윤곽(도로명주소 건물). 건축HUB 건물(core.dim_building)과는 번호 체계가 달라 따로 두고 필지로 이음
CREATE TABLE IF NOT EXISTS core.geo_building (
    bld_id         text PRIMARY KEY,              -- 도로명주소 건물관리번호(bd_mgt_sn). 없으면 V-World 도형 id
    pnu            varchar(25),                   -- 가장 많이 겹치는 필지(geo_parcel)
    name           text,                          -- 건물명(예: 도곡렉슬 101동)
    floors_above   integer,
    floors_below   integer,
    props          jsonb NOT NULL DEFAULT '{}',  -- 받은 속성 전부
    geom           geometry(MultiPolygon, 5186) NOT NULL,
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE INDEX IF NOT EXISTS geo_building_geom_idx ON core.geo_building USING gist (geom);
CREATE INDEX IF NOT EXISTS geo_building_pnu_idx ON core.geo_building (pnu);

-- ── 지도: 단지명 · 「동 지번」 · PNU 하나로 대상 필지 경계, 둘레 이웃 필지, 건물 윤곽 ──
-- 줄마다 도형 하나(geojson: 경위도 GeoJSON, 소수 6자리 ≈ 10cm). 대상 필지가 먼저.
-- row_total은 대상 필지 수. screen_only 줄(이웃 필지 · 건물)은 화면 지도용이라 모델에게는 넘기지 않음
DROP FUNCTION IF EXISTS mart.q_parcel_map(text, text, int);
CREATE FUNCTION mart.q_parcel_map(p_sigungu text, p_query text, p_buffer_m int DEFAULT 60)
RETURNS TABLE (layer text, pnu varchar, label text, jibun text, jimok text, area_m2 numeric,
               floors_above int, lon numeric, lat numeric, geojson text, screen_only boolean,
               source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH cx AS (
        SELECT f.complex_id, f.name FROM core.find_complex(p_sigungu, p_query) f
        WHERE p_query !~ '^[0-9]{19}$' LIMIT 1
    ), tp AS (
        SELECT g.*, coalesce((SELECT name FROM cx), concat_ws(' ', r.emd, g.jibun)) AS lbl
        FROM core.geo_parcel g LEFT JOIN core.dim_region r ON r.lawd_cd = left(g.pnu, 10)
        WHERE left(g.pnu, 5) = p_sigungu
          AND (g.pnu = p_query OR g.pnu IN (SELECT v.pnu FROM core.v_complex_parcel v JOIN cx USING (complex_id)))
    ), area AS (
        SELECT ST_Buffer(ST_Union(geom), greatest(0, least(coalesce(p_buffer_m, 60), 200))) AS b FROM tp
    ), nb AS (
        SELECT g.* FROM core.geo_parcel g, area
        WHERE area.b IS NOT NULL AND g.geom && area.b AND ST_Intersects(g.geom, area.b)
          AND g.pnu NOT IN (SELECT tp.pnu FROM tp)
        ORDER BY ST_Distance(g.geom, (SELECT ST_Union(geom) FROM tp)), g.pnu LIMIT 250
    ), bd AS (
        SELECT b.* FROM core.geo_building b, area
        WHERE area.b IS NOT NULL AND b.geom && area.b AND ST_Intersects(b.geom, area.b)
        ORDER BY b.bld_id LIMIT 250
    ), n AS (SELECT count(*) AS n FROM tp), rows AS (
        SELECT 1 AS o, '대상 필지' AS layer, tp.pnu, tp.lbl AS label, tp.jibun, tp.jimok, tp.geom,
               NULL::int AS floors_above, false AS screen_only, tp.source, tp.asof FROM tp
        UNION ALL
        SELECT 2, '이웃 필지', nb.pnu, NULL, nb.jibun, nb.jimok, nb.geom, NULL, true, nb.source, nb.asof FROM nb
        UNION ALL
        SELECT 3, '건물', bd.pnu, bd.name, NULL, NULL, bd.geom, bd.floors_above, true, bd.source, bd.asof FROM bd
    )
    SELECT r.layer, r.pnu, r.label, r.jibun, r.jimok, round(ST_Area(r.geom)::numeric, 1), r.floors_above,
           round(ST_X(c)::numeric, 6), round(ST_Y(c)::numeric, 6),
           ST_AsGeoJSON(ST_Transform(r.geom, 4326), 6), r.screen_only, r.source, r.asof, n.n
    FROM rows r CROSS JOIN n
    CROSS JOIN LATERAL ST_Transform(ST_PointOnSurface(r.geom), 4326) AS c
    ORDER BY r.o, r.pnu
$$;

ALTER FUNCTION mart.q_parcel_map(text, text, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_parcel_map(text, text, int) SET search_path = pg_catalog, public, extensions;
REVOKE EXECUTE ON FUNCTION mart.q_parcel_map(text, text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_parcel_map(text, text, int) TO hanpilji_gpt;
