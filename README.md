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
| `DATABASE_URL` | Async SQLAlchemy URL for the application database. |
| `TEST_DATABASE_URL` | Async SQLAlchemy URL for the test database. |
| `POSTGRES_USER` | Development PostgreSQL user for Docker. |
| `POSTGRES_PASSWORD` | Development PostgreSQL password for Docker. |
| `POSTGRES_DB` | Development PostgreSQL database for Docker. |
| `TEST_POSTGRES_USER` | Test PostgreSQL user for Docker. |
| `TEST_POSTGRES_PASSWORD` | Test PostgreSQL password for Docker. |
| `TEST_POSTGRES_DB` | Test PostgreSQL database for Docker. |
