# SLOP IdP — the identity provider behind the gateway. Small, single-purpose.
FROM python:3.12-slim

# Don't run as root; the SQLite data dir is a mounted volume owned by this user.
RUN useradd --system --create-home --uid 10080 slop
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# EVERY top-level module, not just app.py. Copying one file by name meant that
# adding updates.py shipped an image whose `import updates` raised ImportError:
# uvicorn never started, the gateway's forward_auth had nothing to ask, and the
# whole site answered 502. tests/test_image_ships_every_module.py pins this.
COPY *.py ./

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
