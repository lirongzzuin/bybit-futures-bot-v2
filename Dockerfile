FROM python:3.11-slim

WORKDIR /app

# 시스템 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 데이터/로그 디렉토리 (볼륨 마운트 포인트)
RUN mkdir -p /data/logs /data/db /data/cache

# 환경변수
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data

CMD ["python", "main.py"]
