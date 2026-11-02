-- 개발서버(대상)에 백업을 푼 뒤 돌림. 백업은 --no-owner --no-acl 이라 권한을 여기서 다시 줌
--   psql -d <대상 DB> -v target=hanpilji -v ON_ERROR_STOP=1 -f sql/single_schema/grants.sql
-- GPT 계정(hanpilji_gpt)은 예전 mart에 있던 뷰 · 조회 함수만 읽음. 표(예전 core · stg · ops)는 못 봄
\if :{?target}
\else
  \set target hanpilji
\endif
SELECT set_config('hanpilji.target', :'target', false);

DO $$
DECLARE
    tgt text := current_setting('hanpilji.target');
    r record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hanpilji_gpt') THEN
        CREATE ROLE hanpilji_gpt LOGIN;   -- 비밀번호는 ALTER ROLE hanpilji_gpt PASSWORD '...' 로 따로
    END IF;
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM PUBLIC, hanpilji_gpt', tgt);
    EXECUTE format('REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA %I FROM PUBLIC', tgt);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO hanpilji_gpt', tgt);
    -- 예전 mart 객체: 뷰 building_card · law_search · zoning_check, 함수 q_* 전부
    FOR r IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = tgt AND c.relkind = 'v' AND c.relname IN ('building_card', 'law_search', 'zoning_check')
    LOOP
        EXECUTE format('GRANT SELECT ON %I.%I TO hanpilji_gpt', tgt, r.relname);
    END LOOP;
    FOR r IN SELECT p.proname, pg_get_function_identity_arguments(p.oid) AS args
             FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = tgt AND p.proname LIKE 'q\_%'
    LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION %I.%I(%s) TO hanpilji_gpt', tgt, r.proname, r.args);
    END LOOP;
END $$;
