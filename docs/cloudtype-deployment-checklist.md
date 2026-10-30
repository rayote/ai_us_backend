# CloudType Deployment Checklist

## Deployment Model (Confirmed 2026-10-23)

The user confirmed that this project's CloudType services are deployed manually: CloudType fetches the connected Git repository, builds a Docker image and deploys the resulting container when the operator explicitly starts deployment. A GitHub push publishes source changes only; it does not redeploy either development or production. Restarting an existing service does not by itself incorporate new repository commits. Do not request a separate automatic-deployment toggle confirmation before an authorized push under this confirmed setup.

1. Review and push the approved frontend and backend commits to their existing `main` branches. Keep unrelated local work and real environment files out of the commits.
2. In CloudType, select the **development backend** service and manually deploy the latest backend `main`. Confirm the intended repository/commit and development Secrets before starting the build. Check that FastAPI, the submission worker and the administrator analysis worker all start successfully.
3. Select the **development frontend** service and manually deploy the latest frontend `main`. Confirm its repository/commit rather than only restarting the previous container.
4. Verify development `/health` reports `environment: development`, the backend exposes the new T2 status/progress routes, and the frontend serves the T2 questionnaire. Check participant submission through worker completion, progress recovery and researcher CSV/download behavior.
5. Leave production containers and configuration unchanged. Development deployment is not production promotion; production requires researcher acceptance, explicit promotion approval and the production backup procedure below.

The assistant may commit and push when requested; the user operates the CloudType manual build/deployment. Record the pushed commit IDs in the handoff so the user can identify the exact source to deploy.

### Round Admission Rollout

Deploy the approved backend before the frontend. Keep the development `T1_SUBMISSIONS_OPEN=false` setting during the first deployment so the existing T1 closure is preserved before a DB override exists. No startup migration or live DB write is required: missing rounds use T1's environment fallback, T2 open and T3/T4 closed. Production remains unchanged without separate approval.

After deployment, `설문 접수 관리` under `미완료자 관리` displays the common round state plus configuration selection, submission permission and explicit period-end controls; only admins can change them. T3/T4 without complete definitions must have all controls disabled. Verify the four records and read-only researcher controls before an authorized change. Preparing overrides permission; ending or unending the period clears permission and never automatically resumes submissions. Existing complete T1 v2-0916/T2 v9-1022 selections are retained without choosing arbitrary draft releases. Confirm the participant frontend reads selected DB specs and matches all four round badges. Future confirmed changes apply without redeployment; participant status refreshes when the list is reopened. Preserve responses, completion history, downloads and accepted jobs. Local browser verification must mock all external API calls and must not change live settings.

## Development Environment

Create three CloudType services in the free-tier account.

1. Frontend service: connect the `ai_us` GitHub repository.
2. Backend service: connect the `ai_us_backend` GitHub repository.
3. MongoDB: create the CloudType preconfigured MongoDB container without a GitHub repository.

This repository provides a CloudType-ready `Dockerfile` based on the provided template. It differs from the Flask example in three required ways: Python 3.11 is used because this codebase requires Python 3.10 or later, `gunicorn` is not used, and its command starts FastAPI plus the submission and administrator analysis workers.

CloudType should build from the repository root using the included `Dockerfile`. Its start command is:

```sh
./deploy/start.sh
```

The script starts Uvicorn, `python -m app.worker` for submission/parse/download processing, and `python -m app.analysis_worker` for the separate `analysis_jobs` queue. Each worker has its own supervisor with 2-to-30-second restart backoff, process lock and heartbeat. All three share one backend container; no additional CloudType service or Secret is required. CloudType supplies the public `PORT`; the script defaults to `5000`, matching the exposed container port, only when no port is set.

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
GEMINI_SCREENSHOT_MODEL=gemini-3.8-flash
```

The backend creates `mongodb://<username>:<password>@<host>:<port>/?authSource=admin` at runtime. `MONGODB_USERNAME` and `MONGODB_PASSWORD` must be CloudType Secrets. The password is URL-encoded by the backend, so do not manually encode special characters. `MONGODB_URI` remains only as an optional legacy override and should not be set for this deployment.

Gmail Secrets are not required. Password reset verifies participant and guardian phone numbers, resets the password to `1234`, and forces first-login password change. Leaving `EMAIL_PROVIDER`, `SMTP_*`, `EMAIL_FROM`, and `PASSWORD_RESET_BASE_URL` unset does not prevent deployment.

`GEMINI_API_KEY` is required only for screenshot transcript parsing. Without it, ZIP/JSON transcript parsers continue to work, while image submissions are reported as unsupported instead of being sent to an external model.

## First Deployment Checks

1. Confirm the backend build installs `requirements.txt` successfully.
2. Confirm Uvicorn, `app.worker` and `app.analysis_worker` start in backend logs.
3. Open `GET /health` and confirm `status` is `ok`.
	- Development must return `environment: development`; production must return `environment: production`.
4. Sign in to the researcher console and confirm API, `설문 Worker` and `관리자 Worker` are green. Inspect authenticated `/api/v1/researcher/system-health` fields `api`, `worker`, and `analysisWorker`; public `/health` alone does not verify either worker. Analysis running inside a menu is not a worker-health failure.
5. Confirm the first admin can log in using the configured bootstrap credentials.
6. Confirm the MongoDB container is not publicly exposed.
6. Set the exact frontend public URL in `FRONTEND_ORIGINS` and verify browser requests are accepted by CORS. Allowed methods must include `GET`, `POST`, `PUT`, `PATCH`, and `DELETE`.
7. Verify the management-status PATCH preflight returns `200` and includes the frontend origin and PATCH method.

```sh
curl -i -X OPTIONS "$BACKEND_URL/api/v1/researcher/chat-submissions/example/review" \
	-H "Origin: $FRONTEND_URL" \
	-H "Access-Control-Request-Method: PATCH" \
	-H "Access-Control-Request-Headers: authorization,content-type"
```

8. When `GEMINI_API_KEY` is configured, submit test screenshots, request parsing from the researcher console, and verify that the run reaches `completed` or an explainable `warning`. Confirm both JSON and CSV downloads before production promotion.

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
GEMINI_SCREENSHOT_MODEL=gemini-3.8-flash
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

After a backend change affecting researcher mutations or downloads, also verify management-status PATCH/DELETE, filtered preview refresh, original ZIP generation, and transcript ZIP generation. The frontend keeps a buttonless loading modal open until each management-status mutation and its subsequent preview request finish.

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

## Consent Management Rollout

- Deploy the backend API and submission worker together before the frontend. The consent interface is a dedicated `동의 관리` menu immediately after `참여 현황`; it also supports CSV-only accounts. No bulk rewrite of existing consents or material deletion is part of startup.
- Verify exact-phone lookup, required reason, revision conflict (409), withdrawal, future-only re-consent and separately requested old-material restoration in an approved test environment. Preserve original application consent, source files, parse records and review/payment notes. Do not exercise these mutations on production accounts merely to check deployment.
- Check default/all/explicit-ID selections, direct original/JSON/CSV downloads and parse paths. Restricted material must be denied independently of review notes. Old completed ZIPs without a submission-ID manifest must be rejected (409) and regenerated on an explicit request; previously delivered copies cannot be recalled.
- Confirm independent analysis-worker health and existing survey submission behavior after rollout. GitHub `main` and the running CloudType commit must be verified separately; do not infer deployment from a successful push.

## Local Read-Only Operations Monitor

This is a local helper, not a CloudType service or a committed backend deployment component. Its files currently live in `/data/ai_us_joint_research/tmp/deployment-monitor/`; retain them separately when moving the workspace. It binds only to `127.0.0.1:4199` and requires an explicitly named `ai_us_production` config. Never print the environment file, connection URI or credentials in logs or handoffs.

Check the page and aggregate endpoint first:

```sh
curl --max-time 5 http://127.0.0.1:4199/
curl --max-time 30 http://127.0.0.1:4199/api/status
ps -eo pid,ppid,etime,args | grep '[d]eployment-monitor/server.py'
```

Only when the process is absent, restart from the backend repository root using the existing local environment and a compatible Python interpreter:

```sh
nohup python ../tmp/deployment-monitor/server.py \
	--env-file .env.production.local --port 4199 \
	> /tmp/ai-us-deployment-monitor.log 2>&1 < /dev/null &
```

The browser refreshes every 30 seconds; the server also caches snapshots for 30 seconds. Metrics cover distinct participant heartbeat activity in the trailing 60/300 seconds, queued/processing counts in both queues, and the submission-worker heartbeat. The local monitor does not independently prove administrator-analysis-worker heartbeat health; use authenticated `system-health` for that. Zero activity is not a guarantee of safe or interruption-free deployment. The process performs aggregate reads only, not production mutations or deployment.

On 2026-10-30, the process was absent and port 4199 refused connections; restarting it restored HTTP 200 for both endpoints and the shared browser. `nohup` protects against terminal hangup but does not provide crash supervision or restart after the container/host reboots. Automatic startup is not configured. If unavailable, `ss` is not required: use process inspection and HTTP checks above. Current log path: `/tmp/ai-us-deployment-monitor.log`.
