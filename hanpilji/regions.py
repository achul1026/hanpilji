"""시군구 코드 묶음. 수집기 --sigungu 에 5자리 코드 대신 묶음 이름(seoul)을 줄 수 있게 함."""

# 서울 25개 구(법정동 코드 앞 5자리)
SEOUL = (
    "11110", "11140", "11170", "11200", "11215", "11230", "11260", "11290", "11305", "11320",
    "11350", "11380", "11410", "11440", "11470", "11500", "11530", "11545", "11560", "11590",
    "11620", "11650", "11680", "11710", "11740",
)
GROUPS = {"seoul": SEOUL}


def expand(codes):
    """["seoul", "41135"] → 서울 25개 구 + 41135. 순서 유지, 중복 제거. 모르는 이름 · 형식이면 ValueError."""
    out = []
    for c in codes:
        c = str(c).strip()
        group = GROUPS.get(c.lower())
        if group is None and not (len(c) == 5 and c.isdigit()):
            raise ValueError(f"시군구는 5자리 코드 또는 {', '.join(GROUPS)} 중 하나: {c!r}")
        for code in group or (c,):
            if code not in out:
                out.append(code)
    return out
