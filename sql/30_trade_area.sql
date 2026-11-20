-- 상권 · 점포 · 유동인구 · 매출(서울). 모두 무료 공공데이터, 이용허락범위 제한 없음(2026-11-20 각 데이터 화면에서 확인)
--   core.ref_store        : 소상공인시장진흥공단 상가(상권)정보, 서울 점포 한 곳 한 줄(업종 · 주소 · 필지 PNU · 층 · 경위도). 분기 파일
--   core.dim_trdar        : 서울시 상권분석서비스 상권 1,650곳(골목 · 발달 · 전통시장 · 관광특구), 중심점 · 면적 · 구 · 행정동
--   core.dim_adm_dong     : 서울 행정동 425곳(상권분석서비스 영역-행정동), 중심점 · 면적
--   core.fact_trdar_stat  : 상권분석서비스 분기 지표(추정매출 · 점포 · 길단위인구 · 상주 · 직장인구 · 집객시설 · 소비 · 아파트 ·
--                           상권변화지표), 상권 또는 행정동 x 분기(x 업종) 한 줄, 지표 값은 metrics(jsonb, 원본 Open API 항목 이름)
--   core.fact_living_pop  : 서울 생활인구(서울시 · KT) 행정동 x 날짜 x 시간대(0~23시) 총생활인구와 성 · 연령 28칸
-- 수집기: hanpilji/collectors/sbiz_store.py · seoul_trdar.py · seoul_living_pop.py
-- 상권 · 행정동 경계(지도)는 PostGIS가 있는 DB에만: sql/31_trade_area_geo.sql
-- 추정매출은 카드 결제를 바탕으로 한 「추정치」이고 상권 · 행정동 합계라 매장 한 곳의 매출이 아님(화면에 그렇게 적을 것)

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('sbiz_store', '소상공인시장진흥공단 상가(상권)정보', '소상공인시장진흥공단', 'file', NULL, 'UTF-8', '분기',
  '이용허락범위 제한 없음', 'https://www.data.go.kr/data/15083033/fileData.do'),
 ('seoul_trdar', '서울시 상권분석서비스', '서울신용보증재단(서울 열린데이터광장)', 'api', NULL, 'UTF-8', '분기',
  '이용허락범위 제한 없음', 'https://data.seoul.go.kr/dataList/OA-15572/S/1/datasetView.do'),
 ('seoul_living_pop', '서울 생활인구(내국인, 행정동)', '서울특별시 · KT(서울 열린데이터광장)', 'api', NULL, 'UTF-8', '일',
  '이용허락범위 제한 없음', 'https://data.seoul.go.kr/dataList/OA-14991/S/1/datasetView.do')
ON CONFLICT (source_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS core.ref_store (
    store_id       text PRIMARY KEY,              -- 상가업소번호
    name           text NOT NULL,                 -- 상호명
    branch         text,                          -- 지점명
    ind_l_cd       text, ind_l_nm text,           -- 상권업종 대분류(10)
    ind_m_cd       text, ind_m_nm text,           -- 중분류(75)
    ind_s_cd       text, ind_s_nm text,           -- 소분류(247)
    ksic_cd        text, ksic_nm text,            -- 표준산업분류
    sigungu_cd     char(5),
    adm_cd         char(8),                       -- 행정동코드(상권분석서비스 · 생활인구와 같은 8자리)
    adm_nm         text,
    lawd_cd        char(10),                      -- 법정동코드
    pnu            varchar(19),                   -- 지번코드(19자리) = 필지 PNU
    jibun_addr     text,
    road_addr      text,
    bld_mgt_no     text,                          -- 건물관리번호
    bld_nm         text,
    floor          text,                          -- 층정보(원문 그대로: 1 · B1 · 2 …)
    unit           text,                          -- 호정보
    lon            numeric(10, 7),
    lat            numeric(10, 7),
    base_ym        char(6) NOT NULL,              -- 파일 기준 연월(예: 202609)
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE INDEX IF NOT EXISTS ref_store_pnu_idx ON core.ref_store (pnu);
CREATE INDEX IF NOT EXISTS ref_store_adm_idx ON core.ref_store (adm_cd, ind_m_cd);
CREATE INDEX IF NOT EXISTS ref_store_latlon_idx ON core.ref_store (lat, lon);

CREATE TABLE IF NOT EXISTS core.dim_trdar (
    trdar_cd       text PRIMARY KEY,              -- 상권_코드
    trdar_nm       text NOT NULL,
    trdar_se_cd    text,                          -- A 골목상권 · D 발달상권 · R 전통시장 · U 관광특구
    trdar_se_nm    text,
    sigungu_cd     char(5),
    sigungu_nm     text,
    adm_cd         char(8),                       -- 중심점이 든 행정동
    adm_nm         text,
    x_5181         numeric(12, 2),                -- 원본 중심점(Korea 2000 중부원점, EPSG:5181)
    y_5181         numeric(12, 2),
    lon            numeric(10, 7),                -- 중심점 경위도(수집기가 바꿈)
    lat            numeric(10, 7),
    area_m2        numeric(14, 1),
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE INDEX IF NOT EXISTS dim_trdar_adm_idx ON core.dim_trdar (adm_cd);

CREATE TABLE IF NOT EXISTS core.dim_adm_dong (
    adm_cd         char(8) PRIMARY KEY,
    adm_nm         text NOT NULL,
    x_5181         numeric(12, 2),
    y_5181         numeric(12, 2),
    lon            numeric(10, 7),
    lat            numeric(10, 7),
    area_m2        numeric(14, 1),
    source         text NOT NULL,
    asof           date NOT NULL
);

CREATE TABLE IF NOT EXISTS core.fact_trdar_stat (
    dataset        text NOT NULL,                 -- 수집기 seoul_trdar.DATASETS의 키(trdar_sales · dong_store …)
    yyqu           int NOT NULL,                  -- 기준 연도 분기(20252 = 2025년 2분기)
    area_level     text NOT NULL,                 -- 상권 · 행정동
    area_cd        text NOT NULL,                 -- 상권_코드 또는 행정동_코드
    area_nm        text,
    induty_cd      text NOT NULL DEFAULT '',      -- 서비스 업종 코드(업종이 없는 지표는 빈 값)
    induty_nm      text,
    metrics        jsonb NOT NULL,                -- 원본 항목 이름 그대로(THSMON_SELNG_AMT 등) → 숫자 또는 글자
    source         text NOT NULL,
    asof           date NOT NULL,
    PRIMARY KEY (dataset, yyqu, area_cd, induty_cd)
);
CREATE INDEX IF NOT EXISTS fact_trdar_stat_area_idx ON core.fact_trdar_stat (area_cd, dataset, yyqu);

CREATE TABLE IF NOT EXISTS core.fact_living_pop (
    stdr_de        date NOT NULL,                 -- 기준일
    tmzon          smallint NOT NULL,             -- 시간대(0~23시)
    adm_cd         char(8) NOT NULL,              -- 행정동코드
    tot            numeric(12, 4) NOT NULL,       -- 총생활인구(추정치라 소수)
    by_sex_age     real[],                        -- 남 0-9 · 10-14 · 15-19 … 65-69 · 70+ (14칸) 다음 여 같은 순서(14칸)
    source         text NOT NULL,
    asof           date NOT NULL,
    PRIMARY KEY (stdr_de, tmzon, adm_cd)
);
CREATE INDEX IF NOT EXISTS fact_living_pop_adm_idx ON core.fact_living_pop (adm_cd, stdr_de);

-- ── 조회(웹 · GPT에 붙일 때 씀) ──

-- 필지 하나의 점포(층 · 업종). 같은 필지에 건물이 여럿이어도 필지로 묶음
DROP FUNCTION IF EXISTS mart.q_store_by_pnu(text);
CREATE FUNCTION mart.q_store_by_pnu(p_pnu text)
RETURNS TABLE (name text, branch text, ind_l_nm text, ind_m_nm text, ind_s_nm text, floor text, unit text,
               bld_nm text, road_addr text, base_ym text, source text, asof date)
LANGUAGE sql STABLE AS $$
    SELECT s.name, s.branch, s.ind_l_nm, s.ind_m_nm, s.ind_s_nm, s.floor, s.unit, s.bld_nm, s.road_addr,
           s.base_ym::text, s.source, s.asof
    FROM core.ref_store s WHERE s.pnu = p_pnu
    ORDER BY s.floor NULLS LAST, s.ind_l_nm, s.name LIMIT 200
$$;

-- 상권 하나의 분기별 추정매출 합계(업종을 모두 더함) · 점포 수 · 길단위 유동인구
DROP FUNCTION IF EXISTS mart.q_trdar_trend(text);
CREATE FUNCTION mart.q_trdar_trend(p_trdar_cd text)
RETURNS TABLE (yyqu int, sales_amt numeric, sales_cnt numeric, store_cnt numeric, flpop numeric, source text)
LANGUAGE sql STABLE AS $$
    WITH s AS (
        SELECT t.yyqu, sum((t.metrics->>'THSMON_SELNG_AMT')::numeric) AS amt, sum((t.metrics->>'THSMON_SELNG_CO')::numeric) AS cnt
        FROM core.fact_trdar_stat t WHERE t.dataset = 'trdar_sales' AND t.area_cd = p_trdar_cd GROUP BY t.yyqu
    ), st AS (
        SELECT t.yyqu, sum((t.metrics->>'STOR_CO')::numeric) AS n
        FROM core.fact_trdar_stat t WHERE t.dataset = 'trdar_store' AND t.area_cd = p_trdar_cd GROUP BY t.yyqu
    ), f AS (
        SELECT t.yyqu, (t.metrics->>'TOT_FLPOP_CO')::numeric AS n
        FROM core.fact_trdar_stat t WHERE t.dataset = 'trdar_flpop' AND t.area_cd = p_trdar_cd
    ), q AS (SELECT yyqu FROM s UNION SELECT yyqu FROM st UNION SELECT yyqu FROM f)
    SELECT q.yyqu, s.amt, s.cnt, st.n, f.n, '서울시 상권분석서비스(추정치)'::text
    FROM q LEFT JOIN s USING (yyqu) LEFT JOIN st USING (yyqu) LEFT JOIN f USING (yyqu)
    ORDER BY q.yyqu
$$;

DO $$
DECLARE f text;
BEGIN
    FOREACH f IN ARRAY ARRAY['mart.q_store_by_pnu(text)', 'mart.q_trdar_trend(text)'] LOOP
        EXECUTE format('ALTER FUNCTION %s SECURITY DEFINER', f);
        EXECUTE format('ALTER FUNCTION %s SET search_path = pg_catalog, public', f);
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hanpilji_gpt') THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO hanpilji_gpt', f);
        END IF;
    END LOOP;
END $$;
