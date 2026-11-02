-- 복원 뒤 표마다 건수. 안내서의 표와 비교
--   psql -d <대상 DB> -v target=hanpilji -f sql/single_schema/check_counts.sql
\if :{?target}
\else
  \set target hanpilji
\endif
SELECT c.relname AS "표",
       (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I.%I', n.nspname, c.relname), false, true, '')))[1]::text::bigint AS "건수"
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = :'target' AND c.relkind = 'r'
ORDER BY 1;
