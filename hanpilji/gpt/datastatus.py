"""데이터 현황 화면(/data)에 줄 값: DB에 쌓인 데이터 묶음마다 줄 수 · 데이터 기간 · 마지막으로 새로 받은 때 · 출처.

마트 함수 둘(sql/23_data_status.sql)만 부르고 모델은 부르지 않음. 큰 표를 다 세느라 몇 초 걸리므로
결과를 CACHE_SECONDS 동안 기억해 두고 씀(데이터는 하루 한 번 새로 받으므로 1시간이면 충분).
"""
import datetime
import threading
import time

CACHE_SECONDS = 3600
GROUPS = ["실거래", "단지 · 건물 · 토지", "법령 · 규제 · 정책", "시장 · 기타"]
KST = datetime.timezone(datetime.timedelta(hours=9))

_lock = threading.Lock()
_cache = {"at": None, "data": None}


def _kst(ts):
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    return ts.astimezone(KST).strftime("%Y-%m-%d %H:%M")


def build(status_rows, source_rows, now=None):
    """함수 결과 두 가지를 화면용 묶음으로. 줄이 0인 묶음은 뺌(그 DB에 아직 안 받은 것)."""
    sources = {}
    for sid, name, operator, cycle, license_, url, last, rows in source_rows:
        sources[sid] = {"id": sid, "name": name, "operator": operator, "cycle": cycle, "license": license_,
                        "url": url, "last_success": last, "last_rows": rows}
    groups = {g: [] for g in GROUPS}
    total = 0
    for grp, label, detail, count, p_from, p_to, ids in status_rows:
        if not count:
            continue
        srcs = [sources[i] for i in (ids or []) if i in sources]
        lasts = [s["last_success"] for s in srcs if s["last_success"]]
        item = {"label": label, "detail": detail, "rows": int(count),
                "period_from": p_from, "period_to": p_to,
                "updated": _kst(max(lasts)) if lasts else None,
                "sources": [{"name": s["name"], "operator": s["operator"], "cycle": s["cycle"],
                             "license": s["license"], "url": s["url"], "last_success": _kst(s["last_success"]),
                             "last_rows": s["last_rows"]} for s in srcs]}
        groups.setdefault(grp, []).append(item)
        total += int(count)
    for items in groups.values():
        items.sort(key=lambda x: -x["rows"])
    now = now or datetime.datetime.now(KST)
    return {"checked_at": now.astimezone(KST).strftime("%Y-%m-%d %H:%M"), "total_rows": total,
            "groups": [{"name": g, "items": items} for g, items in groups.items() if items]}


def fetch(conn):
    status = conn.execute("SELECT * FROM mart.q_data_status()").fetchall()
    sources = conn.execute("SELECT * FROM mart.q_data_sources()").fetchall()
    return build(status, sources)


def get(connect, max_age=CACHE_SECONDS):
    """기억해 둔 값이 max_age초보다 새것이면 그대로, 아니면 DB에서 다시 셈. connect()는 DB 연결을 여는 함수."""
    with _lock:
        if _cache["data"] is not None and time.monotonic() - _cache["at"] < max_age:
            return _cache["data"]
        with connect() as conn:
            data = fetch(conn)
        _cache.update(at=time.monotonic(), data=data)
        return data


def clear():
    with _lock:
        _cache.update(at=None, data=None)
