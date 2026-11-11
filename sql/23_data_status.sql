-- 데이터 현황: 사이트의 「데이터 현황」 화면(/data)이 읽는 함수 셋. 모델을 부르지 않음
--   mart.q_data_status()  : 데이터마다(수집처 · 기준연도 · 법령 · 지표 따로) 줄 수 · 기간 · 최종 업데이트일 · 수집처 id
--   mart.q_data_size()    : 한필지 데이터 전체 용량(바이트) · 표 수
--   mart.q_data_sources() : 수집처마다 이름 · 기관 · 주기 · 이용 조건 · 주소 · 마지막 성공 시각 · 그때 넣은 줄 수
-- 큰 표(실거래 · 지적도)를 다 세므로 몇 초 걸릴 수 있음. 웹이 결과를 1시간 기억해 두고 씀(hanpilji/gpt/datastatus.py)
-- 지적도 · 건물 윤곽 · 주변 정보 표는 PostGIS가 있는 DB에만 있어서, 있을 때만 셈(to_regclass)

DROP FUNCTION IF EXISTS mart.q_data_status();
-- 한 줄 = 데이터 하나(수집처 · 종류 · 기준연도 · 법령 · 지표마다 따로). updated = 그 데이터 줄들의 가장 최근 기준일(asof)
CREATE FUNCTION mart.q_data_status()
RETURNS TABLE (grp text, label text, detail text, row_count bigint, period_from text, period_to text,
               updated date, source_ids text[])
LANGUAGE plpgsql STABLE AS $$
BEGIN
    -- 실거래: 수집처(매매 · 전월세 x 주택 종류)마다 한 줄. source 열에 수집처 id가 들어 있음
    RETURN QUERY
    SELECT '실거래'::text, coalesce(s.name, d.source), '매매(해제 거래 포함)'::text, count(*),
           min(d.contract_date)::text, max(d.contract_date)::text, max(d.asof), ARRAY[d.source]
    FROM core.fact_deal d LEFT JOIN ops.ingest_source s ON s.source_id = d.source
    GROUP BY d.source, s.name ORDER BY count(*) DESC;
    RETURN QUERY
    SELECT '실거래'::text, coalesce(s.name, r.source), '전세 · 월세'::text, count(*),
           min(r.contract_date)::text, max(r.contract_date)::text, max(r.asof), ARRAY[r.source]
    FROM core.fact_rent r LEFT JOIN ops.ingest_source s ON s.source_id = r.source
    GROUP BY r.source, s.name ORDER BY count(*) DESC;

    -- 단지 · 건물 · 토지
    RETURN QUERY
    SELECT '단지 · 건물 · 토지'::text, c.kind || ' 단지 목록', '실거래 자료의 단지명 · 주소로 만듦'::text, count(*),
           NULL::text, NULL::text, max(c.asof), ARRAY['dg_apt_trade', 'dg_offi_trade', 'dg_rh_trade']
    FROM core.dim_complex c GROUP BY c.kind ORDER BY count(*) DESC;
    RETURN QUERY
    SELECT '단지 · 건물 · 토지'::text, '건축물대장(건물)'::text, '표제부 · 총괄표제부'::text, count(*),
           min(b.approval_date)::text, max(b.approval_date)::text, max(b.asof), ARRAY['dg_bld_hub', 'hub_bld']
    FROM core.dim_building b;
    RETURN QUERY
    SELECT '단지 · 건물 · 토지'::text, '필지(토지) 목록'::text, '지번 · 지목 · 대표 용도지역'::text, count(*),
           NULL::text, NULL::text, max(p.asof), ARRAY['seoul_land_price', 'vworld_ned']
    FROM core.dim_parcel p;
    RETURN QUERY
    SELECT '단지 · 건물 · 토지'::text, '개별공시지가 ' || l.base_year || '년', '필지마다 1㎡ 가격'::text, count(*),
           NULL::text, NULL::text, max(l.asof), ARRAY['seoul_land_price', 'vworld_ned']
    FROM core.fact_land_price l GROUP BY l.base_year ORDER BY l.base_year DESC;
    RETURN QUERY
    SELECT '단지 · 건물 · 토지'::text, '토지특성 ' || t.base_year || '년', '이용상황 · 지형 · 도로 접면'::text, count(*),
           NULL::text, NULL::text, max(t.asof), ARRAY['vworld_ned']
    FROM core.fact_land_char t GROUP BY t.base_year ORDER BY t.base_year DESC;
    RETURN QUERY
    SELECT '단지 · 건물 · 토지'::text, h.house_kind || '주택 공시가격 ' || h.base_year || '년', '호마다 공시가격'::text,
           count(*), NULL::text, NULL::text, max(h.asof), ARRAY['molit_house_price']
    FROM core.fact_house_price h GROUP BY h.house_kind, h.base_year ORDER BY h.base_year DESC, h.house_kind;
    IF to_regclass('core.geo_parcel') IS NOT NULL THEN
        RETURN QUERY EXECUTE
            $q$SELECT '단지 · 건물 · 토지'::text, '지적도(필지 경계)'::text, '연속지적도'::text, count(*),
                      NULL::text, NULL::text, max(asof), ARRAY['vworld_geom', 'vworld_parcel'] FROM core.geo_parcel$q$;
    END IF;
    IF to_regclass('core.geo_building') IS NOT NULL THEN
        RETURN QUERY EXECUTE
            $q$SELECT '단지 · 건물 · 토지'::text, '건물 윤곽'::text, '도로명주소 건물'::text, count(*),
                      NULL::text, NULL::text, max(asof), ARRAY['vworld_geom'] FROM core.geo_building$q$;
    END IF;

    -- 법령: 법령마다 한 줄(현행 조문 수, 시행일)
    RETURN QUERY
    SELECT '법령'::text, w.law_name, coalesce(max(w.law_kind), '') || ' · 현행 조문', count(*),
           NULL::text, '시행 ' || max(w.effective_date)::text, max(w.asof), ARRAY['law_go_kr']
    FROM core.doc_law w WHERE w.is_current GROUP BY w.law_name ORDER BY w.law_name;

    -- 규제 · 정책
    RETURN QUERY
    SELECT '규제 · 정책'::text, '용도지역 행위제한'::text, '용도지역 x 건물 종류(가능 · 조건부 · 불가)'::text, count(*),
           NULL::text, NULL::text, max(g.asof), ARRAY['dg_luinfo', 'eum_act_rule']
    FROM core.reg_rule g;
    RETURN QUERY
    SELECT '규제 · 정책'::text, '토지거래허가구역: ' || z.name, z.source, 1::bigint,
           z.start_date::text, z.end_date::text, z.asof, ARRAY[]::text[]
    FROM core.ref_permit_zone z ORDER BY z.start_date;
    RETURN QUERY
    SELECT '규제 · 정책'::text, '부동산 정책 발표'::text, '공식 발표 · 보도로 확인해 직접 입력'::text, count(*),
           min(e.announce_date)::text, max(e.announce_date)::text, max(e.asof), ARRAY[]::text[]
    FROM core.ref_policy_event e;

    -- 시장 지표: 지표마다 한 줄
    RETURN QUERY
    SELECT '시장 지표'::text, st.name, coalesce(st.unit, '') || ' · ' || CASE st.cycle WHEN 'M' THEN '월' WHEN 'Q' THEN '분기'
               WHEN 'A' THEN '연' ELSE st.cycle END || ' · ' || st.source, count(*),
           min(m.period), max(m.period), max(m.asof), ARRAY[st.source_id]
    FROM core.fact_market m JOIN core.dim_stat st USING (stat_id)
    GROUP BY st.stat_id, st.name, st.unit, st.cycle, st.source, st.source_id ORDER BY st.source_id, st.name;

    -- 기타
    RETURN QUERY
    SELECT '기타'::text, '공매 물건'::text, '온비드 입찰 진행 · 예정'::text, count(*),
           NULL::text, NULL::text, max(a.asof), ARRAY['onbid']
    FROM core.fact_auction a;
    IF to_regclass('core.ref_poi') IS NOT NULL THEN
        RETURN QUERY EXECUTE
            $q$SELECT '기타'::text, kind || ' 위치', '주변 정보'::text, count(*), NULL::text, NULL::text, max(asof),
                      CASE WHEN kind = '지하철역' THEN ARRAY['poi_station'] ELSE ARRAY['poi_school'] END
               FROM core.ref_poi GROUP BY kind ORDER BY count(*) DESC$q$;
    END IF;
END
$$;

-- 한필지 데이터 전체 용량(표 + 색인 + 큰 값 저장소). 스키마를 넷으로 나눈 DB든 하나로 합친 DB든 같은 값
DROP FUNCTION IF EXISTS mart.q_data_size();
CREATE FUNCTION mart.q_data_size()
RETURNS TABLE (total_bytes bigint, table_count int)
LANGUAGE sql STABLE AS $$
    SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0)::bigint, count(*)::int
    FROM pg_class c
    WHERE c.relkind IN ('r', 'm', 'p')
      AND c.relnamespace IN (SELECT x.relnamespace FROM pg_class x
                             WHERE x.oid IN (to_regclass('ops.ingest_source'), to_regclass('stg.law_article'),
                                             to_regclass('core.dim_region'), to_regclass('mart.law_search')))
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
ALTER FUNCTION mart.q_data_size() SECURITY DEFINER;
ALTER FUNCTION mart.q_data_size() SET search_path = pg_catalog, public;
ALTER FUNCTION mart.q_data_sources() SET search_path = pg_catalog, public;
REVOKE EXECUTE ON FUNCTION mart.q_data_status() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION mart.q_data_sources() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION mart.q_data_size() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.q_data_status() TO hanpilji_gpt;
GRANT EXECUTE ON FUNCTION mart.q_data_sources() TO hanpilji_gpt;
GRANT EXECUTE ON FUNCTION mart.q_data_size() TO hanpilji_gpt;
