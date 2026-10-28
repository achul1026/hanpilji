"""답 속 숫자 검산. 모델이 쓴 숫자를 그 답에서 부른 도구 결과(원자료)와 맞춰 봄.

모델이 숫자를 잘못 옮겨 적어도 사용자가 알 수 있게, 답에 나온 금액 · 비율 · 면적 · 건수를 뽑아
원자료에 같은 값(답의 반올림 자리까지)이 있는지 찾음. 못 찾은 숫자가 꼭 틀린 것은 아님(모델이 더하거나 나눈 값,
법 조문 속 숫자 등). 그래서 화면에는 「일치」와 「원자료에서 바로 찾지 못함」으로만 나눠 보여 줌.
"""
import json
import re

# 숫자 하나: 1,234 · 12.5
N = r"\d[\d,]*(?:\.\d+)?"
MONEY = re.compile(rf"(?:({N})\s*억)?\s*(?:({N})\s*만)?\s*(원)?")
TOKEN = re.compile(
    rf"(?P<area>(?:{N}\s*만\s*)?{N})\s*(?:㎡|m²)"
    rf"|(?P<money>(?:{N}\s*억(?:\s*{N}\s*만)?|{N}\s*만)(?:\s*원)?(?:\s*/\s*(?:㎡|평))?)"
    rf"|(?P<pct>{N})\s*%(?!p)"
    rf"|(?P<count>{N})\s*(?:건|세대|가구|대|동|층|개(?!월)|곳|필지)"
)
SKIP_BEFORE = re.compile(r"(제\s*|\[|\d{4}-|\d{4}\.)$")   # 조문 번호 · 근거 번호 · 날짜 조각
YEARISH = re.compile(r"^(19|20)\d\d$")


def _num(s):
    return float(s.replace(",", ""))


def _decimals(s):
    return len(s.split(".")[1]) if "." in s else 0


def tokens(text):
    """답 글에서 검산할 숫자를 뽑음. [(보이는 글, 값, 허용 오차, 종류)]"""
    out = []
    for m in TOKEN.finditer(text or ""):
        if SKIP_BEFORE.search(text[max(0, m.start() - 5):m.start()]):
            continue
        shown = m.group(0).strip()
        if m.group("money"):
            mm = MONEY.match(m.group("money"))
            eok, man = mm.group(1), mm.group(2)
            if eok is None and man is None:
                continue
            value = (_num(eok) * 1e8 if eok else 0) + (_num(man) * 1e4 if man else 0)
            # 마지막에 적은 자리까지 반올림했다고 봄(「35억」 → 억 자리, 「35억 6,000만」 → 만 자리)
            unit = 1e4 * 10 ** -_decimals(man) if man else 1e8 * 10 ** -_decimals(eok)
            out.append((shown, value, unit / 2 + 1, "money"))
        elif m.group("pct"):
            s = m.group("pct")
            if _num(s) < 10 and "." not in s:   # 한 자리 정수 비율은 아무 값과도 우연히 맞기 쉬워 세지 않음
                continue
            out.append((shown, _num(s), 0.5 * 10 ** -_decimals(s) + 1e-9, "pct"))
        elif m.group("area"):   # 「13만 8,750㎡」처럼 만 단위로 읽은 면적도
            parts = [x for x in re.split(r"\s*만\s*", m.group("area")) if x]
            s = parts[-1]
            value = _num(s) + (_num(parts[0]) * 1e4 if len(parts) == 2 else 0)
            out.append((shown, value, 0.5 * 10 ** -_decimals(s) + 1e-9, "area"))
        elif m.group("count"):
            s = m.group("count")
            if YEARISH.match(s.replace(",", "")) or "." in s or _num(s) < 10:   # 연도 · 한 자리 수는 세지 않음
                continue
            out.append((shown, _num(s), 0.5, "count"))
    return out


def _walk(v, acc):
    if isinstance(v, bool) or v is None:
        return
    if isinstance(v, (int, float)):
        acc.append(float(v))
    elif isinstance(v, str):
        s = v.strip()
        if s[:1] in "[{":   # 줄 속에 JSON 글자로 든 목록(최근 거래 등)
            try:
                return _walk(json.loads(s), acc)
            except ValueError:
                pass
        for x in re.findall(N, s):
            acc.append(_num(x))
    elif isinstance(v, dict):
        for x in v.values():
            _walk(x, acc)
    elif isinstance(v, (list, tuple)):
        for x in v:
            _walk(x, acc)


def raw_values(results):
    acc = []
    for out in results or []:
        _walk(out.get("rows"), acc)
    return acc


def _found(value, tol, kind, raw):
    for r in raw:
        if abs(r - value) <= tol:
            return True
        if kind == "pct" and abs(r * 100 - value) <= tol:   # 0.421 처럼 비율로 든 값
            return True
    return False


def check(text, results):
    """{"checked", "matched", "items": [{"text", "ok"}]}. 원자료가 없거나 숫자가 없으면 checked 0."""
    raw = raw_values(results)
    items, seen = [], set()
    if raw:
        for shown, value, tol, kind in tokens(text):
            key = (kind, value)
            if key in seen:
                continue
            seen.add(key)
            items.append({"text": shown, "ok": _found(value, tol, kind, raw)})
    return {"checked": len(items), "matched": sum(i["ok"] for i in items), "items": items}
