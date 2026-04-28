FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WX_SERVICE_ROOT=/app \
    WX_SERVICE_DATA_DIR=/data \
    WX_SERVICE_RUNTIME_DIR=/run/wx_service-python \
    WX_SERVICE_LOG_DIR=/logs \
    WX_SERVICE_SOCKET_PATH=

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN chmod +x /app/meituan-query 2>/dev/null || true \
    && mkdir -p /data /logs /run/wx_service-python /run/wx_service

EXPOSE 80

CMD ["python", "run_server.py"]
