-- 기준 값: 수집처 목록(0단계분)과 용도지역 코드

INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url) VALUES
 ('law_go_kr',     '법제처 국가법령정보 Open API(법령 · 별표)', '법제처',          'api',  NULL, 'UTF-8', '바뀔 때',  '공공저작물, 출처 표시', 'https://open.law.go.kr'),
 ('eum_act_rule',  '토지이음 행위제한정보 CSV',                 '국토교통부(토지이음)', 'file', NULL, NULL,    '매월',     '공공누리 제1유형',       'https://www.eum.go.kr/web/op/sv/svItemList.jsp'),
 ('code_lawd',     '법정동코드 전체자료',                       '행정표준코드관리시스템', 'file', NULL, NULL,    '바뀔 때',  NULL,                     'https://www.code.go.kr'),
 ('hub_bld',       '건축HUB 건축물대장 대용량 파일',            '국토교통부(건축HUB)', 'file', NULL, NULL,    '매월 21일 이후', '이용 목적 선택 후 이용', 'https://www.hub.go.kr'),
 ('vworld_parcel', 'V-World 연속지적도 · 토지임야',              '국가공간정보센터',    'file', NULL, NULL,    '매월',     '출처 표시',              'https://www.vworld.kr')
ON CONFLICT (source_id) DO NOTHING;

-- 토지특성정보의 용도지역코드(국가중점데이터 컬럼정의서 26.08.19 「참조코드」 15번)
-- 토지이용계획(UQ…)이나 토지이음 코드는 파일을 확인한 뒤 code_system을 달리해 추가함
INSERT INTO core.dim_zone (zone_cd, code_system, zone_name, law_basis, source, asof) VALUES
 ('11','land_char','제1종전용주거지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('12','land_char','제2종전용주거지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('13','land_char','제1종일반주거지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('14','land_char','제2종일반주거지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('15','land_char','제3종일반주거지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('16','land_char','준주거지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('17','land_char','일반주거지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('21','land_char','중심상업지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('22','land_char','일반상업지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('23','land_char','근린상업지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('24','land_char','유통상업지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('31','land_char','전용공업지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('32','land_char','일반공업지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('33','land_char','준공업지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('41','land_char','보전녹지지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('42','land_char','생산녹지지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('43','land_char','자연녹지지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('44','land_char','개발제한구역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('51','land_char','용도미지정지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('61','land_char','관리지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('62','land_char','보전관리지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('63','land_char','생산관리지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('64','land_char','계획관리지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('71','land_char','농림지역',NULL,'V-World 컬럼정의서','2026-08-19'),
 ('81','land_char','자연환경보전지역',NULL,'V-World 컬럼정의서','2026-08-19')
ON CONFLICT (zone_cd) DO NOTHING;
