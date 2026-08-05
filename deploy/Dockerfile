# Meridian Capital — trading control plane
#
# Build from the repository root:
#   docker build -f deploy/Dockerfile -t meridian .
FROM python:3.11-slim

# Asia/Kolkata is baked in: the algorithm compares naive datetimes against NSE
# session boundaries, so a container on UTC would trade at the wrong hours.
ENV TZ=Asia/Kolkata \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
# numpy, pandas and scipy all publish prebuilt aarch64 wheels, which is what
# keeps a Raspberry Pi build to minutes instead of hours. Refusing a source
# build for them turns a missing wheel into an immediate, readable error
# rather than a long compile that fails at the end on a board with 2 GB.
RUN pip install --no-cache-dir --only-binary=numpy,pandas,scipy -r requirements.txt

COPY backend/ /app/
# The dashboard doubles as a standalone Vercel project, so it is copied in
# rather than living under backend/ — the server can host it either way.
COPY web/ /app/web/

# Runtime state — equity book, ledgers, SQLite — lives on a volume so a
# redeploy never resets the compounding capital.
RUN mkdir -p /data && useradd -m -u 10001 meridian && chown -R meridian:meridian /app /data
USER meridian
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--ws-ping-interval", "20", "--ws-ping-timeout", "20"]
