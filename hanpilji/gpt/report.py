"""필지 리포트 · 두 단지 비교. 모델을 부르지 않고 마트 함수 몇 개만 차례로 불러 한 장으로 모음(요금 없음).

리포트: 단지 · 주소 하나 → 종합 카드, 지적도, 월별 시세 추이, 공시지가 추이, 공시지가 대비 실거래가.
비교: 단지 둘 → 각각의 종합 카드와 월별 시세 추이.
이름이 여러 단지에 맞으면 조회를 더 하지 않고 고를 이름(choices)만 돌려줌.
"""
from hanpilji.gpt import blocks, tools

# 카드로 단지 하나를 정한 다음 그 단지 이름으로 부르는 도구와 인자 이름
REPORT_TOOLS = [("q_parcel_map", "query"), ("q_price_trend", "name"), ("q_land_price_trend", "query"),
                ("q_price_vs_official", "query")]
COMPARE_TOOLS = [("q_price_trend", "name")]
MAX_QUERY = 100


class NotOne(Exception):
    """이름에 맞는 단지가 없거나 여럿."""

    def __init__(self, query, choices):
        super().__init__(query)
        self.query, self.choices = query, choices


def _card(conn, query):
    out = tools.run(conn, "q_property_card", {"query": query})
    rows = out["rows"]
    names = [r["complex_name"] for r in rows if r.get("complex_name")]
    # 이름이 정확히 같은 단지가 하나면 그것으로(「래미안」처럼 일부만 맞는 이름이 여럿 섞여도)
    exact = [r for r in rows if r.get("complex_name") == query]
    if len(rows) != 1 and len(exact) == 1:
        out = {**out, "rows": exact, "shown": 1, "row_total": 1}
    elif len(rows) != 1:
        raise NotOne(query, names)
    return out


def _more(conn, name, specs, results):
    for tool, arg in specs:
        try:
            out = tools.run(conn, tool, {arg: name})
        except tools.ToolError:
            continue
        except Exception:   # 한 도구가 실패해도 나머지는 보여 줌
            conn.rollback()
            continue
        results.append(out)


def _numbered(results):
    return [blocks.block({**out, "ref": i}) for i, out in enumerate(results, 1)]


def report(conn, query):
    """{"subject": 단지명, "blocks": [...]} 또는 NotOne."""
    card = _card(conn, query)
    name = card["rows"][0]["complex_name"]
    results = [card]
    _more(conn, name, REPORT_TOOLS, results)
    return {"subject": name, "blocks": _numbered(results)}


def compare(conn, a, b):
    """{"subjects": [가, 나], "blocks": [카드 가, 카드 나, 추이 가, 추이 나 ...]} 또는 NotOne."""
    cards = [_card(conn, a), _card(conn, b)]
    names = [c["rows"][0]["complex_name"] for c in cards]
    results = list(cards)
    for name in names:
        _more(conn, name, COMPARE_TOOLS, results)
    return {"subjects": names, "blocks": _numbered(results)}

