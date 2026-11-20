-- 상권 · 행정동 경계(서울시 상권분석서비스 영역-상권 OA-15560 · 영역-행정동 OA-22160 SHP 파일, 인증키 없이 받음)
-- PostGIS가 있는 DB에만(init_db.sh --postgis, 15_geo.sql · 30_trade_area.sql 뒤). 원본은 EPSG:5181, 지적도와 같은 5186으로 넣음
-- 수집기: hanpilji/collectors/seoul_trdar.py (--only relm)
SET search_path = public, extensions;

CREATE TABLE IF NOT EXISTS core.geo_trdar (
    trdar_cd       text PRIMARY KEY,
    geom           geometry(MultiPolygon, 5186) NOT NULL,
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE INDEX IF NOT EXISTS geo_trdar_geom_idx ON core.geo_trdar USING gist (geom);

CREATE TABLE IF NOT EXISTS core.geo_adm_dong (
    adm_cd         char(8) PRIMARY KEY,
    geom           geometry(MultiPolygon, 5186) NOT NULL,
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE INDEX IF NOT EXISTS geo_adm_dong_geom_idx ON core.geo_adm_dong USING gist (geom);

-- 필지 하나가 든 상권 · 행정동(필지 안 한 점 기준). 상권 밖 필지는 상권 칸이 빔. 지적도가 없는 필지는 0줄
DROP FUNCTION IF EXISTS mart.q_area_of_pnu(text);
CREATE FUNCTION mart.q_area_of_pnu(p_pnu text)
RETURNS TABLE (pnu text, trdar_cd text, trdar_nm text, trdar_se_nm text, adm_cd text, adm_nm text)
LANGUAGE sql STABLE AS $$
    WITH pt AS (SELECT ST_PointOnSurface(g.geom) AS p FROM core.geo_parcel g WHERE g.pnu = p_pnu LIMIT 1)
    SELECT p_pnu, t.trdar_cd, t.trdar_nm, t.trdar_se_nm, d.adm_cd::text, d.adm_nm
    FROM pt
    LEFT JOIN LATERAL (SELECT gt.trdar_cd FROM core.geo_trdar gt WHERE ST_Contains(gt.geom, pt.p)
                       ORDER BY ST_Area(gt.geom) LIMIT 1) gt ON true
    LEFT JOIN core.dim_trdar t ON t.trdar_cd = gt.trdar_cd
    LEFT JOIN LATERAL (SELECT ga.adm_cd FROM core.geo_adm_dong ga WHERE ST_Contains(ga.geom, pt.p) LIMIT 1) ga ON true
    LEFT JOIN core.dim_adm_dong d ON d.adm_cd = ga.adm_cd
$$;

ALTER FUNCTION mart.q_area_of_pnu(text) SECURITY DEFINER;
ALTER FUNCTION mart.q_area_of_pnu(text) SET search_path = pg_catalog, public, extensions;
REVOKE EXECUTE ON FUNCTION mart.q_area_of_pnu(text) FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hanpilji_gpt') THEN
        GRANT EXECUTE ON FUNCTION mart.q_area_of_pnu(text) TO hanpilji_gpt;
    END IF;
END $$;
