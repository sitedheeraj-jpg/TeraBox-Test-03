FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DOWNLOAD_DIR=/app/data/tmp \
    DATA_DIR=/app/data \
    HEALTH_PORT=8080

WORKDIR /app

RUN useradd --create-home --uid 1001 teradrop \
    && mkdir -p /app/data/tmp \
    && chown -R teradrop:teradrop /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

USER teradrop
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=3)"

CMD ["python", "-m", "app"]
