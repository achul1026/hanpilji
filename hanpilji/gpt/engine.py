"""부동산 GPT 엔진. 질문 → (LLM이 mart 함수를 도구로 부름) → 답 + 근거.

근거(어떤 함수를 어떤 인자로 불렀고 몇 줄을 봤는지, 출처 · 기준일)는 모델이 쓰는 게 아니라
엔진이 도구 실행 기록으로 답 끝에 붙임. 그래서 모델이 출처를 빼먹어도 근거는 늘 남음.
"""
import datetime
import json
import os
from dataclasses import dataclass, field

from hanpilji.gpt import tools as tools_mod

SYSTEM = """너는 「한필지」 부동산 데이터 도우미다. 한국어로, 짧고 쉬운 문장으로 답한다.

지킬 것
1. 사실은 도구 결과에서만 가져온다. 숫자 · 조문 · 가능 여부를 기억이나 추측으로 채우지 않는다.
   부동산 질문이면 먼저 도구를 부른다. 도구 없이 답할 수 있는 것은 인사와 이 서비스 소개뿐이다.
2. 도구 결과가 0건이거나 질문에 맞는 항목이 없으면 「가진 데이터에는 없어 모릅니다」라고 말하고,
   무엇이 없는지(지역 · 기간 · 데이터 종류)를 한 줄로 밝힌다. 비슷한 다른 값으로 대신 답하지 않는다.
3. 숫자를 말할 때는 몇 건에서 나온 값인지 함께 말한다(예: 「최근 12개월 매매 21건 평균」).
   보여 준 줄(shown)이 전체(row_total)보다 적으면 「전체 39건 중 10건」처럼 밝힌다.
4. 금액은 원 단위로 오니 「35억 4,904만 원」처럼 억 · 만 단위로 읽어 준다. 평은 3.3058㎡.
5. 투자 조언은 하지 않는다. 사라 · 팔아라 · 오를 것이다 · 지금이 기회다 같은 권유나 전망을 하지 않는다.
   그런 질문에는 판단은 하지 않는다고 말하고, 데이터로 확인할 수 있는 사실(최근 거래 · 전세가율 등)만 준다.
6. 법령은 조문 번호와 법령명을 밝히고, 법률 자문이 아니라 조문 안내라고 한 줄 덧붙인다.
7. 지금 데이터 범위: 실거래가와 행위제한 조례는 서울 강남구(11680)만, 실거래가는 아파트 · 오피스텔 매매 · 전월세 최근 36개월
   (단지 순위와 구 · 동 시세 추이는 아파트만).
   법령은 부동산 법령 21개(경매 · 공매 법령 포함). 토지 · 공시지가 · 용도지역 · 건축물대장은 강남구 아파트 단지 필지만 있다. 범위 밖이면 그렇다고 말한다.
   시세 추이는 q_price_trend, 공시지가 추이 · 상승률은 q_land_price_trend, 공시지가와 실거래가 비교는 q_price_vs_official,
   금리 · 가격지수 · 물가 · 인구 같은 시장 지표는 q_market_series로 찾는다. 지표 사이의 원인 · 결과는 말하지 않는다.
   공매 물건과 실거래 대비 할인율은 q_auction_discount, 경매 · 공매 권리 확인 항목은 q_auction_checklist로 찾는다.
   할인율을 말할 때는 권리관계를 보지 않은 값이라는 점과 확인 항목을 함께 말하고, 입찰을 권하지 않는다.
   한 단지 · 한 주소를 종합해서 물으면 q_property_card를 먼저 부르고, 필요하면 다른 도구로 더 찾는다.
   위치 · 지도 · 필지 경계는 q_parcel_map으로 찾는다(한 곳을 종합할 때 q_property_card와 함께 불러도 된다).
   최고가 · 최저가 · 층별 가격은 q_deal_list로, 「가장 많은 · 가장 비싼 단지」 같은 순위는 q_complex_rank로 찾는다.
8. 근거 목록(함수 · 건수 · 출처)은 시스템이 답 끝에 자동으로 붙이니 따로 길게 적지 않는다.
   도구 결과마다 ref 번호가 있다. 숫자 · 조문 · 가능 여부를 말한 문장 끝에 그 값이 나온 결과의 번호를 [1]처럼 붙인다.
   화면에서 이 번호를 누르면 원본 표 · 그래프 · 조문이 열리므로, 긴 표를 답에 다시 옮겨 적지 않고 요점만 쓴다.

오늘: {today}"""

# 웹 화면에서는 도구 결과가 표 · 그래프 · 조문 원문 블록으로 답 옆에 그대로 보임
SCREEN = """

화면 안내: 이 답은 웹 화면에 나간다. 도구 결과는 답 아래와 옆 「근거 서랍」에 표 · 그래프 · 조문 원문으로 그대로 보인다.
- 그래서 월별 값 · 거래 목록 · 조문 전문을 답에 표나 긴 목록으로 다시 옮겨 적지 않는다.
- 큰 제목(#)은 쓰지 않는다. 같은 [번호]를 문장마다 되풀이하지 않는다.
- 답은 이 순서와 모양으로 쓴다(도구 결과가 없거나 숫자가 없는 답이면 1과 3만):
  1. 첫 줄: 질문에 대한 결론 한 문장. 꼭 필요한 숫자 1~2개와 [번호].
  2. 핵심 숫자 2~4개를 아래 모양 그대로(한 줄에 하나, 칸은 「 | 」로 나눔). 값은 도구 결과의 숫자를 반올림만 해서 쓴다.
     :::숫자
     이름 | 값 | 몇 건 · 어느 기간에서 나온 값인지 짧게 | 번호
     :::
     예) 평당 매매가 · 최근 12개월 | 1억 2,823만 원 | 매매 61건 | 1
  3. 요점 2~5문장(또는 항목 5개 이하의 짧은 목록). 굵은 소제목으로 나누지 않는다.
     핵심 숫자와 화면의 단지 머리(주소 · 준공 · 세대 · 층)에 이미 보이는 값은 되풀이하지 않고, 범위 · 비교 · 주의점만 쓴다.
  4. 질문과 관련 있는데 가진 데이터로 확인 못 한 것이 있으면(빈 칸 · 수집 전 · 범위 밖) 아래 모양으로 1~3개:
     :::확인 못 함
     - 무엇: 왜 모르는지 한 줄
     :::
- 다음에 볼 것은 화면이 단추로 따로 보여 주니 「더 궁금하면 물어보세요」 같은 끝인사는 쓰지 않는다."""

FALLBACK_REFUSAL = "이 질문에는 답을 드리기 어렵습니다. 부동산 데이터(시세 · 법령 · 용도지역)에 관한 질문으로 다시 물어 주세요."


@dataclass
class Answer:
    text: str
    calls: list = field(default_factory=list)   # tools.run 결과에서 rows만 뺀 것
    footer: str = ""
    stop: str = ""
    model: str = ""
    messages: list = field(default_factory=list)   # 다음 질문에 이어 쓸 대화
    results: list = field(default_factory=list)   # 성공한 tools.run 결과 전체(rows 포함, ref 붙음). 화면 블록용

    def full(self):
        return self.text.rstrip() + ("\n\n" + self.footer if self.footer else "")


def footer(calls):
    """도구 실행 기록 → 답 끝에 붙일 근거 줄."""
    if not calls:
        return "근거: 데이터 조회 없이 답함"
    lines = ["근거"]
    for c in calls:
        args = ", ".join(f"{k}={v}" for k, v in c["args"].items())
        if c.get("error"):
            lines.append(f"- {c['tool']}({args}) → 오류: {c['error']}")
            continue
        ref = f"[{c['ref']}] " if c.get("ref") else ""
        n = f"{c['row_total']:,}건" if c["shown"] == c["row_total"] else f"{c['row_total']:,}건 중 {c['shown']}건 표시"
        src = " · ".join(c["sources"]) if c["sources"] else ""
        tail = " · ".join(x for x in (src, c["asof"] and f"기준 {c['asof']}") if x)
        lines.append(f"- {ref}{c['tool']}({args}) → {n}" + (f" ({tail})" if tail else ""))
    return "\n".join(lines)


MAX_RESULT = "조회를 여러 번 했지만 답을 정리하지 못했습니다. 질문을 좁혀서 다시 물어 주세요."
KEEP_TURNS = 10      # 이어 묻기에 넘기는 앞선 질문 수(오래된 것부터 버림. 문맥 · 메모리가 끝없이 늘지 않게)
MAX_CALLS = 12       # 질문 하나에 실행하는 도구 호출 수 상한(한 번에 여러 개를 불러도 합쳐서 셈)


def trim(history, keep=KEEP_TURNS):
    """앞선 대화에서 최근 질문 keep개부터만 남김. 질문(user) 칸에서 끊어서 도구 호출 · 결과 짝이 깨지지 않게 함."""
    history = list(history or [])
    starts = [i for i, m in enumerate(history) if m["role"] == "user"]
    return history[starts[-keep]:] if len(starts) > keep else history


def _ended(messages, text, calls, stop, model, results=None):
    """엔진이 만든 답(거절 · 한도)도 대화에 남겨서 이어 묻기 때 모델이 사용자가 본 답을 알게 함."""
    messages.append({"role": "assistant", "text": text, "calls": [], "raw": None, "provider": ""})
    return Answer(text, calls, footer(calls), stop, model, messages, results or [])


def rounds_from(cfg):
    """config.toml [gpt] max_rounds를 확인해 돌려줌. 시작할 때 불러서 잘못된 값이면 바로 알림."""
    n = cfg.get("gpt", {}).get("max_rounds", 6)
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError(f"config.toml [gpt] max_rounds는 1 이상 정수여야 함: {n!r}")
    return n


def ask(question, llm, conn, history=None, max_rounds=6, today=None, on_event=None, screen=False):
    """질문 하나에 답함. history는 앞선 Answer.messages(이어 묻기).
    도구는 최대 max_rounds번 주고받고, 마지막 결과를 정리할 기회를 한 번 더 줌.
    on_event(dict)를 주면 도구를 부르기 전({"type": "tool"})과 결과가 나온 뒤({"type": "result"})에 알림(웹 진행 표시용).
    screen=True면 결과가 화면 블록으로 함께 보인다고 모델에게 알려 답을 짧게 쓰게 함."""
    emit = on_event or (lambda e: None)
    rounds_from({"gpt": {"max_rounds": max_rounds}})
    system = SYSTEM.format(today=(today or datetime.date.today()).isoformat()) + (SCREEN if screen else "")
    specs = tools_mod.specs()
    messages = trim(history) + [{"role": "user", "text": question}]
    calls, results_full = [], []
    for round_no in range(max_rounds + 1):
        r = llm.chat(system, messages, specs)
        if r.stop == "refusal":
            return _ended(messages, FALLBACK_REFUSAL, calls, r.stop, r.model, results_full)
        messages.append({"role": "assistant", "text": r.text, "calls": r.calls, "raw": r.raw,
                         "provider": getattr(llm, "name", "")})
        if not r.calls:
            text = r.text if r.stop != "max_tokens" else r.text + "\n\n(답이 길어 중간에 끊겼습니다)"
            return Answer(text, calls, footer(calls), r.stop, r.model, messages, results_full)
        last = round_no == max_rounds
        results = []
        for c in r.calls:
            if last or len(calls) >= MAX_CALLS:   # 실행하지 않고 짝만 맞춰 둠(도구 호출엔 결과가 꼭 따라야 함)
                results.append({"id": c["id"], "content": "호출 한도를 넘어 실행하지 않음", "is_error": True})
                continue
            emit({"type": "tool", "tool": c["name"], "args": c["input"]})
            try:
                out = dict(tools_mod.run(conn, c["name"], c["input"]), ref=len(results_full) + 1)
                results.append({"id": c["id"], "content": json.dumps(tools_mod.for_model(out), ensure_ascii=False)})
                calls.append({k: v for k, v in out.items() if k != "rows"})
                results_full.append(out)
                emit({"type": "result", "result": out})
            except tools_mod.ToolError as e:
                results.append({"id": c["id"], "content": f"인자 오류: {e}", "is_error": True})
                calls.append({"tool": c["name"], "args": c["input"] if isinstance(c["input"], dict) else {},
                              "error": str(e)})
            except Exception as e:   # DB 오류도 모델에게 알려 「모른다」로 답하게 함
                conn.rollback()
                results.append({"id": c["id"], "content": f"조회 실패: {type(e).__name__}", "is_error": True})
                calls.append({"tool": c["name"], "args": c["input"], "error": type(e).__name__})
        messages.append({"role": "tool", "results": results})
        if last:
            return _ended(messages, MAX_RESULT, calls, "max_rounds", r.model, results_full)


def connect(cfg):
    """GPT용 읽기 전용 접속. HANPILJI_GPT_DSN(hanpilji_gpt 계정)을 권장, 없으면 HANPILJI_DSN.
    HANPILJI_DB_VIA=supabase-api면 Supabase를 HTTPS로 읽음(DB 직접 연결이 막힌 클라우드 작업 환경용)."""
    if os.environ.get("HANPILJI_DB_VIA") == "supabase-api":
        from hanpilji import supabase
        return supabase.SupabaseConn()
    import psycopg
    dsn = os.environ.get("HANPILJI_GPT_DSN") or cfg["db"]["dsn"]
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5,
                           options="-c default_transaction_read_only=on -c statement_timeout=5000")
    return conn
