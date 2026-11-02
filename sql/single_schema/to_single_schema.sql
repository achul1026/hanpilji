-- 네 스키마(ops · stg · core · mart)를 스키마 하나로 합침. 복사본 DB에서만 돌릴 것(원본 Supabase 아님)
--   psql -d <복사본> -v target=hanpilji -v ON_ERROR_STOP=1 -f sql/single_schema/to_single_schema.sql
-- 표 · 뷰 · 일련번호 · 함수를 옮기고, 함수 본문 속 스키마 이름(core.x 등)을 새 이름으로 바꿈.
-- 뷰 · 기본값 · 색인 · 제약은 이름이 아니라 내부 번호로 이어져 있어 따로 손댈 것 없음.
\if :{?target}
\else
  \set target hanpilji
\endif
SELECT set_config('hanpilji.target', :'target', false);

DO $$
DECLARE
    tgt text := current_setting('hanpilji.target');
    r record;
    def text;
    newdef text;
BEGIN
    EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', tgt);

    -- 표 · 뷰 · 따로 선 일련번호(표에 딸린 일련번호는 표와 같이 옮겨짐)
    FOR r IN
        SELECT n.nspname AS s, c.relname AS t, c.relkind AS k
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname IN ('ops', 'stg', 'core', 'mart')
          AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
          AND NOT (c.relkind = 'S' AND EXISTS (
                SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')
                  AND d.refclassid = 'pg_class'::regclass))
        ORDER BY c.relkind DESC, 1, 2
    LOOP
        EXECUTE format('ALTER %s %I.%I SET SCHEMA %I',
            CASE r.k WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW'
                     WHEN 'S' THEN 'SEQUENCE' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END,
            r.s, r.t, tgt);
    END LOOP;

    -- 함수: 옮긴 뒤 본문의 스키마 이름을 바꿔 다시 만듦(SECURITY DEFINER · search_path 설정은 그대로 따라감)
    FOR r IN
        SELECT p.oid, n.nspname AS s, p.proname, pg_get_function_identity_arguments(p.oid) AS args, p.prokind
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname IN ('ops', 'stg', 'core', 'mart')
    LOOP
        EXECUTE format('ALTER %s %I.%I(%s) SET SCHEMA %I',
            CASE r.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END, r.s, r.proname, r.args, tgt);
    END LOOP;
    FOR r IN
        SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = tgt AND p.prokind IN ('f', 'p')
    LOOP
        def := pg_get_functiondef(r.oid);
        newdef := regexp_replace(def, '(?<![[:alnum:]_.])(ops|stg|core|mart)\.(?=[[:alpha:]_"])', tgt || '.', 'g');
        IF newdef <> def THEN
            EXECUTE newdef;
        END IF;
    END LOOP;

    -- 비었으면 옛 스키마 지움(남은 게 있으면 오류로 멈춤)
    DROP SCHEMA ops, stg, core, mart RESTRICT;

    -- GPT 읽기 계정: 예전 mart 뷰 · 함수 권한은 객체에 붙어 함께 옮겨짐. 새 스키마 사용권만 줌
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hanpilji_gpt') THEN
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO hanpilji_gpt', tgt);
    END IF;
END $$;
