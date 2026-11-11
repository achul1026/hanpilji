-- 부동산 지식 DB 3단계: 공인중개사 시험 목차(단원) → 법령 · 조문 연결
--   core.kb_topic     : 단원 하나 = 한 줄(과목 · 영역 · 단원 · 핵심 내용 · 채우는 방법 · 우선순위). 시험 과목은 목차로만 씀
--   core.kb_topic_law : 단원마다 근거 법령과 조문 범위(제n조~제m조, 비면 법령 전체). 범위를 조 번호로 못 적는 것은 scope_note에 글로
--   mart.q_kb_topics(과목)       : 단원 목록 + 지금 DB에 있는 현행 조문 수
--   mart.q_kb_topic_articles(단원) : 그 단원에 연결된 현행 조문 원문
-- 목차 원본: 공유 폴더 04_서비스개발(4단계)/부동산지식DB/원본/syllabus.csv(1단계 목차표). 다시 넣어도 같은 결과(멱등)
-- 조문은 법령 수집기(core.doc_law)가 받은 것을 이름 · 조 번호로 찾음. 아직 안 받은 법령은 조문 수 0으로 나옴

CREATE TABLE IF NOT EXISTS core.kb_topic (
    topic_id     text PRIMARY KEY,               -- 과목 기호 + 순번. G 개론 · M 민법 · J 중개사법 · P 공법 · R 공시법 · T 세법
    subject      text NOT NULL,                  -- 시험 과목
    area         text NOT NULL,                  -- 영역
    unit         text NOT NULL,                  -- 단원
    summary      text,                           -- 핵심 내용
    fill_method  text,                           -- 채우는 방법(법령 · 판례 · 공공자료 · 개념 노트 · 우리 데이터)
    priority     text,                           -- A · B · C
    sort_no      int NOT NULL,
    asof         date NOT NULL DEFAULT current_date
);
CREATE TABLE IF NOT EXISTS core.kb_topic_law (
    topic_id     text NOT NULL REFERENCES core.kb_topic ON DELETE CASCADE,
    law_name     text NOT NULL,                  -- core.doc_law.law_name과 같은 이름
    art_from     int,                            -- 조 번호(제n조의m도 n으로 셈). 둘 다 NULL이면 법령 전체
    art_to       int,
    scope_note   text,                           -- 조 번호로 못 적은 범위(예: 양도소득 부분). 지금은 법령 전체를 연결
    CHECK ((art_from IS NULL) = (art_to IS NULL) AND (art_from IS NULL OR art_from <= art_to))
);
CREATE INDEX IF NOT EXISTS kb_topic_law_topic_idx ON core.kb_topic_law (topic_id);

INSERT INTO core.kb_topic (topic_id, subject, area, unit, summary, fill_method, priority, sort_no) VALUES
('G01', '부동산학개론', '부동산학총론', '부동산의 개념과 분류', '복합개념(법률·경제·기술)', '개념 노트', 'C', 1),
('G02', '부동산학개론', '부동산학총론', '부동산의 특성', '자연적 특성(부동성·영속성 등) · 인문적 특성', '개념 노트', 'C', 2),
('G03', '부동산학개론', '부동산경제론', '수요와 공급', '수요·공급 변화와 탄력성', '개념 노트+우리 데이터', 'C', 3),
('G04', '부동산학개론', '부동산경제론', '부동산 가격의 형성과 균형', '균형가격·경기변동', '개념 노트+우리 데이터', 'C', 4),
('G05', '부동산학개론', '부동산시장론', '부동산시장', '시장의 특성·효율적 시장', '개념 노트+우리 데이터', 'C', 5),
('G06', '부동산학개론', '부동산시장론', '입지·도시공간구조 이론', '리카도·튀넨·알론소·베버 등', '개념 노트', 'C', 6),
('G07', '부동산학개론', '부동산정책론', '토지정책', '토지이용규제·토지거래허가', '법령+개념 노트', 'B', 7),
('G08', '부동산학개론', '부동산정책론', '주택정책', '공공임대·임대료 규제·분양가 상한', '법령+개념 노트', 'B', 8),
('G09', '부동산학개론', '부동산정책론', '부동산 조세정책', '취득·보유·양도 단계 세금', '법령+개념 노트', 'B', 9),
('G10', '부동산학개론', '부동산투자론', '투자분석과 화폐의 시간가치', '수익률·할인·현금흐름', '개념 노트', 'C', 10),
('G11', '부동산학개론', '부동산투자론', '포트폴리오 이론', '위험과 수익·분산투자', '개념 노트', 'C', 11),
('G12', '부동산학개론', '부동산금융론', '지분금융과 부채금융', '대출·LTV·DSR', '법령+공공자료', 'B', 12),
('G13', '부동산학개론', '부동산금융론', '저당 유동화와 리츠', 'MBS·리츠', '법령+개념 노트', 'B', 13),
('G14', '부동산학개론', '부동산개발 및 관리·마케팅론', '부동산 개발', '개발 단계·방식', '법령+개념 노트', 'B', 14),
('G15', '부동산학개론', '부동산개발 및 관리·마케팅론', '부동산 관리·마케팅', '관리 방식·마케팅 전략', '법령+개념 노트', 'C', 15),
('G16', '부동산학개론', '감정평가론', '감정평가 3방식', '비교방식·원가방식·수익방식', '법령', 'B', 16),
('G17', '부동산학개론', '감정평가론', '가격공시제도', '표준지·개별공시지가·주택가격 공시', '법령+우리 데이터', 'B', 17),
('M01', '민법 및 민사특별법', '민법총칙', '법률행위', '반사회질서·불공정 법률행위', '법령+판례', 'A', 18),
('M02', '민법 및 민사특별법', '민법총칙', '의사표시', '비진의·허위표시·착오·사기강박', '법령+판례', 'A', 19),
('M03', '민법 및 민사특별법', '민법총칙', '대리', '대리권·무권대리·표현대리', '법령+판례', 'A', 20),
('M04', '민법 및 민사특별법', '민법총칙', '무효와 취소·조건과 기한', '무효·취소·조건·기한', '법령', 'B', 21),
('M05', '민법 및 민사특별법', '물권법', '물권변동', '등기·점유', '법령+판례', 'A', 22),
('M06', '민법 및 민사특별법', '물권법', '점유권·소유권', '점유·상린관계·공동소유', '법령+판례', 'B', 23),
('M07', '민법 및 민사특별법', '물권법', '용익물권', '지상권·지역권·전세권', '법령+판례', 'A', 24),
('M08', '민법 및 민사특별법', '물권법', '담보물권', '유치권·저당권', '법령+판례', 'A', 25),
('M09', '민법 및 민사특별법', '계약법', '계약 총론', '계약 성립·해제·위험부담', '법령+판례', 'A', 26),
('M10', '민법 및 민사특별법', '계약법', '매매·교환', '계약금·담보책임', '법령+판례', 'A', 27),
('M11', '민법 및 민사특별법', '계약법', '임대차', '임대인·임차인 권리의무', '법령+판례', 'A', 28),
('M12', '민법 및 민사특별법', '민사특별법', '주택임대차보호법', '대항력·우선변제·계약갱신', '법령+판례+공공자료', 'A', 29),
('M13', '민법 및 민사특별법', '민사특별법', '상가건물 임대차보호법', '적용범위·권리금·갱신', '법령+판례', 'A', 30),
('M14', '민법 및 민사특별법', '민사특별법', '집합건물법', '구분소유·대지사용권·관리단', '법령', 'B', 31),
('M15', '민법 및 민사특별법', '민사특별법', '가등기담보·부동산실명법', '가등기담보·명의신탁', '법령+판례', 'B', 32),
('J01', '공인중개사법령 및 중개실무', '공인중개사법', '총칙·자격·개설등록', '중개대상물·결격사유·등록기준', '법령', 'A', 33),
('J02', '공인중개사법령 및 중개실무', '공인중개사법', '중개업무', '확인·설명·거래계약서·손해배상·중개보수', '법령+판례', 'A', 34),
('J03', '공인중개사법령 및 중개실무', '공인중개사법', '지도·감독과 벌칙', '행정처분·과태료', '법령', 'B', 35),
('J04', '공인중개사법령 및 중개실무', '부동산거래신고법', '부동산 거래신고·주택임대차계약 신고', '신고 기한·내용', '법령+공공자료', 'A', 36),
('J05', '공인중개사법령 및 중개실무', '부동산거래신고법', '외국인 특례·토지거래허가', '허가구역·허가 기준', '법령+우리 데이터', 'A', 37),
('J06', '공인중개사법령 및 중개실무', '중개실무', '중개대상물 조사·확인', '공부 확인·권리분석', '법령+우리 데이터', 'A', 38),
('J07', '공인중개사법령 및 중개실무', '중개실무', '경매·공매', '매수신청대리·권리분석', '법령+우리 데이터', 'B', 39),
('P01', '부동산공법', '국토계획법', '용도지역·지구·구역', '행위제한·건폐율·용적률', '법령+우리 데이터', 'A', 40),
('P02', '부동산공법', '국토계획법', '도시·군계획과 개발행위허가', '계획 수립·개발행위', '법령', 'B', 41),
('P03', '부동산공법', '도시개발법', '도시개발사업', '구역 지정·환지', '법령', 'B', 42),
('P04', '부동산공법', '도시정비법', '정비사업', '재개발·재건축 절차', '법령', 'B', 43),
('P05', '부동산공법', '건축법', '건축 허가·용도변경', '건축물 용도·허가·신고', '법령+우리 데이터', 'A', 44),
('P06', '부동산공법', '주택법', '주택 건설·공급', '사업계획승인·분양가 상한·전매제한', '법령', 'B', 45),
('P07', '부동산공법', '농지법', '농지 소유·전용', '농지취득자격증명·전용허가', '법령', 'B', 46),
('R01', '부동산공시법령', '공간정보관리법', '지적', '토지 등록·지목·면적·지적공부', '법령+우리 데이터', 'B', 47),
('R02', '부동산공시법령', '부동산등기법', '등기 절차', '등기부 구성·등기 신청', '법령+공공자료', 'A', 48),
('T01', '부동산세법', '조세 총론', '국세·지방세 공통', '납세의무 성립·확정·소멸', '법령', 'C', 49),
('T02', '부동산세법', '지방세', '취득세·등록면허세·재산세', '과세표준·세율', '법령+공공자료', 'A', 50),
('T03', '부동산세법', '국세', '종합부동산세', '과세 대상·세율', '법령+공공자료', 'A', 51),
('T04', '부동산세법', '국세', '양도소득세', '비과세·중과·장기보유특별공제', '법령+공공자료', 'A', 52)
ON CONFLICT (topic_id) DO UPDATE SET subject = excluded.subject, area = excluded.area, unit = excluded.unit, summary = excluded.summary, fill_method = excluded.fill_method, priority = excluded.priority, sort_no = excluded.sort_no, asof = current_date;

DELETE FROM core.kb_topic_law;
INSERT INTO core.kb_topic_law (topic_id, law_name, art_from, art_to, scope_note) VALUES
('G07', '국토의 계획 및 이용에 관한 법률', NULL, NULL, NULL),
('G07', '부동산 거래신고 등에 관한 법률', NULL, NULL, NULL),
('G08', '주택법', NULL, NULL, NULL),
('G08', '민간임대주택에 관한 특별법', NULL, NULL, NULL),
('G08', '주택임대차보호법', NULL, NULL, NULL),
('G09', '지방세법', NULL, NULL, NULL),
('G09', '종합부동산세법', NULL, NULL, NULL),
('G09', '소득세법', NULL, NULL, NULL),
('G12', '한국주택금융공사법', NULL, NULL, NULL),
('G12', '주택도시기금법', NULL, NULL, NULL),
('G13', '자산유동화에 관한 법률', NULL, NULL, NULL),
('G13', '부동산투자회사법', NULL, NULL, NULL),
('G14', '부동산개발업의 관리 및 육성에 관한 법률', NULL, NULL, NULL),
('G14', '도시개발법', NULL, NULL, NULL),
('G15', '공동주택관리법', NULL, NULL, NULL),
('G16', '감정평가 및 감정평가사에 관한 법률', NULL, NULL, NULL),
('G16', '감정평가에 관한 규칙', NULL, NULL, NULL),
('G17', '부동산 가격공시에 관한 법률', NULL, NULL, NULL),
('M01', '민법', 103, 106, NULL),
('M02', '민법', 107, 113, NULL),
('M03', '민법', 114, 136, NULL),
('M04', '민법', 137, 154, NULL),
('M05', '민법', 185, 191, NULL),
('M06', '민법', 192, 278, NULL),
('M07', '민법', 279, 319, NULL),
('M08', '민법', 320, 328, NULL),
('M08', '민법', 356, 372, NULL),
('M09', '민법', 527, 553, NULL),
('M10', '민법', 563, 597, NULL),
('M11', '민법', 618, 654, NULL),
('M12', '주택임대차보호법', NULL, NULL, NULL),
('M12', '주택임대차보호법 시행령', NULL, NULL, NULL),
('M13', '상가건물 임대차보호법', NULL, NULL, NULL),
('M14', '집합건물의 소유 및 관리에 관한 법률', NULL, NULL, NULL),
('M15', '가등기담보 등에 관한 법률', NULL, NULL, NULL),
('M15', '부동산 실권리자명의 등기에 관한 법률', NULL, NULL, NULL),
('J01', '공인중개사법', NULL, NULL, NULL),
('J01', '공인중개사법 시행령', NULL, NULL, NULL),
('J01', '공인중개사법 시행규칙', NULL, NULL, NULL),
('J02', '공인중개사법', 25, 33, NULL),
('J02', '공인중개사법 시행령', NULL, NULL, NULL),
('J02', '공인중개사법 시행규칙', NULL, NULL, NULL),
('J03', '공인중개사법', 35, 51, NULL),
('J04', '부동산 거래신고 등에 관한 법률', 3, 6, NULL),
('J04', '부동산 거래신고 등에 관한 법률 시행령', NULL, NULL, NULL),
('J05', '부동산 거래신고 등에 관한 법률', 7, 26, NULL),
('J06', '공인중개사법', NULL, NULL, NULL),
('J06', '부동산등기법', NULL, NULL, NULL),
('J07', '민사집행법', NULL, NULL, NULL),
('J07', '민사집행규칙', NULL, NULL, NULL),
('J07', '국세징수법', NULL, NULL, NULL),
('P01', '국토의 계획 및 이용에 관한 법률', NULL, NULL, NULL),
('P01', '국토의 계획 및 이용에 관한 법률 시행령', NULL, NULL, NULL),
('P01', '국토의 계획 및 이용에 관한 법률 시행규칙', NULL, NULL, NULL),
('P02', '국토의 계획 및 이용에 관한 법률', NULL, NULL, NULL),
('P03', '도시개발법', NULL, NULL, NULL),
('P04', '도시 및 주거환경정비법', NULL, NULL, NULL),
('P05', '건축법', NULL, NULL, NULL),
('P05', '건축법 시행령', NULL, NULL, NULL),
('P06', '주택법', NULL, NULL, NULL),
('P07', '농지법', NULL, NULL, NULL),
('R01', '공간정보의 구축 및 관리 등에 관한 법률', NULL, NULL, '지적 부분'),
('R02', '부동산등기법', NULL, NULL, NULL),
('T01', '국세기본법', NULL, NULL, NULL),
('T02', '지방세법', NULL, NULL, '취득세·등록면허세·재산세 장'),
('T03', '종합부동산세법', NULL, NULL, NULL),
('T04', '소득세법', NULL, NULL, '양도소득 부분');

-- 단원 목록. 과목을 주면 그 과목만. article_count = 연결된 현행 조문 수(법령을 아직 안 받았으면 0)
DROP FUNCTION IF EXISTS mart.q_kb_topics(text);
CREATE FUNCTION mart.q_kb_topics(p_subject text DEFAULT NULL)
RETURNS TABLE (topic_id text, subject text, area text, unit text, summary text, fill_method text, priority text,
               laws text, article_count bigint)
LANGUAGE sql STABLE AS $$
    SELECT t.topic_id, t.subject, t.area, t.unit, t.summary, t.fill_method, t.priority,
           (SELECT string_agg(DISTINCT l.law_name, ' · ') FROM core.kb_topic_law l WHERE l.topic_id = t.topic_id),
           (SELECT count(DISTINCT w.article_id) FROM core.kb_topic_law l
              JOIN core.doc_law w ON w.law_name = l.law_name AND w.is_current
               AND (l.art_from IS NULL OR substring(w.article_no FROM '^제(\d+)조')::int BETWEEN l.art_from AND l.art_to)
             WHERE l.topic_id = t.topic_id)
    FROM core.kb_topic t
    WHERE p_subject IS NULL OR t.subject = p_subject
    ORDER BY t.sort_no
$$;

-- 단원에 연결된 현행 조문 원문(법령 · 조 번호 순)
DROP FUNCTION IF EXISTS mart.q_kb_topic_articles(text);
CREATE FUNCTION mart.q_kb_topic_articles(p_topic text)
RETURNS TABLE (law_name text, article_no text, article_title text, body text, effective_date date,
               scope_note text, source text, asof date)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (w.law_name, n, w.article_no)
           w.law_name, w.article_no, w.article_title, w.body, w.effective_date, l.scope_note, w.source, w.asof
    FROM core.kb_topic_law l
    JOIN core.doc_law w ON w.law_name = l.law_name AND w.is_current
    CROSS JOIN LATERAL (SELECT substring(w.article_no FROM '^제(\d+)조')::int AS n) x
    WHERE l.topic_id = p_topic AND (l.art_from IS NULL OR x.n BETWEEN l.art_from AND l.art_to)
    ORDER BY w.law_name, n, w.article_no
$$;

ALTER FUNCTION mart.q_kb_topics(text) SECURITY DEFINER;
ALTER FUNCTION mart.q_kb_topics(text) SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_kb_topic_articles(text) SECURITY DEFINER;
ALTER FUNCTION mart.q_kb_topic_articles(text) SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_kb_topics(text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION mart.q_kb_topic_articles(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_kb_topics(text) TO hanpilji_gpt;
GRANT EXECUTE ON FUNCTION mart.q_kb_topic_articles(text) TO hanpilji_gpt;
