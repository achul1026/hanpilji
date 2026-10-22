"""README 머리 그림(도트 그림) 만들기. 외부 그림 없이 사각형만 써서 저작권 걱정 없음.

    python docs/assets/make_banner.py   # → docs/assets/banner.svg
"""
from pathlib import Path

P = 8                 # 도트 한 칸 크기(px)
W, H = 128, 36        # 칸 수 → 1024 x 288

SKY_TOP, SKY_BOTTOM = "#1b1f3b", "#2e3a6b"
# 용도지역 지도 색(토지이음 지도 범례를 닮게): 주거 · 상업 · 공업 · 녹지
ZONE = {"R": "#f6d365", "C": "#f28b82", "I": "#b39ddb", "G": "#81c995", "W": "#4fc3f7"}
ROAD = "#3c4043"

# 5x7 도트 글꼴(필요한 글자만)
FONT = {
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
}

# 아래쪽 필지 지도: 한 글자 = 한 칸 묶음(4x4 도트). '.'은 도로
MAP = [
    "RRRR.CCCC.RRRR.GGGG.RRRR.IIII.RRRR.CCCC",
    "RRRR.CCCC.RRRR.GGGG.WWWW.IIII.RRRR.CCCC",
]

# 건물: (x칸, 높이칸, 너비칸, 색)
BUILDINGS = [
    (3, 7, 5, "#5c6bc0"), (10, 9, 4, "#7986cb"), (16, 5, 6, "#3f51b5"), (24, 8, 4, "#5c6bc0"),
    (40, 4, 6, "#7986cb"), (78, 5, 5, "#3f51b5"),
    (88, 7, 5, "#5c6bc0"), (95, 9, 4, "#7986cb"), (101, 6, 7, "#3f51b5"),
    (110, 8, 5, "#5c6bc0"), (117, 5, 6, "#7986cb"),
]
STARS = [(4, 2), (9, 6), (2, 11), (13, 15), (115, 1), (120, 5), (124, 10), (116, 14), (40, 1), (88, 1)]


def rect(x, y, w, h, fill, extra=""):
    return f'<rect x="{x * P}" y="{y * P}" width="{w * P}" height="{h * P}" fill="{fill}"{extra}/>'


def main():
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W * P} {H * P}" width="{W * P}" height="{H * P}" '
        'shape-rendering="crispEdges" role="img" aria-label="HANPILJI 한필지">',
        '<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{SKY_TOP}"/><stop offset="1" stop-color="{SKY_BOTTOM}"/></linearGradient></defs>',
        f'<rect width="{W * P}" height="{H * P}" fill="url(#sky)"/>',
    ]
    for i, (x, y) in enumerate(STARS):
        out.append(rect(x, y, 1, 1, "#ffffff", f' opacity="{0.5 + (i % 3) * 0.25}"'))

    ground = H - 9
    for x, h, w, color in BUILDINGS:
        top = ground - h
        out.append(rect(x, top, w, h, color))
        for wy in range(top + 1, ground - 1, 2):          # 창문
            for wx in range(x + 1, x + w - 1, 2):
                lit = (wx * 7 + wy * 3) % 5 != 0
                out.append(rect(wx, wy, 1, 1, "#ffe082" if lit else "#283593"))

    # 필지 지도 띠
    out.append(rect(0, ground, W, H - ground, ROAD))
    for row, line in enumerate(MAP):
        for col, ch in enumerate(line * 4):
            if col * 1 >= W // 4 * 4:
                break
            if ch in ZONE:
                out.append(rect(col * 1 + 0, ground + 1 + row * 4, 1, 3, ZONE[ch]))

    # 제목 HANPILJI: 글자 도트 하나 = 2칸
    text, s = "HANPILJI", 2
    width = len(text) * 6 * s - s
    x0, y0 = (W - width) // 2, 3
    for i, ch in enumerate(text):
        for r, bits in enumerate(FONT[ch]):
            for c, b in enumerate(bits):
                if b == "1":
                    gx, gy = x0 + (i * 6 + c) * s, y0 + r * s
                    out.append(rect(gx + 1, gy + 1, s, s, "#0d1030"))   # 그림자
                    out.append(rect(gx, gy, s, s, "#fff8e1"))

    # 가운데 필지 하나에 핀
    px, py = W // 2 - 1, ground - 4
    out.append(rect(px, py, 3, 3, "#ff5252"))
    out.append(rect(px + 1, py + 1, 1, 1, "#ffffff"))
    out.append(rect(px + 1, py + 3, 1, 2, "#ff5252"))

    out.append("</svg>")
    Path(__file__).with_name("banner.svg").write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    main()
