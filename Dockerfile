# One image for both the web service and the daily ingestion job.
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" \
    MODEL_CACHE=/app/models DATA_DIR=/tmp/data PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache

# Bake the embedding model into the image so instances start without downloading it.
COPY disclosures/__init__.py disclosures/config.py disclosures/store.py disclosures/index.py ./disclosures/
RUN python -c "from disclosures.config import Settings; from disclosures.index import FastEmbedder; \
    c = Settings(); FastEmbedder(c.embed_model, c.model_cache).embed(['ok'])" \
    && chmod -R a+rX /app/models

COPY disclosures ./disclosures
RUN uv sync --frozen --no-dev --no-cache

# The model is already in the image, so never reach out to Hugging Face at run time.
ENV HF_HUB_OFFLINE=1
RUN useradd --create-home app
USER app
EXPOSE 8080
CMD ["sh", "-c", "uvicorn disclosures.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8080}"]
