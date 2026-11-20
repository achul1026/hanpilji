"""서울 열린데이터광장 공통: Open API 부르기 · 파일 받기 · 좌표 바꾸기 · SHP 읽기.

Open API: http://openapi.seoul.go.kr:8088/{키}/json/{서비스}/{시작}/{끝}/{조건…}  한 번에 1,000줄까지.
  키는 환경변수 SEOUL_API_KEY(열린데이터광장 「인증키 신청」, 무료). 8088 포트라 클라우드 컨테이너에서는 막혀 있고 서버에서 돎.
  응답 모양은 2026-11-20 열린데이터광장 화면의 샘플 호출로 확인({"서비스": {"list_total_count", "RESULT", "row": [...]}}).
  자료가 없으면 {"RESULT": {"CODE": "INFO-200"}} 만 옴.
파일: 데이터 화면의 「내려받기」와 같은 주소(datafile.seoul.go.kr)로 받음. 인증키 없음.
"""
import json
import math
import re
import struct
import urllib.parse
import urllib.request

from hanpilji import ingest

API = "http://openapi.seoul.go.kr:8088"
PAGE = 1000
UA = {"User-Agent": "hanpilji-collector/0.1"}
VIEW_URL = "https://data.seoul.go.kr/dataList/{inf}/S/1/datasetView.do"
DOWNLOAD_URL = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false"


class ApiError(RuntimeError):
    """키 오류 · 서비스 이름 오류 등 다시 불러도 같은 오류. 수집을 멈춤."""


def api_url(key, service, start, end, *args):
    tail = "/".join(urllib.parse.quote(str(a), safe="") for a in args)
    return f"{API}/{urllib.parse.quote(key, safe='')}/json/{service}/{start}/{end}/" + (tail + "/" if tail else "")


def parse(service, body):
    """응답 → (전체 줄 수, 줄 목록). 자료 없음(INFO-200)은 (0, []). 그 밖의 오류 코드는 ApiError."""
    data = json.loads(body)
    block = data.get(service) or data
    result = block.get("RESULT") or {}
    code = result.get("CODE", "")
    if code == "INFO-200":
        return 0, []
    if code != "INFO-000":
        raise ApiError(f"{service}: {code} {result.get('MESSAGE', '')}".strip())
    return int(block.get("list_total_count") or 0), block.get("row") or []


class Client:
    """페이지를 넘기며 다 받음. calls는 이번 실행에서 부른 횟수(한도 관리용)."""

    def __init__(self, key, fetch=None, max_calls=None):
        if not key:
            raise ApiError("SEOUL_API_KEY 없음(.env에 서울 열린데이터광장 인증키를 넣을 것)")
        self.key = key
        self.fetch = fetch or (lambda url: ingest.http_get(url, timeout=60))
        self.max_calls = max_calls
        self.calls = 0

    def left(self):
        return None if self.max_calls is None else self.max_calls - self.calls

    def rows(self, service, *args):
        """서비스의 모든 줄(조건 args). 한도에 닿으면 LimitReached."""
        start, total, out = 1, None, []
        while total is None or start <= total:
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise LimitReached(service)
            body = self.fetch(api_url(self.key, service, start, start + PAGE - 1, *args))
            self.calls += 1
            total, rows = parse(service, body)
            out.extend(rows)
            if not rows:
                break
            start += PAGE
        return out


class LimitReached(RuntimeError):
    pass


# ── 파일 내려받기(인증키 없음) ──

def list_files(inf, opener=urllib.request.urlopen):
    """데이터 화면 → (infSeq, [(seq, 파일 이름)…]). 파일이 없으면 빈 목록."""
    page = opener(urllib.request.Request(VIEW_URL.format(inf=inf), headers=UA), timeout=60).read().decode("utf-8", "replace")
    form = re.search(r'name="frmFile".*?</form>', page, flags=re.S)
    m = re.search(r'name="infSeq" value="(\d*)"', form.group(0)) if form else None
    files = []
    for row in re.findall(r"<tr.*?</tr>", page, flags=re.S):
        m_seq = re.search(r"downloadFile\('(\d+)'\)", row)
        m_name = re.search(r">\s*([^<>]+\.(?:zip|csv))\s*<", row)
        if m_seq and m_name and (m_seq.group(1), m_name.group(1).strip()) not in files:
            files.append((m_seq.group(1), m_name.group(1).strip()))
    return (m.group(1) if m else ""), files


def download(inf, inf_seq, seq, dest, opener=urllib.request.urlopen):
    data = urllib.parse.urlencode({"infId": inf, "seqNo": "", "seq": seq, "infSeq": inf_seq}).encode()
    with opener(urllib.request.Request(DOWNLOAD_URL, data=data, headers=UA), timeout=600) as r, open(dest, "wb") as out:
        while chunk := r.read(1 << 20):
            out.write(chunk)


# ── 좌표: Korea 2000 중부원점(EPSG:5181 · 5186, GRS80, 중앙자오선 127°, 원점 38°, 축척 1) ↔ 경위도 ──
# 5181과 5186은 가짜 북쪽 값만 다름(500,000 · 600,000 m)

_A = 6378137.0
_F = 1 / 298.257222101
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)
_LON0 = math.radians(127.0)
_LAT0 = math.radians(38.0)
FE, FN_5181, FN_5186 = 200000.0, 500000.0, 600000.0


def _meridian(phi):
    e2 = _E2
    return _A * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
                 - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
                 + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
                 - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))


_M0 = _meridian(_LAT0)


def tm_to_lonlat(x, y, fn=FN_5181):
    """TM(m) → (경도, 위도). Snyder 횡메르카토르 역변환(서울 범위 오차 1mm 수준)."""
    e2, ep2 = _E2, _EP2
    m = _M0 + (y - fn)
    mu = m / (_A * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    s, c, t = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1, t1 = ep2 * c * c, t * t
    n1 = _A / math.sqrt(1 - e2 * s * s)
    r1 = _A * (1 - e2) / (1 - e2 * s * s) ** 1.5
    d = (x - FE) / n1
    lat = phi1 - (n1 * t / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
                                 + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = _LON0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                   + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120) / c
    return math.degrees(lon), math.degrees(lat)


def lonlat_to_tm(lon, lat, fn=FN_5181):
    """(경도, 위도) → TM(m). 시험에서 되돌려 맞춰 보는 데 씀."""
    e2, ep2 = _E2, _EP2
    phi, lam = math.radians(lat), math.radians(lon)
    s, c, t = math.sin(phi), math.cos(phi), math.tan(phi)
    n = _A / math.sqrt(1 - e2 * s * s)
    tt, cc, a = t * t, ep2 * c * c, (lam - _LON0) * c
    m = _meridian(phi)
    x = FE + n * (a + (1 - tt + cc) * a ** 3 / 6 + (5 - 18 * tt + tt ** 2 + 72 * cc - 58 * ep2) * a ** 5 / 120)
    y = fn + (m - _M0) + n * t * (a ** 2 / 2 + (5 - tt + 9 * cc + 4 * cc ** 2) * a ** 4 / 24
                                  + (61 - 58 * tt + tt ** 2 + 600 * cc - 330 * ep2) * a ** 6 / 720)
    return x, y


# ── SHP(다각형) · DBF 읽기: 영역 파일 하나에 다각형 수천 개라 라이브러리 없이 읽음 ──

def read_dbf(data, encoding="utf-8"):
    """DBF 바이트 → dict 목록(글자 그대로, 앞뒤 공백 뺌). 지운 줄(*)은 뺌."""
    n, header_len, rec_len = struct.unpack("<4xIHH", data[:12])
    fields, p = [], 32
    while data[p] != 0x0D:
        fields.append((data[p:p + 11].split(b"\0")[0].decode("ascii"), data[p + 16]))
        p += 32
    out = []
    for i in range(n):
        rec = data[header_len + i * rec_len: header_len + (i + 1) * rec_len]
        if not rec or rec[:1] == b"*":
            continue
        row, o = {}, 1
        for name, ln in fields:
            row[name] = rec[o:o + ln].decode(encoding, "replace").strip()
            o += ln
        out.append(row)
    return out


def read_shp_polygons(data):
    """SHP 바이트 → 레코드마다 고리(점 목록) 목록. 다각형(5)만, 빈 도형은 []."""
    shapes, p = [], 100
    while p + 8 <= len(data):
        _, words = struct.unpack(">ii", data[p:p + 8])
        content = data[p + 8: p + 8 + words * 2]
        p += 8 + words * 2
        kind = struct.unpack("<i", content[:4])[0]
        if kind == 0:
            shapes.append([])
            continue
        if kind != 5:
            raise ValueError(f"다각형이 아닌 도형 종류 {kind}")
        nparts, npts = struct.unpack("<ii", content[36:44])
        parts = list(struct.unpack(f"<{nparts}i", content[44:44 + 4 * nparts])) + [npts]
        base = 44 + 4 * nparts
        pts = [struct.unpack("<dd", content[base + 16 * k: base + 16 * k + 16]) for k in range(npts)]
        shapes.append([pts[parts[k]:parts[k + 1]] for k in range(nparts)])
    return shapes


def _signed_area(ring):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:]))


def rings_to_wkt(rings, dy=0.0):
    """SHP 고리 → MULTIPOLYGON WKT. 시계 방향 고리가 바깥, 반시계는 바로 앞 바깥 고리의 구멍(SHP 규칙).
    dy를 더해 5181 → 5186(+100,000 m)으로 옮길 수 있음."""
    polys = []
    for ring in rings:
        if len(ring) < 4:
            continue
        if _signed_area(ring) <= 0 or not polys:   # 시계 방향(넓이 음수) = 바깥
            polys.append([ring])
        else:
            polys[-1].append(ring)
    if not polys:
        return None
    fmt = lambda r: "(" + ",".join(f"{x:.3f} {y + dy:.3f}" for x, y in r) + ")"
    return "MULTIPOLYGON(" + ",".join("(" + ",".join(fmt(r) for r in poly) + ")" for poly in polys) + ")"
