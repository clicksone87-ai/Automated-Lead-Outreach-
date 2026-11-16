# Backend image for api, worker and scheduler.
# Includes Playwright Chromium (screenshots/PDF), Node + Lighthouse CLI + axe-core (audits),
# and git (local repo per website preview).

FROM node:22.14.0-bookworm-slim AS node

FROM python:3.12.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NODE_PATH=/usr/local/lib/node_modules

# Node runtime (for Lighthouse, axe-core and building site templates)
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl postgresql-client potrace libatomic1 \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g lighthouse@12.2.1 axe-core@4.10.2 \
 && npm cache clean --force

WORKDIR /app
COPY pyproject.toml ./
# Install dependencies first (cached layer), then the app itself.
RUN mkdir -p app && touch app/__init__.py \
 && pip install ".[dev]" \
 && rm -rf app
# Separate layer so a browser-install problem doesn't redo the slow pip layer.
# (Playwright's bundled Node driver needs libatomic1, installed above.)
RUN playwright install --with-deps chromium

# pg_dump must match the server major version (PostgreSQL 16); Debian bookworm ships 15.
# Debian's pg_wrapper picks the newest installed client, so `pg_dump` runs version 16.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates gnupg \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 \
 && rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-deps -e . \
 && useradd --create-home --uid 1000 appuser \
 && mkdir -p /data && chown -R appuser /data /app

# Lighthouse uses Playwright's Chromium
RUN CHROME=$(find /ms-playwright -type f -name chrome -path "*chrome-linux*" | head -n1) \
 && ln -s "$CHROME" /usr/local/bin/chromium
ENV CHROME_PATH=/usr/local/bin/chromium

USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
