-- 건축HUB 건축물대장 대용량 파일(hanpilji/collectors/bld_file.py)
-- 서울 모든 건물(빌라 · 단독 · 상가 포함)을 필지 수십만 개 단위로 올리므로, 필지마다 단지를 찾는 조회에 색인을 둠
CREATE INDEX IF NOT EXISTS dim_complex_pnu_idx ON core.dim_complex (pnu);

-- 파일 모양(2026-09월 파일로 확인): UTF-8, 구분자 「|」, 머리줄 없음
UPDATE ops.ingest_source SET encoding = 'UTF-8' WHERE source_id = 'hub_bld' AND encoding IS DISTINCT FROM 'UTF-8';
