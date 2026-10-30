-- 주택 공시가격: 국토교통부가 해마다 1월 1일 기준으로 공시하는 공동주택(아파트 · 연립 · 다세대) 호별 가격
-- 원본은 공공데이터포털 파일데이터 「국토교통부_주택 공시가격 정보」(3073746): 전국 한 해치 CSV 하나(2026년 1,585만 호, 압축 155MB)
-- 같은 정보를 주던 오픈API(1611000/nsdi/ApartHousingPriceService)는 폐기됨(2026-10-30 확인). 파일은 인증키 없이 받음
-- 수집기는 hanpilji/collectors/house_price.py. 시범 구만 골라 넣음
-- 오피스텔은 공동주택이 아니라 공시가격이 없음(국세청 기준시가가 따로 있음)

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('molit_house_price', '국토교통부 주택 공시가격 정보(공동주택 호별)', '국토교통부 · 한국부동산원', 'file', NULL, 'UTF-8', '매년',
  '이용허락범위 제한 없음', 'https://www.data.go.kr/data/3073746/fileData.do')
ON CONFLICT (source_id) DO NOTHING;

-- 02_core.sql의 첫 모양(unit_pk → dim_unit, pnu → dim_parcel)은 호 · 필지 표가 차야 넣을 수 있어 한 줄도 쓰지 않았음.
-- 그 모양이 비어 있으면 지우고 새 모양으로 만듦(데이터가 있으면 멈춤)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'core' AND table_name = 'fact_house_price' AND column_name = 'unit_pk') THEN
        IF EXISTS (SELECT 1 FROM core.fact_house_price) THEN
            RAISE EXCEPTION 'core.fact_house_price(옛 모양)에 데이터가 있어 바꾸지 않음';
        END IF;
        DROP TABLE core.fact_house_price;
    END IF;
END $$;

-- 한 줄 = 한 해의 한 호. 키는 원본의 단지 · 동 · 호 코드(해마다 같은 호는 같은 코드)
CREATE TABLE IF NOT EXISTS core.fact_house_price (
    house_kind     text NOT NULL DEFAULT '공동' CHECK (house_kind IN ('공동', '개별')),
    base_year      smallint NOT NULL,
    complex_code   text NOT NULL,                 -- 부동산원 공시 단지코드(실거래 단지 번호와는 다름)
    dong_code      integer NOT NULL,
    ho_code        integer NOT NULL,
    lawd_cd        char(10) NOT NULL,             -- 법정동코드
    pnu            varchar(19),                   -- 블럭지번(택지지구 「A3블록」 등)은 본번이 0이라 비움
    lot_name       text,                          -- 블럭지번일 때 원본 특수지명. 예) 수서역세권 공공주택지구내 A3블록
    complex_name   text,
    dong           text,                          -- 동명(빌라는 비어 있을 때가 많음)
    ho             text,
    excl_area      numeric(10, 2),                -- 전용면적 ㎡
    price          bigint NOT NULL CHECK (price > 0),  -- 원
    bld_unit_pk    text,                          -- 집합건축물대장 전유부 키(세움터 BLDRGST_SEQNO)
    source         text NOT NULL,
    asof           date NOT NULL,                 -- 원본 파일이 포털에 올라온 날
    PRIMARY KEY (house_kind, base_year, complex_code, dong_code, ho_code)
);
CREATE INDEX IF NOT EXISTS fact_house_price_pnu_idx ON core.fact_house_price (pnu, base_year);
CREATE INDEX IF NOT EXISTS fact_house_price_lawd_idx ON core.fact_house_price (lawd_cd, base_year);

-- 실거래 단지(core.dim_complex) ↔ 공시 단지. 필지(PNU)가 같으면 같은 단지로 봄.
-- 블럭지번이라 PNU가 없는 공시 단지는 같은 법정동에서 이름(띄어쓰기 · 끝 괄호 무시)이 같은 것으로 맞춤
CREATE OR REPLACE FUNCTION core.house_price_rows(p_complex_id text, p_year int DEFAULT NULL)
RETURNS SETOF core.fact_house_price
LANGUAGE sql STABLE AS $$
    WITH c AS (
        SELECT pnu, lawd_cd, regexp_replace(replace(name, ' ', ''), '\([^()]*\)$', '') AS k
        FROM core.dim_complex WHERE complex_id = p_complex_id
    ), hit AS (
        SELECT h.* FROM core.fact_house_price h, c
        WHERE h.house_kind = '공동'
          AND (h.pnu = c.pnu
               OR (h.pnu IS NULL AND h.lawd_cd = c.lawd_cd
                   AND regexp_replace(replace(h.complex_name, ' ', ''), '\([^()]*\)$', '') = c.k))
    )
    SELECT * FROM hit
    WHERE base_year = coalesce(p_year, (SELECT max(base_year) FROM hit))
$$;

-- ── 공시가격: 단지 x 전용면적별 공시가격과 같은 면적의 최근 실거래 ──
-- 공시가격은 그 면적 호들의 중간값 · 최저 · 최고(층 · 향마다 다름). 실거래는 해제 뺀 매매 평균(최근 p_months개월, ±p_area_tol㎡)
-- official_ratio = 공시가격 중간값 ÷ 매매 평균 x 100. 정부의 「현실화율」과는 셈법이 달라 그대로 비교하면 안 됨
-- p_dong · p_ho를 주면 그 호 하나의 공시가격(동은 「101」 「101동」 둘 다 받음). 최대 20줄
CREATE OR REPLACE FUNCTION mart.q_house_price(
    p_sigungu text, p_query text, p_area numeric DEFAULT NULL, p_dong text DEFAULT NULL, p_ho text DEFAULT NULL,
    p_months int DEFAULT 12, p_area_tol numeric DEFAULT 3)
RETURNS TABLE (complex_name text, kind text, area_m2 numeric, dong text, ho text,
               unit_count bigint, official_median bigint, official_min bigint, official_max bigint,
               official_yoy_pct numeric, base_year smallint,
               trade_count bigint, trade_avg bigint, official_ratio numeric,
               period_from date, period_to date, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH cx AS (
        SELECT f.complex_id, f.name, x.kind FROM core.find_complex(p_sigungu, p_query) f
        JOIN core.dim_complex x USING (complex_id)
        LIMIT 5
    ), h AS (
        SELECT cx.complex_id, round(r.excl_area) AS area_m2, r.*
        FROM cx, LATERAL core.house_price_rows(cx.complex_id) r
        WHERE (p_area IS NULL OR abs(r.excl_area - p_area) <= p_area_tol)
          AND (p_dong IS NULL OR regexp_replace(r.dong, '동$', '') = regexp_replace(btrim(p_dong), '동$', ''))
          AND (p_ho IS NULL OR regexp_replace(r.ho, '호$', '') = regexp_replace(btrim(p_ho), '호$', ''))
    ), prev AS (   -- 같은 호의 한 해 전 값(다음 해 파일부터 생김)
        SELECT h.complex_id, h.area_m2, p.price AS prev_price, h.price
        FROM h JOIN core.fact_house_price p
          ON p.house_kind = h.house_kind AND p.base_year = h.base_year - 1
         AND p.complex_code = h.complex_code AND p.dong_code = h.dong_code AND p.ho_code = h.ho_code
    ), one AS (    -- 호를 짚었으면 호마다 한 줄, 아니면 면적마다 한 줄
        SELECT h.complex_id, h.area_m2,
               CASE WHEN p_ho IS NOT NULL THEN h.dong END AS dong, CASE WHEN p_ho IS NOT NULL THEN h.ho END AS ho,
               count(*) AS n, (percentile_disc(0.5) WITHIN GROUP (ORDER BY h.price))::bigint AS med,
               min(h.price) AS lo, max(h.price) AS hi, max(h.base_year) AS yr, max(h.asof) AS asof
        FROM h GROUP BY 1, 2, 3, 4
    ), yoy AS (
        SELECT complex_id, area_m2, round((sum(price)::numeric / sum(prev_price) - 1) * 100, 1) AS pct
        FROM prev GROUP BY 1, 2
    ), win AS (
        SELECT (current_date - make_interval(months => p_months))::date AS d_from, current_date AS d_to
    ), t AS (
        SELECT one.complex_id, one.area_m2, count(d.*) AS n, avg(d.amount)::bigint AS avg_amt
        FROM one CROSS JOIN win
        LEFT JOIN core.fact_deal d
          ON d.complex_id = one.complex_id AND NOT d.is_cancelled AND d.contract_date >= win.d_from
         AND abs(d.area - one.area_m2) <= p_area_tol
        GROUP BY 1, 2
    )
    SELECT cx.name, cx.kind, one.area_m2, one.dong, one.ho, one.n, one.med, one.lo, one.hi, yoy.pct, one.yr,
           t.n, t.avg_amt,
           CASE WHEN t.avg_amt > 0 THEN round(one.med::numeric / t.avg_amt * 100, 1) END,
           win.d_from, win.d_to,
           '국토교통부 주택 공시가격 정보(' || one.yr || '년 1월 1일 기준) · 국토교통부 ' || cx.kind || ' 매매 실거래가',
           one.asof, count(*) OVER ()
    FROM one JOIN cx USING (complex_id)
    JOIN t USING (complex_id, area_m2)
    LEFT JOIN yoy USING (complex_id, area_m2)
    CROSS JOIN win
    ORDER BY cx.name, one.area_m2, one.dong, one.ho
    LIMIT 20
$$;

ALTER FUNCTION mart.q_house_price(text, text, numeric, text, text, int, numeric) SECURITY DEFINER;
ALTER FUNCTION mart.q_house_price(text, text, numeric, text, text, int, numeric) SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_house_price(text, text, numeric, text, text, int, numeric) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_house_price(text, text, numeric, text, text, int, numeric) TO hanpilji_gpt;
