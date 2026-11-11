"""bge-m3 dense 임베딩(ONNX). 서버 배치(collectors/embed.py)와 사이트(질문 벡터)가 같이 씀.

모델: Hugging Face BAAI/bge-m3 의 onnx/ 폴더(커밋 고정, MIT). ONNX는 계산 그래프만 담아 파일 속 코드가 실행되지 않음.
필요한 패키지: onnxruntime · tokenizers · numpy (수집용 venv와 따로 둔 venv-embed에만 설치). 인터넷 없이 로컬 파일만 읽음.
벡터 = [CLS] 위치의 마지막 은닉 상태를 L2 정규화(bge-m3 dense 방식). 모델이 문장 벡터를 바로 내면 그것을 정규화해서 씀.
"""
import os
from pathlib import Path

MODEL_REV = "5617a9f61b028005a4858fdac845db406aefb181"
MODEL_NAME = f"BAAI/bge-m3@{MODEL_REV[:7]}"
DIM = 1024
DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "models" / f"bge-m3-{MODEL_REV}"


class Embedder:
    def __init__(self, model_dir=None, max_length=512, threads=None):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np = np
        d = Path(model_dir or os.environ.get("HANPILJI_EMBED_MODEL") or DEFAULT_DIR)
        if not (d / "model.onnx").exists():
            raise FileNotFoundError(f"bge-m3 ONNX 모델이 없음: {d}/model.onnx")
        self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self.tok.enable_truncation(max_length=max_length)
        pad_id = self.tok.token_to_id("<pad>")
        self.tok.enable_padding(pad_id=1 if pad_id is None else pad_id, pad_token="<pad>")
        opt = ort.SessionOptions()
        opt.intra_op_num_threads = int(threads or os.environ.get("HANPILJI_EMBED_THREADS") or 2)
        self.sess = ort.InferenceSession(str(d / "model.onnx"), sess_options=opt, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        outs = [o.name for o in self.sess.get_outputs()]
        # 문장 벡터 출력(dense_vecs · sentence_embedding)이 있으면 그것, 없으면 첫 출력(마지막 은닉 상태)
        self.output = next((o for o in outs if "dense" in o or "sentence" in o), outs[0])

    def encode(self, texts):
        """글 목록 → 정규화된 1024차원 벡터 목록(list[list[float]])."""
        np = self.np
        enc = self.tok.encode_batch([t or "" for t in texts])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        out = self.sess.run([self.output], {k: v for k, v in feed.items() if k in self.inputs})[0]
        vec = out[:, 0, :] if out.ndim == 3 else out          # [CLS] 또는 이미 문장 벡터
        if vec.shape[1] != DIM:
            raise ValueError(f"벡터 차원이 {vec.shape[1]} (기대 {DIM})")
        vec = vec / np.clip(np.linalg.norm(vec, axis=1, keepdims=True), 1e-12, None)
        return vec.astype(np.float32).tolist()


def to_literal(vec):
    """pgvector 입력 꼴 '[0.1,0.2,…]'."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
