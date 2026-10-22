-- 선택: PostGIS가 깔린 서버에서만 실행. 없어도 나머지는 모두 동작함
CREATE EXTENSION IF NOT EXISTS postgis;

ALTER TABLE core.dim_parcel   ADD COLUMN IF NOT EXISTS geom geometry(MultiPolygon, 5186);
ALTER TABLE core.dim_building ADD COLUMN IF NOT EXISTS geom geometry(MultiPolygon, 5186);
CREATE INDEX IF NOT EXISTS dim_parcel_geom_idx   ON core.dim_parcel   USING gist (geom);
CREATE INDEX IF NOT EXISTS dim_building_geom_idx ON core.dim_building USING gist (geom);
-- 5186 = Korea 2000 중부원점. V-World SHP 좌표계를 받은 뒤 맞으면 유지, 다르면 바꿈(미확인)
