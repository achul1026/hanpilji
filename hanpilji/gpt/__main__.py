"""터미널 대화.

    HANPILJI_LLM_API_KEY=... python -m hanpilji.gpt                 # 대화
    python -m hanpilji.gpt "도곡렉슬 84㎡ 최근 1년 매매가랑 전세가"   # 한 번만 묻기
    python -m hanpilji.gpt --verbose ...                          # 도구 호출 과정도 보기
"""
import argparse
import sys

from hanpilji import config as config_mod
from hanpilji.gpt import engine, llm


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m hanpilji.gpt", description="한필지 부동산 GPT")
    p.add_argument("question", nargs="*")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--verbose", action="store_true", help="답한 모델 이름 · 멈춘 이유를 보여 줌")
    a = p.parse_args(argv)
    cfg = config_mod.load(a.config)
    try:
        model = llm.make(cfg)
    except llm.MissingKey as e:
        print(f"{e}. README의 「부동산 GPT」 절에서 키 넣는 법을 보세요.", file=sys.stderr)
        return 2
    conn = engine.connect(cfg)
    rounds = cfg.get("gpt", {}).get("max_rounds", 6)

    def one(q, history):
        ans = engine.ask(q, model, conn, history, max_rounds=rounds)
        print(ans.full())
        if a.verbose:
            print(f"\n[{ans.model} · 멈춘 이유 {ans.stop}]", file=sys.stderr)
        return ans.messages

    if a.question:
        one(" ".join(a.question), None)
        return 0
    print("한필지 부동산 GPT. 끝내려면 빈 줄에서 Enter 또는 Ctrl+D", file=sys.stderr)
    history = None
    while True:
        try:
            q = input("\n질문> ").strip()
        except EOFError:
            break
        if not q:
            break
        history = one(q, history)
    return 0


if __name__ == "__main__":
    sys.exit(main())
