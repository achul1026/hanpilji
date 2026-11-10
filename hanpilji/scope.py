"""웹 · GPT가 「지금 가진 데이터 범위」를 어떻게 말할지.

- gangnam: Supabase 구성(GitHub Actions가 강남구만 받음)
- seoul: 개발서버 구성(서버 배치가 서울 25개 구 실거래를 받음)

HANPILJI_DATA_SCOPE로 정하고, 없으면 스키마를 하나로 합친 DB(HANPILJI_DB_SCHEMA)면 seoul, 아니면 gangnam.
Cloud Run을 Supabase로 되돌리면 변수를 지우는 것만으로 안내문도 강남구로 돌아감.
"""
import os

SCOPES = ("gangnam", "seoul")


def current(env=None):
    env = os.environ if env is None else env
    v = (env.get("HANPILJI_DATA_SCOPE") or "").strip().lower()
    if v in SCOPES:
        return v
    return "seoul" if (env.get("HANPILJI_DB_SCHEMA") or "").strip() else "gangnam"
