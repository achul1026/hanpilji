-- 정책 축: 부동산 정책 · 규제 변화 목록과, 발표 전후 시장이 어떻게 달라졌는지 맞대 보는 분석
-- 정책 목록은 한필지가 정부 발표 · 보도를 보고 손으로 정리한 표(core.ref_policy_event). 줄마다 출처 주소를 둠
-- 기준금리 변경은 따로 적지 않고 ECOS 기준금리(core.fact_market base_rate)에서 값이 바뀐 달을 찾아 정책 목록에 함께 냄
-- 분석은 「전후 비교」일 뿐 인과가 아님: 같은 시기의 금리 · 다른 정책 · 계절이 함께 움직임. 그래서 겹친 정책을 늘 같이 보여 줌

CREATE TABLE IF NOT EXISTS core.ref_policy_event (
    event_id       text PRIMARY KEY,              -- 예) 2025-10-15 (발표일. 같은 날 둘이면 뒤에 -b)
    name           text NOT NULL,                 -- 예) 10·15 주택시장 안정화 대책
    announce_date  date NOT NULL,                 -- 발표일. 분석의 기준일(시장은 발표에 먼저 반응)
    effective_date date,                          -- 시행일(항목마다 다르거나 확인 못 했으면 비움)
    kind           text NOT NULL,                 -- 토지거래허가 · 규제지역 · 대출 · 세제 · 공급
    direction      text NOT NULL CHECK (direction IN ('강화', '완화', '공급')),
    region_scope   text NOT NULL,                 -- 사람이 읽는 적용 지역. 예) 서울 전역 · 경기 12곳
    sigungu        text[],                        -- 직접 적용되는 시군구 코드 5자리. 비우면 전국 · 수도권처럼 넓은 범위
    emd            text[],                        -- 일부 동만 적용될 때 그 동 이름. 예) {대치동,삼성동,청담동}
    summary        text NOT NULL,                 -- 무엇이 바뀌었나(한두 문장)
    law_name       text,                          -- 근거 법령(법제처 법령명 그대로)
    article_no     text,                          -- 「제10조」 형태
    source         text NOT NULL,                 -- 답에 적을 출처. 예) 금융위원회 보도자료
    source_url     text NOT NULL,
    asof           date NOT NULL
);

-- 2017년부터: R-ONE 가격지수가 2016-11부터라 그 앞 정책은 비교할 값이 없음. 실거래(강남구)는 2023-11부터
-- 날짜는 정부 발표 · 보도로 확인한 것만 적음. 시행일이 항목마다 다르면 가장 먼저 시행된 핵심 항목의 날짜
INSERT INTO core.ref_policy_event (event_id, name, announce_date, effective_date, kind, direction, region_scope, sigungu, emd,
                                   summary, law_name, article_no, source, source_url, asof) VALUES
 ('2017-08-02', '8·2 부동산 대책', '2017-08-02', '2017-08-03', '규제지역', '강화', '서울 전역 · 과천 · 세종(투기과열지구), 강남 4구 등(투기지역)',
  '{11680}', NULL,
  '서울 전역을 투기과열지구로, 강남구를 포함한 서울 11개 구와 세종을 투기지역으로 지정. 규제지역 LTV · DTI 40%로 강화',
  '주택법', '제63조', '뉴시스 보도(국토교통부 발표)', 'https://www.newsis.com/view/NISX20170810_0000065218', '2026-10-29'),
 ('2018-09-13', '9·13 주택시장 안정 대책', '2018-09-13', NULL, '대출', '강화', '규제지역(서울 전역 포함)',
  '{11680}', NULL,
  '규제지역에서 집이 있는 사람의 새 주택 구입용 주담대를 원칙적으로 막고, 종합부동산세 세율 · 세부담 상한을 올림',
  NULL, NULL, '뉴시스 보도(관계부처 합동 발표)', 'https://www.newsis.com/view/NISX20180913_0000418291', '2026-10-29'),
 ('2019-12-16', '12·16 주택시장 안정화 방안', '2019-12-16', '2019-12-17', '대출', '강화', '투기지역 · 투기과열지구',
  '{11680}', NULL,
  '투기지역 · 투기과열지구에서 시가 15억 원 넘는 아파트 주담대를 금지하고, 9억 원 넘는 부분의 LTV를 20%로 낮춤',
  NULL, NULL, '데일리안 보도(관계부처 합동 발표)', 'https://www.dailian.co.kr/news/view/853146/', '2026-10-29'),
 ('2020-06-17', '6·17 대책: 잠실 · 삼성 · 대치 · 청담 토지거래허가구역 지정', '2020-06-17', '2020-06-23', '토지거래허가', '강화',
  '강남구 대치동 · 삼성동 · 청담동, 송파구 잠실동', '{11680}', '{대치동,삼성동,청담동}',
  '국제교류복합지구 인근 4개 동을 토지거래허가구역으로 지정. 주거용은 허가를 받고 2년 실거주해야 함',
  '부동산 거래신고 등에 관한 법률', '제10조', '파이낸셜뉴스 보도(국토교통부 발표)', 'https://www.fnnews.com/news/202006231723097168', '2026-10-29'),
 ('2023-01-03', '규제지역 해제(강남 · 서초 · 송파 · 용산 제외)', '2023-01-03', '2023-01-05', '규제지역', '완화',
  '서울 강남 · 서초 · 송파 · 용산을 뺀 전 지역', NULL, NULL,
  '강남 · 서초 · 송파 · 용산을 뺀 서울 전 지역을 투기과열지구 · 조정대상지역 · 투기지역에서 해제. 강남구는 규제가 그대로 남음',
  '주택법', '제63조의2', '뉴시스 보도(국토교통부 발표)', 'https://mobile.newsis.com/view/NISX20230103_0002146183', '2026-10-29'),
 ('2024-09-01', '스트레스 DSR 2단계 시행', '2024-09-01', '2024-09-01', '대출', '강화', '전국(수도권 주담대는 가산 금리 더 높게)',
  NULL, NULL,
  'DSR을 셀 때 미래 금리 상승분(스트레스 금리)을 더하는 범위를 은행 신용대출 · 2금융권 주담대로 넓힘. 대출 한도가 줄어듦',
  NULL, NULL, 'SBS Biz 보도(금융위원회 발표)', 'https://biz.sbs.co.kr/amp/article/20000177947', '2026-10-29'),
 ('2025-02-12', '잠실 · 삼성 · 대치 · 청담 토지거래허가구역 해제', '2025-02-12', '2025-02-13', '토지거래허가', '완화',
  '강남구 대치동 · 삼성동 · 청담동, 송파구 잠실동 아파트 291곳', '{11680}', '{대치동,삼성동,청담동}',
  '서울시가 4개 동 아파트 305곳 중 291곳의 토지거래허가구역을 해제. 은마 · 개포우성 등 재건축 14곳은 유지',
  '부동산 거래신고 등에 관한 법률', '제10조', '뉴시스 보도(서울시 발표)', 'https://www.newsis.com/view/NISX20250212_0003063014', '2026-10-29'),
 ('2025-03-19', '강남 3구 · 용산구 아파트 토지거래허가구역 지정', '2025-03-19', '2025-03-24', '토지거래허가', '강화',
  '강남구 · 서초구 · 송파구 · 용산구 아파트 전체', '{11680}', NULL,
  '해제 35일 만에 4개 구 아파트 약 2,200단지 · 40만 호를 토지거래허가구역으로 지정(3월 24일 ~ 9월 30일, 뒤에 2026년 말까지 연장)',
  '부동산 거래신고 등에 관한 법률', '제10조', '뉴시스 보도(국토교통부 · 서울시 발표)', 'https://www.newsis.com/view/NISX20250319_0003104417', '2026-10-29'),
 ('2025-06-27', '6·27 가계부채 관리 강화 방안', '2025-06-27', '2025-06-28', '대출', '강화', '수도권 · 규제지역',
  NULL, NULL,
  '수도권 · 규제지역 주택구입용 주담대를 1인당 최대 6억 원으로 묶고, 주담대를 받으면 6개월 안에 전입하게 함',
  NULL, NULL, '금융위원회 보도자료', 'https://www.fsc.go.kr/edu/news/84845', '2026-10-29'),
 ('2025-07-01', '스트레스 DSR 3단계 시행', '2025-07-01', '2025-07-01', '대출', '강화', '전국',
  NULL, NULL,
  '스트레스 금리 1.5%를 사실상 모든 가계대출의 DSR 계산에 적용',
  NULL, NULL, '블록미디어 보도(금융위원회 발표)', 'https://www.blockmedia.co.kr/archives/909908', '2026-10-29'),
 ('2025-09-07', '9·7 주택공급 확대 방안(규제지역 LTV 40%)', '2025-09-07', '2025-09-08', '대출', '강화', '규제지역 · 수도권',
  '{11680}', NULL,
  '수도권 135만 호 착공 계획과 함께 규제지역 주담대 LTV를 50%에서 40%로 낮추고, 주택매매 · 임대사업자 주담대를 막음',
  NULL, NULL, '금융위원회 보도자료', 'https://www.fsc.go.kr/edu/news/85343', '2026-10-29'),
 ('2025-10-15', '10·15 주택시장 안정화 대책', '2025-10-15', '2025-10-16', '규제지역', '강화', '서울 전역 · 경기 12곳',
  '{11680}', NULL,
  '서울 전역 · 경기 12곳을 조정대상지역 · 투기과열지구 · 토지거래허가구역(10월 20일부터)으로 지정. 시가 15억 원 넘는 주택 주담대 한도를 4억 원(25억 원 초과는 2억 원)으로 낮춤',
  '부동산 거래신고 등에 관한 법률', '제10조', '금융위원회 보도자료', 'https://www.fsc.go.kr/edu/news/85498', '2026-10-29'),
 ('2026-02-12', '다주택자 양도세 중과 유예 종료 발표', '2026-02-12', '2026-05-10', '세제', '강화', '조정대상지역',
  '{11680}', NULL,
  '조정대상지역 다주택자 양도세 중과 유예를 2026년 5월 9일에 예정대로 끝냄. 5월 10일 양도분부터 2주택 +20%p, 3주택 이상 +30%p',
  NULL, NULL, '대한민국 정책브리핑', 'https://www.korea.kr/news/policyNewsView.do?newsId=148959488', '2026-10-29'),
 ('2026-08-13', '8·13 주택 신속공급 방안', '2026-08-13', NULL, '공급', '공급', '수도권',
  NULL, NULL,
  '수도권에 신규 공공주택지구 약 10만 호를 포함한 23만 호 이상 추가 공급 계획. 정비사업 · 비아파트 공급에 세제 · 금융 지원',
  NULL, NULL, '파이낸셜뉴스 보도(국토교통부 발표)', 'https://www.fnnews.com/news/202608161823343393', '2026-10-29')
ON CONFLICT (event_id) DO UPDATE SET name = excluded.name, announce_date = excluded.announce_date,
    effective_date = excluded.effective_date, kind = excluded.kind, direction = excluded.direction,
    region_scope = excluded.region_scope, sigungu = excluded.sigungu, emd = excluded.emd, summary = excluded.summary,
    law_name = excluded.law_name, article_no = excluded.article_no, source = excluded.source,
    source_url = excluded.source_url, asof = excluded.asof;

-- 정책 목록 + 기준금리가 바뀐 달. 금리는 월 단위 값이라 기준일을 그달 1일로 둠(정확한 결정일은 한국은행 발표)
CREATE OR REPLACE VIEW core.v_policy_event AS
    SELECT e.event_id, e.name, e.announce_date, e.effective_date, e.kind, e.direction, e.region_scope, e.sigungu, e.emd,
           e.summary, e.law_name, e.article_no, e.source, e.source_url, e.asof
    FROM core.ref_policy_event e
    UNION ALL
    SELECT 'rate-' || r.period,
           '기준금리 ' || CASE WHEN r.value > r.prev THEN '인상' ELSE '인하' END || ' ' || r.prev::float8 || '% → ' || r.value::float8 || '%',
           to_date(r.period, 'YYYYMM'), NULL, '금리', CASE WHEN r.value > r.prev THEN '강화' ELSE '완화' END, '전국', NULL, NULL,
           '한국은행 기준금리가 ' || to_char(to_date(r.period, 'YYYYMM'), 'YYYY"년" FMMM"월"') || ' ' ||
               abs(r.value - r.prev)::float8 || '%p ' || CASE WHEN r.value > r.prev THEN '올랐음' ELSE '내렸음' END || '(월 단위 값 기준)',
           NULL, NULL, r.source, 'https://ecos.bok.or.kr/', r.asof
    FROM (SELECT m.period, m.value, lag(m.value) OVER (ORDER BY m.period) AS prev, s.source, m.asof
          FROM core.fact_market m JOIN core.dim_stat s USING (stat_id)
          WHERE m.stat_id = 'base_rate' AND m.lawd_cd = '0000000000') r
    WHERE r.prev IS NOT NULL AND r.value <> r.prev;

-- ── 정책 목록: 무엇이 언제 바뀌었나 ──
-- p_query: 이름 · 내용 · 지역 일부(예: 토지거래허가, 대출, 10·15). p_kind: 토지거래허가 · 규제지역 · 대출 · 세제 · 공급 · 금리
-- 근거 조문이 수집돼 있으면 조문 제목을 붙임. 최근 것부터 최대 30줄
DROP FUNCTION IF EXISTS mart.q_policy_events(text, text, date);
CREATE FUNCTION mart.q_policy_events(p_query text DEFAULT NULL, p_kind text DEFAULT NULL, p_from date DEFAULT NULL)
RETURNS TABLE (event_id text, name text, announce_date date, effective_date date, kind text, direction text,
               region_scope text, summary text, law_name text, article_no text, article_title text,
               source_url text, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    SELECT e.event_id, e.name, e.announce_date, e.effective_date, e.kind, e.direction, e.region_scope, e.summary,
           e.law_name, e.article_no, l.article_title, e.source_url,
           e.source || CASE WHEN l.article_title IS NOT NULL THEN ' · 법제처 국가법령정보 ' || e.law_name ELSE '' END,
           e.asof, count(*) OVER ()
    FROM core.v_policy_event e
    LEFT JOIN LATERAL (
        SELECT d.article_title FROM core.doc_law d
        WHERE d.law_name = e.law_name AND d.article_no = e.article_no AND d.is_current
        ORDER BY d.effective_date DESC LIMIT 1
    ) l ON true
    WHERE (p_kind IS NULL OR e.kind = p_kind)
      AND (p_from IS NULL OR e.announce_date >= p_from)
      AND (p_query IS NULL OR replace(replace(concat_ws(' ', e.event_id, e.name, e.summary, e.region_scope, e.kind), ' ', ''), '.', '·')
                              LIKE '%' || replace(replace(p_query, ' ', ''), '.', '·') || '%')
    ORDER BY e.announce_date DESC, e.event_id
    LIMIT 40
$$;

-- ── 정책 전후 비교: 발표일 앞뒤 같은 길이의 기간을 맞댐 ──
-- 정책은 event_id(예: 2025-10-15, rate-202410) 또는 이름 일부(예: 10·15)로 고름. 여럿 맞으면 가장 최근 것
-- 줄 묶음(section)
--   정책        : 고른 정책 한 줄(이름 · 날짜 · 내용 · 출처)
--   실거래      : 구 아파트 매매 · 전세의 30일 환산 건수와 ㎡당 중위 단가. 발표 전 p_days일 대 발표 뒤 p_days일
--   같은 단지   : 두 기간 모두 거래된 「같은 단지 · 같은 전용면적(㎡ 반올림)」끼리 단가 변화율의 중위값(단지 구성이 바뀌어 생기는 착시를 덜어 냄)
--   지정 동     : 정책이 일부 동에만 적용될 때(예: 대치 · 삼성 · 청담) 그 동과 나머지 동을 따로
--   가격지수    : R-ONE 아파트 매매 · 전세가격지수. 발표 전달을 기준으로 직전 p_months개월과 이후 p_months개월 변화율, 서울 · 전국과 비교
--   금리        : 발표 전달과 p_months개월 뒤의 기준금리 · 주담대 금리
--   겹친 정책   : 앞뒤 p_days일 안에 다른 정책 · 금리 변경이 있었는지(있으면 효과를 나눠 말할 수 없음)
--   그래프      : 화면 전용(screen_only) 월별 지수 · 건수. 모델에게는 넘기지 않고 row_total에도 넣지 않음
-- partial: 뒤 기간이 아직 다 지나지 않았거나 실거래 신고 기한(30일)이 안 지나 값이 더 바뀔 수 있음
-- 실거래 범위 밖(2023-11 이전 등)이면 실거래 줄 값을 비우고 note에 이유를 적음
DROP FUNCTION IF EXISTS mart.q_policy_impact(text, text, int, int);
CREATE FUNCTION mart.q_policy_impact(p_event text, p_sigungu text DEFAULT '11680', p_days int DEFAULT 90, p_months int DEFAULT 6)
RETURNS TABLE (section text, metric text, scope text, before_period text, before_value numeric,
               after_period text, after_value numeric, change numeric, change_pct numeric, unit text,
               n_before bigint, n_after bigint, partial boolean, note text, screen_only boolean,
               event_id text, event_name text, base_date date, source text, asof date, row_total bigint)
LANGUAGE sql STABLE AS $$
    WITH ev AS (
        SELECT e.* FROM core.v_policy_event e
        WHERE e.event_id = p_event
           OR replace(replace(e.name, ' ', ''), '.', '·') LIKE '%' || replace(replace(p_event, ' ', ''), '.', '·') || '%'
        ORDER BY (e.event_id = p_event) DESC, e.announce_date DESC LIMIT 1
    ), prm AS (
        SELECT greatest(30, least(coalesce(p_days, 90), 180)) AS days, greatest(1, least(coalesce(p_months, 6), 24)) AS months
    ), w AS (
        SELECT ev.announce_date AS d0, ev.announce_date - prm.days AS b_from, ev.announce_date + prm.days AS a_to,
               prm.days, prm.months,
               (date_trunc('month', ev.announce_date) - interval '1 month')::date AS m_base,
               (SELECT max(d.asof) FROM core.fact_deal d WHERE left(d.lawd_cd, 5) = p_sigungu) AS deal_asof,
               (SELECT min(d.contract_date) FROM core.fact_deal d WHERE left(d.lawd_cd, 5) = p_sigungu) AS deal_min,
               (SELECT sigungu FROM core.dim_region WHERE lawd_cd = p_sigungu || '00000') AS sgg_name
        FROM ev, prm
    ), cov AS (   -- 실거래로 비교할 수 있나, 뒤 기간이 며칠 지났나
        SELECT w.*, w.deal_min IS NOT NULL AND w.b_from >= w.deal_min AND w.d0 <= w.deal_asof AS ok,
               greatest(0, least(w.a_to, w.deal_asof) - w.d0) AS a_days,
               w.a_to + 30 > coalesce(w.deal_asof, current_date) AS part
        FROM w
    ), area AS (   -- 지정 동에 속한 단지
        SELECT x.complex_id FROM core.dim_complex x JOIN core.dim_region g ON g.lawd_cd = x.lawd_cd, ev
        WHERE ev.emd IS NOT NULL AND left(x.lawd_cd, 5) = p_sigungu AND g.emd = ANY (ev.emd)
    ), dl AS (
        SELECT CASE WHEN d.contract_date < cov.d0 THEN 'b' ELSE 'a' END AS p, d.complex_id, round(d.area) AS a,
               d.amount / d.area AS u, d.complex_id IN (SELECT complex_id FROM area) AS in_area, d.asof
        FROM core.fact_deal d, cov
        WHERE cov.ok AND NOT d.is_cancelled AND d.deal_type = '아파트' AND left(d.lawd_cd, 5) = p_sigungu AND d.area > 0
          AND d.contract_date >= cov.b_from AND d.contract_date < least(cov.a_to, cov.deal_asof + 1)
    ), rl AS (
        SELECT CASE WHEN e.contract_date < cov.d0 THEN 'b' ELSE 'a' END AS p, e.deposit / e.area AS u, e.asof
        FROM core.fact_rent e, cov
        WHERE cov.ok AND e.rent_type = '아파트' AND e.monthly_rent = 0 AND e.deposit > 0 AND e.area > 0
          AND left(e.lawd_cd, 5) = p_sigungu
          AND e.contract_date >= cov.b_from AND e.contract_date < least(cov.a_to, cov.deal_asof + 1)
    ), agg AS (   -- 묶음별 건수 · 중위 단가
        SELECT g.grp, g.p, count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY g.u) AS med, max(g.asof) AS asof
        FROM (SELECT 'deal' AS grp, p, u, asof FROM dl
              UNION ALL SELECT 'rent', p, u, asof FROM rl
              UNION ALL SELECT 'deal_in', p, u, asof FROM dl WHERE in_area
              UNION ALL SELECT 'deal_out', p, u, asof FROM dl WHERE NOT in_area AND EXISTS (SELECT 1 FROM area)) g
        GROUP BY 1, 2
    ), pair AS (   -- 같은 단지 · 같은 면적의 전후 중위 단가
        SELECT complex_id, a,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY u) FILTER (WHERE p = 'b') AS mb,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY u) FILTER (WHERE p = 'a') AS ma
        FROM dl GROUP BY 1, 2
    ), ix AS (   -- 지수 · 금리 값(기준월 앞뒤)
        SELECT m.stat_id, m.lawd_cd, to_date(m.period, 'YYYYMM') AS m, m.value
        FROM core.fact_market m, w
        WHERE m.stat_id IN ('apt_sale_idx', 'apt_jeonse_idx', 'base_rate', 'mortgage_rate')
          AND m.lawd_cd IN ('0000000000', '1100000000', p_sigungu || '00000')
          AND to_date(m.period, 'YYYYMM') BETWEEN w.m_base - make_interval(months => w.months * 2)
                                              AND w.m_base + make_interval(months => w.months * 2)
    ), ixr AS (   -- 지표 x 지역마다 직전(pre) · 기준(base) · 이후(post) 값. 이후 달 값이 아직 없으면 있는 마지막 달
        SELECT s.stat_id, s.lawd_cd, s.name AS stat_name, s.unit AS stat_unit, s.source AS stat_source,
               coalesce(g.sigungu, g.sido) AS region,
               (SELECT x.value FROM ix x WHERE x.stat_id = s.stat_id AND x.lawd_cd = s.lawd_cd
                  AND x.m = w.m_base - make_interval(months => w.months)) AS v_pre,
               (SELECT x.value FROM ix x WHERE x.stat_id = s.stat_id AND x.lawd_cd = s.lawd_cd AND x.m = w.m_base) AS v_base,
               (SELECT x.m FROM ix x WHERE x.stat_id = s.stat_id AND x.lawd_cd = s.lawd_cd AND x.m > w.m_base
                  AND x.m <= w.m_base + make_interval(months => w.months) ORDER BY x.m DESC LIMIT 1) AS m_post,
               w.m_base, w.months
        FROM (SELECT DISTINCT ix.stat_id, ix.lawd_cd, d.name, d.unit, d.source FROM ix JOIN core.dim_stat d USING (stat_id)) s
        JOIN core.dim_region g ON g.lawd_cd = s.lawd_cd, w
    ), ixv AS (
        SELECT ixr.*, (SELECT x.value FROM ix x WHERE x.stat_id = ixr.stat_id AND x.lawd_cd = ixr.lawd_cd AND x.m = ixr.m_post) AS v_post,
               (SELECT max(m.asof) FROM core.fact_market m WHERE m.stat_id = ixr.stat_id) AS asof
        FROM ixr
    ), ov AS (
        SELECT string_agg(o.name || '(' || to_char(o.announce_date, 'YYYY-MM-DD') || ')', ', ' ORDER BY o.announce_date) AS names,
               count(*) AS n
        FROM core.v_policy_event o, ev, w
        WHERE o.event_id <> ev.event_id AND o.announce_date > w.b_from AND o.announce_date < w.a_to
          AND (o.sigungu IS NULL OR p_sigungu = ANY (o.sigungu))   -- 다른 구에만 적용된 정책은 뺌
    ), out AS (
        -- 정책
        SELECT 1 AS ord, '정책' AS section, ev.name AS metric, ev.region_scope AS scope,
               NULL::text AS bp, NULL::numeric AS bv, NULL::text AS ap, NULL::numeric AS av, NULL::numeric AS ch,
               NULL::numeric AS chp, NULL::text AS unit, NULL::bigint AS nb, NULL::bigint AS na, false AS part,
               concat_ws(' ', ev.kind || CASE WHEN ev.direction <> ev.kind THEN ' ' || ev.direction ELSE '' END || '.', ev.summary,
                         '발표 ' || to_char(ev.announce_date, 'YYYY-MM-DD'),
                         CASE WHEN ev.effective_date IS NOT NULL THEN '· 시행 ' || to_char(ev.effective_date, 'YYYY-MM-DD') END,
                         '· ' || ev.source_url) AS note,
               false AS so, ev.source AS src, ev.asof AS asof
        FROM ev
        UNION ALL
        -- 실거래: 건수(30일 환산) · ㎡당 중위
        SELECT 10 + k.o, k.sec, k.metric, k.scope,
               to_char(cov.b_from, 'YYYY-MM-DD') || '~' || to_char(cov.d0 - 1, 'YYYY-MM-DD'),
               k.vb, to_char(cov.d0, 'YYYY-MM-DD') || '~' || to_char(least(cov.a_to, cov.deal_asof + 1) - 1, 'YYYY-MM-DD'),
               k.va, CASE WHEN k.is_rate THEN round(k.va - k.vb, 1) ELSE round(k.va - k.vb) END,
               round((k.va / nullif(k.vb, 0) - 1) * 100, 1), k.unit, k.nb, k.na, cov.part,
               CASE WHEN NOT cov.ok THEN '실거래 데이터가 ' || coalesce(to_char(cov.deal_min, 'YYYY-MM-DD'), '-') || ' ~ '
                                        || coalesce(to_char(cov.deal_asof, 'YYYY-MM-DD'), '-') || ' 범위라 앞뒤 ' || cov.days || '일을 비교할 수 없음'
                    WHEN cov.a_days < cov.days THEN '발표 뒤 ' || cov.a_days || '일만 지나 그 기간으로 환산. 신고 기한(계약 후 30일)이 안 지난 거래가 더 들어와 건수가 늘 수 있음'
                    WHEN cov.part THEN '신고 기한(계약 후 30일)이 안 지난 거래가 더 들어올 수 있음' END,
               false, '국토교통부 아파트 매매 · 전월세 실거래가', (SELECT max(asof) FROM agg)
        FROM cov, LATERAL (
            SELECT 1 AS o, '실거래' AS sec, '아파트 매매 건수(30일 환산)' AS metric, cov.sgg_name AS scope,
                   round(b.n * 30.0 / cov.days, 1) AS vb, round(a.n * 30.0 / nullif(cov.a_days, 0), 1) AS va,
                   '건' AS unit, b.n AS nb, a.n AS na, true AS is_rate
            FROM (SELECT 1) z LEFT JOIN agg b ON b.grp = 'deal' AND b.p = 'b' LEFT JOIN agg a ON a.grp = 'deal' AND a.p = 'a'
            UNION ALL
            SELECT 2, '실거래', '아파트 매매 ㎡당 중위 단가', cov.sgg_name, round(b.med::numeric), round(a.med::numeric), '원/㎡', b.n, a.n, false
            FROM (SELECT 1) z LEFT JOIN agg b ON b.grp = 'deal' AND b.p = 'b' LEFT JOIN agg a ON a.grp = 'deal' AND a.p = 'a'
            UNION ALL
            SELECT 3, '실거래', '아파트 전세 건수(30일 환산)', cov.sgg_name, round(b.n * 30.0 / cov.days, 1),
                   round(a.n * 30.0 / nullif(cov.a_days, 0), 1), '건', b.n, a.n, true
            FROM (SELECT 1) z LEFT JOIN agg b ON b.grp = 'rent' AND b.p = 'b' LEFT JOIN agg a ON a.grp = 'rent' AND a.p = 'a'
            UNION ALL
            SELECT 4, '실거래', '아파트 전세 ㎡당 중위 보증금', cov.sgg_name, round(b.med::numeric), round(a.med::numeric), '원/㎡', b.n, a.n, false
            FROM (SELECT 1) z LEFT JOIN agg b ON b.grp = 'rent' AND b.p = 'b' LEFT JOIN agg a ON a.grp = 'rent' AND a.p = 'a'
            UNION ALL
            SELECT 6 + j.o, '지정 동', j.metric, j.scope, j.vb, j.va, j.unit, j.nb, j.na, j.is_rate
            FROM (SELECT 1 AS o, '아파트 매매 건수(30일 환산)' AS metric, array_to_string(ev.emd, ' · ') AS scope,
                         round(b.n * 30.0 / cov.days, 1) AS vb, round(a.n * 30.0 / nullif(cov.a_days, 0), 1) AS va,
                         '건' AS unit, b.n AS nb, a.n AS na, true AS is_rate
                  FROM ev LEFT JOIN agg b ON b.grp = 'deal_in' AND b.p = 'b' LEFT JOIN agg a ON a.grp = 'deal_in' AND a.p = 'a'
                  WHERE ev.emd IS NOT NULL
                  UNION ALL
                  SELECT 2, '아파트 매매 ㎡당 중위 단가', array_to_string(ev.emd, ' · '), round(b.med::numeric), round(a.med::numeric),
                         '원/㎡', b.n, a.n, false
                  FROM ev LEFT JOIN agg b ON b.grp = 'deal_in' AND b.p = 'b' LEFT JOIN agg a ON a.grp = 'deal_in' AND a.p = 'a'
                  WHERE ev.emd IS NOT NULL
                  UNION ALL
                  SELECT 3, '아파트 매매 건수(30일 환산)', '그 밖의 ' || cov.sgg_name, round(b.n * 30.0 / cov.days, 1),
                         round(a.n * 30.0 / nullif(cov.a_days, 0), 1), '건', b.n, a.n, true
                  FROM ev LEFT JOIN agg b ON b.grp = 'deal_out' AND b.p = 'b' LEFT JOIN agg a ON a.grp = 'deal_out' AND a.p = 'a'
                  WHERE ev.emd IS NOT NULL
                  UNION ALL
                  SELECT 4, '아파트 매매 ㎡당 중위 단가', '그 밖의 ' || cov.sgg_name, round(b.med::numeric), round(a.med::numeric),
                         '원/㎡', b.n, a.n, false
                  FROM ev LEFT JOIN agg b ON b.grp = 'deal_out' AND b.p = 'b' LEFT JOIN agg a ON a.grp = 'deal_out' AND a.p = 'a'
                  WHERE ev.emd IS NOT NULL) j
        ) k
        UNION ALL
        -- 같은 단지 · 같은 면적
        SELECT 15, '같은 단지', '같은 단지 · 같은 면적 매매 단가 변화(중위)', cov.sgg_name,
               to_char(cov.b_from, 'YYYY-MM-DD') || '~' || to_char(cov.d0 - 1, 'YYYY-MM-DD'), NULL,
               to_char(cov.d0, 'YYYY-MM-DD') || '~' || to_char(least(cov.a_to, cov.deal_asof + 1) - 1, 'YYYY-MM-DD'), NULL, NULL,
               round(((percentile_cont(0.5) WITHIN GROUP (ORDER BY pair.ma / pair.mb) - 1) * 100)::numeric, 1), '%',
               count(pair.complex_id), count(pair.complex_id), cov.part,
               CASE WHEN NOT cov.ok THEN '실거래 범위 밖'
                    WHEN count(pair.complex_id) < 10 THEN '비교할 단지 · 면적 쌍이 ' || count(pair.complex_id) || '개뿐이라 흔들릴 수 있음'
                    ELSE '두 기간 모두 거래된 단지 · 면적 ' || count(pair.complex_id) || '쌍의 단가 변화율 중위값' END,
               false, '국토교통부 아파트 매매 실거래가', (SELECT max(asof) FROM agg)
        FROM cov LEFT JOIN pair ON pair.mb > 0 AND pair.ma > 0
        GROUP BY cov.sgg_name, cov.b_from, cov.d0, cov.a_to, cov.deal_asof, cov.part, cov.ok
        UNION ALL
        -- 가격지수: 직전 · 이후 변화율
        SELECT 20 + CASE x.stat_id WHEN 'apt_sale_idx' THEN 0 ELSE 10 END
                  + CASE x.lawd_cd WHEN '0000000000' THEN 4 WHEN '1100000000' THEN 2 ELSE 0 END + t.o,
               '가격지수', x.stat_name || ' ' || t.lbl, x.region,
               to_char(t.m_from, 'YYYYMM'), round(t.v_from, 2), to_char(t.m_to, 'YYYYMM'), round(t.v_to, 2),
               round(t.v_to - t.v_from, 2), round((t.v_to / nullif(t.v_from, 0) - 1) * 100, 2), '지수', NULL, NULL,
               t.is_post AND (t.m_to IS NULL OR t.m_to < x.m_base + make_interval(months => x.months)),
               CASE WHEN t.is_post AND t.m_to IS NULL THEN '발표 뒤 지수가 아직 없음'
                    WHEN t.is_post AND t.m_to < x.m_base + make_interval(months => x.months)
                         THEN '이후 ' || x.months || '개월이 다 차지 않아 ' || to_char(t.m_to, 'YYYYMM') || '까지' END,
               false, x.stat_source, x.asof
        FROM ixv x, LATERAL (
            SELECT 0 AS o, '직전 ' || x.months || '개월' AS lbl, x.m_base - make_interval(months => x.months) AS m_from, x.v_pre AS v_from,
                   x.m_base AS m_to, x.v_base AS v_to, false AS is_post
            UNION ALL
            SELECT 1, '이후 ' || x.months || '개월', x.m_base, x.v_base, x.m_post, x.v_post, true
        ) t
        WHERE x.stat_id IN ('apt_sale_idx', 'apt_jeonse_idx') AND (x.stat_id = 'apt_sale_idx' OR x.lawd_cd <> '0000000000')
        UNION ALL
        -- 가격지수: 구 - 서울 상승률 차(%p). 직전과 이후를 함께 보면 정책 뒤 격차가 달라졌는지 보임
        SELECT 40 + t.o, '가격지수', '매매가격지수 ' || t.lbl || ' 상승률 차(' || g.region || ' - 서울)', g.region,
               NULL, NULL, NULL, NULL,
               round((t.g_chg - t.s_chg) * 100, 2), NULL, '%p', NULL, NULL,
               t.is_post AND (g.m_post IS NULL OR g.m_post < g.m_base + make_interval(months => g.months)),
               '같은 기간 서울 전체보다 얼마나 더(덜) 올랐나', false, g.stat_source, g.asof
        FROM ixv g JOIN ixv s ON s.stat_id = g.stat_id AND s.lawd_cd = '1100000000', LATERAL (
            SELECT 1 AS o, '직전 ' || g.months || '개월' AS lbl, g.v_base / nullif(g.v_pre, 0) - 1 AS g_chg,
                   s.v_base / nullif(s.v_pre, 0) - 1 AS s_chg, false AS is_post
            UNION ALL
            SELECT 2, '이후 ' || g.months || '개월', g.v_post / nullif(g.v_base, 0) - 1, s.v_post / nullif(s.v_base, 0) - 1, true
        ) t
        WHERE g.stat_id = 'apt_sale_idx' AND g.lawd_cd = p_sigungu || '00000'
        UNION ALL
        -- 금리
        SELECT 50 + CASE x.stat_id WHEN 'base_rate' THEN 0 ELSE 1 END, '금리', x.stat_name, x.region,
               to_char(x.m_base, 'YYYYMM'), x.v_base, to_char(x.m_post, 'YYYYMM'), x.v_post,
               round(x.v_post - x.v_base, 2), NULL, '%', NULL, NULL,
               x.m_post IS NULL OR x.m_post < x.m_base + make_interval(months => x.months),
               '차이는 %p', false, x.stat_source, x.asof
        FROM ixv x WHERE x.stat_id IN ('base_rate', 'mortgage_rate')
        UNION ALL
        -- 겹친 정책
        SELECT 60, '겹친 정책', '앞뒤 ' || w.days || '일 안의 다른 정책 · 금리 변경', NULL, NULL, NULL, NULL, NULL,
               ov.n, NULL, '건', NULL, NULL, false,
               coalesce(ov.names, '없음') || CASE WHEN ov.n > 0 THEN '. 이 변화들이 함께 섞여 있어 이 정책만의 효과로 나눌 수 없음' ELSE '' END,
               false, '한필지 정책 목록', NULL
        FROM ov, w
        UNION ALL
        -- 그래프(화면 전용): 월별 매매가격지수(기준월 = 100) · 구 아파트 매매 건수
        SELECT 90, '그래프', coalesce(g.sigungu, g.sido) || ' 매매가격지수(발표 전달 = 100)', NULL,
               to_char(x.m, 'YYYYMM'), round(x.value / nullif(b.value, 0) * 100, 2), NULL, NULL, NULL, NULL, '지수', NULL, NULL,
               false, NULL, true, NULL, NULL
        FROM ix x JOIN core.dim_region g ON g.lawd_cd = x.lawd_cd, w
        JOIN ix b ON b.m = w.m_base
        WHERE x.stat_id = 'apt_sale_idx' AND b.stat_id = 'apt_sale_idx' AND b.lawd_cd = x.lawd_cd
        UNION ALL
        SELECT 91, '그래프', cov.sgg_name || ' 아파트 매매 건수', NULL, to_char(mm.m, 'YYYYMM'), count(d.deal_id), NULL, NULL,
               NULL, NULL, '건', NULL, NULL, (mm.m + interval '1 month' + interval '30 days')::date > coalesce(cov.deal_asof, current_date),
               NULL, true, NULL, NULL
        FROM cov
        CROSS JOIN LATERAL generate_series(greatest(date_trunc('month', cov.deal_min), cov.m_base - make_interval(months => cov.months * 2)),
                                           least(date_trunc('month', cov.deal_asof), cov.m_base + make_interval(months => cov.months * 2)),
                                           interval '1 month') AS mm(m)
        LEFT JOIN core.fact_deal d ON NOT d.is_cancelled AND d.deal_type = '아파트' AND left(d.lawd_cd, 5) = p_sigungu
             AND d.contract_date >= mm.m AND d.contract_date < mm.m + interval '1 month'
        WHERE cov.deal_min IS NOT NULL
        GROUP BY mm.m, cov.sgg_name, cov.deal_asof
    )
    SELECT o.section, o.metric, o.scope, o.bp, o.bv, o.ap, o.av, o.ch, o.chp, o.unit, o.nb, o.na, o.part, o.note, o.so,
           -- 정책 이름 · 기준일은 첫 줄에만(줄마다 되풀이하면 토큰 낭비)
           CASE WHEN o.ord = 1 THEN ev.event_id END, CASE WHEN o.ord = 1 THEN ev.name END,
           CASE WHEN o.ord = 1 THEN ev.announce_date END, o.src, o.asof,
           count(*) FILTER (WHERE NOT o.so) OVER ()   -- 그래프 줄은 세지 않음(모델에게 안 넘김)
    FROM out o, ev
    ORDER BY o.ord, o.metric, o.bp
$$;

ALTER FUNCTION mart.q_policy_events(text, text, date) SECURITY DEFINER;
ALTER FUNCTION mart.q_policy_events(text, text, date) SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_policy_impact(text, text, int, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_policy_impact(text, text, int, int) SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_policy_events(text, text, date), mart.q_policy_impact(text, text, int, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_policy_events(text, text, date), mart.q_policy_impact(text, text, int, int) TO hanpilji_gpt;
