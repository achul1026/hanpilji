-- 4단계 데모 채우기: 건축HUB 건축물대장 API → 건물 · 용도지역 · 대지면적
-- 토지 지목 · 공시지가는 공공데이터포털 API가 폐기되고 V-World로만 제공되어 여기서는 받지 않음(2026-10-27 확인)

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('dg_bld_hub', '건축HUB 건축물대장정보 서비스(표제부 · 총괄표제부 · 지역지구 · 부속지번)', '국토교통부', 'api', 10000,
  'UTF-8', '매월', '이용허락범위 제한 없음', 'https://www.data.go.kr/data/15134735/openapi.do')
ON CONFLICT (source_id) DO NOTHING;

-- 필지(PNU) 하나 x 기능 하나의 응답 item 전부. 원본 모양 그대로(jsonb 배열)
CREATE TABLE IF NOT EXISTS stg.bld_raw (
    run_id         bigint NOT NULL REFERENCES ops.ingest_run,
    pnu            varchar(25) NOT NULL,
    op             text NOT NULL,                 -- getBrTitleInfo · getBrRecapTitleInfo · getBrJijiguInfo · getBrAtchJibunInfo
    row_data       jsonb NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pnu, op)
);
