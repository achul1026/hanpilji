"""GPT가 부를 수 있는 도구 = mart 함수 14개. GPT는 SQL을 짜지 않고 이름과 인자만 고름.

도구 결과에는 보여 준 줄 수(shown) · 전체 건수(row_total) · 출처 · 기준일을 늘 붙여서
엔진이 답 끝에 근거를 기계적으로 달 수 있게 함.
화면 전용 값(지도 도형 · screen_only 줄)은 웹 블록에만 쓰고 모델에게는 for_model()로 빼고 넘김(토큰 아끼기).
"""
import datetime
import decimal

from hanpilji.collectors.market import SERIES

# 시범 구가 아직 하나라 기본값으로 씀. 시군구 이름을 코드로 바꾸는 표(데이터가 있는 곳만)
SIGUNGU = {"강남구": "11680", "서울 강남구": "11680", "서울특별시 강남구": "11680"}
DEFAULT_SIGUNGU = "11680"

ZONES = ("제1종전용주거지역 제2종전용주거지역 제1종일반주거지역 제2종일반주거지역 제3종일반주거지역 준주거지역 "
         "중심상업지역 일반상업지역 근린상업지역 유통상업지역 전용공업지역 일반공업지역 준공업지역 "
         "보전녹지지역 생산녹지지역 자연녹지지역").split()

BODY_MAX = 1200   # 조문 본문이 길면 잘라서 넘김(토큰 아끼기). 잘랐다는 표시를 붙임


def _sigungu(v):
    if v in (None, ""):
        return DEFAULT_SIGUNGU
    v = str(v).strip()
    return SIGUNGU.get(v, v)


def _months(a):
    return max(1, min(int(a.get("months") or 12), 36))


TOOLS = [
    {
        "name": "q_complex_price",
        "description": (
            "아파트 단지의 전용면적별 매매 · 전세 시세. 국토교통부 실거래가에서 해제된 거래와 월세를 빼고 셈. "
            "금액 단위는 원. 매매 평균 · ㎡당 · 평당 단가, 마지막 거래, 전세 평균 · ㎡당 단가, 전세가율(%)을 줌. "
            "단지명은 일부만 써도 됨(띄어쓰기 · 끝 괄호 무시). 최대 20줄. 지금 데이터는 서울 강남구(11680)만 있음."),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "단지명 또는 그 일부. 예: 도곡렉슬"},
                "sigungu": {"type": "string", "description": "시군구 코드 5자리 또는 이름. 기본 11680(강남구)"},
                "area_m2": {"type": "number", "description": "전용면적 ㎡. ±3㎡ 안을 같은 타입으로 봄. 없으면 모든 면적"},
                "months": {"type": "integer", "description": "오늘부터 거꾸로 몇 개월. 기본 12, 최대 36"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_complex_price(%s::text, %s::text, %s::numeric, %s::int)",
        "args": lambda a: (_sigungu(a.get("sigungu")), a["name"], a.get("area_m2"), _months(a)),
    },
    {
        "name": "q_property_card",
        "description": (
            "단지명 또는 「동 지번」(예: 도곡동 527) 하나로 그 단지의 종합 카드. 위치(주소 · 도로명 · PNU · 준공연도), "
            "전용면적 타입, 최근 거래 요약(매매 건수 · 평당가 · 마지막 거래 · 최근 매매 5건, 전세 건수 · 평균, 월세 건수), "
            "전세가율(jeonse_ratio_area ㎡ 타입 하나에서 같은 면적끼리 비교한 값), "
            "단지에 걸린 필지 전부의 토지(지목 · 면적. 면적 출처는 land_area_kind) · 공시지가 · 용도지역 · 규제 규칙 수, "
            "단지 건물 전부의 건축물대장(주건축물 동 수 · 용도 · 사용승인 · 최고층 · 세대 · 주차 · 승강기 · 건폐율 · 용적률)을 "
            "한 줄로 줌. bld_elevator가 null이면 대장에 승강기 수가 비어 있어 모르는 것이고, bld_violation은 아직 늘 null(모름). "
            "missing에 아직 수집 전이라 비어 있는 칸이 적혀 있으니 그 칸은 「데이터 수집 전」이라고 말할 것. "
            "「○○ 알려줘」 「이 주소 어때」처럼 한 곳을 종합해서 물을 때 먼저 부름. 같은 이름이 여럿이면 최대 5줄. 강남구(11680)만."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "단지명 일부 또는 「동 지번」. 예: 도곡렉슬, 대치동 1027"},
                "months": {"type": "integer", "description": "거래 요약 기간(개월). 기본 12, 최대 36"},
                "sigungu": {"type": "string", "description": "시군구 코드 5자리 또는 이름. 기본 11680(강남구)"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_property_card(%s::text, %s::text, %s::int)",
        "args": lambda a: (_sigungu(a.get("sigungu")), a["query"], _months(a)),
    },
    {
        "name": "q_deal_list",
        "description": (
            "아파트 단지 한 곳의 실거래를 한 건씩(계약일 · 전용면적 · 층 · 금액). 최고가 · 최저가 · 최근 거래 · 층별 가격을 볼 때. "
            "kind: 매매 · 전세 · 월세 · 전월세. amount는 매매가 또는 보증금(원), 월세는 monthly_rent(원). "
            "order: recent(최근순, 기본) · price_desc(비싼 순, 최고가는 첫 줄) · price_asc(싼 순). "
            "최대 30줄이고 row_total이 전체 건수. 해제된 매매는 뺌. 월세를 값 순으로 보면 월세액 순. 단지명은 일부만 써도 되지만 "
            "여러 단지에 걸리고 이름이 똑같은 단지가 없으면 그 단지들이 섞여 나오니 줄마다 complex_name을 확인할 것. "
            "지금 데이터는 강남구(11680)만."),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "단지명 또는 그 일부. 예: 은마"},
                "kind": {"type": "string", "enum": ["매매", "전세", "월세", "전월세"], "description": "기본 매매"},
                "area_m2": {"type": "number", "description": "전용면적 ㎡. ±3㎡ 안을 같은 타입으로 봄. 없으면 모든 면적"},
                "months": {"type": "integer", "description": "오늘부터 거꾸로 몇 개월. 기본 12, 최대 36"},
                "order": {"type": "string", "enum": ["recent", "price_desc", "price_asc"], "description": "기본 recent"},
                "sigungu": {"type": "string", "description": "시군구 코드 5자리 또는 이름. 기본 11680(강남구)"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_deal_list(%s::text, %s::text, %s::text, %s::numeric, %s::int, %s::text)",
        "args": lambda a: (_sigungu(a.get("sigungu")), a["name"], a.get("kind") or "매매", a.get("area_m2"),
                           _months(a), a.get("order") or "recent"),
    },
    {
        "name": "q_complex_rank",
        "description": (
            "시군구 안 아파트 단지 순위(단지 이름 없이). metric: trade_count(매매 건수) · jeonse_count(전세 건수) · "
            "wolse_count(월세 건수) · trade_per_m2(매매 ㎡당 단가) · jeonse_per_m2(전세 ㎡당 단가) · jeonse_ratio(전세가율). "
            "단가 · 전세가율 순위는 그 거래가 3건 이상인 단지만. order: desc(큰 값부터, 기본) · asc(작은 값부터). min_build_year로 신축만 고를 수 있음"
            "(건축년도는 매매 신고 값이라 기간 안에 매매가 없던 단지는 빠질 수 있음). 최대 20줄, row_total은 순위에 든 단지 수. "
            "지금 데이터는 강남구(11680)만."),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": ["trade_count", "jeonse_count", "wolse_count",
                                                      "trade_per_m2", "jeonse_per_m2", "jeonse_ratio"]},
                "months": {"type": "integer", "description": "오늘부터 거꾸로 몇 개월. 기본 12, 최대 36"},
                "area_m2": {"type": "number", "description": "전용면적 ㎡(±3㎡)만 셀 때"},
                "min_build_year": {"type": "integer", "description": "이 해 이후 준공 단지만. 예: 2020"},
                "limit": {"type": "integer", "description": "몇 곳. 기본 10, 최대 20"},
                "order": {"type": "string", "enum": ["desc", "asc"], "description": "기본 desc"},
                "sigungu": {"type": "string", "description": "시군구 코드 5자리 또는 이름. 기본 11680(강남구)"},
            },
            "required": ["metric"],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_complex_rank(%s::text, %s::text, %s::int, %s::numeric, %s::int, %s::int, %s::text)",
        "args": lambda a: (_sigungu(a.get("sigungu")), a["metric"], _months(a), a.get("area_m2"),
                           a.get("min_build_year"), max(1, min(int(a.get("limit") or 10), 20)),
                           a.get("order") or "desc"),
    },
    {
        "name": "q_law_search",
        "description": (
            "부동산 법령 21개(국토계획법 · 건축법 · 주택법 · 주택임대차보호법 · 상가건물 임대차보호법 · "
            "공인중개사법 · 도시정비법 등과 시행령, 경매 · 공매용 민사집행법 · 민사집행규칙 · 국세기본법 · 국세징수법)의 현행 조문 검색. 낱말 절반 이상이 맞는 조문을 점수순으로 최대 10개. "
            "「100분의 5」 같은 표현은 「5%」로도 찾힘. 짧은 핵심 낱말 2~4개로 물을 것."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "찾을 낱말. 예: 차임 증액 청구"},
                "law_name": {"type": "string", "description": "법령명을 정확히 알 때만. 예: 주택임대차보호법"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_law_search(%s::text, %s::text)",
        "args": lambda a: (a["query"], a.get("law_name") or None),
    },
    {
        "name": "q_zoning_check",
        "description": (
            "용도지역에서 어떤 건물을 지을 수 있는지(가능 · 불가와 조건). 토지이용규제정보서비스 행위제한 기준, "
            "조례 규칙이 있으면 그것을 줌. 건물 종류는 아파트 · 휴게음식점 · 일반음식점 · 오피스텔 · 학원처럼 세부 이름으로. "
            "용도지역 이름: " + ", ".join(ZONES) + ". 최대 20줄. 지금 조례 데이터는 강남구(11680)만 있음."),
        "input_schema": {
            "type": "object",
            "properties": {
                "zone": {"type": "string", "description": "용도지역 이름(위 목록) 또는 UQ 코드"},
                "act": {"type": "string", "description": "건물 종류 세부 이름 또는 그 일부. 예: 휴게음식점"},
                "sigungu": {"type": "string", "description": "시군구 코드 5자리 또는 이름. 기본 11680(강남구)"},
            },
            "required": ["zone", "act"],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_zoning_check(%s::text, %s::text, %s::text)",
        "args": lambda a: (a["zone"], a["act"], _sigungu(a.get("sigungu"))),
    },
    {
        "name": "q_building_filter",
        "description": (
            "건축물대장 조건 검색(준공 경과연수 · 승강기 · 위반건축물 · 주용도 · 주차대수). 오래된 순 최대 50줄. "
            "아직 건축물대장을 수집하지 않아 대개 0건임. 0건이면 데이터가 없다고 답할 것."),
        "input_schema": {
            "type": "object",
            "properties": {
                "lawd_prefix": {"type": "string", "description": "법정동코드 앞자리. 예: 11680(강남구 전체)"},
                "min_age": {"type": "integer", "description": "준공 후 최소 연수"},
                "elevator": {"type": "boolean", "description": "승강기 있음/없음"},
                "violation": {"type": "boolean", "description": "위반건축물 여부"},
                "use": {"type": "string", "description": "주용도 일부. 예: 근린생활시설"},
                "min_parking": {"type": "integer", "description": "최소 주차대수"},
            },
            "required": ["lawd_prefix"],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_building_filter(%s::text, %s::int, %s::boolean, %s::boolean, %s::text, %s::int)",
        "args": lambda a: (a["lawd_prefix"], a.get("min_age"), a.get("elevator"), a.get("violation"),
                           a.get("use"), a.get("min_parking")),
    },
    {
        "name": "q_price_trend",
        "description": (
            "실거래가로 만든 월별 시세 추세(구 전체 · 동 · 단지). 달마다 매매 건수 · 매매 ㎡당 중위 단가(전용면적) · 평당 중위 단가 · "
            "전월 대비(trade_mom_pct) · 전년 같은 달 대비(trade_yoy_pct) %, 전세 건수 · 전세 ㎡당 중위 단가 · 전년 같은 달 대비 %. "
            "중위값이라 비싼 단지가 몰린 달에도 덜 튐. 그래도 건수가 적은 달(특히 단지 하나)은 값이 크게 흔들리니 건수와 함께 말할 것. "
            "partial=true인 달은 신고 기한(계약 후 30일)이 안 지나 거래가 덜 들어온 달이라 「잠정」이라고 밝힐 것. "
            "「요즘 강남 집값 어때」 「대치동 시세 추이」 「○○ 단지 가격 변화」에 씀. 최대 36개월. 강남구(11680) 아파트만."),
        "input_schema": {
            "type": "object",
            "properties": {
                "months": {"type": "integer", "description": "이번 달까지 몇 개월. 기본 24, 최대 36"},
                "emd": {"type": "string", "description": "법정동 이름. 예: 대치동. 없으면 구 전체"},
                "name": {"type": "string", "description": "단지명 일부 또는 「동 지번」. 주면 그 단지 하나(거래가 가장 많은 곳)"},
                "area_m2": {"type": "number", "description": "전용면적 ㎡(±3㎡)만 셀 때"},
                "sigungu": {"type": "string", "description": "시군구 코드 5자리 또는 이름. 기본 11680(강남구)"},
            },
            "required": [],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_price_trend(%s::text, %s::int, %s::text, %s::text, %s::numeric)",
        "args": lambda a: (_sigungu(a.get("sigungu")), max(1, min(int(a.get("months") or 24), 36)),
                           a.get("emd") or None, a.get("name") or None, a.get("area_m2")),
    },
    {
        "name": "q_land_price_trend",
        "description": (
            "개별공시지가(원/㎡) 연도별 추세. 해마다 필지 수 · 중위 공시지가 · 면적 가중 평균 · 전년 대비 상승률(yoy_pct, 두 해 모두 값이 있는 "
            "같은 필지끼리 비교한 상승률의 중위값) · 첫 해를 100으로 둔 누적 지수(index_100). 2013년부터. "
            "query가 동 이름(예: 대치동)이면 그 동, 단지명 · 「동 지번」이면 그 단지 필지, 비우면 구 전체. "
            "주의: 지금 가진 필지는 아파트 단지가 선 필지(강남구 1,168개)뿐이라 「구 전체」도 아파트 단지 필지의 공시지가임. 그렇게 밝힐 것. "
            "공시지가는 매년 1월 1일 기준, 5월 말쯤 공시. 강남구(11680)만."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "동 이름 · 단지명 일부 · 「동 지번」. 비우면 구 전체"},
                "from_year": {"type": "integer", "description": "이 해부터. 예: 2020. 없으면 2013년부터"},
                "sigungu": {"type": "string", "description": "시군구 코드 5자리 또는 이름. 기본 11680(강남구)"},
            },
            "required": [],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_land_price_trend(%s::text, %s::text, %s::int)",
        "args": lambda a: (_sigungu(a.get("sigungu")), a.get("query") or None, a.get("from_year")),
    },
    {
        "name": "q_price_vs_official",
        "description": (
            "공시지가 대비 실거래가. 단지마다 매매 ㎡당 중위 단가(전용면적, 최근 months개월) · 단지 필지의 최신 개별공시지가(토지 ㎡당, 면적 가중) · "
            "배수(price_multiple = 거래 단가 ÷ 공시지가) · 공시지가 전년 대비(land_yoy_pct) · 매매 단가 최근 12개월 대 그 앞 12개월(trade_yoy_pct) · 용적률. "
            "배수는 시세 반영률(현실화율)이 아님: 공시지가는 땅 ㎡당이고 거래 단가는 집 전용면적 ㎡당이라 면적 기준이 다르고, "
            "용적률이 높은 단지(한 필지에 집이 많음)일수록 배수가 작게 나옴. 답할 때 이 점을 한 줄 밝힐 것. "
            "query가 있으면 그 단지(최대 5), 없으면 매매 3건 이상 단지의 배수 순위(order desc 큰 순 · asc 작은 순, 최대 20). 강남구(11680)만."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "단지명 일부 또는 「동 지번」. 없으면 순위"},
                "months": {"type": "integer", "description": "매매 단가 기간(개월). 기본 12, 최대 36"},
                "limit": {"type": "integer", "description": "순위 몇 곳. 기본 10, 최대 20"},
                "order": {"type": "string", "enum": ["desc", "asc"], "description": "기본 desc"},
                "sigungu": {"type": "string", "description": "시군구 코드 5자리 또는 이름. 기본 11680(강남구)"},
            },
            "required": [],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_price_vs_official(%s::text, %s::text, %s::int, %s::int, %s::text)",
        "args": lambda a: (_sigungu(a.get("sigungu")), a.get("query") or None, _months(a),
                           max(1, min(int(a.get("limit") or 10), 20)), a.get("order") or "desc"),
    },
    {
        "name": "q_market_series",
        "description": (
            "시장 · 거시 지표의 월별 값. 지표: " + " · ".join(f"{s['id']}({s['name']})" for s in SERIES) + ". "
            "stat을 비우면 지표마다 가장 최근 값 한 줄씩(무엇이 있는지 볼 때). change는 전월 대비 차이, change_pct는 변화율, "
            "yoy_change · yoy_pct는 전년 같은 달 대비. 금리(%)는 차이를 %p로 말하고, 지수 · 인구는 변화율로 말할 것. "
            "출처는 한국은행 ECOS · 한국부동산원 R-ONE · KOSIS. 0건이면 그 지표는 아직 수집 전이니 「데이터 수집 전」이라고 말할 것. "
            "금리와 집값을 함께 물으면 이 도구와 q_price_trend를 같이 부름(두 값의 인과는 말하지 않음)."),
        "input_schema": {
            "type": "object",
            "properties": {
                "stat": {"type": "string", "enum": [s["id"] for s in SERIES], "description": "지표. 없으면 전체 목록"},
                "region": {"type": "string", "description": "전국 · 서울 · 강남구. 금리 · 물가는 전국만. 없으면 그 지표의 모든 지역"},
                "months": {"type": "integer", "description": "최근 몇 개월. 기본 24, 최대 120"},
            },
            "required": [],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_market_series(%s::text, %s::text, %s::int)",
        "args": lambda a: (a.get("stat") or None, a.get("region") or None, max(1, min(int(a.get("months") or 24), 120))),
    },
    {
        "name": "q_auction_discount",
        "description": (
            "공매 물건(한국자산관리공사 온비드)의 최저입찰가를 같은 단지 · 비슷한 면적(±3㎡)의 최근 실거래와 비교. 물건마다 현재 회차 한 줄: "
            "물건관리번호 · 물건명 · 주소 · 용도 · 면적 · 감정가 · 최저입찰가 · 감정가 대비 % · 유찰 횟수 · 입찰 기간 · 찾은 단지 · "
            "실거래 건수 · ㎡당 중위 단가 · 추정 시세(est_market_price = 단가 x 면적) · 실거래 대비 할인율(discount_pct) · label. "
            "할인율 큰 순. 단지는 주소의 「동 지번」으로 찾고, 못 찾거나 같은 면적 거래가 없으면 할인율이 비고 label에 이유가 있음. "
            "주의 세 가지를 답에 밝힐 것: ① 권리관계(선순위 임차인 · 유치권 등)는 보지 않아 할인이 커도 실제로는 비쌀 수 있음 — "
            "q_auction_checklist로 확인 항목을 함께 안내, ② 온비드 면적이 실거래의 전용면적과 기준이 다를 수 있음, "
            "③ 법원경매는 공식 API가 없어 들어 있지 않음. 0건이면 「공매 데이터 수집 전이거나 조건에 맞는 물건이 없음」. "
            "입찰 권유는 하지 않음. 강남구(11680)만."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "물건명 · 주소 · 용도 일부. 예: 아파트, 대치동"},
                "open_only": {"type": "boolean", "description": "입찰이 안 끝난 물건만. 기본 true"},
                "min_discount": {"type": "number", "description": "실거래 대비 할인율이 이 % 이상인 것만. 예: 15"},
                "months": {"type": "integer", "description": "비교할 실거래 기간(개월). 기본 12, 최대 36"},
                "limit": {"type": "integer", "description": "몇 건. 기본 20, 최대 30"},
                "sigungu": {"type": "string", "description": "시군구 코드 5자리 또는 이름. 기본 11680(강남구)"},
            },
            "required": [],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_auction_discount(%s::text, %s::text, %s::boolean, %s::numeric, %s::int, %s::int)",
        "args": lambda a: (_sigungu(a.get("sigungu")), a.get("query") or None,
                           True if a.get("open_only") is None else a["open_only"], a.get("min_discount"), _months(a),
                           max(1, min(int(a.get("limit") or 20), 30))),
    },
    {
        "name": "q_auction_checklist",
        "description": (
            "경매 · 공매 입찰 전에 확인할 권리 항목 14개(말소기준권리 · 선순위 임차인 · 배당요구 · 소액임차인 · 당해세 · 유치권 · "
            "대지권 · 관리비 · 위반건축물 · 매각물건명세서 · 입찰보증금 · 실제 점유 등). 항목마다 무엇을 · 어느 서류에서 보는지와 "
            "근거 조문(법령명 · 조문 번호, 수집돼 있으면 조문 제목 · 본문 앞부분). 「경매 권리분석 어떻게 해」 「말소기준권리가 뭐야」 "
            "「선순위 임차인 있으면 어떻게 돼」나 공매 물건을 안내할 때 부름. 개별 물건의 권리관계를 판단하는 도구가 아님: "
            "등기부 · 매각물건명세서는 갖고 있지 않으니 「서류를 직접 확인하고 복잡하면 전문가 상담」을 한 줄 덧붙일 것."),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["경매", "공매"], "description": "경매 · 공매 하나만. 없으면 전부"},
                "query": {"type": "string", "description": "항목 이름 일부. 예: 임차인, 유치권"},
            },
            "required": [],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_auction_checklist(%s::text, %s::text)",
        "args": lambda a: (a.get("kind") or None, a.get("query") or None),
    },
    {
        "name": "q_parcel_map",
        "description": (
            "단지명 · 「동 지번」 · PNU 하나로 그 땅의 지적도(연속지적도 필지 경계)와 건물 윤곽을 지도로 보여 줌. "
            "결과 줄은 대상 필지마다 번지 · 지목(한 글자) · 도형 면적(㎡) · 중심 경위도. 지도는 화면에 따로 그려지니 좌표를 답에 옮겨 적지 않음. "
            "「어디야」 「위치」 「지도」 「경계」 「모양」을 묻거나 q_property_card로 한 곳을 종합할 때 함께 부름. "
            "도형 면적은 연속지적도에서 잰 값이라 토지대장 면적(q_property_card)과 조금 다를 수 있고, "
            "연속지적도는 참고용 도면이라 측량 · 경계 확정에 쓸 수 없다고 말할 것. 강남구(11680)만."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "단지명 일부, 「동 지번」 또는 PNU 19자리. 예: 도곡렉슬, 대치동 1027"},
                "sigungu": {"type": "string", "description": "시군구 코드 5자리 또는 이름. 기본 11680(강남구)"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "sql": "SELECT * FROM mart.q_parcel_map(%s::text, %s::text)",
        "args": lambda a: (_sigungu(a.get("sigungu")), str(a["query"]).strip()),
    },
]
BY_NAME = {t["name"]: t for t in TOOLS}


def specs():
    """LLM에 넘길 도구 정의(이름 · 설명 · 입력 형식만)."""
    return [{k: t[k] for k in ("name", "description", "input_schema")} for t in TOOLS]


class ToolError(ValueError):
    pass


def check_args(name, args):
    """모델이 준 인자를 형식대로인지 확인. 틀리면 ToolError(모델에게 오류로 돌려줌)."""
    tool = BY_NAME.get(name)
    if tool is None:
        raise ToolError(f"없는 도구: {name}")
    if not isinstance(args, dict):
        raise ToolError("인자는 객체여야 함")
    schema = tool["input_schema"]
    props = schema["properties"]
    extra = set(args) - set(props)
    if extra:
        raise ToolError(f"모르는 인자: {', '.join(sorted(extra))}")
    for k in schema["required"]:
        if args.get(k) in (None, ""):
            raise ToolError(f"필수 인자 없음: {k}")
    kinds = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
    for k, v in args.items():
        want = kinds[props[k]["type"]]
        if v is not None and (not isinstance(v, want) or (want is not bool and isinstance(v, bool))):
            raise ToolError(f"{k}의 형식이 {props[k]['type']}가 아님")
        if v is not None and "enum" in props[k] and v not in props[k]["enum"]:
            raise ToolError(f"{k}는 {' · '.join(props[k]['enum'])} 중 하나")
    return tool


SCREEN_KEYS = ("geojson", "screen_only")   # 화면 전용 열


def for_model(out):
    """모델에게 넘길 모양: screen_only 줄과 화면 전용 열을 뺌. 뺀 줄이 있으면 screen_rows에 수만 남김."""
    rows = out.get("rows") or []
    if not any(k in r for r in rows for k in SCREEN_KEYS):
        return out
    keep = [{k: v for k, v in r.items() if k not in SCREEN_KEYS} for r in rows if not r.get("screen_only")]
    return {**out, "rows": keep, "screen_rows": len(rows) - len(keep)}


def _plain(v):
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, str) and len(v) > BODY_MAX:
        return v[:BODY_MAX] + f" …(뒤 {len(v) - BODY_MAX}자 생략)"
    return v


def run(conn, name, args):
    """도구 하나를 실행해 JSON으로 보낼 수 있는 dict로 돌려줌.
    {"tool", "args", "shown", "row_total", "sources", "asof", "rows"}"""
    tool = check_args(name, args)
    cur = conn.execute(tool["sql"], tool["args"](args))
    cols = [d.name for d in cur.description]
    rows = [{c: v if c in SCREEN_KEYS else _plain(v) for c, v in zip(cols, r)} for r in cur.fetchall()]
    total = rows[0].pop("row_total") if rows else 0
    for r in rows[1:]:
        r.pop("row_total", None)
    sources = sorted({r["source"] for r in rows if r.get("source")})
    asof = max((r["asof"] for r in rows if r.get("asof")), default=None)
    for r in rows:   # 출처 · 기준일은 위로 한 번만 올림(줄마다 반복하면 토큰 낭비)
        r.pop("source", None)
        r.pop("asof", None)
    return {"tool": name, "args": args, "shown": sum(1 for r in rows if not r.get("screen_only")), "row_total": total,
            "sources": sources, "asof": asof, "rows": rows}
