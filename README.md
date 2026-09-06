# ai_us_backend

Python backend for collecting survey data from the separately managed `ai_us` frontend and storing it in MongoDB.

## Repository boundaries

- `ai_us` (frontend repository): HTML, inline JavaScript, design assets, and survey UI.
- `ai_us_backend` (this repository): API, validation, MongoDB connectivity, deployment configuration, and backend tests.

The frontend should call the deployed API with `fetch()` from its existing inline script blocks. It must not contain database credentials.

## CloudType environments

CloudType deploys the frontend and backend services by connecting each service to its own GitHub repository. Local Docker and MongoDB installation are not required for this project.

- Development: a free-tier CloudType account runs a frontend service from `ai_us`, a backend service from this repository, and a CloudType preconfigured MongoDB container.
- Production: a separate paid CloudType account uses the same three-service layout with separate secrets, database data, and public URLs.
- MongoDB is provisioned in CloudType and is not connected to a GitHub repository.
- Each environment keeps its own `MONGODB_URI`, `JWT_SECRET`, email settings, and allowed frontend origins in CloudType secrets.

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

The backend repository is connected to GitHub. CloudType can use this repository as its backend service source.

```bash
git remote add origin <AI_US_BACKEND_GITHUB_URL>
git add .
git commit -m "Initialize backend project structure"
git push -u origin main
```