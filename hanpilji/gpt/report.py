"""필지 리포트 · 두 단지 비교. 모델을 부르지 않고 마트 함수 몇 개만 차례로 불러 한 장으로 모음(요금 없음).

리포트: 단지 · 주소 하나 → 종합 카드, 지적도, 월별 시세 추이, 공시지가 추이, 공시지가 대비 실거래가, 주택 공시가격.
비교: 단지 둘 → 각각의 종합 카드와 월별 시세 추이.
이름이 여러 단지에 맞으면 조회를 더 하지 않고 고를 이름(choices)만 돌려줌.
auto: 질문 글만 보고 리포트 · 비교로 열지 정함(탭 없이). 아니면 None이라 화면이 모델에게 물음.
"""
import re

from hanpilji.gpt import blocks, tools

# 카드로 단지 하나를 정한 다음 그 단지 이름으로 부르는 도구와 인자 이름
REPORT_TOOLS = [("q_parcel_map", "query"), ("q_price_trend", "name"), ("q_land_price_trend", "query"),
                ("q_price_vs_official", "query"), ("q_house_price", "query")]
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



# ---------------------------------------------------------------- 질문 의도(모델 없이 글 모양만 봄)
_END = r"\s*(?:보여\s*줘|알려\s*줘|해\s*줘|해\s*주세요|줘|주세요|해\s*봐|하면)?\s*[?？.!~]*$"
COMPARE_WORD = re.compile(r"\s*(?:을|를)?\s*(?:비교|어디가\s*(?:더\s*)?(?:나아|나을까|좋아|좋을까)|중\s*(?:어디|어느\s*곳|뭐)\S*)" + _END)
REPORT_WORD = re.compile(r"\s*(?:의\s*)?(?:필지\s*)?(?:리포트|보고서|종합\s*(?:정보|카드)?|한눈에\s*\S*|모든\s*정보|총정리|정리)" + _END)
PAIR = re.compile(r"^(.+?)(?:\s*[,/]\s*|\s+vs\.?\s+|(?<=\S)(?:이랑|랑|와|과|하고)\s+)(.+)$", re.I)
PAIR_BARE = re.compile(r"^(.+?)(?:\s*[,/]\s*|\s+vs\.?\s+)(.+)$", re.I)   # 「비교」 없이 두 이름만 넣은 경우
AREA = re.compile(r"\s*\d+(?:\.\d+)?\s*(?:㎡|m2|m²|평)형?")
NAME = re.compile(r"^[가-힣A-Za-z0-9][가-힣A-Za-z0-9 \-]{0,24}$")
# 값 · 조건을 묻는 말이 섞이면 모델이 답함(리포트 · 비교표는 단지 전체를 한 장으로 보여 줄 때만)
ASK_WORD = re.compile(r"얼마|시세|전세|월세|매매|가격|평당|법|규제|가능|몇|언제|어떻게|왜|될까|돼|나요|대출|세금|LTV|허가|용적률|건폐율|"
                      r"추이|변동|거래|사도|팔|살까|어때|어떤|뭐|무엇|있|없|강남구|서울|아파트$|[구시]$", re.I)
MAX_BARE_CHOICES = 5


def _name(t):
    t = AREA.sub("", t or "").strip(" ,./")
    return t if NAME.match(t) and not ASK_WORD.search(t) and len(t.split()) <= 3 else None


def intent(q):
    """("compare", 가, 나) · ("report", 이름, 키워드로 물었는지) · None(모델에게)."""
    q = " ".join((q or "").split())
    m = COMPARE_WORD.search(q)
    rest = q[:m.start()] if m else q
    p = (PAIR if m else PAIR_BARE).match(rest)
    if p:
        a, b = _name(p.group(1)), _name(p.group(2))
        return ("compare", a, b) if a and b and a != b else None
    if m:
        return None
    m = REPORT_WORD.search(q)
    name = _name(q[:m.start()] if m else q)
    return ("report", name, bool(m)) if name else None


def auto(conn, q):
    """의도가 리포트 · 비교면 그 결과({"kind", ...}), 아니면 None.
    맞는 단지가 없거나, 이름만 넣었는데 너무 여럿이 맞으면 None(모델이 되묻게)."""
    it = intent(q)
    if not it:
        return None
    try:
        if it[0] == "compare":
            return {"kind": "compare", "pair": list(it[1:]), **compare(conn, it[1], it[2])}
        return {"kind": "report", **report(conn, it[1])}
    except NotOne as e:
        bare = it[0] == "report" and not it[2]
        if not e.choices or (bare and len(e.choices) > MAX_BARE_CHOICES):
            return None
        out = {"kind": it[0], "query": e.query, "choices": e.choices[:5]}
        return {**out, "pair": list(it[1:])} if it[0] == "compare" else out
