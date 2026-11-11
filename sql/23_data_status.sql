-- 데이터 현황: 사이트의 「데이터 현황」 화면(/data)이 읽는 함수 둘. 모델을 부르지 않음
--   mart.q_data_status()  : 데이터 묶음마다 줄 수 · 데이터 기간 · 수집처 id
--   mart.q_data_sources() : 수집처마다 이름 · 기관 · 주기 · 이용 조건 · 주소 · 마지막 성공 시각 · 그때 넣은 줄 수
-- 큰 표(실거래 · 지적도)를 다 세므로 몇 초 걸릴 수 있음. 웹이 결과를 1시간 기억해 두고 씀(hanpilji/gpt/datastatus.py)
-- 지적도 · 건물 윤곽 · 주변 정보 표는 PostGIS가 있는 DB에만 있어서, 있을 때만 셈(to_regclass)

DROP FUNCTION IF EXISTS mart.q_data_status();
CREATE FUNCTION mart.q_data_status()
RETURNS TABLE (grp text, label text, detail text, row_count bigint, period_from text, period_to text, source_ids text[])
LANGUAGE plpgsql STABLE AS $$
BEGIN
    -- 실거래: 수집처(매매 · 전월세 x 주택 종류)마다 한 줄. source 열에 수집처 id가 들어 있음
    RETURN QUERY
    SELECT '실거래'::text, coalesce(s.name, d.source), '매매 · 해제 거래 포함'::text, count(*),
           min(d.contract_date)::text, max(d.contract_date)::text, ARRAY[d.source]
    FROM core.fact_deal d LEFT JOIN ops.ingest_source s ON s.source_id = d.source
    GROUP BY d.source, s.name;
    RETURN QUERY
    SELECT '실거래'::text, coalesce(s.name, r.source), '전세 · 월세'::text, count(*),
           min(r.contract_date)::text, max(r.contract_date)::text, ARRAY[r.source]
    FROM core.fact_rent r LEFT JOIN ops.ingest_source s ON s.source_id = r.source
    GROUP BY r.source, s.name;

    -- 단지 · 건물 · 토지
    RETURN QUERY
    SELECT '단지 · 건물 · 토지'::text, c.kind || ' 단지', '실거래 자료의 단지명 · 주소로 만듦'::text, count(*),
           NULL::text, max(c.asof)::text, ARRAY['dg_apt_trade', 'dg_offi_trade', 'dg_rh_trade']
    FROM core.dim_complex c GROUP BY c.kind;
    RETURN QUERY
    SELECT '단지 · 건물 · 토지'::text, '건축물대장(건물)'::text, '표제부 · 총괄표제부'::text, count(*),
           NULL::text, max(b.asof)::text, ARRAY['dg_bld_hub', 'hub_bld']
    FROM core.dim_building b;
    RETURN QUERY
    SELECT '단지 · 건물 · 토지'::text, '필지(토지)'::text, '지번 · 지목 · 대표 용도지역'::text, count(*),
           NULL::text, max(p.asof)::text, ARRAY['seoul_land_price', 'vworld_ned']
    FROM core.dim_parcel p;
    RETURN QUERY
    SELECT '단지 · 건물 · 토지'::text, '개별공시지가'::text, '필지 x 기준연도'::text, count(*),
           min(l.base_year)::text, max(l.base_year)::text, ARRAY['seoul_land_price', 'vworld_ned']
    FROM core.fact_land_price l;
    RETURN QUERY
    SELECT '단지 · 건물 · 토지'::text, '토지특성'::text, '이용상황 · 지형 · 도로 접면'::text, count(*),
           min(t.base_year)::text, max(t.base_year)::text, ARRAY['vworld_ned']
    FROM core.fact_land_char t;
    RETURN QUERY
    SELECT '단지 · 건물 · 토지'::text, '공동주택 공시가격'::text, '아파트 · 연립 · 다세대 호별'::text, count(*),
           min(h.base_year)::text, max(h.base_year)::text, ARRAY['molit_house_price']
    FROM core.fact_house_price h;
    IF to_regclass('core.geo_parcel') IS NOT NULL THEN
        RETURN QUERY EXECUTE
            $q$SELECT '단지 · 건물 · 토지'::text, '지적도(필지 경계)'::text, '연속지적도'::text, count(*),
                      NULL::text, max(asof)::text, ARRAY['vworld_geom', 'vworld_parcel'] FROM core.geo_parcel$q$;
    END IF;
    IF to_regclass('core.geo_building') IS NOT NULL THEN
        RETURN QUERY EXECUTE
            $q$SELECT '단지 · 건물 · 토지'::text, '건물 윤곽'::text, '도로명주소 건물'::text, count(*),
                      NULL::text, max(asof)::text, ARRAY['vworld_geom'] FROM core.geo_building$q$;
    END IF;

    -- 법령 · 규제 · 정책
    RETURN QUERY
    SELECT '법령 · 규제 · 정책'::text, '법령 조문(현행)'::text, count(DISTINCT w.law_name) || '개 법령', count(*),
           NULL::text, max(w.asof)::text, ARRAY['law_go_kr']
    FROM core.doc_law w WHERE w.is_current;
    RETURN QUERY
    SELECT '법령 · 규제 · 정책'::text, '용도지역 행위제한'::text, '용도지역 x 건물 종류(가능 · 조건부 · 불가)'::text, count(*),
           NULL::text, max(g.asof)::text, ARRAY['dg_luinfo', 'eum_act_rule']
    FROM core.reg_rule g;
    RETURN QUERY
    SELECT '법령 · 규제 · 정책'::text, '토지거래허가구역'::text, '공식 발표 · 보도로 확인해 직접 입력'::text, count(*),
           min(z.start_date)::text, max(z.end_date)::text, ARRAY[]::text[]
    FROM core.ref_permit_zone z;
    RETURN QUERY
    SELECT '법령 · 규제 · 정책'::text, '부동산 정책 발표'::text, '공식 발표 · 보도로 확인해 직접 입력'::text, count(*),
           min(e.announce_date)::text, max(e.announce_date)::text, ARRAY[]::text[]
    FROM core.ref_policy_event e;

    -- 시장 · 기타
    RETURN QUERY
    SELECT '시장 · 기타'::text, '시장 · 거시 지표'::text, count(DISTINCT m.stat_id) || '개 지표(금리 · 가격지수 · 물가 · 인구 등)',
           count(*), min(m.period), max(m.period), ARRAY['ecos', 'rone', 'kosis']
    FROM core.fact_market m;
    RETURN QUERY
    SELECT '시장 · 기타'::text, '공매 물건'::text, '온비드 입찰 진행 · 예정'::text, count(*),
           NULL::text, max(a.asof)::text, ARRAY['onbid']
    FROM core.fact_auction a;
    IF to_regclass('core.ref_poi') IS NOT NULL THEN
        RETURN QUERY EXECUTE
            $q$SELECT '시장 · 기타'::text, CASE WHEN kind = '지하철역' THEN '지하철역' ELSE '학교' END,
                      '위치(주변 정보)'::text, count(*), NULL::text, max(asof)::text,
                      CASE WHEN kind = '지하철역' THEN ARRAY['poi_station'] ELSE ARRAY['poi_school'] END
               FROM core.ref_poi GROUP BY kind = '지하철역'$q$;
    END IF;
END
$$;

DROP FUNCTION IF EXISTS mart.q_data_sources();
CREATE FUNCTION mart.q_data_sources()
RETURNS TABLE (source_id text, name text, operator text, cycle text, license text, url text,
               last_success timestamptz, last_rows integer)
LANGUAGE sql STABLE AS $$
    SELECT s.source_id, s.name, s.operator, s.cycle, s.license, s.url, r.finished_at, r.rows_loaded
    FROM ops.ingest_source s
    LEFT JOIN LATERAL (SELECT x.finished_at, x.rows_loaded FROM ops.ingest_run x
                       WHERE x.source_id = s.source_id AND x.status = 'done'
                       ORDER BY x.finished_at DESC NULLS LAST LIMIT 1) r ON true
    ORDER BY s.source_id
$$;

ALTER FUNCTION mart.q_data_status() SECURITY DEFINER;
ALTER FUNCTION mart.q_data_status() SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_data_sources() SECURITY DEFINER;
ALTER FUNCTION mart.q_data_sources() SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_data_status() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION mart.q_data_sources() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_data_status() TO hanpilji_gpt;
GRANT EXECUTE ON FUNCTION mart.q_data_sources() TO hanpilji_gpt;
