# ---------------------------------------------------------------------
# FMS — Enterprise Fleet Management System
#
# Multi-stage: wheels are built in a throwaway stage so the final image
# doesn't carry a C toolchain it will never use again. Keeps the runtime
# image smaller and reduces the amount of software exposed in production.
# ---------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential is needed to compile a few wheels; default-libmysqlclient-dev
# for the MySQL driver. Neither is carried into the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential default-libmysqlclient-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_APP=wsgi.py

# curl is used by the compose healthcheck; netcat lets the entrypoint
# wait for MySQL to accept connections before running migrations.
#
# tesseract-ocr and poppler-utils power text extraction from scanned
# Certificates of Registration. They are runtime binaries, not Python
# packages, so they must be installed in the image -- pytesseract is
# only a wrapper and does nothing without the tesseract executable.
#
# The application degrades gracefully if they are absent (extraction
# simply reports that it is unavailable and everything falls back to
# manual entry), so an older image will not break -- it just will not
# extract. Roughly 120MB of image size; worth it against a fleet's worth
# of manual typing.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl netcat-openbsd \
        tesseract-ocr tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

WORKDIR /app
COPY . .

# Run as a non-root user. If the container is ever compromised, the
# attacker lands as an unprivileged account rather than root.
RUN useradd --create-home --shell /bin/bash fms \
    && mkdir -p /app/instance /app/uploads \
    && chown -R fms:fms /app
USER fms

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
