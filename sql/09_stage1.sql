-- 1~2단계: 공공데이터포털 API(인증키 필요) — 행위제한 API · 아파트 · 오피스텔 · 연립다세대 매매 · 전월세 실거래가

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('dg_luinfo',     '토지이용규제정보서비스(행위제한)',  '국토교통부', 'api', 1000,  'EUC-KR', '매월',  '이용허락범위 제한 없음', 'https://www.data.go.kr/data/15058410/openapi.do'),
 ('dg_apt_trade',  '아파트 매매 실거래가 공개 자료',     '국토교통부', 'api', 10000, 'UTF-8', '매일',  '이용허락범위 제한 없음', 'https://www.data.go.kr/data/15126469/openapi.do'),
 ('dg_apt_rent',   '아파트 전월세 실거래가 공개 자료',   '국토교통부', 'api', 10000, 'UTF-8', '매일',  '이용허락범위 제한 없음', 'https://www.data.go.kr/data/15126474/openapi.do'),
 ('dg_offi_trade', '오피스텔 매매 실거래가 자료',        '국토교통부', 'api', 10000, 'UTF-8', '매일',  '이용허락범위 제한 없음', 'https://www.data.go.kr/data/15126464/openapi.do'),
 ('dg_offi_rent',  '오피스텔 전월세 실거래가 자료',      '국토교통부', 'api', 10000, 'UTF-8', '매일',  '이용허락범위 제한 없음', 'https://www.data.go.kr/data/15126475/openapi.do'),
 ('dg_rh_trade',   '연립다세대 매매 실거래가 자료',      '국토교통부', 'api', 10000, 'UTF-8', '매일',  '이용허락범위 제한 없음', 'https://www.data.go.kr/data/15126467/openapi.do'),
 ('dg_rh_rent',    '연립다세대 전월세 실거래가 자료',    '국토교통부', 'api', 10000, 'UTF-8', '매일',  '이용허락범위 제한 없음', 'https://www.data.go.kr/data/15126473/openapi.do')
ON CONFLICT (source_id) DO UPDATE SET encoding = excluded.encoding;

-- 실거래가 응답 한 건 = 한 줄. 원본 모양 그대로(jsonb) 받고 core로 옮길 때 풂
CREATE TABLE IF NOT EXISTS stg.rtms_raw (
    run_id         bigint NOT NULL REFERENCES ops.ingest_run,
    source_id      text NOT NULL REFERENCES ops.ingest_source,
    sigungu_cd     char(5) NOT NULL,
    deal_ym        char(6) NOT NULL,
    row_key        text NOT NULL,                 -- core의 deal_id · rent_id와 같은 값
    row_data       jsonb NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, row_key)
);
CREATE INDEX IF NOT EXISTS rtms_raw_month_idx ON stg.rtms_raw (source_id, sigungu_cd, deal_ym);

-- 행위제한 API 응답 한 건(지역지구 x 행위). 조건 · 예외 문구까지 통째로
CREATE TABLE IF NOT EXISTS stg.luinfo_raw (
    run_id         bigint NOT NULL REFERENCES ops.ingest_run,
    sigungu_cd     char(5) NOT NULL,
    ucode          text NOT NULL,
    act_query      text NOT NULL,                 -- 물어본 행위 이름
    row_data       jsonb NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sigungu_cd, ucode, act_query)
);

-- 토지이용계획 지역지구 코드(UQ…) 중 용도지역 16개. 행위제한 API의 ucodeList에 넣는 값
-- 코드값 16개 모두 행위제한 API 응답의 UNAME으로 확인함(강남구, 2026-10-27)
INSERT INTO core.dim_zone (zone_cd, code_system, zone_name, law_basis, source, asof) VALUES
 ('UQA111','uq','제1종전용주거지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA112','uq','제2종전용주거지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA121','uq','제1종일반주거지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA122','uq','제2종일반주거지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA123','uq','제3종일반주거지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA130','uq','준주거지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA210','uq','중심상업지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA220','uq','일반상업지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA230','uq','근린상업지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA240','uq','유통상업지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA310','uq','전용공업지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA320','uq','일반공업지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA330','uq','준공업지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA410','uq','보전녹지지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA420','uq','생산녹지지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27'),
 ('UQA430','uq','자연녹지지역','국토의 계획 및 이용에 관한 법률','토지이용규제 지역지구 코드(API로 확인)','2026-10-27')
ON CONFLICT (zone_cd) DO UPDATE SET source = excluded.source, asof = excluded.asof;
