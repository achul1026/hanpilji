"""GPT가 부를 수 있는 도구 = mart 함수 6개. GPT는 SQL을 짜지 않고 이름과 인자만 고름.

도구 결과에는 보여 준 줄 수(shown) · 전체 건수(row_total) · 출처 · 기준일을 늘 붙여서
엔진이 답 끝에 근거를 기계적으로 달 수 있게 함.
"""
import datetime
import decimal

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
            "부동산 법령 17개(국토계획법 · 건축법 · 주택법 · 주택임대차보호법 · 상가건물 임대차보호법 · "
            "공인중개사법 · 도시정비법 등과 시행령)의 현행 조문 검색. 낱말 절반 이상이 맞는 조문을 점수순으로 최대 10개. "
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


def _plain(v):
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
    rows = [dict(zip(cols, map(_plain, r))) for r in cur.fetchall()]
    total = rows[0].pop("row_total") if rows else 0
    for r in rows[1:]:
        r.pop("row_total", None)
    sources = sorted({r["source"] for r in rows if r.get("source")})
    asof = max((r["asof"] for r in rows if r.get("asof")), default=None)
    for r in rows:   # 출처 · 기준일은 위로 한 번만 올림(줄마다 반복하면 토큰 낭비)
        r.pop("source", None)
        r.pop("asof", None)
    return {"tool": name, "args": args, "shown": len(rows), "row_total": total,
            "sources": sources, "asof": asof, "rows": rows}
