"""지식 DB 검색 정확도 점검(data/kb_eval/questions.toml). 서버의 venv-embed에서 돎. DB는 읽기만 함.

    python scripts/kb_eval.py [--config config.toml] [--model-dir … --file model_quantized.onnx]

질문마다 글자만 · 글자+의미 두 가지로 찾아, 정답이 몇 위에 왔는지와 1위 · 3위 안 · 8위 안 정답 수를 보여 줌.
"""
import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hanpilji import config as config_mod, db  # noqa: E402
from hanpilji.embedding import Embedder, to_literal  # noqa: E402


def rank(rows, want):
    for i, (title, label) in enumerate(rows, 1):
        for w in want:
            t, _, l = w.partition("|")
            if t in (title or "") and (label or "").startswith(l):
                return i
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--questions", default=str(ROOT / "data" / "kb_eval" / "questions.toml"))
    ap.add_argument("--model-dir")
    ap.add_argument("--file")
    a = ap.parse_args(argv)
    qs = tomllib.loads(Path(a.questions).read_text(encoding="utf-8"))["q"]
    emb = Embedder(a.model_dir, file=a.file)
    cfg = config_mod.load(a.config)
    score = {"글자만": [0, 0, 0], "글자+의미": [0, 0, 0]}
    with db.connect(cfg["db"]["dsn"]) as conn:
        print("글자만 | 글자+의미 | 질문   (숫자 = 정답 순위, - = 8위 안에 없음)")
        for item in qs:
            vec = to_literal(emb.encode([item["q"]])[0])
            got = {}
            for name, v in (("글자만", None), ("글자+의미", vec)):
                rows = conn.execute("SELECT title, label FROM mart.q_kb_search(%s, %s::public.vector, 8)",
                                    (item["q"], v)).fetchall()
                r = got[name] = rank(rows, item["want"])
                for i, k in enumerate((1, 3, 8)):
                    score[name][i] += bool(r and r <= k)
            print(f"{got['글자만'] or '-':>5} | {got['글자+의미'] or '-':>8} | {item['q']}")
    n = len(qs)
    print(f"\n{n}문항   1위 · 3위 안 · 8위 안 정답 수")
    for name, (a1, a3, a8) in score.items():
        print(f"{name:>6}: {a1} · {a3} · {a8}  ({a3 / n:.0%} 3위 안)")


if __name__ == "__main__":
    main()
