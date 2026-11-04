-- 서울 개별공시지가: 서울 열린데이터광장 「서울시 개별공시지가 정보」(OA-1180) 해마다 파일 하나 → core.fact_land_price
-- 수집기는 hanpilji/collectors/seoul_land_price.py(처음 돌 때 아래 줄이 없으면 스스로 넣음)
-- 이용허락: 공공누리 1유형(출처표시, 상업적 이용 · 변경 가능)
INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('seoul_land_price', '서울시 개별공시지가 정보', '서울특별시(부동산정보광장)', 'file', NULL, 'CP949', '매년',
  '공공누리 1유형(출처표시)', 'https://data.seoul.go.kr/dataList/OA-1180/F/1/datasetView.do')
ON CONFLICT (source_id) DO NOTHING;
