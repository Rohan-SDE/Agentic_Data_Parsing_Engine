ARG PYTHON_BASE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && groupadd --gid 10001 adpe && useradd --uid 10001 --gid adpe --no-create-home adpe
COPY --chown=adpe:adpe pyproject.toml ./
COPY --chown=adpe:adpe engine ./engine
COPY --chown=adpe:adpe alembic ./alembic
COPY --chown=adpe:adpe alembic.ini ./
RUN mkdir /data && chown adpe:adpe /data
USER 10001:10001
ENV ADPE_DATA_DIR=/data
EXPOSE 8000
CMD ["python", "-m", "engine.cli", "serve", "--host", "0.0.0.0"]
