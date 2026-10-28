# Sysible updater sidecar.
#
# Based on the Docker CLI image because this service's whole job is to run
# `docker compose up --build` on the host's daemon — the CLI and the compose
# plugin come from the base rather than being installed by hand. git is added
# for the checkout pull.
#
# It runs as ROOT and is handed the host's Docker socket. That is unavoidable for
# what it does and is why its API surface is one allowlisted key (see
# backend/apps.py) and why it is expose-only with a shared-secret gate.
FROM docker:27-cli

RUN apk add --no-cache python3 py3-pip git

WORKDIR /app
COPY requirements.txt /app/requirements.txt
# PEP 668: the base image's Python is externally managed, and this container runs
# exactly one application, so installing into it directly is fine and avoids
# carrying a venv.
RUN pip install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY backend /app/backend

EXPOSE 8080
CMD ["python3", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8080"]

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
