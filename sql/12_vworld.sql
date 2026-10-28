-- 4단계 데모 채우기: V-World 국가중점데이터 API → 지목 · 토지 면적 · 개별공시지가 · 토지특성 · 토지이용계획
-- 공공데이터포털의 토지 API가 폐기되어 V-World로만 받음(2026-10-27 확인). 키는 환경변수 VWORLD_KEY

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('vworld_ned', 'V-World 국가중점데이터(토지특성 · 토지이용계획)', '국토교통부 공간정보정책관', 'api', NULL,
  'UTF-8', '매년(공시지가) · 수시(토지이용계획)', '출처 표시(개발키는 개발 · 시험용, 서비스 공개 전 운영키 필요)',
  'https://www.vworld.kr/dev/v4dv_2ddataguide2_s001.do')
ON CONFLICT (source_id) DO NOTHING;

-- 필지(PNU) 하나 x 기능 하나의 응답 줄 전부. 원본 모양 그대로(jsonb 배열)
CREATE TABLE IF NOT EXISTS stg.vworld_raw (
    pnu            varchar(25) NOT NULL,
    op             text NOT NULL,                 -- getLandCharacteristics · getLandUseAttr
    row_data       jsonb NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pnu, op)
);
