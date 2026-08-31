# PCBuilder API

FastAPI backend foundation for PCBuilder.

## Architecture

The search flow is asynchronous: FastAPI validates and reserves a request in Redis,
Celery executes the DNS provider in a browser, and the status endpoint reads Celery
state without waiting for completion. The provider is built on the minimal
`AbstractBrowserParser`; `BrowserIdentity` keeps proxy, seed, locale and timezone
together. Only DNS is registered today. Results are JSON-normalized before being
stored by Celery.

The default region is Санкт-Петербург. DNS sets and verifies the region before it
loads product cards. `limit=null` enables bounded full-catalog scrolling; normal
limits stop after the requested number of cards. Browser tasks run with a maximum
Celery concurrency of five.

## Local Setup

Install dependencies:

```bash
poetry install --with dev
```

Create a local environment file:

```bash
cp .env.example .env
```

Run the API:

```bash
poetry run uvicorn app.main:app --reload
```

Healthcheck:

```bash
curl http://localhost:8000/health
```

## Docker

Start the app with PostgreSQL and test PostgreSQL:

```bash
docker compose up --build
```

The API runs on `http://localhost:8000`.

Docker Compose also starts Redis and a Celery worker. Redis database `0` is used as
the broker and database `1` as the result backend. Check the worker with:

```bash
docker compose exec app python -c "from app.tasks import ping; print(ping.delay().get(timeout=10))"
```

The expected result is `pong`.

## Celery

For local development, start Redis and then run:

```bash
poetry run celery -A app.celery_app:celery_app worker --loglevel=INFO
```

Tasks are defined in `app/tasks.py`. The included connectivity task can be queued
with `ping.delay()`.

The worker runs at most five tasks concurrently. Search uses a provider registry and
currently registers only the DNS parser; the other parser modules are not part of the
search workflow yet.

Create a search job:

```bash
curl -X POST http://localhost:8000/api/v1/search/jobs \
  -H 'Content-Type: application/json' \
  -d '{"name":"Intel Core i5 12400F","limit":20,"sort":"popular","region":"Санкт-Петербург"}'
```

The API responds with `202 Accepted` and a task ID. Read its state and normalized
product data without blocking the API process:

```bash
curl http://localhost:8000/api/v1/search/jobs/<task_id>
```

Completed products are returned under `result.items`. Each item contains the original
`price_text`, numeric `price_value` in rubles, and `currency="RUB"` when a price was
available. The only current sort mode is `popular`; it preserves the popularity order
returned by DNS. Celery results expire after one hour, and an individual parser task
has a five-minute hard time limit.

The DNS parser verifies the requested region before searching. The default region is
`Санкт-Петербург`. Set `limit` to an integer from 1 to 100 to stop after that many
items, or to `null` to scroll until the catalog stops growing. Full-catalog searches
remain bounded by the Celery task time limit.

Browser proxy identities are configured internally and are never accepted from the
public API. `DNS_BROWSER_IDENTITIES` is a JSON array. Every entry binds one stable
InvisiblePlaywright `seed` to one proxy configuration; retries rotate the whole
identity. Leave the value as `[]` to use a direct connection. Keep proxy credentials
only in the local environment and never commit them.

Search creation is limited per client IP. Equivalent active searches reuse the same
task ID instead of opening another browser. Unknown or expired task IDs return `404`;
temporary Redis or broker outages return `503`.

`BROWSER_HEADLESS=true` is the default and is suitable for the Docker worker. Set it
to `false` only when debugging the parser interactively with a display available.

## Logging

API requests and Celery tasks use the same console log format. Set `LOG_LEVEL` to
control verbosity.

The compose build installs dev dependencies so `pytest`, `ruff`, and `pre-commit` are available inside the `app` container. A plain `docker build .` keeps the default production dependency set.

## Tests

Run tests locally:

```bash
poetry run pytest
```

Run tests in Docker:

```bash
docker compose run --rm app poetry run pytest
```

The test database URL is configured with `TEST_DATABASE_URL`. Keep test database credentials separate from development and production values.

## Technical debt

- **DNS anti-bot access:** the DNS site may return a Qrator challenge to the Docker
  worker, before the catalog and region selector are rendered. Until an approved
  proxy pool or another permitted session/access mechanism is available, real DNS
  smoke tests are informational only; the parser must keep failing safely when the
  region cannot be confirmed.
- **Distributed identity leasing:** the temporary in-memory identity provider is
  stable and thread-safe within one worker process, but does not coordinate leases
  across Celery prefork processes. Add a Redis lease/allocator before using a shared
  proxy pool in production.
- **Authentication and ownership:** task visibility is currently scoped to the
  client IP. Replace this with an authenticated user/tenant ID before exposing the
  search API publicly.
- **Redis hardening:** configure ACL/TLS, private networking, secret rotation and
  monitoring for broker/result Redis in production.
- **Retry and overload control:** add exponential backoff with jitter, proxy
  circuit-breaking, separate full-mode capacity limits and idempotency protection
  for Celery redelivery.
- **Configuration validation:** move proxy identities and seeds to a secret manager
  and add an explicit allowlist/policy for permitted proxy endpoints and regions.
- **Production smoke access:** provide an approved proxy/session mechanism that can
  pass DNS anti-bot checks; until then real browser smoke tests cannot prove catalog
  parsing from the worker network.

## Migrations

Create a migration after adding SQLAlchemy models:

```bash
poetry run alembic revision --autogenerate -m "describe change"
```

Apply migrations:

```bash
poetry run alembic upgrade head
```

## Quality Gate

```bash
poetry check
poetry run pytest
poetry run ruff check .
poetry run black .
poetry run pre-commit run -a
docker compose config
docker compose build
docker compose run --rm app poetry run pytest
```

## Environment Variables

Use `.env` for local values and keep it out of git. `.env.example` contains safe placeholders.

| Variable | Description |
| --- | --- |
| `APP_NAME` | FastAPI application title. |
| `ENVIRONMENT` | Runtime environment name. |
| `DEBUG` | Enables FastAPI debug mode when `true`. |
| `LOG_LEVEL` | Logging verbosity for the API and Celery worker. |
| `BROWSER_HEADLESS` | Runs parser browsers without a visible window when `true`. |
| `DNS_BROWSER_IDENTITIES` | JSON array of internal stable proxy/seed identities; `[]` uses a direct connection. |
| `SEARCH_RATE_LIMIT` | Maximum search requests per client during one rate window. |
| `SEARCH_RATE_WINDOW_SECONDS` | Search rate-limit window in seconds. |
| `DATABASE_URL` | Async SQLAlchemy URL for the application database. |
| `TEST_DATABASE_URL` | Async SQLAlchemy URL for the test database. |
| `REDIS_URL` | General Redis connection URL. |
| `CELERY_BROKER_URL` | Redis URL used by Celery as its broker. |
| `CELERY_RESULT_BACKEND` | Redis URL used for Celery task results. |
| `POSTGRES_USER` | Development PostgreSQL user for Docker. |
| `POSTGRES_PASSWORD` | Development PostgreSQL password for Docker. |
| `POSTGRES_DB` | Development PostgreSQL database for Docker. |
| `TEST_POSTGRES_USER` | Test PostgreSQL user for Docker. |
| `TEST_POSTGRES_PASSWORD` | Test PostgreSQL password for Docker. |
| `TEST_POSTGRES_DB` | Test PostgreSQL database for Docker. |
