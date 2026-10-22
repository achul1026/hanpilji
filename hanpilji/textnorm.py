"""검색용 문장 보강.

이너시티 실측: 조문은 「연 100분의 5」로 쓰는데 사용자는 「5%」로 물어 검색이 안 됐음.
그래서 조문 본문 뒤에 같은 뜻의 퍼센트 표기를 덧붙인 search_text를 따로 만듦.
"""
import re

_FRACTION = re.compile(r"(\d+(?:,\d{3})*)\s*분의\s*(\d+(?:\.\d+)?)")


def _pct(denominator, numerator):
    value = float(numerator) / float(denominator.replace(",", "")) * 100
    return f"{value:g}%"


def percent_aliases(text):
    """「100분의 5」 → 「5%」, 「20분의 1」 → 「5%」 처럼 본문에 나온 분수를 퍼센트로 바꾼 목록."""
    seen = []
    for d, n in _FRACTION.findall(text):
        p = _pct(d, n)
        if p not in seen:
            seen.append(p)
    return seen


def search_text(body):
    aliases = percent_aliases(body)
    return body if not aliases else f"{body}\n[같은 뜻: {' '.join(aliases)}]"
