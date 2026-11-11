"""정부 해설 책자 PDF → 쪽마다 글 한 줄씩 담은 JSONL(data/guides/<이름>.jsonl).

    python scripts/guide_extract.py 받은파일.pdf <이름> [--skip 1-4] [--offset 0]
    python scripts/guide_extract.py 받은파일.txt <이름>      (쪽 구분 없는 글: 빈 줄 경계로 1,500자쯤씩 「n번째 부분」)

· 서버에는 PDF 도구를 두지 않으려고, 글 뽑기는 클라우드 · PC에서 하고 결과(JSONL)만 저장소에 넣음.
  서버는 git pull 뒤 hanpilji.collectors.guide 로 DB에 넣음(표준 라이브러리만 씀).
· --skip: 표지 · 목차처럼 넣지 않을 PDF 쪽(1부터, 예: 1-4,7). 글이 거의 없는 쪽(40자 미만)은 저절로 뺌.
· --offset: 책에 인쇄된 쪽 번호 = PDF 쪽 - offset (답에 「책 12쪽」처럼 적으려고).
· 이름은 data/guides/catalog.toml의 [[doc]] file과 같게. 자료 정보(제목 · 펴낸 곳 · 공공누리 유형 …)는 그 파일에 적음.
· 쓰는 도구: pypdf(pip install pypdf). 글자가 그림으로 된 쪽(스캔본)은 비어서 빠짐 → 결과에서 쪽 수를 꼭 확인.
"""
import argparse
import json
import re
import sys
from pathlib import Path

MIN_CHARS = 40


def parse_ranges(s):
    out = set()
    for part in filter(None, (s or "").split(",")):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out


def tidy(text):
    """줄 끝 공백 · 빈 줄 정리, 하이픈 · 쪽 번호만 있는 줄은 뺌."""
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln and not re.fullmatch(r"[-–·\s]*\d{1,4}[-–·\s]*", ln)]
    return "\n".join(lines).strip()


def extract(pdf_path, skip=(), offset=0):
    from pypdf import PdfReader
    rows = []
    for i, page in enumerate(PdfReader(pdf_path).pages, start=1):
        if i in skip:
            continue
        text = tidy(page.extract_text())
        if len(text) < MIN_CHARS:
            continue
        rows.append({"page": i, "printed": i - offset if i - offset > 0 else None, "text": text})
    return rows


def extract_text(txt_path, size=1500):
    """쪽 구분이 없는 글(PDF를 글로 바꿔 받은 파일): 빈 줄 경계로 size자쯤씩 나눔. 쪽 번호는 없음(page=None)."""
    raw = Path(txt_path).read_text(encoding="utf-8").replace("\r\n", "\n")
    if "\f" in raw:   # 쪽 나눔 문자가 있으면 그것으로 쪽을 셈
        pages = [tidy(t) for t in raw.split("\f")]
        return [{"page": i, "printed": i, "text": t} for i, t in enumerate(pages, start=1) if len(t) >= MIN_CHARS]
    rows, cur = [], ""
    for block in re.split(r"\n\s*\n", raw):
        block = tidy(block)
        if not block:
            continue
        if cur and len(cur) + len(block) > size:
            rows.append(cur)
            cur = block
        else:
            cur = f"{cur}\n{block}" if cur else block
    if cur:
        rows.append(cur)
    return [{"page": None, "label": f"{i}번째 부분", "text": t} for i, t in enumerate(rows, start=1) if len(t) >= MIN_CHARS]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("name", help="data/guides/<name>.jsonl (영문 · 숫자 · _ · -)")
    ap.add_argument("--skip", default="")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent.parent / "data" / "guides"))
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", a.name):
        print("이름은 영문 · 숫자 · _ · - 만", file=sys.stderr)
        return 2
    if a.pdf.lower().endswith(".txt"):
        rows = extract_text(a.pdf)
    else:
        rows = extract(a.pdf, parse_ranges(a.skip), a.offset)
    out = Path(a.out_dir) / f"{a.name}.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{out}: {len(rows)}조각, {sum(len(r['text']) for r in rows):,}자")
    return 0


if __name__ == "__main__":
    sys.exit(main())
