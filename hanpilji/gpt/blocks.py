"""도구 결과 → 화면 블록(표 · 그래프 · 조문 · 단지 카드). 웹 화면의 「근거 서랍」에 그대로 그림.

블록 값은 모델이 옮겨 쓴 것이 아니라 도구 결과(mart 함수가 돌려준 줄) 그대로라서,
모델이 숫자를 잘못 옮겨도 블록 속 값은 원본과 같음. 도구마다 어떤 모양으로 보일지만 여기서 정함.

블록 공통: {"ref", "tool", "args", "kind", "title", "source": {"sources", "asof", "shown", "row_total"}, "requery"}
  table    : "columns": [{"key", "label", "fmt"}], "rows"
  chart    : "x", "series": [{"key", "label", "fmt"}], "count" (점 크기에 쓸 건수 열), "partial" (잠정 표시 열), "rows"
  document : "items": [{"title", "subtitle", "body", "meta", "url"}], "highlight": [낱말]
  card     : "rows" (q_property_card 줄 그대로)
  map      : "features": [{"layer", "pnu", "label", "jibun", "jimok", "area_m2", "floors_above", "lon", "lat", "geometry"}],
             "note". layer는 대상 필지 · 이웃 필지 · 건물, geometry는 경위도 GeoJSON
fmt: won(원 → 억 · 만) · won_m2 · won_py · int · pct · pp · m2 · num · date · ym · text · allowed
"""
import json
from urllib.parse import quote

from hanpilji.gpt import tools as tools_mod

# 화면에서 기간만 바꿔 다시 조회할 수 있는 도구(모델을 다시 부르지 않음 · 요금 없음)
# 도구 → (고를 수 있는 개월 수, 인자를 비웠을 때 도구가 쓰는 기본값)
MONTHS_CHOICES = {
    "q_price_trend": ([12, 24, 36], 24), "q_deal_list": ([6, 12, 36], 12), "q_complex_rank": ([6, 12, 36], 12),
    "q_complex_price": ([6, 12, 36], 12), "q_auction_discount": ([6, 12, 36], 12), "q_price_vs_official": ([6, 12, 36], 12), "q_market_series": ([24, 60, 120], 24),
}

TOOL_LABEL = {
    "q_property_card": "단지 종합 카드", "q_complex_price": "면적별 시세", "q_deal_list": "실거래 목록",
    "q_complex_rank": "단지 순위", "q_law_search": "법령 조문", "q_zoning_check": "행위제한(지을 수 있는 건물)",
    "q_building_filter": "건축물대장 검색", "q_price_trend": "월별 시세 추이", "q_land_price_trend": "공시지가 추이",
    "q_price_vs_official": "공시지가 대비 실거래가", "q_market_series": "시장 지표",
    "q_auction_discount": "공매 물건 · 실거래 대비", "q_auction_checklist": "입찰 전 권리 확인 항목",
    "q_parcel_map": "지적도 · 건물 윤곽",
}

# 진행 표시 문구(도구를 부르는 동안 화면에 보임)
TOOL_DOING = {
    "q_property_card": "단지 기록을 모으는 중", "q_complex_price": "실거래가에서 시세를 셈하는 중",
    "q_deal_list": "실거래 목록을 찾는 중", "q_complex_rank": "단지 순위를 매기는 중",
    "q_law_search": "법령 조문을 찾는 중", "q_zoning_check": "행위제한 규칙을 찾는 중",
    "q_building_filter": "건축물대장을 찾는 중", "q_price_trend": "월별 시세 추이를 만드는 중",
    "q_land_price_trend": "공시지가 추이를 만드는 중", "q_price_vs_official": "공시지가와 실거래가를 맞대는 중",
    "q_market_series": "시장 지표를 찾는 중", "q_auction_discount": "공매 물건을 실거래와 맞대는 중",
    "q_auction_checklist": "입찰 전 확인 항목을 찾는 중", "q_parcel_map": "지적도를 펼치는 중",
}

COL = {   # 열 이름 → (보이는 이름, 형식)
    "rank": ("순위", "int"), "complex_name": ("단지", "text"), "emd": ("동", "text"), "build_year": ("준공", "int"),
    "area_m2": ("전용", "m2"), "kind": ("구분", "text"), "contract_date": ("계약일", "date"), "floor": ("층", "int"),
    "amount": ("금액", "won"), "monthly_rent": ("월세", "won"), "contract_kind": ("계약", "text"),
    "trade_count": ("매매 건수", "int"), "trade_avg": ("매매 평균", "won"), "trade_per_m2": ("매매 ㎡당", "won_m2"),
    "trade_per_pyeong": ("매매 평당", "won_py"), "trade_last_date": ("마지막 매매", "date"),
    "trade_last_amount": ("마지막 매매가", "won"), "jeonse_count": ("전세 건수", "int"), "jeonse_avg": ("전세 평균", "won"),
    "jeonse_per_m2": ("전세 ㎡당", "won_m2"), "jeonse_ratio": ("전세가율", "pct"), "wolse_count": ("월세 건수", "int"),
    "trade_median_per_m2": ("매매 ㎡당 중위", "won_m2"), "land_price_per_m2": ("공시지가 ㎡당", "won_m2"),
    "land_price_year": ("공시 연도", "int"), "price_multiple": ("배수", "num"), "land_yoy_pct": ("공시지가 전년비", "pct"),
    "trade_yoy_pct": ("매매 단가 전년비", "pct"), "vl_rat": ("용적률", "pct"),
    "zone_name": ("용도지역", "text"), "act_name": ("건물", "text"), "allowed": ("가능", "allowed"),
    "condition_text": ("조건", "text"), "scope": ("기준", "text"),
    "stat_name": ("지표", "text"), "region": ("지역", "text"), "period": ("기준월", "ym"), "value": ("값", "num"),
    "unit": ("단위", "text"), "change": ("전월 대비", "num"), "yoy_pct": ("전년비", "pct"),
    "name": ("물건", "text"), "usage": ("용도", "text"), "appraisal": ("감정가", "won"), "min_bid": ("최저입찰가", "won"),
    "min_bid_pct_of_appraisal": ("감정가 대비", "pct"), "fail_count": ("유찰", "int"), "bid_end": ("입찰 마감", "text"),
    "est_market_price": ("실거래 기준 추정", "won"), "discount_pct": ("실거래 대비 할인", "pct"), "label": ("비고", "text"),
    "step": ("순서", "int"), "item": ("항목", "text"), "what_to_check": ("무엇을", "text"), "where_to_look": ("어디서", "text"),
    "article_no": ("조문", "text"),
}

TABLE_COLS = {
    "q_complex_price": ["complex_name", "area_m2", "trade_count", "trade_avg", "trade_per_pyeong", "trade_last_date",
                        "trade_last_amount", "jeonse_count", "jeonse_avg", "jeonse_ratio"],
    "q_deal_list": ["contract_date", "complex_name", "kind", "area_m2", "floor", "amount", "monthly_rent"],
    "q_complex_rank": ["rank", "complex_name", "build_year", "trade_count", "trade_per_pyeong", "jeonse_count",
                       "jeonse_ratio", "wolse_count"],
    "q_zoning_check": ["zone_name", "act_name", "allowed", "condition_text", "scope"],
    "q_price_vs_official": ["rank", "complex_name", "build_year", "trade_count", "trade_median_per_m2",
                            "land_price_per_m2", "price_multiple", "land_yoy_pct", "trade_yoy_pct"],
    "q_market_series": ["stat_name", "region", "period", "value", "unit", "change", "yoy_pct"],
    "q_auction_discount": ["rank", "name", "usage", "area_m2", "appraisal", "min_bid", "min_bid_pct_of_appraisal",
                           "fail_count", "bid_end", "complex_name", "est_market_price", "discount_pct", "label"],
    "q_auction_checklist": ["step", "item", "what_to_check", "where_to_look", "article_no"],
}

LAW_URL = "https://www.law.go.kr/법령/{law}/{article}"


def _columns(keys, rows):
    """보일 열: 정한 순서대로, 결과에 있고 값이 하나라도 있는 열만. 정해 둔 게 없으면 결과 열 전부."""
    have = set().union(*(r.keys() for r in rows)) if rows else set()
    keys = [k for k in keys if k in have] if keys else sorted(have)
    keys = [k for k in keys if any(r.get(k) not in (None, "") for r in rows)]
    return [{"key": k, "label": COL.get(k, (k, ""))[0], "fmt": COL.get(k, ("", "text"))[1]} for k in keys]


def _title(out):
    a, tool, rows = out.get("args") or {}, out["tool"], out.get("rows") or []
    label = TOOL_LABEL.get(tool, tool)
    if tool == "q_zoning_check":
        return f"{a.get('zone', '')} · {a.get('act', '')} {label}"
    if tool == "q_law_search":
        return " · ".join(x for x in (a.get("law_name") or "법령 조문", a.get("query")) if x)
    if tool == "q_market_series":
        names = list(dict.fromkeys(r.get("stat_name") for r in rows if r.get("stat_name")))
        return names[0] if len(names) == 1 else label
    subject = " ".join(str(x) for x in (a.get("name") or a.get("query") or a.get("emd"),
                                         a.get("area_m2") and f"{a['area_m2']:g}㎡", a.get("kind")) if x)
    return f"{subject} {label}".strip()


def _law(out):
    items = []
    for r in out["rows"]:
        art = r.get("article_no") or ""
        items.append({"title": f"{r.get('law_name', '')} {art}".strip(), "subtitle": r.get("article_title") or "",
                      "body": r.get("body") or "", "meta": f"시행 {r['effective_date']}" if r.get("effective_date") else "",
                      "url": LAW_URL.format(law=quote(r.get("law_name", "")), article=quote(art)) if art else None})
    words = [w for w in str((out.get("args") or {}).get("query", "")).split() if len(w) >= 2]
    return {"kind": "document", "items": items, "highlight": words}


def _market(out):
    rows = out["rows"]
    if not (out.get("args") or {}).get("stat") or len({r.get("period") for r in rows}) < 3:
        return {"kind": "table", "columns": _columns(TABLE_COLS["q_market_series"], rows), "rows": rows}
    # 지표 하나 · 여러 지역이면 지역마다 선 하나. 기준월(YYYYMM)을 x로
    regions = list(dict.fromkeys(r.get("region") for r in rows))
    pivot = {}
    for r in rows:
        pivot.setdefault(r["period"], {"period": r["period"]})[f"v{regions.index(r.get('region'))}"] = r.get("value")
    unit = rows[0].get("unit") or ""
    return {"kind": "chart", "x": "period", "x_fmt": "ym", "rows": [pivot[p] for p in sorted(pivot)],
            "series": [{"key": f"v{i}", "label": f"{reg} ({unit})" if unit else reg, "fmt": "num"}
                       for i, reg in enumerate(regions)],
            "note": rows[0].get("note") or ""}


MAP_NOTE = "연속지적도는 참고용 도면이라 측량 · 경계 확정에 쓸 수 없음"


def _map(out):
    feats = []
    for r in out["rows"]:
        g = r.get("geojson")
        if not g:
            continue
        f = {k: r.get(k) for k in ("layer", "pnu", "label", "jibun", "jimok", "area_m2", "floors_above", "lon", "lat")}
        f["geometry"] = json.loads(g) if isinstance(g, str) else g
        feats.append(f)
    return {"kind": "map", "features": feats, "note": MAP_NOTE}


def _shape(out):
    tool, rows = out["tool"], out["rows"]
    if tool == "q_law_search":
        return _law(out)
    if tool == "q_property_card":
        return {"kind": "card", "rows": rows}
    if tool == "q_parcel_map":
        return _map(out)
    if tool == "q_price_trend" and rows:
        return {"kind": "chart", "x": "month", "x_fmt": "ym", "rows": rows, "count": "trade_count", "partial": "partial",
                "series": [{"key": "trade_median_per_pyeong", "label": "매매 평당 중위가", "fmt": "won_py"},
                           {"key": "jeonse_median_per_m2", "label": "전세 ㎡당 중위가", "fmt": "won_m2"}],
                "note": rows[0].get("scope") or ""}
    if tool == "q_land_price_trend" and rows:
        return {"kind": "chart", "x": "base_year", "x_fmt": "int", "rows": rows, "count": "parcel_count",
                "series": [{"key": "median_per_m2", "label": "공시지가 ㎡당 중위", "fmt": "won_m2"}],
                "note": rows[0].get("scope") or ""}
    if tool == "q_market_series" and rows:
        return _market(out)
    return {"kind": "table", "columns": _columns(TABLE_COLS.get(tool), rows), "rows": rows}


def block(out):
    """tools.run 결과 하나(ref 붙은 것) → 블록."""
    b = {"ref": out.get("ref"), "tool": out["tool"], "args": out.get("args") or {}, "title": _title(out),
         "source": {k: out.get(k) for k in ("sources", "asof", "shown", "row_total")}}
    b.update(_shape(out))
    spec = tools_mod.BY_NAME.get(out["tool"], {}).get("input_schema", {}).get("properties", {})
    if "months" in spec and out["tool"] in MONTHS_CHOICES:
        choices, default = MONTHS_CHOICES[out["tool"]]
        b["requery"] = {"param": "months", "label": "기간", "choices": choices,
                        "current": b["args"].get("months") or default}
    return b


def blocks(results):
    return [block(o) for o in results]


# 답 아래 「이어서 볼 것」. 모델 없이 마트 함수 하나만 부르는 것(요금 없음)이라 /api/tool로 바로 엶
FOLLOW_TOOLS = {"q_property_card", "q_price_trend", "q_land_price_trend", "q_price_vs_official", "q_parcel_map",
                "q_deal_list"}


def _subject(results):
    """답이 다룬 단지 하나(단지명). 카드 · 시세 결과에서 찾고, 없으면 None."""
    for out in results:
        rows = out.get("rows") or []
        if out["tool"] == "q_property_card" and len(rows) == 1 and rows[0].get("complex_name"):
            return rows[0]["complex_name"]
    for out in results:
        name = (out.get("args") or {}).get("name") or (out.get("args") or {}).get("query")
        if out["tool"] in ("q_complex_price", "q_deal_list", "q_price_trend", "q_parcel_map") and name:
            return str(name)
    return None


def next_steps(results, limit=4):
    """답에서 다룬 단지로 바로 열 수 있는 다음 조회 몇 개. [{"label", "tool", "args"}]"""
    name = _subject(results)
    if not name:
        return []
    done = {out["tool"] for out in results}
    steps = [
        ("q_property_card", f"{name} 종합 카드", {"query": name}),
        ("q_price_trend", f"{name} 월별 시세 추이", {"name": name}),
        ("q_deal_list", f"{name} 최근 실거래 목록", {"name": name}),
        ("q_land_price_trend", f"{name} 공시지가 추이", {"query": name}),
        ("q_price_vs_official", "공시지가 대비 실거래가", {"query": name}),
        ("q_parcel_map", f"{name} 지적도", {"query": name}),
    ]
    return [{"label": label, "tool": tool, "args": args} for tool, label, args in steps if tool not in done][:limit]
