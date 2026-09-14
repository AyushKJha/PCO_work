# AgentPGO

This repository contains the AgentPGO backend and the TwineRun frontend experience.

## Frontend

The `landingpage/` directory contains the Vite React app. It opens on the TwineRun landing page and routes to the optimizer studio at `#studio`; content pages are available at `#benefits`, `#how-it-works`, `#benchmarks`, `#faqs`, and `#pricing`.

```bash
cd landingpage
npm ci
npm run lint
npm run build
```
http://127.0.0.1:3000/
## AWS delivery

Pushes to `frontendv1` run `.github/workflows/deploy-frontend.yml`, which builds the frontend, packages the static site with its Lambda adapter, deploys the existing AWS Lambda Function URL, waits for the update, and runs a live smoke test.
