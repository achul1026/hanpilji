# 한필지 웹을 Cloud Run 등 컨테이너 호스팅에 올리는 이미지. 설명은 docs/deploy_cloudrun.md
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HANPILJI_WEB_PUBLIC=1 HANPILJI_TRUST_PROXY=1
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
