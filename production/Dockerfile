ARG PYTHON_BASE=python:3.12.14-alpine3.23
FROM ${PYTHON_BASE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN apk upgrade --no-cache && apk add --no-cache libstdc++ tar \
    && pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.lock \
    && addgroup -g 10001 adpe && adduser -D -H -u 10001 -G adpe adpe
COPY --chown=adpe:adpe pyproject.toml ./
COPY --chown=adpe:adpe engine ./engine
COPY --chown=adpe:adpe alembic ./alembic
COPY --chown=adpe:adpe alembic.ini ./
RUN mkdir /data && chown adpe:adpe /data
USER 10001:10001
ENV ADPE_DATA_DIR=/data
EXPOSE 8000
CMD ["python", "-m", "engine.cli", "serve", "--host", "0.0.0.0"]
