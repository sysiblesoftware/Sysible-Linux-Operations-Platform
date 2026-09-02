# Sysible Visualizer — the SLOP activity & log console.
FROM python:3.12-slim

# Non-root runtime user. Visualizer is STATELESS: it stores nothing and mounts no
# volume — every record it shows is fetched live from the app that owns it.
RUN useradd --system --uid 10073 --home-dir /app --shell /usr/sbin/nologin visualizer
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend
RUN chown -R visualizer:visualizer /app
USER visualizer

EXPOSE 8080
# TLS is terminated by the SLOP gateway; the app serves plain HTTP on 8080 inside
# the private network.
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
