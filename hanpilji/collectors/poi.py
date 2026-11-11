"""공공데이터포털 표준데이터 → core.ref_poi(학교 · 지하철역 위치). 인증키 없이 받음.

    python -m hanpilji.collectors.poi [--only school|station]

  학교: 전국초중등학교위치표준데이터(15021148). 운영 중인 학교만
  지하철역: 전국도시철도역사정보표준데이터(15013205). 환승역은 노선을 한 줄로 묶음
서울과 그 둘레(경위도 범위, 경계 너머 학교 · 역도 「가까운 곳」에 들어가게)만 넣음. 다시 돌리면 그 종류를 통째로 바꿈.
포털 화면의 표준데이터 「CSV 내려받기」 단추와 같은 순서로 부름(열 목록 → 1만 줄씩 자료). 2026-11-11 실제 응답으로 확인.
"""
import argparse
import datetime
import http.cookiejar
import json
import re
import sys
import urllib.parse
import urllib.request

from hanpilji import db, config as config_mod
from hanpilji import ingest

PORTAL = "https://www.data.go.kr"
PER_PAGE = 10000
# 서울(지적도 칸 126.760~127.190 · 37.410~37.720)에 q_nearby 최대 반경 3km를 더한 경위도 범위
BBOX = (126.725, 37.383, 127.225, 37.747)
SETS = {
    "school": {"pk": "15021148", "source_id": "poi_school", "label": "전국초중등학교위치표준데이터"},
    "station": {"pk": "15013205", "source_id": "poi_station", "label": "전국도시철도역사정보표준데이터"},
}


class StdPortal:
    """표준데이터 내려받기(화면이 부르는 주소)."""

    def __init__(self, opener=None):
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _get(self, path, timeout=120):
        req = urllib.request.Request(PORTAL + path, headers={"User-Agent": "hanpilji-collector/0.1",
                                                             "X-Requested-With": "XMLHttpRequest"})
        with self.opener.open(req, timeout=timeout) as r:
            return json.loads(r.read())

    def rows(self, pk):
        """→ 줄(열 코드 → 값) 목록 전부."""
        head = self._get(f"/download/columList.json?pk={pk}&ext=CSV")
        t, total = head["tableVO"], int(head["totalCount"])
        out = []
        for page in range(1, (total + PER_PAGE - 1) // PER_PAGE + 1):
            q = urllib.parse.urlencode([("publicDataPk", pk), ("totalCount", total), ("svcTableNm", t["svcTableNm"]),
                                        ("perPage", PER_PAGE), ("page", page)] + [("colNmList", c) for c in t["colNmList"]])
            part = self._get(f"/download/standard.json?{q}")
            if not isinstance(part, list):
                raise RuntimeError(f"표준데이터 응답 모양이 다름: {str(part)[:200]}")
            out += part
        if len(out) < total:
            raise RuntimeError(f"표준데이터 {pk}: {total}줄 중 {len(out)}줄만 옴")
        return out


def _f(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _in_box(lon, lat):
    x0, y0, x1, y1 = BBOX
    return lon is not None and lat is not None and x0 <= lon <= x1 and y0 <= lat <= y1


def _date(v):
    return v if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()) else None


def schools(rows, today):
    """학교 줄 → ref_poi 줄. 운영 중이고 범위 안인 것만."""
    out = []
    for r in rows:
        lon, lat = _f(r.get("LONGITUDE")), _f(r.get("LATITUDE"))
        if (r.get("OPER_STTUS") or "").strip() != "운영" or not _in_box(lon, lat) or not r.get("SCHOOL_ID"):
            continue
        detail = " · ".join(x for x in ((r.get("FOND_TYPE") or "").strip(), (r.get("BNHH_SE") or "").strip()) if x) or None
        out.append({"poi_id": f"school:{r['SCHOOL_ID']}", "kind": (r.get("SCHOOL_SE") or "학교").strip(),
                    "name": (r.get("SCHOOL_NM") or "").strip(), "detail": detail,
                    "address": (r.get("RDNMADR") or r.get("LNMADR") or "").strip() or None,
                    "lon": lon, "lat": lat, "asof": _date(r.get("REFERENCE_DATE")) or today.isoformat()})
    return [x for x in out if x["name"]]


def stations(rows, today):
    """역 줄 → ref_poi 줄. 같은 이름 · 거의 같은 자리(약 1km 칸)의 노선은 한 줄로 묶음(환승역)."""
    groups = {}
    for r in rows:
        lon, lat = _f(r.get("LONGITUDE")), _f(r.get("LATITUDE"))
        name = (r.get("STATN_NM") or "").strip()
        if not name or not _in_box(lon, lat):
            continue
        key = (name, round(lat, 2), round(lon, 2))
        g = groups.setdefault(key, {"poi_id": f"station:{name}:{key[1]}:{key[2]}", "kind": "지하철역", "name": name,
                                    "routes": [], "address": (r.get("RDNMADR") or "").strip() or None,
                                    "lon": lon, "lat": lat, "asof": _date(r.get("REFERENCE_DATE")) or today.isoformat()})
        route = (r.get("ROUTE_NM") or "").strip()
        if route and route not in g["routes"]:
            g["routes"].append(route)
        g["asof"] = max(g["asof"], _date(r.get("REFERENCE_DATE")) or g["asof"])
    out = []
    for g in groups.values():
        g["detail"] = " · ".join(sorted(g.pop("routes"))) or None
        out.append(g)
    return out


FIELDS = ("poi_id", "kind", "name", "detail", "address", "lon", "lat", "source", "asof")


def load(conn, label, rows):
    """그 출처 줄을 지우고 새로 넣음 → 넣은 줄 수."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM core.ref_poi WHERE source = %s", (label,))
        with cur.copy(f"COPY core.ref_poi ({', '.join(FIELDS)}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row([label if f == "source" else r[f] for f in FIELDS])
    return len(rows)


def main(argv=None, portal=None, today=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--only", choices=sorted(SETS), action="append", help="이 종류만(기본: 둘 다)")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    portal = portal or StdPortal()
    today = today or datetime.date.today()
    failed = 0
    with db.connect(cfg["db"]["dsn"]) as conn:
        for kind in args.only or sorted(SETS):
            s = SETS[kind]
            run_id = ingest.start_run(conn, s["source_id"])
            conn.commit()
            try:
                raw = portal.rows(s["pk"])
                rows = (schools if kind == "school" else stations)(raw, today)
                if not rows:
                    raise RuntimeError(f"{s['label']}: 범위 안 줄이 0개(응답 {len(raw)}줄) — 열 이름이 바뀌었는지 확인")
                n = load(conn, s["label"], rows)
                newest = max(r["asof"] for r in rows)
                ingest.finish_run(conn, run_id, "done", loaded=n, note=f"원본 {len(raw)}줄 중 {n}줄 · 기준일 최근 {newest}")
                conn.commit()
                print(f"{s['label']}: 원본 {len(raw):,}줄 중 서울 둘레 {n:,}줄 (기준일 최근 {newest})")
            except Exception as e:
                conn.rollback()
                ingest.finish_run(conn, run_id, "failed", note=str(e)[:500])
                conn.commit()
                failed += 1
                print(f"failed {kind}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
