FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgl1 libxrender1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir cadquery==2.6.1 \
    && useradd --create-home --uid 10001 worker

COPY --chown=worker:worker render.py /app/render.py
USER worker
WORKDIR /app
ENTRYPOINT ["python", "/app/render.py"]
