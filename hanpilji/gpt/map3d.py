"""답 카드 아래 「3D 지도 · 주변」 자료(모델 없이 마트 함수 하나, 요금 없음).

mart.q_map3d가 단지 둘레 사방 300m 안팎의 필지 · 건물 · 도로 포장면 · 역 · 학교 · 주변 단지를 JSON 하나로 줌.
화면(static/map3d.js)이 건물을 층수만큼 세워 도면식 3D로 그림. 지적도가 없는 곳이면 None.
"""
import psycopg

from hanpilji import scope
from hanpilji.gpt import tools

HALF_M = 300          # 대상 필지 가운데에서 사방으로 몇 m(함수가 150~500으로 자름)


def sigungu_of(conn, query, data_scope=None):
    """서울 범위면 단지 · 동 이름으로 구를 찾음. 못 찾거나 강남구만 있는 DB면 강남구."""
    if (data_scope or scope.current()) != "seoul":
        return tools.DEFAULT_SIGUNGU
    try:
        row = conn.execute("SELECT mart.q_find_sigungu(%s::text)", (query,)).fetchone()
    except psycopg.Error:
        return tools.DEFAULT_SIGUNGU
    return row[0] if row and row[0] else tools.DEFAULT_SIGUNGU


def get(conn, query, half=HALF_M, data_scope=None):
    query = (query or "").strip()
    if not query:
        return None
    sg = sigungu_of(conn, query, data_scope)
    row = conn.execute("SELECT mart.q_map3d(%s::text, %s::text, %s::int)", (sg, query, int(half))).fetchone()
    out = row[0] if row else None
    if out:
        out["asof"] = str(out["asof"]) if out.get("asof") else None
    return out
