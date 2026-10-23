# syntax=docker/dockerfile:1
# One Dockerfile for all SpendWise services:
#   docker build --build-arg SERVICE=auth-service -t spendwise/auth-service .

########## Stage 1: build wheels ##########
FROM python:3.12-slim AS builder
ARG SERVICE
WORKDIR /build
COPY ${SERVICE}/requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

########## Stage 2: runtime ##########
FROM python:3.12-slim
ARG SERVICE
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    SERVICE_NAME=${SERVICE}

RUN groupadd --system app \
 && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# Dependencies first: this layer is reused until requirements.txt changes
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* \
 && rm -rf /wheels

# Code last: changes most often
COPY common/ ./common/
COPY ${SERVICE}/ ./

RUN mkdir -p /data && chown app:app /data
USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"

CMD ["gunicorn", "--preload", "--workers", "2", "--bind", "0.0.0.0:8080", "wsgi:app"]
