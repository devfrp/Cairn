FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# psycopg[binary] embarque libpq : aucune dépendance système à installer.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Les migrations sont lues depuis db/migrations au démarrage.
COPY db/ ./db/

RUN useradd --create-home --uid 1000 cairn && \
    mkdir -p /data/activities && chown -R cairn:cairn /data /app
USER cairn

ENTRYPOINT ["cairn"]
CMD ["--help"]
