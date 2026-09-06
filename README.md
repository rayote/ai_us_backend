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
- Each environment keeps its own `MONGODB_URI`, `JWT_SECRET`, Gmail SMTP settings, and allowed frontend origins in CloudType secrets.
- Set `RESEARCHER_BOOTSTRAP_USERNAME` and `RESEARCHER_BOOTSTRAP_PASSWORD` as development and production Secrets separately. They create the first researcher account when the backend first connects to MongoDB.
- The bootstrap account has the `admin` role. An admin can create individual `researcher` accounts; both roles can use researcher data-management functions.
- An admin registers each questionnaire using `surveyRound` and `surveyVersion`. Both admin and researcher accounts can download CSV exports for the stored response version.
- When a questionnaire has many items, use a bulk survey-definition import rather than entering each item individually. CSV or Excel is the preferred source; Word or PDF parsing requires a reviewed preview before registration.
- Admin and researcher accounts can import participant accounts from the frontend's UTF-8 CSV template. Excel participant imports are not implemented yet.
- Chat submissions retain both the original link or copied text and a normalized transcript. The current dummy parser handles basic pasted-text speaker labels and marks shared links as placeholders until service-specific parsers are added.
- Enter those values in the CloudType Configure panel. CloudType injects them as backend process environment variables, which `Settings.from_environment()` reads directly.
- `.env.example` is only a reference list of Secret names. The application does not load a local `.env` file automatically.
- When the CloudType backend service is created, add its provided Dockerfile template and the required build/start commands to this repository's deployment configuration. No local Docker installation is required.
- Start Uvicorn and the Queue daemon as separate OS processes in the same backend container. The Queue command is `python -m app.worker`; its startup recovery returns interrupted `processing` jobs to `queued`. It currently processes final survey-response jobs; chat-submission and notification handlers will be added with those features.
- Use `deploy/start.sh` as the backend start command after adapting the CloudType Dockerfile template. The CloudType deployment checklist is in `docs/cloudtype-deployment-checklist.md`.

## Email sender

Use one dedicated `@gmail.com` account as the initial sender for password-reset email. Enable Google 2-Step Verification on that account, create an app password, and enter `SMTP_USERNAME`, `SMTP_APP_PASSWORD`, and `EMAIL_FROM` in the CloudType Configure panel. Do not use a normal Gmail password or commit the app password. SMS is out of scope until a separate provider is selected.

Gmail settings are optional until the password-reset email feature is implemented. Their absence does not stop the current backend API or Queue worker from starting.

## Initial layout

- `app/api`: HTTP route handlers.
- `app/core`: settings, security, and application configuration.
- `app/db`: MongoDB connection and data-access code.
- `app/schemas`: request and response validation models.
- `app/services`: survey submission business logic.
- `tests`: backend tests.
- `docs`: API contract and deployment notes shared with the frontend team.
- `deploy`: CloudType deployment assets.

## Local checks

This server does not run local Docker or MongoDB. The API can still be checked with Python tests before it is connected to CloudType:

```bash
python3 -m pytest -q
```

Use `.env.example` only as a list of CloudType Secret names. Do not commit a real `.env` file.

## GitHub connection

The backend repository is connected to GitHub. CloudType can use this repository as its backend service source.

```bash
git remote add origin <AI_US_BACKEND_GITHUB_URL>
git add .
git commit -m "Initialize backend project structure"
git push -u origin main
```