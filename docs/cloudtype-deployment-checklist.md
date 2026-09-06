# CloudType Deployment Checklist

## Development Environment

Create three CloudType services in the free-tier account.

1. Frontend service: connect the `ai_us` GitHub repository.
2. Backend service: connect the `ai_us_backend` GitHub repository.
3. MongoDB: create the CloudType preconfigured MongoDB container without a GitHub repository.

This repository provides a CloudType-ready `Dockerfile` based on the provided template. It differs from the Flask example in three required ways: Python 3.11 is used because this codebase requires Python 3.10 or later, `gunicorn` is not used, and its command starts the FastAPI API and Queue worker.

CloudType should build from the repository root using the included `Dockerfile`. Its start command is:

```sh
./deploy/start.sh
```

The script starts `python -m app.worker` for Queue processing and Uvicorn for FastAPI. CloudType supplies the public `PORT`; the script defaults to `5000`, matching the exposed container port, only when no port is set.

The CloudType MongoDB container currently reports wire version 7 (MongoDB 4.0 compatible). Do not upgrade the backend dependency to PyMongo 4.x unless the MongoDB container is upgraded to MongoDB 4.4 or later. This repository pins `pymongo>=3.12,<4.0` for compatibility.

## Required Backend Secrets

Enter these values in CloudType's Configure panel.

```text
APP_ENV=development
MONGODB_URI=<CloudType MongoDB connection URI>
DATABASE_NAME=ai_us_development
FRONTEND_ORIGINS=<frontend service public URL>
JWT_SECRET=<random secret of at least 32 bytes>
RESEARCHER_BOOTSTRAP_USERNAME=<first admin username>
RESEARCHER_BOOTSTRAP_PASSWORD=<first admin password>
```

Gmail Secrets are optional at this stage. The current backend does not start a Gmail worker handler or a password-reset endpoint, so leaving `EMAIL_PROVIDER`, `SMTP_*`, `EMAIL_FROM`, and `PASSWORD_RESET_BASE_URL` unset does not prevent deployment.

## First Deployment Checks

1. Confirm the backend build installs `requirements.txt` successfully.
2. Confirm both Uvicorn and the Queue worker start in backend logs.
3. Open `GET /health` and confirm `status` is `ok`.
4. Confirm the first admin can log in using the configured bootstrap credentials.
5. Confirm the MongoDB container is not publicly exposed.
6. Set the exact frontend public URL in `FRONTEND_ORIGINS` and verify browser requests are accepted by CORS.

## Production Promotion

Create the same three-service layout in the paid CloudType account. Use a different database name, MongoDB URI, JWT secret, bootstrap admin credentials, and frontend URL. Do not copy development research data into production.