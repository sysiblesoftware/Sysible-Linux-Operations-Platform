# SLOP IdP — the identity provider behind the gateway. Small, single-purpose.
FROM python:3.12-slim

# Don't run as root; the SQLite data dir is a mounted volume owned by this user.
RUN useradd --system --create-home --uid 10080 slop
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV SLOP_DATA_DIR=/data PORT=8080
RUN mkdir -p /data && chown slop:slop /data
VOLUME ["/data"]
USER slop

EXPOSE 8080
# One worker: sessions/throttle live partly in-process, and CE runs a single
# instance behind the gateway. (EE can move sessions fully to the DB and scale out.)
HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3).status==200 else 1)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
