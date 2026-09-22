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
MONGODB_HOST=svc.sel3.cloudtype.app
MONGODB_PORT=32075
MONGODB_USERNAME=<MongoDB admin username>
MONGODB_PASSWORD=<MongoDB admin password>
DATABASE_NAME=ai_us_development
FRONTEND_ORIGINS=<frontend service public URL>
JWT_SECRET=<random secret of at least 32 bytes>
JWT_EXPIRATION_MINUTES=60
PARTICIPANT_JWT_EXPIRATION_MINUTES=240
RESEARCHER_BOOTSTRAP_USERNAME=<first admin username>
RESEARCHER_BOOTSTRAP_PASSWORD=<first admin password>
GEMINI_API_KEY=<Gemini API key for screenshot parsing>
GEMINI_SCREENSHOT_MODEL=gemini-2.5-flash
```

The backend creates `mongodb://<username>:<password>@<host>:<port>/?authSource=admin` at runtime. `MONGODB_USERNAME` and `MONGODB_PASSWORD` must be CloudType Secrets. The password is URL-encoded by the backend, so do not manually encode special characters. `MONGODB_URI` remains only as an optional legacy override and should not be set for this deployment.

Gmail Secrets are not required. Password reset verifies participant and guardian phone numbers, resets the password to `1234`, and forces first-login password change. Leaving `EMAIL_PROVIDER`, `SMTP_*`, `EMAIL_FROM`, and `PASSWORD_RESET_BASE_URL` unset does not prevent deployment.

`GEMINI_API_KEY` is required only for screenshot transcript parsing. Without it, ZIP/JSON transcript parsers continue to work, while image submissions are reported as unsupported instead of being sent to an external model.

## First Deployment Checks

1. Confirm the backend build installs `requirements.txt` successfully.
2. Confirm both Uvicorn and the Queue worker start in backend logs.
3. Open `GET /health` and confirm `status` is `ok`.
	- Development must return `environment: development`; production must return `environment: production`.
4. Confirm the first admin can log in using the configured bootstrap credentials.
5. Confirm the MongoDB container is not publicly exposed.
6. Set the exact frontend public URL in `FRONTEND_ORIGINS` and verify browser requests are accepted by CORS.

## Production Promotion

Create the same three-service layout in the paid CloudType account. Use a different database name, MongoDB URI, JWT secret, bootstrap admin credentials, and frontend URL. Do not copy development research data into production.

Current production public URLs:

```text
Frontend: https://web-ai-us-mu2jfq4sfccf2f38.sel3.cloudtype.app/
Backend:  https://port-0-ai-us-backend-mu2jfq4sfccf2f38.sel3.cloudtype.app/
```

Set the production backend's CloudType Secrets as follows, using only production MongoDB credentials and the separately generated production JWT secret.

```text
APP_ENV=production
DATABASE_NAME=<production database name>
FRONTEND_ORIGINS=https://web-ai-us-mu2jfq4sfccf2f38.sel3.cloudtype.app
JWT_SECRET=<production-only secret>
JWT_EXPIRATION_MINUTES=60
PARTICIPANT_JWT_EXPIRATION_MINUTES=240
RESEARCHER_BOOTSTRAP_USERNAME=<production first admin username>
RESEARCHER_BOOTSTRAP_PASSWORD=<production first admin password>
GEMINI_API_KEY=<production Gemini API key>
GEMINI_SCREENSHOT_MODEL=gemini-2.5-flash
```

The frontend maps the development hostname to the development backend and the production hostname to the production backend. Any unknown hostname falls back to the production backend.

`FLASK_ENV` is ignored because this service uses FastAPI. Set `APP_ENV=production` explicitly in the production backend.

## Survey Definition Promotion

Treat frontend `SURVEY_SETS` as the authoritative survey source. Before synchronization, run `git fetch`, compare local `HEAD` with `origin/main`, and update with `git pull --ff-only`.

```sh
python scripts/sync_frontend_survey_definitions.py --dry-run
```

For a new `surveyVersion`, register it through the admin API and keep earlier definitions and responses. The default backend URL is development.

```sh
python scripts/register_frontend_surveys.py
```

Use `--replace` only for a reviewed correction to the same version. Back up `survey_definitions` first and verify that existing response keys remain compatible.

```sh
python scripts/register_frontend_surveys.py --backend-url <backend-url> --replace
```

After registration, verify the definition list, participant submission and Queue completion, preview, and CSV export. The current round-1 v2 flattened counts are 230/209 for elementary part 1/2 and 230/234 for secondary part 1/2. Production promotion must happen only after the same checks pass in development.

When adding `followup.researchConsent` to an already-used part-two version, run `scripts/backfill_followup_consent.py` in dry-run mode first. Only after reviewing the count should `--apply --confirm BACKFILL-FOLLOWUP-CONSENT` be used; existing responses are assigned `2` (아니오) only when the key is absent.

## Survey display metadata repair

Survey version labels in the researcher console are generated from `surveyRound`, `part`, and `audience`, not manually entered title text. To repair existing title metadata without changing questions or responses, run the following against each database target:

```sh
python scripts/update_survey_definition_titles.py --database-name ai_us_development
python scripts/update_survey_definition_titles.py --database-name ai_us_production
```

For an empty development database, seed the frontend definitions first:

```sh
python scripts/sync_frontend_survey_definitions.py --database-name ai_us_development --upsert-missing
```

Always run either command with `--dry-run` first when targeting an unfamiliar database.

로컬에서 운영 데이터를 점검해야 할 때는 `.env`를 바꾸지 말고 `.env.production.local`을 사용합니다. 이 파일은 Git에서 제외되며 앱이 자동으로 읽지 않습니다. 운영 조회 도구에서 `--env-file .env.production.local`처럼 명시적으로 지정할 때만 `AI_US_PRODUCTION_MONGODB_URI`와 `AI_US_PRODUCTION_DATABASE_NAME`을 로드하고, DB명이 `ai_us_production`이 아니면 즉시 중단해야 합니다. 가능하면 MongoDB 읽기 전용 계정을 사용하며, 운영 쓰기 작업은 별도 확인 플래그 없이 실행할 수 없도록 유지합니다.