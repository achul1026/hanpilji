-- 권한 분리: GPT(또는 AI-PLUG)용 계정은 mart만 읽음. core · stg · ops는 못 봄
-- 비밀번호는 여기에 쓰지 않음. 서버에서 ALTER ROLE hanpilji_gpt PASSWORD '...' 로 따로 정함
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hanpilji_gpt') THEN
        CREATE ROLE hanpilji_gpt LOGIN;
    END IF;
END $$;

REVOKE ALL ON SCHEMA ops, stg, core FROM hanpilji_gpt;
GRANT USAGE ON SCHEMA mart TO hanpilji_gpt;
GRANT SELECT ON ALL TABLES IN SCHEMA mart TO hanpilji_gpt;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA mart TO hanpilji_gpt;
ALTER DEFAULT PRIVILEGES IN SCHEMA mart GRANT SELECT ON TABLES TO hanpilji_gpt;
ALTER DEFAULT PRIVILEGES IN SCHEMA mart GRANT EXECUTE ON FUNCTIONS TO hanpilji_gpt;

-- 마트 뷰 · 함수는 소유자 권한으로 core를 읽음(뷰 기본 동작). 함수도 같은 방식이 되도록 SECURITY DEFINER
ALTER FUNCTION mart.q_zoning_check(text, text, text) SECURITY DEFINER;
ALTER FUNCTION mart.q_law_search(text, text) SECURITY DEFINER;
ALTER FUNCTION mart.q_building_filter(text, int, boolean, boolean, text, int) SECURITY DEFINER;
ALTER FUNCTION mart.q_zoning_check(text, text, text) SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_law_search(text, text) SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_building_filter(text, int, boolean, boolean, text, int) SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_complex_price(text, text, numeric, int, numeric) SECURITY DEFINER;
ALTER FUNCTION mart.q_complex_price(text, text, numeric, int, numeric) SET search_path = pg_catalog, public;
