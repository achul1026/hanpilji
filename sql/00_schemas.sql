-- 한필지 데이터셋 — 스키마
-- ops  : 수집 기록(공통 모듈)
-- stg  : 원본 컬럼 그대로 옮긴 스테이징
-- core : PNU로 연결한 정제 테이블 18개
-- mart : GPT·고객이 읽는 질문별 결과. 읽기 전용 계정은 여기만 봄

CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS mart;

CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- 법령 조문 부분 일치 검색(의미 검색 전 단계)
