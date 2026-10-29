-- core 정제 테이블 18개 (dim 6 · bridge 3 · fact 7 · 규정 2)
-- 근거: 부동산_수집처_수요처_20261017.xlsx 「테이블 정의」 시트
-- 모든 테이블에 source(어느 사이트) · asof(어느 날짜 자료) 컬럼이 들어감
-- 공간 도형(geom)과 의미 검색 값(embedding)은 확장이 깔린 서버에서만 붙임
--   → 03_core_postgis.sql · 04_core_pgvector.sql
-- 설계 초안과 달라진 점은 각 컬럼 주석에 「추가」로 표시함

-- ───────────── 차원 ─────────────

CREATE TABLE IF NOT EXISTS core.dim_region (
    lawd_cd        char(10) PRIMARY KEY,          -- 법정동코드 10자리
    sido           text,
    sigungu        text,
    emd            text,                          -- 읍면동(리 포함)
    valid_from     date,
    valid_to       date,                          -- 없어진 동이면 채움
    source         text NOT NULL,
    asof           date NOT NULL
);

CREATE TABLE IF NOT EXISTS core.dim_parcel (
    pnu            varchar(25) PRIMARY KEY,       -- 19자리 이상, 길이 고정 안 함
    lawd_cd        char(10) REFERENCES core.dim_region,
    jibun          text,
    ledger_type    text,                          -- 토지 · 임야
    land_category  text,                          -- 지목
    area_m2        numeric(14, 2),
    rep_zone_cd    text,                          -- 대표 용도지역
    source         text NOT NULL,
    asof           date NOT NULL,
    CHECK (pnu ~ '^[0-9]{19,}$')
);
CREATE INDEX IF NOT EXISTS dim_parcel_lawd_idx ON core.dim_parcel (lawd_cd);

CREATE TABLE IF NOT EXISTS core.dim_zone (
    zone_cd        text PRIMARY KEY,
    code_system    text NOT NULL DEFAULT 'land_char',  -- 추가: land_char(토지특성 2자리) · uq(토지이용계획 UQ코드) · eum(토지이음)
    zone_name      text NOT NULL,
    law_basis      text,                          -- 근거 법령
    source         text NOT NULL,
    asof           date NOT NULL
);

CREATE TABLE IF NOT EXISTS core.dim_building (
    bld_pk         text PRIMARY KEY,              -- 건축HUB 새 건물 번호
    pnu            varchar(25) REFERENCES core.dim_parcel,  -- 대표 필지
    name           text,
    main_use       text,                          -- 설계서의 use. 예약어를 피해 이름만 바꿈
    structure      text,
    approval_date  date,                          -- 사용승인일(준공)
    floors_above   integer,                       -- 추가: 설계서 floors를 지상·지하로 나눔
    floors_below   integer,
    total_area     numeric(14, 2),                -- 연면적 ㎡
    bc_rat         numeric(7, 2),                 -- 건폐율 %
    vl_rat         numeric(8, 2),                 -- 용적률 %
    has_elevator   boolean,                       -- 추가: 「승강기 없는 건물」 질문용
    parking_count  integer,                       -- 추가: 「주차 2대 이상」 질문용
    is_violation   boolean,                       -- 위반건축물 여부
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE INDEX IF NOT EXISTS dim_building_pnu_idx ON core.dim_building (pnu);

CREATE TABLE IF NOT EXISTS core.dim_unit (
    unit_pk        text PRIMARY KEY,
    bld_pk         text NOT NULL REFERENCES core.dim_building,
    dong           text,
    floor          text,
    ho             text,
    excl_area      numeric(10, 2),                -- 전용면적 ㎡
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE INDEX IF NOT EXISTS dim_unit_bld_idx ON core.dim_unit (bld_pk);

CREATE TABLE IF NOT EXISTS core.dim_complex (
    complex_id     text PRIMARY KEY,              -- 부동산원 단지 번호
    name           text NOT NULL,
    name_history   jsonb,                         -- [{name, from, to}]
    households     integer,
    dong_count     integer,
    lawd_cd        char(10) REFERENCES core.dim_region,  -- 추가: 지역 합계용
    kind           text NOT NULL DEFAULT '아파트',  -- 추가: 아파트 · 오피스텔(실거래가 API 종류)
    source         text NOT NULL,
    asof           date NOT NULL
);
-- 이미 만든 DB에도 붙임(오피스텔 실거래 전에 쌓인 단지는 모두 아파트)
ALTER TABLE core.dim_complex ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT '아파트';

-- ───────────── 연결 ─────────────

CREATE TABLE IF NOT EXISTS core.bridge_parcel_zone (
    pnu            varchar(25) NOT NULL REFERENCES core.dim_parcel,
    zone_cd        text NOT NULL REFERENCES core.dim_zone,
    overlap        text NOT NULL CHECK (overlap IN ('포함', '저촉', '접함')),
    source         text NOT NULL,
    asof           date NOT NULL,
    PRIMARY KEY (pnu, zone_cd)
);

CREATE TABLE IF NOT EXISTS core.bridge_parcel_building (
    pnu            varchar(25) NOT NULL REFERENCES core.dim_parcel,
    bld_pk         text NOT NULL REFERENCES core.dim_building,
    is_main_lot    boolean NOT NULL DEFAULT false,
    source         text NOT NULL,
    asof           date NOT NULL,
    PRIMARY KEY (pnu, bld_pk)
);

CREATE TABLE IF NOT EXISTS core.bridge_complex_building (
    complex_id     text NOT NULL REFERENCES core.dim_complex,
    bld_pk         text NOT NULL REFERENCES core.dim_building,
    dong_name      text,
    source         text NOT NULL,
    asof           date NOT NULL,
    PRIMARY KEY (complex_id, bld_pk)
);

-- ───────────── 팩트 ─────────────

CREATE TABLE IF NOT EXISTS core.fact_land_char (
    pnu            varchar(25) NOT NULL REFERENCES core.dim_parcel,
    base_year      smallint NOT NULL,
    land_use       text,                          -- 토지 이용 상황
    terrain_height text,                          -- 설계서 terrain을 높이·모양으로 나눔
    terrain_shape  text,
    road_side      text,
    zone_cd1       text,
    zone_cd2       text,
    source         text NOT NULL,
    asof           date NOT NULL,
    PRIMARY KEY (pnu, base_year)
);

CREATE TABLE IF NOT EXISTS core.fact_land_price (
    pnu            varchar(25) NOT NULL REFERENCES core.dim_parcel,
    base_year      smallint NOT NULL,
    is_standard    boolean NOT NULL DEFAULT false,  -- 표준지 여부(같은 해에 둘 다 있을 수 있어 키에 넣음)
    price_per_m2   bigint NOT NULL CHECK (price_per_m2 > 0),
    source         text NOT NULL,
    asof           date NOT NULL,
    PRIMARY KEY (pnu, base_year, is_standard)
);

CREATE TABLE IF NOT EXISTS core.fact_house_price (
    unit_pk        text REFERENCES core.dim_unit,   -- 단독주택은 호가 없어 비움
    pnu            varchar(25) NOT NULL REFERENCES core.dim_parcel,
    base_year      smallint NOT NULL,
    price          bigint NOT NULL CHECK (price > 0),
    house_kind     text NOT NULL CHECK (house_kind IN ('공동', '개별')),  -- 추가
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS fact_house_price_uq
    ON core.fact_house_price (coalesce(unit_pk, ''), pnu, base_year);

-- 이름 · 주소는 담지 않음. 구분 · 지분 · 변동일까지만
CREATE TABLE IF NOT EXISTS core.fact_ownership (
    pnu            varchar(25) NOT NULL REFERENCES core.dim_parcel,
    bld_pk         text REFERENCES core.dim_building,  -- 건축물소유정보일 때
    base_date      date NOT NULL,
    owner_seq      integer NOT NULL,
    owner_type     text,                          -- 개인 · 법인 · 국공유 …
    residence      text,                          -- 관내 · 관외
    change_cause   text,
    change_date    date,
    share_count    integer,
    quota          text,                          -- 지분 비율
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS fact_ownership_uq
    ON core.fact_ownership (pnu, coalesce(bld_pk, ''), base_date, owner_seq);

CREATE TABLE IF NOT EXISTS core.fact_deal (
    deal_id        text PRIMARY KEY,              -- 원본 항목으로 만든 해시(재수집해도 같은 값)
    lawd_cd        char(10) NOT NULL REFERENCES core.dim_region,
    pnu            varchar(25) REFERENCES core.dim_parcel,     -- 못 붙였으면 비움
    complex_id     text REFERENCES core.dim_complex,
    deal_type      text NOT NULL,                 -- 아파트 · 오피스텔 · 연립다세대 … 8종
    contract_date  date NOT NULL,
    amount         bigint NOT NULL CHECK (amount > 0),  -- 원
    area           numeric(10, 2) NOT NULL CHECK (area > 0),  -- ㎡
    floor          integer,                       -- 음수는 지하
    build_year     smallint,                      -- 추가: 준공 연도(응답에 있음)
    is_cancelled   boolean NOT NULL DEFAULT false,
    match_method   text,                          -- 번지 · 단지번호 · 이름 …
    match_score    numeric(3, 2) CHECK (match_score BETWEEN 0 AND 1),
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE INDEX IF NOT EXISTS fact_deal_region_idx ON core.fact_deal (lawd_cd, deal_type, contract_date);
CREATE INDEX IF NOT EXISTS fact_deal_complex_idx ON core.fact_deal (complex_id, contract_date);

CREATE TABLE IF NOT EXISTS core.fact_rent (
    rent_id        text PRIMARY KEY,
    lawd_cd        char(10) NOT NULL REFERENCES core.dim_region,
    pnu            varchar(25) REFERENCES core.dim_parcel,
    complex_id     text REFERENCES core.dim_complex,
    rent_type      text NOT NULL,                 -- 추가: 아파트 · 오피스텔 · 연립다세대 · 단독다가구
    contract_date  date NOT NULL,                 -- 추가
    area           numeric(10, 2),                -- 추가
    floor          integer,                       -- 추가
    deposit        bigint NOT NULL CHECK (deposit >= 0),
    monthly_rent   bigint NOT NULL DEFAULT 0 CHECK (monthly_rent >= 0),
    contract_kind  text,                          -- 신규 · 갱신
    renewal        boolean,                       -- 갱신요구권 사용
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE INDEX IF NOT EXISTS fact_rent_region_idx ON core.fact_rent (lawd_cd, rent_type, contract_date);
CREATE INDEX IF NOT EXISTS fact_rent_complex_idx ON core.fact_rent (complex_id, contract_date);

CREATE TABLE IF NOT EXISTS core.fact_market (
    lawd_cd        char(10) NOT NULL REFERENCES core.dim_region,
    stat_id        text NOT NULL,                 -- 통계표 번호
    period         text NOT NULL,                 -- 기준월 YYYYMM 또는 주 YYYY-Www
    value          numeric NOT NULL,
    unit           text,                          -- 추가
    source         text NOT NULL,
    asof           date NOT NULL,
    PRIMARY KEY (lawd_cd, stat_id, period)
);

-- ───────────── 규정 ─────────────

-- 법령 조문 하나(시행일별). article_id = 법령ID:조번호[-가지번호]:시행일
CREATE TABLE IF NOT EXISTS core.doc_law (
    article_id     text PRIMARY KEY,
    law_id         text NOT NULL,                 -- 추가: 법제처 법령ID
    law_name       text NOT NULL,
    law_kind       text,                          -- 추가: 법률 · 대통령령 · 국토교통부령 · 행정규칙
    article_no     text NOT NULL,                 -- 「제12조의2」 형태
    article_title  text,                          -- 추가
    body           text NOT NULL,                 -- 조문 + 항 · 호 · 목 전체
    search_text    text NOT NULL,                 -- 추가: 본문 + 숫자 표기 보강(「100분의 5」 → 「5%」)
    effective_date date NOT NULL,                 -- 시행일
    is_current     boolean NOT NULL DEFAULT true, -- 추가: 최신 시행본 여부
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE INDEX IF NOT EXISTS doc_law_name_idx ON core.doc_law (law_name, article_no);
CREATE INDEX IF NOT EXISTS doc_law_trgm_idx ON core.doc_law USING gin (search_text gin_trgm_ops);

-- 용도지역 하나에서 건물 종류(행위) 하나를 지을 수 있는지
CREATE TABLE IF NOT EXISTS core.reg_rule (
    rule_id        bigserial PRIMARY KEY,
    zone_cd        text NOT NULL REFERENCES core.dim_zone,
    sigungu_cd     char(5),                       -- 조례가 다를 때만. 비우면 전국 기준
    act_name       text NOT NULL,                 -- 건물 종류 · 행위
    allowed        text NOT NULL CHECK (allowed IN ('가능', '조건부', '불가')),
    condition_text text,                          -- 추가: 조건부일 때 조건
    article_id     text REFERENCES core.doc_law,  -- 근거 조문(못 찾으면 비움)
    source         text NOT NULL,
    asof           date NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS reg_rule_uq
    ON core.reg_rule (zone_cd, coalesce(sigungu_cd, ''), act_name);
