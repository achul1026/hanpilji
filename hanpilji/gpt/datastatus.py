"""데이터 현황 화면(/data)에 줄 값: DB에 쌓인 데이터마다 줄 수 · 데이터 기간 · 최종 업데이트일 · 출처, 그리고 전체 용량.

마트 함수 셋(sql/23_data_status.sql)만 부르고 모델은 부르지 않음. 큰 표를 다 세느라 몇 초 걸리므로
결과를 CACHE_SECONDS 동안 기억해 두고 씀(데이터는 하루 한 번 새로 받으므로 1시간이면 충분).
"""
import datetime
import sys
import threading
import time

CACHE_SECONDS = 3600
RETRY_SECONDS = 300   # 용량을 못 셌을 때는 이만큼만 기억하고 다시 셈
GROUPS = ["실거래", "단지 · 건물 · 토지", "법령", "해설 자료", "규제 · 정책", "시장 지표", "기타"]
KST = datetime.timezone(datetime.timedelta(hours=9))

_lock = threading.Lock()
_cache = {"at": None, "data": None, "ttl": CACHE_SECONDS}


def _kst(ts):
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    return ts.astimezone(KST).strftime("%Y-%m-%d %H:%M")


def _size(n):
    """바이트 → 사람이 읽는 크기(1024 단위)."""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit in ("B", "KB") else f"{n:,.1f} {unit}"
        n /= 1024


def build(status_rows, source_rows, size_row=None, now=None):
    """함수 결과를 화면용으로. 한 줄 = 데이터 하나. 줄이 0인 것은 뺌(그 DB에 아직 안 받은 것).
    updated = 데이터 자체의 최종 업데이트일(줄들의 가장 최근 기준일), checked = 수집처에서 마지막으로 받아 온 시각."""
    sources = {}
    for sid, name, operator, cycle, license_, url, last, rows in source_rows:
        sources[sid] = {"id": sid, "name": name, "operator": operator, "cycle": cycle, "license": license_,
                        "url": url, "last_success": last, "last_rows": rows}
    groups = {g: [] for g in GROUPS}
    total = 0
    for r in status_rows:
        if len(r) == 7:   # updated 열이 없던 예전 함수(서버 SQL을 아직 안 바꿨을 때)
            r = (*r[:6], None, r[6])
        grp, label, detail, count, p_from, p_to, updated, ids = r
        if not count:
            continue
        srcs = [sources[i] for i in (ids or []) if i in sources]
        lasts = [s["last_success"] for s in srcs if s["last_success"]]
        item = {"label": label, "detail": detail, "rows": int(count),
                "manual": ids is not None and len(ids) == 0,   # [] = 직접 입력, None = 출처 미등록
                "period_from": p_from, "period_to": p_to,
                "updated": (updated.isoformat() if hasattr(updated, "isoformat") else str(updated)) if updated else None,
                "checked": _kst(max(lasts)) if lasts else None,
                "sources": [{"name": s["name"], "operator": s["operator"], "cycle": s["cycle"],
                             "license": s["license"], "url": s["url"], "last_success": _kst(s["last_success"]),
                             "last_rows": s["last_rows"]} for s in srcs]}
        groups.setdefault(grp, []).append(item)
        total += int(count)
    now = now or datetime.datetime.now(KST)
    total_bytes, table_count = size_row or (None, None)
    return {"checked_at": now.astimezone(KST).strftime("%Y-%m-%d %H:%M"), "total_rows": total,
            "total_bytes": total_bytes, "total_size": _size(total_bytes) if total_bytes is not None else None,
            "table_count": table_count,
            "groups": [{"name": g, "items": items} for g, items in groups.items() if items]}


def fetch(conn):
    status = conn.execute("SELECT * FROM mart.q_data_status()").fetchall()
    sources = conn.execute("SELECT * FROM mart.q_data_sources()").fetchall()
    try:   # 서버에 q_data_size를 아직 안 넣었으면 용량만 비우고 나머지는 보여 줌
        size = conn.execute("SELECT * FROM mart.q_data_size()").fetchone()
    except Exception as e:
        print(f"용량 조회 오류: {e!r}", file=sys.stderr)
        size = None
    return build(status, sources, size)


def get(connect, max_age=CACHE_SECONDS):
    """기억해 둔 값이 max_age초보다 새것이면 그대로, 아니면 DB에서 다시 셈. connect()는 DB 연결을 여는 함수."""
    with _lock:
        if _cache["data"] is not None and time.monotonic() - _cache["at"] < min(max_age, _cache["ttl"]):
            return _cache["data"]
        with connect() as conn:
            data = fetch(conn)
        ttl = CACHE_SECONDS if data["total_bytes"] is not None else RETRY_SECONDS
        _cache.update(at=time.monotonic(), data=data, ttl=ttl)
        return data


def clear():
    with _lock:
        _cache.update(at=None, data=None, ttl=CACHE_SECONDS)
