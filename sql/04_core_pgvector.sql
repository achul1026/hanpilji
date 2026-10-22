-- 선택: pgvector가 깔린 서버에서만 실행. 없으면 mart.law_search가 pg_trgm 부분 일치로 동작함
CREATE EXTENSION IF NOT EXISTS vector;

-- 차원 수는 쓸 임베딩 모델에 맞춰 바꿈(GPT 엔진 미정 → 1024 임시)
ALTER TABLE core.doc_law ADD COLUMN IF NOT EXISTS embedding vector(1024);
CREATE INDEX IF NOT EXISTS doc_law_embedding_idx ON core.doc_law USING hnsw (embedding vector_cosine_ops);
