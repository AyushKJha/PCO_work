# TwineRun / AgentPGO

This local integration combines the React frontend and backend-v1.

## Frontend
The Vite app is in `landingpage/`. Run `npm ci`, `npm run lint`, and `npm run dev` there.
The homepage leads to real `/signin`, `/signup`, and `/studio` routes.
Local Vite requests to `/api/v1` are proxied to the local API at port 8000.
Set `VITE_API_BASE_URL` explicitly for hosted builds.

## Backend
- FastAPI, authentication and OTLP ingestion: `apps/api/`
- SDKs and Vercel AI SDK adapter: `packages/`
- Profiling, evaluation, optimization and workers: `services/`
- CLI: `cli/`
- Alembic migrations: `migrations/`

Install Python >=3.11 and run `pip install -e ".[dev]"`, then `pytest -q`.
Run migrations before the API: `alembic -c migrations/alembic.ini upgrade head`.
Start with `uvicorn apps.api.main:app --host 127.0.0.1 --port 8000`.

Optimization/provider execution requires configured runners. Local auth and onboarding do not imply that live optimization or billing is configured.
The interactive landing experiment is an illustrative simulation, not a live benchmark.
```bash
cd landingpage
npm ci
npm run lint
npm run build
```
http://127.0.0.1:3000/
## AWS delivery

## Deployment boundaries
The existing frontend workflow deploys from `frontendv1`; backend deployment uses `main`.
This integration is being reviewed locally. Do not promote it until the backend review and deployment configuration are complete.
The legacy root Vercel configuration serves architecture documentation; use the frontend project root for frontend deployments.
