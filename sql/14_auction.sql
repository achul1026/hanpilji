-- 경매 · 공매 분석: 공매 물건(한국자산관리공사 온비드)을 실거래가와 비교하고, 입찰 전 권리 확인 항목을 법령 근거와 함께 줌
-- 물건은 core.fact_auction(물건 x 회차), 수집기는 hanpilji/collectors/onbid.py
-- 법원경매(courtauction.go.kr)는 공식 API가 없어 아직 받지 않음. kind 열을 두어 나중에 같은 표에 넣을 수 있게 함

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('onbid', '한국자산관리공사 차세대 온비드 부동산 물건목록 조회서비스', '한국자산관리공사', 'api', 1000, 'UTF-8', '매일',
  '이용허락범위 제한 없음', 'https://www.data.go.kr/')
ON CONFLICT (source_id) DO NOTHING;

-- 온비드 원본 응답 줄. 요청 하나(재산유형 x 쪽) = 한 줄
CREATE TABLE IF NOT EXISTS stg.auction_raw (
    source_id      text NOT NULL,
    req_key        text NOT NULL,                 -- 예) 서울특별시 강남구:0007:p1
    row_data       jsonb NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, req_key)
);

-- 물건의 회차 한 줄. 같은 물건이 유찰되면 회차(pbct_cdtn_no)가 늘고 최저입찰가가 내려감
CREATE TABLE IF NOT EXISTS core.fact_auction (
    cltr_mng_no    text NOT NULL,                 -- 물건관리번호(온비드) · 사건번호(법원경매)
    pbct_cdtn_no   text NOT NULL,                 -- 공매조건번호 = 회차 하나
    kind           text NOT NULL DEFAULT '공매' CHECK (kind IN ('공매', '경매')),
    sigungu_cd     char(5),                       -- 시군구 이름으로 core.dim_region에서 찾은 값
    sido           text,
    sigungu        text,
    emd            text,                          -- 법정동 · 읍면
    jibun          text,                          -- 주소에서 뽑은 지번. 예) 316, 12-3
    pnu            varchar(25),                   -- 지번 PNU(온비드 ltnoPnu). 단지를 찾는 첫째 열쇠
    address        text,
    name           text,                          -- 물건명. 예) 서울특별시 강남구 대치동 316 은마아파트 제28동 …
    usage          text,                          -- 용도. 예) 아파트
    prpt_div       text,                          -- 재산 구분. 예) 압류재산 · 국유재산
    dsps_method    text,                          -- 처분 방식. 매각 · 임대
    org            text,                          -- 집행 기관
    area_m2        numeric(12, 2),                -- 건물 면적(없으면 토지 면적). 실거래의 전용면적과 기준이 다를 수 있음
    appraisal      bigint,                        -- 감정가(원)
    min_bid        bigint,                        -- 이 회차 최저입찰가(원)
    fail_count     integer,                       -- 유찰 횟수
    round_no       integer,                       -- 공매 회차
    status         text,                          -- 입찰준비중 · 입찰진행중 · 낙찰 · 유찰 …
    bid_begin      timestamp,                     -- 한국 시각(KST)
    bid_end        timestamp,
    source         text NOT NULL,
    asof           date NOT NULL,
    PRIMARY KEY (cltr_mng_no, pbct_cdtn_no)
);
ALTER TABLE core.fact_auction ADD COLUMN IF NOT EXISTS pnu varchar(25);
CREATE INDEX IF NOT EXISTS fact_auction_sgg_idx ON core.fact_auction (sigungu_cd, bid_end);

-- 입찰 전 확인 항목. 기사(무드모드 2026-10-27)의 체크리스트를 법령 근거와 함께 정리. 조문은 core.doc_law에 있으면 붙임
CREATE TABLE IF NOT EXISTS core.ref_auction_check (
    step           smallint PRIMARY KEY,
    item           text NOT NULL,                 -- 확인 항목
    what_to_check  text NOT NULL,                 -- 무엇을 어떻게 보나
    where_to_look  text NOT NULL,                 -- 어느 서류 · 어디서
    applies_to     text NOT NULL DEFAULT '경매 · 공매',
    law_name       text,                          -- 근거 법령(법제처 법령명 그대로)
    article_no     text,                          -- 「제91조」 형태
    source         text NOT NULL,
    asof           date NOT NULL
);

INSERT INTO core.ref_auction_check (step, item, what_to_check, where_to_look, applies_to, law_name, article_no, source, asof) VALUES
 (1, '말소기준권리', '근저당 · 저당 · 압류 · 가압류 · 담보가등기 중 날짜가 가장 빠른 권리. 그보다 뒤의 권리는 매각으로 지워지고, 앞선 지상권 · 전세권 · 등기된 임차권은 매수인이 떠안음',
  '등기부(갑구 · 을구), 매각물건명세서의 「최선순위 설정」 칸', '경매 · 공매', '민사집행법', '제91조', '한필지 정리(법령 근거)', '2026-10-28'),
 (2, '선순위 임차인(대항력)', '임차인의 전입일 다음 날 0시가 말소기준권리보다 빠르면 대항력이 있음. 보증금을 배당으로 다 못 받으면 남은 돈은 매수인이 물어 줘야 함',
  '매각물건명세서 · 현황조사서 · 전입세대 열람', '경매 · 공매', '주택임대차보호법', '제3조', '한필지 정리(법령 근거)', '2026-10-28'),
 (3, '배당요구 · 확정일자', '선순위 임차인이 배당요구 종기까지 배당요구를 했고 확정일자가 있는지. 했다면 순서대로 배당받고, 안 했으면 보증금 전부를 매수인이 떠안을 수 있음',
  '매각물건명세서(배당요구 여부 · 일자)', '경매', '민사집행법', '제88조', '한필지 정리(법령 근거)', '2026-10-28'),
 (4, '확정일자 임차인의 우선변제', '대항요건과 확정일자를 갖춘 임차인은 후순위 권리자보다 먼저 보증금을 받음',
  '매각물건명세서', '경매 · 공매', '주택임대차보호법', '제3조의2', '한필지 정리(법령 근거)', '2026-10-28'),
 (5, '소액임차인 최우선변제', '보증금이 작은 임차인은 일정액을 다른 권리보다 먼저 받음. 금액 기준은 시행령(지역 · 담보 설정일)에 따름',
  '매각물건명세서 · 시행령 금액표', '경매 · 공매', '주택임대차보호법', '제8조', '한필지 정리(법령 근거)', '2026-10-28'),
 (6, '당해세 · 세금 체납', '그 집에 매겨진 세금(당해세)과 법정기일이 빠른 국세는 앞 순서로 배당될 수 있음. 2023년 4월부터는 확정일자보다 늦게 생긴 당해세보다 임차인 보증금이 먼저(제35조)',
  '등기부의 압류 · 교부청구 내역', '경매 · 공매', '국세기본법', '제35조', '한필지 정리(법령 근거)', '2026-10-28'),
 (7, '유치권', '유치권 신고가 있으면 매수인이 그 채권을 갚아야 할 수 있음. 신고 여부와 실제 점유를 확인',
  '매각물건명세서 비고 · 현황조사서 · 현장', '경매', '민사집행법', '제91조', '한필지 정리(법령 근거)', '2026-10-28'),
 (8, '대지권 미등기 · 토지별도등기', '아파트의 땅 지분(대지사용권)이 등기에 없거나 토지에 따로 권리가 있으면 매수 뒤 문제가 될 수 있음',
  '등기부 표제부(대지권 표시) · 매각물건명세서', '경매 · 공매', '집합건물의 소유 및 관리에 관한 법률', '제20조', '한필지 정리(법령 근거)', '2026-10-28'),
 (9, '관리비 체납', '전 소유자가 밀린 관리비 중 공용부분은 매수인이 물려받음',
  '관리사무소 문의 · 매각물건명세서 비고', '경매 · 공매', '집합건물의 소유 및 관리에 관한 법률', '제18조', '한필지 정리(법령 근거)', '2026-10-28'),
 (10, '위반건축물', '건축물대장에 위반건축물로 적혀 있으면 원상복구 명령 · 이행강제금이 매수인에게 넘어올 수 있음',
  '건축물대장(표제부 위반 여부)', '경매 · 공매', '건축법', '제79조', '한필지 정리(법령 근거)', '2026-10-28'),
 (11, '매각물건명세서 확인', '법원이 권리 · 점유 관계를 적어 둔 서류. AI 요약과 별도로 반드시 직접 열어 볼 것',
  '법원경매정보 물건 상세(경매) · 온비드 공매재산명세서(공매)', '경매', '민사집행법', '제105조', '한필지 정리(법령 근거)', '2026-10-28'),
 (12, '공매재산명세서 확인', '공매는 국세징수법 절차라 서류 이름과 효력이 경매와 다름. 경매와 달리 인도명령 제도가 없어 점유자를 내보내려면 소송이 필요할 수 있음',
  '온비드 물건 상세의 공매재산명세서', '공매', '국세징수법', '제77조', '한필지 정리(법령 근거)', '2026-10-28'),
 (13, '입찰보증금', '경매는 보통 최저매각가격의 10%(재매각은 법원이 20% 등으로 올리기도 함), 공매는 공매예정가격의 10%. 대출 가능 금액도 미리 확인',
  '매각기일 공고 · 온비드 공고문', '경매 · 공매', '민사집행규칙', '제63조', '한필지 정리(법령 근거)', '2026-10-28'),
 (14, '실제 점유 · 현장', '서류에 안 보이는 임차인(외국인, 세대주가 사망한 세대 등)도 대항력이 인정될 수 있음. 현장에서 누가 사는지 확인',
  '현황조사서 · 현장 방문', '경매 · 공매', NULL, NULL, '한필지 정리', '2026-10-28')
ON CONFLICT (step) DO UPDATE SET item = excluded.item, what_to_check = excluded.what_to_check,
    where_to_look = excluded.where_to_look, applies_to = excluded.applies_to, law_name = excluded.law_name,
    article_no = excluded.article_no, source = excluded.source, asof = excluded.asof;

-- ── 공매 물건 할인율: 최저입찰가를 같은 단지 · 비슷한 면적의 최근 실거래와 비교 ──
-- 물건마다 현재 회차 하나(입찰이 안 끝난 회차 중 가장 이른 것, 없으면 가장 최근 회차). 입찰 시각은 한국 시각이라 DB 시간대와 상관없이 KST로 비교
-- 단지는 지번 PNU로 단지 필지(core.v_complex_parcel)에서 찾고, 없으면 주소의 「동 지번」으로 찾음(core.find_complex). 실거래는 그 단지의 최근 p_months개월 매매 중 면적 ±3㎡의 ㎡당 중위 단가
-- 같은 종류끼리만 비교: 용도 아파트 → 아파트 단지 · 아파트 매매, 용도 오피스텔 → 오피스텔 단지 · 오피스텔 매매,
--   용도 다세대주택 · 연립주택 → 연립다세대 단지(건물 한 동) · 연립다세대 매매
--   용도 「업무시설」은 오피스텔이 법상 업무시설이라 그 필지에 오피스텔 단지가 있을 때만 오피스텔 매매와 비교(label에 적음). 상가 · 사무실은 비교 안 함
-- est_market_price = ㎡당 중위 단가 x 물건 면적. discount_pct = (1 - 최저입찰가 ÷ est_market_price) x 100
-- 권리관계는 보지 않음: 할인이 커도 선순위 임차인 · 유치권이 있으면 실제로는 비쌀 수 있음(q_auction_checklist)
DROP FUNCTION IF EXISTS mart.q_auction_discount(text, text, boolean, numeric, int, int);
CREATE FUNCTION mart.q_auction_discount(p_sigungu text, p_query text DEFAULT NULL, p_open_only boolean DEFAULT true,
                                        p_min_discount numeric DEFAULT NULL, p_months int DEFAULT 12, p_limit int DEFAULT 20)
RETURNS TABLE (rank bigint, kind text, cltr_mng_no text, name text, address text, usage text, area_m2 numeric,
               appraisal bigint, min_bid bigint, min_bid_pct_of_appraisal numeric, fail_count int, round_no int,
               status text, bid_begin timestamp, bid_end timestamp, complex_name text, deal_count bigint,
               deal_median_per_m2 bigint, est_market_price bigint, discount_pct numeric, label text,
               source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH cur AS (
        SELECT DISTINCT ON (a.cltr_mng_no) a.*
        FROM core.fact_auction a
        WHERE a.sigungu_cd = p_sigungu
          AND (a.dsps_method IS NULL OR a.dsps_method LIKE '%매각%')
          AND (p_query IS NULL OR replace(concat_ws(' ', a.name, a.address, a.usage), ' ', '')
                                  LIKE '%' || replace(p_query, ' ', '') || '%')
        ORDER BY a.cltr_mng_no, (a.bid_end >= (now() AT TIME ZONE 'Asia/Seoul')) DESC NULLS LAST,
                 CASE WHEN a.bid_end >= (now() AT TIME ZONE 'Asia/Seoul') THEN a.bid_begin END ASC,
                 a.bid_begin DESC NULLS LAST
    ), ck AS (   -- 물건 용도로 비교할 실거래 종류를 정함. NULL이면 비교 안 함
        SELECT cur.*, CASE WHEN coalesce(cur.usage, '') LIKE '%아파트%' THEN '아파트'
                           WHEN coalesce(cur.usage, '') LIKE '%오피스텔%' OR cur.usage = '업무시설' THEN '오피스텔'
                           WHEN coalesce(cur.usage, '') ~ '다세대|연립' THEN '연립다세대' END AS want
        FROM cur
    ), m AS (
        SELECT c.*, cx.complex_id, cx.name AS cx_name, t.n, t.med, round(t.med::numeric * c.area_m2) AS est
        FROM ck c
        LEFT JOIN LATERAL (   -- 같은 필지에 여러 종류 단지가 같이 있으면(주상복합) 물건과 같은 종류 단지
            SELECT * FROM (
                SELECT 1 AS k, x.complex_id, x.name, x.kind FROM core.v_complex_parcel v JOIN core.dim_complex x USING (complex_id)
                WHERE c.pnu IS NOT NULL AND v.pnu = c.pnu
                UNION ALL
                SELECT 2, f.complex_id, f.name, x.kind FROM core.find_complex(p_sigungu, c.emd || c.jibun) f
                JOIN core.dim_complex x USING (complex_id)
                WHERE c.emd IS NOT NULL AND c.jibun IS NOT NULL
            ) z WHERE c.want IS NULL OR z.kind = c.want   -- 비교 안 하는 용도도 단지 이름은 보여 줌
            ORDER BY z.k LIMIT 1
        ) cx ON true
        LEFT JOIN LATERAL (
            SELECT count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY d.amount / d.area) AS med
            FROM core.fact_deal d
            WHERE d.complex_id = cx.complex_id AND NOT d.is_cancelled AND c.area_m2 IS NOT NULL
              AND d.deal_type = c.want   -- 같은 건물의 상가 · 오피스텔을 아파트 값과 비교하지 않음(반대도)
              AND abs(d.area - c.area_m2) <= 3
              AND d.contract_date > current_date - make_interval(months => greatest(1, least(p_months, 36)))
        ) t ON true
        WHERE NOT p_open_only OR c.bid_end >= (now() AT TIME ZONE 'Asia/Seoul')
    ), r AS (
        SELECT m.*, round((1 - m.min_bid / nullif(m.est, 0)) * 100, 1) AS disc
        FROM m
    )
    SELECT rank() OVER (ORDER BY r.disc DESC NULLS LAST), r.kind, r.cltr_mng_no, r.name, r.address, r.usage, r.area_m2,
           r.appraisal, r.min_bid, round(r.min_bid * 100.0 / nullif(r.appraisal, 0), 1), r.fail_count, r.round_no,
           r.status, r.bid_begin, r.bid_end, r.cx_name, coalesce(r.n, 0), r.med::bigint, r.est::bigint, r.disc,
           CASE WHEN r.est IS NULL THEN CASE WHEN r.want IS NULL THEN '시세 비교 불가(아파트 · 오피스텔 · 연립다세대가 아님)'
                                             WHEN r.complex_id IS NULL AND r.usage = '업무시설' THEN '시세 비교 불가(업무시설 · 오피스텔 단지 없음)'
                                             WHEN r.complex_id IS NULL THEN '시세 비교 불가(단지를 못 찾음)'
                                             ELSE '시세 비교 불가(같은 면적 거래 없음)' END
                WHEN r.disc >= 15 THEN '실거래 대비 15% 이상 낮음 · 권리 확인 전'
                WHEN r.disc > 0 THEN '실거래보다 낮음 · 권리 확인 전'
                ELSE '실거래보다 낮지 않음' END
             || CASE WHEN r.est IS NOT NULL AND r.usage = '업무시설' THEN ' · 업무시설을 오피스텔 거래와 비교' ELSE '' END,
           r.source || CASE WHEN r.est IS NOT NULL THEN ' · 국토교통부 ' || r.want || ' 매매 실거래가' ELSE '' END,
           r.asof, count(*) OVER ()
    FROM r
    WHERE p_min_discount IS NULL OR r.disc >= p_min_discount
    ORDER BY r.disc DESC NULLS LAST, r.bid_end
    LIMIT greatest(1, least(coalesce(p_limit, 20), 30))
$$;

-- ── 입찰 전 권리 확인 목록: 항목마다 무엇을 · 어디서 보나와 근거 조문(수집돼 있으면 조문 제목 · 본문 앞부분) ──
-- p_kind: 경매 · 공매 · 비우면 전부. p_query: 항목 이름 일부(예: 임차인)
DROP FUNCTION IF EXISTS mart.q_auction_checklist(text, text);
CREATE FUNCTION mart.q_auction_checklist(p_kind text DEFAULT NULL, p_query text DEFAULT NULL)
RETURNS TABLE (step smallint, item text, what_to_check text, where_to_look text, applies_to text,
               law_name text, article_no text, article_title text, article_body text,
               source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    SELECT c.step, c.item, c.what_to_check, c.where_to_look, c.applies_to, c.law_name, c.article_no,
           l.article_title, left(l.body, 600),
           CASE WHEN l.article_id IS NOT NULL THEN c.source || ' · 법제처 국가법령정보 ' || c.law_name
                ELSE c.source END,
           greatest(c.asof, l.asof), count(*) OVER ()
    FROM core.ref_auction_check c
    LEFT JOIN LATERAL (
        SELECT d.article_id, d.article_title, d.body, d.asof FROM core.doc_law d
        WHERE d.law_name = c.law_name AND d.article_no = c.article_no AND d.is_current
        ORDER BY d.effective_date DESC LIMIT 1
    ) l ON true
    WHERE (p_kind IS NULL OR c.applies_to LIKE '%' || p_kind || '%')
      AND (p_query IS NULL OR replace(c.item || c.what_to_check, ' ', '') LIKE '%' || replace(p_query, ' ', '') || '%')
    ORDER BY c.step
$$;

ALTER FUNCTION mart.q_auction_discount(text, text, boolean, numeric, int, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_auction_discount(text, text, boolean, numeric, int, int) SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_auction_checklist(text, text) SECURITY DEFINER;
ALTER FUNCTION mart.q_auction_checklist(text, text) SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_auction_discount(text, text, boolean, numeric, int, int),
    mart.q_auction_checklist(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_auction_discount(text, text, boolean, numeric, int, int),
    mart.q_auction_checklist(text, text) TO hanpilji_gpt;
