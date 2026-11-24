"""웹 · GPT가 「지금 가진 데이터 범위」를 어떻게 말할지.

- seoul: 개발서버 구성(서버 배치가 서울 25개 구 실거래를 받음). 웹사이트가 쓰는 유일한 DB라 기본값
- gangnam: 강남구만 채운 DB(로컬 시범 DB · 예전 백업에서 되살린 DB)

HANPILJI_DATA_SCOPE로 정하고, 없으면 seoul.
"""
import os

SCOPES = ("gangnam", "seoul")


def current(env=None):
    env = os.environ if env is None else env
    v = (env.get("HANPILJI_DATA_SCOPE") or "").strip().lower()
    return v if v in SCOPES else "seoul"
