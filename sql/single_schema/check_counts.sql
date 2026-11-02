-- 표마다 건수와 한 줄 지문. 원래 DB(Supabase)와 개발서버에서 똑같이 돌려 지문이 같으면 건수가 모두 같음
--   개발서버: docker exec -i datalake-database-dev psql -U dl -d datalake < sql/single_schema/check_counts.sql
-- 표 이름만으로 맞춤(Supabase는 ops · stg · core, 개발서버는 hanpilji 하나. 이름이 겹치지 않음). 정렬은 "C"로 고정
WITH t AS (
    SELECT c.relname AS tbl,
           (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I.%I', n.nspname, c.relname),
                                                false, true, '')))[1]::text::bigint AS n
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('ops', 'stg', 'core', 'hanpilji') AND c.relkind = 'r'
)
SELECT md5(string_agg(tbl || ':' || n, ',' ORDER BY tbl COLLATE "C")) AS fingerprint,
       count(*) AS tables, sum(n) AS total_rows
FROM t;
