-- 단지 · 동 이름으로 서울 어느 구인지 찾기(2026-11-10). 개발서버처럼 서울 25개 구 실거래가 있을 때,
-- 「마포래미안푸르지오 시세」처럼 구를 말하지 않은 질문에 GPT 도구가 먼저 불러 sigungu를 채움.
-- 1) 단지 이름(끝 괄호 · 띄어쓰기 무시)이 맞는 단지가 가장 많은 구. 이름이 똑같은 단지가 있는 구를 먼저.
-- 2) 없으면 이름이 동 이름으로 시작하는 구(「대치동 316」). 같은 동 이름이 여러 구에 있으면 거래가 많은 구.
-- 못 찾으면 NULL(도구는 기본 구로 찾음). 여러 번 돌려도 같은 결과.
CREATE OR REPLACE FUNCTION mart.q_find_sigungu(p_name text)
RETURNS text
LANGUAGE sql STABLE AS $$
    WITH q AS (
        SELECT regexp_replace(replace(coalesce(p_name, ''), ' ', ''), '\([^()]*\)$', '') AS n
    ), by_name AS (
        SELECT left(x.lawd_cd, 5) AS sg,
               bool_or(regexp_replace(replace(x.name, ' ', ''), '\([^()]*\)$', '') = q.n) AS exact,
               count(*) AS hits
        FROM core.dim_complex x, q
        WHERE length(q.n) >= 2
          AND regexp_replace(replace(x.name, ' ', ''), '\([^()]*\)$', '') LIKE '%' || q.n || '%'
        GROUP BY 1
    ), by_emd AS (
        SELECT left(r.lawd_cd, 5) AS sg, length(r.emd) AS len,
               (SELECT count(*) FROM core.dim_complex x WHERE x.lawd_cd = r.lawd_cd) AS complexes
        FROM core.dim_region r, q
        WHERE r.emd IS NOT NULL AND length(r.emd) >= 2 AND q.n LIKE r.emd || '%'
    )
    SELECT coalesce(
        (SELECT sg FROM by_name ORDER BY exact DESC, hits DESC, sg LIMIT 1),
        (SELECT sg FROM by_emd ORDER BY len DESC, complexes DESC, sg LIMIT 1))
$$;

ALTER FUNCTION mart.q_find_sigungu(text) SECURITY DEFINER;
ALTER FUNCTION mart.q_find_sigungu(text) SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_find_sigungu(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_find_sigungu(text) TO hanpilji_gpt;
