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
7. 지금 데이터 범위: 실거래가와 행위제한 조례는 서울 강남구(11680)만, 실거래가는 아파트 · 오피스텔 · 연립다세대 매매 · 전월세 최근 36개월
   (단지 순위와 구 · 동 시세 추이는 아파트만).
   법령은 부동산 법령 21개(경매 · 공매 법령 포함). 토지 · 공시지가 · 용도지역 · 건축물대장은 강남구 아파트 단지 필지만 있다. 범위 밖이면 그렇다고 말한다.
   시세 추이는 q_price_trend, 공시지가 추이 · 상승률은 q_land_price_trend, 공시지가와 실거래가 비교는 q_price_vs_official,
   금리 · 가격지수 · 물가 · 인구 같은 시장 지표는 q_market_series로 찾는다. 지표 사이의 원인 · 결과는 말하지 않는다.
   공매 물건과 실거래 대비 할인율은 q_auction_discount, 경매 · 공매 권리 확인 항목은 q_auction_checklist로 찾는다.
   할인율을 말할 때는 권리관계를 보지 않은 값이라는 점과 확인 항목을 함께 말하고, 입찰을 권하지 않는다.
   한 단지 · 한 주소를 종합해서 물으면 q_property_card를 먼저 부르고, 필요하면 다른 도구로 더 찾는다.
   위치 · 지도 · 필지 경계는 q_parcel_map으로 찾는다(한 곳을 종합할 때 q_property_card와 함께 불러도 된다).
   최고가 · 최저가 · 층별 가격은 q_deal_list로, 「가장 많은 · 가장 비싼 단지」 같은 순위는 q_complex_rank로 찾는다.
   정책 · 규제(토지거래허가 · 규제지역 · 대출 · 세제 · 공급 · 금리 변경)가 언제 무엇이었는지는 q_policy_events로,
   그 정책 전후로 거래 · 가격이 어떻게 달라졌는지는 q_policy_impact로 찾는다. 정책 효과를 물어도 「전후 비교이고 인과는 아니다」를
   밝히고, 겹친 정책이 있으면 함께 말한다. 정책이 효과가 있었다 · 없었다고 단정하지 않는다.
8. 근거 목록(함수 · 건수 · 출처)은 시스템이 답 끝에 자동으로 붙이니 따로 길게 적지 않는다.
   도구 결과마다 ref 번호가 있다. 숫자 · 조문 · 가능 여부를 말한 문장 끝에 그 값이 나온 결과의 번호를 [1]처럼 붙인다.
   화면에서 이 번호를 누르면 원본 표 · 그래프 · 조문이 열리므로, 긴 표를 답에 다시 옮겨 적지 않고 요점만 쓴다.

오늘: {today}"""

# 웹 화면: 핵심 하나만 보이고 부가정보는 눌러야 열림(2026-10-29 방향). 도구 결과는 「열기」로 원자료 블록이 열림
SCREEN = """

화면 안내: 이 답은 웹 화면에 나간다. 사용자는 긴 답을 읽지 않는다. 묻는 것의 핵심 하나만 보이고, 나머지는 눌러야 열린다.
도구 결과(표 · 그래프 · 조문 원문 · 거래 목록)와 출처는 화면이 「열기」 줄과 출처 도장으로 따로 보여 준다.
위 8번 대신 아래를 따른다.
- 글에는 굵은 소제목 · 표 · 긴 목록 · 큰 제목(#)을 쓰지 않는다. 문장에 [번호]를 붙이지 않는다(번호는 아래 칸에만).
- 답은 아래 모양만 쓴다. 쓸 게 없는 칸은 통째로 뺀다.
  1. 첫 줄: 질문에 대한 결론 한 문장(40자 안팎). 「~입니다」보다 「~예요」처럼 짧고 부드럽게.
     아래 핵심 칸이 있으면 같은 숫자를 첫 줄에 되풀이하지 않는다(예: 「85㎡는 최근 1년 21건이 거래됐어요.」).
  2. 답의 중심이 되는 값 하나를 아래 모양 그대로(한 줄, 칸은 「 | 」로 나눔). 값은 도구 결과의 숫자를 반올림만 해서 쓴다.
     :::핵심
     무엇의 값인지 | 값 | 몇 건 · 어느 기간 · 마지막 거래 같은 한 줄 | 번호
     :::
     예) 도곡렉슬 전용 85㎡ · 최근 1년 매매 평균 | 35억 4,904만 원 | 21건 평균 · 마지막 거래 35억 6,000만 원 (10월 13일) | 1
     법 질문이면 값은 「5%까지」 · 「가능」 · 「불가」처럼 짧게 쓰고, 셋째 칸에 조건 한 줄을 쓴다.
  3. 꼭 필요할 때만 덧붙이는 말 한 문장(주의점 하나). 없으면 뺀다. 예시 계산 · 6번의 「조문 안내」 문구는 쓰지 않는다(화면이 붙임).
  4. 사용자가 더 궁금해할 만한 것(전세가 · 다른 면적 · 조문 원문 · 계산 방법 · 겹친 정책 등)은 글로 풀지 말고 아래 칸에 2~4줄.
     화면은 「이름」만 보여 주고, 누르면 내용과 그 번호의 원자료가 열린다.
     :::더보기
     이름 | 열었을 때 보일 한두 문장(숫자는 도구 결과 그대로) | 번호
     :::
     예) 전세가 · 전세가율 | 같은 기간 전세 76건 평균 14억 9,511만 원, 전세가율 42.1%예요. | 1
  5. 질문과 관련 있는데 가진 데이터로 확인 못 한 것(빈 칸 · 수집 전 · 범위 밖)이 있으면 1~3개. 화면은 접어서 보여 준다.
     :::확인 못 함
     - 무엇: 왜 모르는지 한 줄
     :::
  6. 사용자가 이어서 물을 만한 질문 2~3개. 지금 데이터로 바로 답할 수 있는 것만, 15자 안팎의 짧은 질문으로. 누르면 그대로 물어진다.
     :::다음
     - 은마 84㎡와 비교
     - 최근 3년으로
     :::
- 질문이 넓어 답이 여러 갈래면(면적이 여럿인 단지 시세를 면적 없이 물음, 지역 · 기간 · 용도가 빠짐 등) 다 늘어놓지 말고 먼저 되묻는다.
  도구로 고를 거리를 확인한 뒤, 첫 줄에 짧은 되물음 한 문장, 그리고 고를 것 2~5개만 쓴다(핵심 · 더보기 칸은 쓰지 않는다).
     :::고르기
     - 85㎡ · 매매 21건
     - 60㎡ · 매매 16건
     - 전체 면적
     :::
  고를 것은 거래가 많은 순 등 데이터에서 나온 순서로, 누르면 그 글이 그대로 다음 질문이 된다.
  사용자가 고른 뒤에는 앞 결과를 기억으로 옮기지 말고 고른 조건으로 도구를 다시 불러 답한다(화면의 출처 · 원자료가 그 조회에서 나온다).
- 가진 데이터 범위 밖의 질문(꼬마빌딩 · 상가 · 토지 매물, 강남구 밖 등)이면 도구를 부르지 않아도 된다. 첫 줄에 무엇이 없는지 한 문장만 쓰고,
  비슷한 다른 데이터(아파트 목록 등)를 대신 늘어놓지 않는다. 대신 :::다음 칸에 지금 데이터로 답할 수 있는 관련 질문 3개를 준다.
- 끝인사 · 「더 궁금하면 물어보세요」는 쓰지 않는다."""

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
