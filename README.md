# PCBuilder API

FastAPI backend foundation for PCBuilder.

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
  -d '{"name":"Intel Core i5 12400F","limit":20,"search_depth":20}'
```

The API responds with `202 Accepted` and a task ID. Read its state and raw provider
data without blocking the API process:

```bash
curl http://localhost:8000/api/v1/search/jobs/<task_id>
```

Completed data is grouped by provider under `result.providers`. Celery results expire
after one hour, and an individual parser task has a five-minute hard time limit.

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
