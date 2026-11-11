-- 주변 정보: 학교 · 지하철역 위치(공공데이터포털 표준데이터, 인증키 없이 받음) → core.ref_poi
--   전국초중등학교위치표준데이터(15021148) · 전국도시철도역사정보표준데이터(15013205)
-- 수집기는 hanpilji/collectors/poi.py. 서울과 그 둘레(경위도 범위)만 넣음
-- 단지 위치는 지적도(core.geo_parcel)에서 잡으므로 PostGIS가 있어야 함(init_db.sh --postgis, 15_geo.sql 뒤)
SET search_path = public, extensions;

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('poi_school', '전국초중등학교위치표준데이터', '교육부 · 한국교육시설안전원', 'file', NULL, 'UTF-8', '분기',
  '이용허락범위 제한 없음', 'https://www.data.go.kr/data/15021148/standard.do'),
 ('poi_station', '전국도시철도역사정보표준데이터', '국토교통부 · 각 도시철도 운영기관', 'file', NULL, 'UTF-8', '수시',
  '이용허락범위 제한 없음', 'https://www.data.go.kr/data/15013205/standard.do')
ON CONFLICT (source_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS core.ref_poi (
    poi_id         text PRIMARY KEY,              -- school:학교ID · station:역사명:노선 묶음 위치
    kind           text NOT NULL,                 -- 초등학교 · 중학교 · 고등학교 · 특수학교 · 지하철역 …
    name           text NOT NULL,
    detail         text,                          -- 학교: 설립형태 · 본교/분교, 역: 노선(환승이면 여러 개)
    address        text,
    lon            numeric(10, 7) NOT NULL,
    lat            numeric(10, 7) NOT NULL,
    source         text NOT NULL,
    asof           date NOT NULL                  -- 원본의 데이터기준일자
);
CREATE INDEX IF NOT EXISTS ref_poi_latlon_idx ON core.ref_poi (lat, lon);

-- ── 단지 둘레 학교 · 지하철역 ──
-- 단지 위치 = 단지 필지(지적도) 안의 한 점. 지적도가 아직 없는 단지는 0줄
-- 거리는 직선거리(m), 도보 분은 분속 67m(시속 4km)로 셈한 어림값. 종류마다 가까운 5곳, 최대 25줄
DROP FUNCTION IF EXISTS mart.q_nearby(text, text, int);
CREATE FUNCTION mart.q_nearby(p_sigungu text, p_query text, p_radius_m int DEFAULT 1000)
RETURNS TABLE (complex_name text, kind text, place_name text, detail text, distance_m int, walk_min int,
               address text, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH cx AS (
        SELECT f.complex_id, f.name FROM core.find_complex(p_sigungu, p_query) f LIMIT 1
    ), pt AS (
        SELECT ST_Transform(ST_PointOnSurface(ST_Union(g.geom)), 4326) AS p
        FROM core.geo_parcel g JOIN core.v_complex_parcel v USING (pnu) JOIN cx USING (complex_id)
    ), r AS (
        SELECT least(greatest(coalesce(p_radius_m, 1000), 200), 3000) AS m
    ), hit AS (
        SELECT o.*, round(ST_Distance(ST_SetSRID(ST_MakePoint(o.lon, o.lat), 4326)::geography, pt.p::geography))::int AS d
        FROM core.ref_poi o, pt, r
        WHERE pt.p IS NOT NULL
          AND o.lat BETWEEN ST_Y(pt.p) - r.m / 110000.0 AND ST_Y(pt.p) + r.m / 110000.0
          AND o.lon BETWEEN ST_X(pt.p) - r.m / 88000.0 AND ST_X(pt.p) + r.m / 88000.0
    ), ranked AS (
        SELECT hit.*, row_number() OVER (PARTITION BY hit.kind ORDER BY hit.d, hit.poi_id) AS k
        FROM hit, r WHERE hit.d <= r.m
    )
    SELECT (SELECT cx.name FROM cx), x.kind, x.name, x.detail, x.d, ceil(x.d / 67.0)::int,
           x.address, x.source, x.asof, count(*) OVER ()
    FROM ranked x
    WHERE x.k <= 5
    ORDER BY CASE x.kind WHEN '지하철역' THEN 1 WHEN '초등학교' THEN 2 WHEN '중학교' THEN 3 WHEN '고등학교' THEN 4 ELSE 5 END,
             x.d, x.poi_id
    LIMIT 25
$$;

ALTER FUNCTION mart.q_nearby(text, text, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_nearby(text, text, int) SET search_path = pg_catalog, public, extensions;
REVOKE EXECUTE ON FUNCTION mart.q_nearby(text, text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_nearby(text, text, int) TO hanpilji_gpt;
