"""LLM 제공자. 엔진은 아래 형식의 대화만 알고, 제공자가 자기 API 모양으로 바꿈(제공자 교체가 쉬움).

대화 한 칸:
  {"role": "user", "text": "..."}
  {"role": "assistant", "text": "...", "calls": [{"id", "name", "input"}], "raw": 제공자 원본, "provider": 이름}
  {"role": "tool", "results": [{"id", "content": "JSON 문자열", "is_error": bool}]}

새 제공자를 붙이려면 chat(system, messages, tools) -> Reply 하나만 만들고 PROVIDERS에 등록하면 됨.
"""
import os
from dataclasses import dataclass, field

KEY_ENV = "HANPILJI_LLM_API_KEY"   # ANTHROPIC_API_KEY를 쓰지 않음: 클라우드 환경에 넣으면 Claude Code 세션 자체가 그 키를 씀


@dataclass
class Reply:
    text: str = ""
    calls: list = field(default_factory=list)   # [{"id", "name", "input"}]
    raw: object = None
    stop: str = ""                               # end · tool · refusal · max_tokens
    model: str = ""
    usage: dict = field(default_factory=dict)


class MissingKey(RuntimeError):
    pass


class Anthropic:
    """Anthropic Claude (Messages API, 도구 호출). 키는 환경변수 HANPILJI_LLM_API_KEY."""
    name = "anthropic"

    def __init__(self, model, effort="medium", max_tokens=16000, fallbacks="default", client=None):
        self.model, self.effort, self.max_tokens, self.fallbacks = model, effort, max_tokens, fallbacks
        if client is None:
            key = os.environ.get(KEY_ENV)
            if not key:
                raise MissingKey(f"환경변수 {KEY_ENV}가 없습니다")
            import anthropic
            # 한 번 호출에 90초까지(재시도 1번). 웹 채팅에서 요청 스레드가 오래 묶이지 않게
            client = anthropic.Anthropic(api_key=key, timeout=90.0, max_retries=1,
                                         base_url=os.environ.get("HANPILJI_LLM_BASE_URL", "https://api.anthropic.com"))
        self.client = client

    def to_native(self, messages):
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                if m.get("provider") == self.name and m.get("raw") is not None:
                    content = m["raw"]   # 생각 블록까지 그대로 돌려줘야 함
                else:
                    content = ([{"type": "text", "text": m["text"]}] if m.get("text") else []) + [
                        {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]}
                        for c in m.get("calls", [])]
                out.append({"role": "assistant", "content": content})
            elif m["role"] == "tool":
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"],
                     **({"is_error": True} if r.get("is_error") else {})}
                    for r in m["results"]]})
        return out

    def request(self, system, messages, tools):
        kw = dict(model=self.model, max_tokens=self.max_tokens, system=system, tools=tools,
                  messages=self.to_native(messages), thinking={"type": "adaptive"},
                  output_config={"effort": self.effort},
                  cache_control={"type": "ephemeral"})   # 도구 정의 · 지시문이 매번 같아서 캐시가 잘 맞음
        if self.fallbacks:
            # 안전 분류기가 거절하면 서버가 다른 모델로 다시 돌림
            kw.update(fallbacks=self.fallbacks, betas=["server-side-fallback-2026-07-01"])
        return kw

    def chat(self, system, messages, tools):
        r = self.client.beta.messages.create(**self.request(system, messages, tools))
        stop = {"end_turn": "end", "tool_use": "tool", "refusal": "refusal",
                "max_tokens": "max_tokens"}.get(r.stop_reason, r.stop_reason or "")
        if stop == "refusal":
            return Reply(stop=stop, raw=r.content, model=r.model)
        text = "".join(b.text for b in r.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "input": b.input} for b in r.content if b.type == "tool_use"]
        u = r.usage
        return Reply(text=text, calls=calls, raw=r.content, stop=stop, model=r.model,
                     usage={"input": u.input_tokens, "output": u.output_tokens,
                            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0})


class Scripted:
    """시험 · 키 없는 시연용. 미리 정한 Reply를 차례로 돌려주고, 받은 대화를 seen에 남김."""
    name = "scripted"

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def chat(self, system, messages, tools):
        self.seen.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        return self.replies.pop(0)


PROVIDERS = {"anthropic": Anthropic}


def make(cfg):
    """config.toml [gpt] → 제공자. 환경변수 HANPILJI_LLM_PROVIDER · HANPILJI_LLM_MODEL이 우선."""
    g = cfg.get("gpt", {})
    name = os.environ.get("HANPILJI_LLM_PROVIDER", g.get("provider", "anthropic"))
    if name not in PROVIDERS:
        raise ValueError(f"모르는 제공자: {name} (가능: {', '.join(PROVIDERS)})")
    return PROVIDERS[name](model=os.environ.get("HANPILJI_LLM_MODEL", g.get("model", "claude-opus-5-5")),
                           effort=g.get("effort", "medium"), max_tokens=g.get("max_tokens", 16000),
                           fallbacks=g.get("fallbacks", "default"))
