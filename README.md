# ai_us_backend

Python backend for collecting survey data from the separately managed `ai_us` frontend and storing it in MongoDB.

## Repository boundaries

- `ai_us` (frontend repository): HTML, inline JavaScript, design assets, and survey UI.
- `ai_us_backend` (this repository): API, validation, MongoDB connectivity, deployment configuration, and backend tests.

The frontend should call the deployed API with `fetch()` from its existing inline script blocks. It must not contain database credentials.

## Initial layout

- `app/api`: HTTP route handlers.
- `app/core`: settings, security, and application configuration.
- `app/db`: MongoDB connection and data-access code.
- `app/schemas`: request and response validation models.
- `app/services`: survey submission business logic.
- `tests`: backend tests.
- `docs`: API contract and deployment notes shared with the frontend team.
- `deploy`: CloudType deployment assets.

## GitHub connection

Connect the blank GitHub repository after its clone URL is available:

```bash
git remote add origin <AI_US_BACKEND_GITHUB_URL>
git add .
git commit -m "Initialize backend project structure"
git push -u origin main
```