# Backend image for api, worker and scheduler.
# Includes Playwright Chromium (screenshots/PDF), Node + Lighthouse CLI + axe-core (audits),
# and git (local repo per website preview).

FROM node:22.11.0-bookworm-slim AS node

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
 && apt-get install -y --no-install-recommends git curl postgresql-client potrace \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g lighthouse@12.2.1 axe-core@4.10.2 \
 && npm cache clean --force

WORKDIR /app
COPY pyproject.toml ./
# Install dependencies first (cached layer), then the app itself.
RUN mkdir -p app && touch app/__init__.py \
 && pip install ".[dev]" \
 && playwright install --with-deps chromium \
 && rm -rf app

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
