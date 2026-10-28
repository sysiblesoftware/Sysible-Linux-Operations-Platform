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

# WHICH COMMIT THIS IMAGE WAS BUILT FROM.
#
# Three rounds of "is this my browser, my server, or your code?" came down to
# having no way to see what a container was actually running: the console is
# server-rendered, so a stale image and a fresh one are indistinguishable from
# the outside. The console shows this in its footer.
#
# Last in the file deliberately — changing it rebuilds one ENV layer rather
# than invalidating the COPY above and reinstalling every dependency.
ARG SYSIBLE_REV=""
ENV SYSIBLE_REV=$SYSIBLE_REV
