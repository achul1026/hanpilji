"""사이트 이미지용 질문 벡터 모델(bge-m3 압축판 int8) 내려받기. Dockerfile이 빌드 때 부름.

    python scripts/fetch_embed_model.py /app/models/bge-m3-int8

커밋과 파일 지문(sha256)을 고정해서, 같은 파일이 아니면 빌드를 멈춤.
2026-11-11 서버 시험: 원본(BAAI/bge-m3) 문서 벡터에 이 압축판 질문 벡터로 찾아도 10문항 모두 1위가 같았음(유사도 0.985~0.990).
"""
import hashlib
import os
import sys
import urllib.request

REPO = "Xenova/bge-m3"
REV = "4de13258303883538bd53b696b452bf8099f0858"
FILES = {   # 저장소 경로 → (저장 이름, sha256)
    "onnx/model_quantized.onnx": ("model_quantized.onnx",
                                  "0826f8c1ab9edf1801db86c61919d4d108e8bfc0b809ec823ad366882ff0b77d"),
    "tokenizer.json": ("tokenizer.json", "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790"),
}


def main(dest):
    os.makedirs(dest, exist_ok=True)
    for src, (name, want) in FILES.items():
        path = os.path.join(dest, name)
        urllib.request.urlretrieve(f"https://huggingface.co/{REPO}/resolve/{REV}/{src}", path)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        if h.hexdigest() != want:
            os.remove(path)
            sys.exit(f"{name} 지문이 다름: {h.hexdigest()} (기대 {want})")
        print(name, os.path.getsize(path), "OK")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "models/bge-m3-int8")
