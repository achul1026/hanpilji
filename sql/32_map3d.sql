-- 답 카드 아래 「3D 지도 · 주변」(2026-11-27): 단지 하나 둘레 사방 300m 안팎의 필지 · 건물 · 도로 · 역 · 학교 · 주변 단지를
-- JSON 하나로 돌려줌. 화면(static/map3d.js)이 건물을 층수만큼 세워 도면식 3D로 그림. 모델에게는 넘기지 않음(화면 전용)
-- 가로수는 화면이 도로 포장면 가장자리를 따라 그림(실제 위치 아님)
-- 좌표는 EPSG:5186(m)에서 대상 필지 가운데를 0,0으로 옮긴 값(x 동쪽, y 북쪽, 소수 1자리 ≈ 10cm)
-- PostGIS가 있는 DB에만(init_db.sh --postgis, 15_geo.sql · 22_poi.sql 뒤)
SET search_path = public, extensions;

DROP FUNCTION IF EXISTS mart.q_map3d(text, text, int);
CREATE FUNCTION mart.q_map3d(p_sigungu text, p_query text, p_half_m int DEFAULT 300)
RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN (
    WITH cx AS (
        SELECT f.complex_id, f.name FROM core.find_complex(p_sigungu, p_query) f
        WHERE p_query !~ '^[0-9]{19}$' LIMIT 1
    ), tp AS (   -- 대상 필지(단지 필지 또는 PNU 하나)
        SELECT g.pnu, g.geom FROM core.geo_parcel g
        WHERE left(g.pnu, 5) = p_sigungu
          AND (g.pnu = p_query OR g.pnu IN (SELECT v.pnu FROM core.v_complex_parcel v JOIN cx USING (complex_id)))
    ), c AS (
        SELECT ST_PointOnSurface(ST_Union(geom)) AS p FROM tp
    ), o AS (
        SELECT ST_X(c.p) AS x, ST_Y(c.p) AS y, least(greatest(coalesce(p_half_m, 300), 150), 500) AS h,
               ST_Transform(c.p, 4326) AS ll
        FROM c WHERE c.p IS NOT NULL
    ), bx AS (
        SELECT ST_MakeEnvelope(o.x - o.h, o.y - o.h, o.x + o.h, o.y + o.h, 5186) AS b FROM o
    ), pa AS (   -- 필지(상자에 맞춰 자름)
        SELECT g.pnu, g.jibun, g.jimok, ST_Intersection(g.geom, bx.b) AS geom
        FROM core.geo_parcel g, bx WHERE g.geom && bx.b AND ST_Intersects(g.geom, bx.b)
    ), bu AS (   -- 건물(가운데가 상자 안). 용도는 같은 필지 건축물대장 표제부 가운데 가장 높은 것
        SELECT b.bld_id, b.pnu, b.name, b.floors_above, b.geom,
               (SELECT d.main_use FROM core.dim_building d WHERE d.pnu = b.pnu AND d.main_use IS NOT NULL
                ORDER BY d.floors_above DESC NULLS LAST LIMIT 1) AS use
        FROM core.geo_building b, bx WHERE b.geom && bx.b AND ST_Contains(bx.b, ST_PointOnSurface(b.geom))
        LIMIT 3000
    ), rd AS (   -- 도로 포장면: 지목이 도로인 필지를 합쳐 3m 안쪽으로 줄임(보도 몫)
        SELECT ST_Intersection(ST_Buffer(ST_Union(geom), -3, 'join=mitre'), (SELECT b FROM bx)) AS geom
        FROM pa WHERE jimok = '도'
    ), po AS (   -- 지하철역 · 학교
        SELECT r.kind, r.name, r.detail, ST_Transform(ST_SetSRID(ST_MakePoint(r.lon, r.lat), 4326), 5186) AS p
        FROM core.ref_poi r, o
        WHERE r.lat BETWEEN ST_Y(o.ll) - (o.h + 100) / 110000.0 AND ST_Y(o.ll) + (o.h + 100) / 110000.0
          AND r.lon BETWEEN ST_X(o.ll) - (o.h + 100) / 88000.0 AND ST_X(o.ll) + (o.h + 100) / 88000.0
    ), nc AS (   -- 상자 안 단지와 최근 12개월 매매(취소 뺌)
        SELECT x.complex_id, x.pnu, x.name, x.kind, x.households, ST_PointOnSurface(g.geom) AS p,
               (x.complex_id IN (SELECT complex_id FROM cx)) AS is_target
        FROM core.dim_complex x JOIN core.geo_parcel g ON g.pnu = x.pnu, bx
        WHERE g.geom && bx.b AND ST_Intersects(g.geom, bx.b)
    ), nd AS (
        SELECT d.complex_id, count(*) AS n, max(d.contract_date) AS last_date,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d.amount / d.area * 3.3058) AS med_py
        FROM core.fact_deal d JOIN nc USING (complex_id)
        WHERE NOT d.is_cancelled AND d.contract_date >= current_date - interval '12 months'
        GROUP BY d.complex_id
    ), by_ AS (
        SELECT d.complex_id, min(d.build_year) AS build_year FROM core.fact_deal d JOIN nc USING (complex_id)
        WHERE d.build_year IS NOT NULL GROUP BY d.complex_id
    )
    SELECT CASE WHEN o.x IS NULL THEN NULL ELSE jsonb_build_object(
        'name', (SELECT name FROM cx), 'query', p_query, 'half', o.h,
        'lon', round(ST_X(o.ll)::numeric, 6), 'lat', round(ST_Y(o.ll)::numeric, 6),
        'target', (SELECT jsonb_agg(pnu ORDER BY pnu) FROM tp),
        'parcels', (SELECT jsonb_agg(jsonb_build_object('p', pnu, 'j', jibun, 'k', jimok,
                        'g', ST_AsGeoJSON(ST_Translate(ST_SimplifyPreserveTopology(geom, 0.2), -o.x, -o.y), 1)::jsonb -> 'coordinates',
                        't', ST_GeometryType(geom) = 'ST_MultiPolygon'))
                    FROM pa WHERE NOT ST_IsEmpty(geom) AND ST_Dimension(geom) = 2),
        'buildings', (SELECT jsonb_agg(jsonb_build_object('n', coalesce(name, ''), 'f', coalesce(floors_above, 0), 'p', pnu, 'u', use,
                        'g', ST_AsGeoJSON(ST_Translate(ST_SimplifyPreserveTopology(geom, 0.2), -o.x, -o.y), 1)::jsonb -> 'coordinates',
                        'i', CASE WHEN coalesce(floors_above, 0) >= 4 THEN
                                ST_AsGeoJSON(ST_Translate(ST_SimplifyPreserveTopology(ST_Buffer(geom, -0.45, 'join=mitre'), 0.3), -o.x, -o.y), 1)::jsonb -> 'coordinates' END)
                        ORDER BY bld_id) FROM bu),
        'roads', (SELECT ST_AsGeoJSON(ST_Translate(ST_SimplifyPreserveTopology(geom, 0.4), -o.x, -o.y), 1)::jsonb FROM rd
                  WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom)),
        'pois', (SELECT jsonb_agg(jsonb_build_object('k', kind, 'n', name, 'd', detail,
                        'x', round((ST_X(p) - o.x)::numeric, 1), 'y', round((ST_Y(p) - o.y)::numeric, 1),
                        'm', round(ST_Distance(p, ST_SetSRID(ST_MakePoint(o.x, o.y), 5186)))::int)
                        ORDER BY ST_Distance(p, ST_SetSRID(ST_MakePoint(o.x, o.y), 5186))) FROM po),
        'complexes', (SELECT jsonb_agg(jsonb_build_object('id', nc.complex_id, 'p', nc.pnu, 'n', nc.name, 'k', nc.kind, 'hh', nc.households,
                        'y', by_.build_year, 'nd', coalesce(nd.n, 0), 'py', round(nd.med_py), 'ld', nd.last_date, 't', nc.is_target,
                        'x', round((ST_X(nc.p) - o.x)::numeric, 1), 'yy', round((ST_Y(nc.p) - o.y)::numeric, 1),
                        'm', round(ST_Distance(nc.p, ST_SetSRID(ST_MakePoint(o.x, o.y), 5186)))::int)
                        ORDER BY nc.is_target DESC, coalesce(nd.n, 0) DESC)
                      FROM nc LEFT JOIN nd USING (complex_id) LEFT JOIN by_ USING (complex_id)),
        'asof', (SELECT max(asof) FROM core.geo_building),
        'sources', jsonb_build_array('V-World 연속지적도 · 도로명주소 건물', '건축HUB 건축물대장', '국토교통부 실거래가', '공공데이터포털 학교 · 역 위치')
    ) END
    FROM (SELECT 1) one LEFT JOIN o ON true
    );
END
$$;

ALTER FUNCTION mart.q_map3d(text, text, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_map3d(text, text, int) SET search_path = pg_catalog, public, extensions;
REVOKE EXECUTE ON FUNCTION mart.q_map3d(text, text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_map3d(text, text, int) TO hanpilji_gpt;
