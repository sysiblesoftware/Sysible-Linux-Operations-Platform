# Sysible Flashback — the standalone SLOP module.
FROM python:3.12-slim

# Non-root runtime user; the version store lives on a mounted volume at /data.
RUN useradd --system --uid 10072 --home-dir /app --shell /usr/sbin/nologin flashback
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend

ENV SYSIBLE_FLASHBACK_DATA=/data
RUN mkdir -p /data && chown -R flashback:flashback /data /app
USER flashback

EXPOSE 8080
# TLS is terminated by the SLOP gateway; the app serves plain HTTP on 8080 inside
# the private network. Override the port/host via the compose command if needed.
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
