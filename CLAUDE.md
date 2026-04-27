# Project Conventions

## Commands

- Setup local env files: `cp .env.example .env && cp .env.test.example .env.test`
- Start Supabase local stack: `supabase start`
- Run app locally: `.venv/bin/python -m uvicorn main:app --reload --port 8000`
- Run unit-style tests: `.venv/bin/python -m pytest tests -v --ignore=tests/integration --ignore=tests/performance`
- Run integration tests: `.venv/bin/python -m pytest tests/integration -v`
- Run performance tests: `.venv/bin/python -m pytest tests/performance -v -s`

## Git

- Keep `main` clean for deploy-ready code.
- Push ongoing V1 work to a dedicated long-lived branch.
- Never commit local secrets or machine-specific env files.

## Ignore Rules

- Track `.env.example` and `.env.test.example`.
- Ignore `.env`, `.env.local`, `.env.test`, Python caches, coverage artifacts, editor files, macOS junk, and Supabase local state.
