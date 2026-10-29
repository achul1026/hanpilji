# 한필지 웹을 Cloud Run 등 컨테이너 호스팅에 올리는 이미지. 설명은 docs/deploy_cloudrun.md
FROM python:3.12-slim

# 공개 웹은 Sonnet 5.5(질문당 약 76원, Opus 5.5는 약 185원. 2026-10-29 실측). 터미널 GPT는 config.toml대로 Opus
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HANPILJI_WEB_PUBLIC=1 HANPILJI_TRUST_PROXY=1 HANPILJI_LLM_MODEL=claude-sonnet-5-5
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.toml .
COPY hanpilji ./hanpilji

# 관리자 권한 없이 실행
RUN useradd --create-home app
USER app

# Cloud Run이 정해 주는 포트(PORT, 보통 8080)를 web.py가 읽음
CMD ["python", "-m", "hanpilji.gpt.web", "--host", "0.0.0.0"]
