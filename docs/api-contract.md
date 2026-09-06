# API Contract

Base URL: the public backend URL deployed by CloudType for the current environment.

All request and response bodies use JSON unless an endpoint explicitly returns a file.

The API supports the current CloudType preconfigured MongoDB service through PyMongo 3.x compatibility. This does not change the HTTP request or response contract.

## Survey data terminology

- `surveyRound`: the numbered survey round, such as `1` or `2`.
- `surveyVersion`: the version of the questionnaire used in that round, such as `2026-round-2-v2`.

Final survey submission, temporary browser storage, MongoDB documents, and CSV exports use both values. This preserves the questionnaire version when a later round changes its questions.

## Register survey definition

`POST /api/v1/admin/survey-definitions`

This endpoint requires an `admin` bearer token. It registers the fixed CSV column order for a single `surveyRound` and `surveyVersion` before responses are collected.

```json
{
  "surveyRound": 2,
  "surveyVersion": "2026-round-2-v2",
  "questions": [
    {
      "key": "q1",
      "csvColumn": "첫 번째 문항",
      "order": 1
    }
  ]
}
```

The same round and version cannot be registered more than once. Register a new `surveyVersion` when the questionnaire changes.

## Planned bulk survey-definition import

For large questionnaires, the admin will use a future bulk import endpoint or reviewed backend import command instead of registering items one by one. The preferred source is CSV or Excel with the following columns:

```csv
surveyRound,surveyVersion,key,csvColumn,order
2,2026-round-2-v2,q1,AI 사용 빈도,1
2,2026-round-2-v2,q2,AI 사용 목적,2
```

Word or PDF sources may be parsed only after the extracted round, version, question keys, column names, and order are shown in a preview and reviewed. The current `POST /api/v1/admin/survey-definitions` endpoint remains the final registration step.

## Export survey responses

`GET /api/v1/researcher/exports/survey-responses?survey_round=2&survey_version=2026-round-2-v2`

This endpoint accepts an `admin` or `researcher` bearer token and returns a UTF-8 BOM CSV file. The response columns follow the registered question order; missing answers remain blank.

## Submit survey response

`POST /api/v1/survey-responses`

This endpoint requires a participant bearer token. The backend validates the survey definition and queues the final response before returning.

```json
{
  "surveyRound": 1,
  "surveyVersion": "2026-round-1-v1",
  "answers": {
    "q1": "응답"
  },
  "submissionId": "browser-generated-id"
}
```

Successful response: `202 Accepted`

```json
{
  "submissionId": "<queue job id>",
  "status": "queued"
}
```

The frontend keeps its `localStorage` draft until the status endpoint reports `completed`.

## Get survey submission status

`GET /api/v1/submission-jobs/{submissionId}`

This endpoint requires the submitting participant bearer token and returns the job's `queued`, `processing`, `completed`, or `failed` status. A participant cannot read another participant's submission status.

## Submit AI chat transcript

`POST /api/v1/chat-submissions`

This endpoint requires a participant bearer token and is available only to participants who consented to AI chat submission at application approval. It queues the submitted original input.

```json
{
  "submissionPoint": "afterRound1",
  "sourceType": "text",
  "rawInput": "사용자: 안녕하세요\nAI: 무엇을 도와드릴까요?",
  "submissionId": "browser-generated-chat-id"
}
```

`submissionPoint` is `afterRound1` or `afterRound4`; `sourceType` is `link` or `text`. The response is `202 Accepted` with the Queue `submissionId` and current status. Use `GET /api/v1/submission-jobs/{submissionId}` to check completion.

The backend stores `rawInput` separately from the normalized transcript. The current dummy parser normalizes pasted-text speaker labels. A valid shared link receives `placeholder` parser status until a service-specific parser is added; it is not treated as extracted transcript text.

## Export AI chat transcripts

`GET /api/v1/researcher/exports/chat-submissions?submission_point=afterRound1`

This endpoint accepts an `admin` or `researcher` bearer token. It returns a UTF-8 BOM CSV containing the original input, normalized transcript, parser status, parser version, and parser warnings. The optional `submission_point` is `afterRound1` or `afterRound4`.

## Researcher login

`POST /api/v1/auth/researcher/login`

```json
{
  "username": "researcher",
  "password": "<researcher password>"
}
```

The response is a `200 OK` bearer token response with `role` set to either `admin` or `researcher` and `needsPasswordChange` set to `false`.

The first account is created with the `admin` role only when both `RESEARCHER_BOOTSTRAP_USERNAME` and `RESEARCHER_BOOTSTRAP_PASSWORD` are configured as CloudType Secrets. This bootstrap account is inserted once and is not overwritten on later backend restarts.

## Researcher application management

Both endpoints accept either an `admin` or `researcher` bearer token.

`GET /api/v1/researcher/applications?school_level=elementary`

The optional `school_level` accepts `elementary`, `middle`, or `high`. The response is a list of application records.

`POST /api/v1/researcher/applications/approve`

```json
{
  "applicationIds": ["65f000000000000000000001"]
}
```

Successful response: `200 OK`

```json
{
  "approvedCount": 1
}
```

For each pending application, approval creates a participant account with the hashed initial password `1234` and requires that participant to change the password at first login. Repeating an approval does not create a duplicate participant account.

If the same phone number has already been created through CSV import or a prior approval, the endpoint returns `409 Conflict` and leaves the application pending for researcher review.

## Import participants from CSV

`POST /api/v1/researcher/participants/imports`

This endpoint accepts an `admin` or `researcher` bearer token and a `multipart/form-data` file field named `file`. It currently accepts UTF-8 CSV files only. The required columns are `이름`, `휴대폰번호`, `학교급`, and `학년`.

Successful response: `200 OK`

```json
{
  "createdCount": 2,
  "skippedCount": 1,
  "errors": [
    {
      "row": 4,
      "message": "휴대폰번호는 숫자 11자리여야 합니다."
    }
  ]
}
```

Each valid row creates a participant account with the hashed initial password `1234` and requires the first password change. Existing phone numbers are skipped. Invalid rows do not stop other valid rows from importing.

## Create researcher account

`POST /api/v1/admin/researchers`

This endpoint requires an `admin` bearer token. It creates an individual account with the `researcher` role.

```json
{
  "username": "research-assistant",
  "password": "individual-password"
}
```

Successful response: `201 Created`

```json
{
  "researcherId": "65f000000000000000000002",
  "username": "research-assistant",
  "role": "researcher"
}
```

## Health check

`GET /health`

```json
{
  "status": "ok",
  "environment": "development"
}
```

## Submit participant application

`POST /api/v1/applications`

The frontend sends the full set of application and consent fields. Phone numbers may include hyphens; the backend removes them before storage.

```json
{
  "gender": "여",
  "grade": "중학교 2학년",
  "phone": "010-1234-5678",
  "guardianPhone": "010-9876-5432",
  "email": "participant@example.com",
  "consents": {
    "documentRead": true,
    "survey": true,
    "chat": false,
    "participant": true,
    "guardian": true
  }
}
```

Successful response: `201 Created`

```json
{
  "applicationId": "65f000000000000000000000",
  "status": "pending"
}
```

Failure responses:

- `409 Conflict`: the participant phone number already has an application.
- `422 Unprocessable Entity`: required fields, consent values, phone number, or email format are invalid.
- `503 Service Unavailable`: MongoDB has not been configured or is unavailable.

The frontend must show the current application-complete panel only after a `201` response. It must retain the input and show its existing failure message for any other response.

## Participant login

`POST /api/v1/auth/participant/login`

```json
{
  "phone": "010-1234-5678",
  "password": "1234",
  "audience": "elementary"
}
```

Successful response: `200 OK`

```json
{
  "accessToken": "<JWT>",
  "tokenType": "bearer",
  "role": "participant",
  "needsPasswordChange": true
}
```

`audience` is required: use `elementary` for the elementary-school mode and `secondary` for the middle/high-school mode. The backend compares it with the participant's registered school level and returns `403 Forbidden` when they do not match. The frontend sends the access token in the `Authorization: Bearer <JWT>` header for authenticated requests. When `needsPasswordChange` is `true`, it must show the first-password-change screen before opening the survey panel.

## Change participant password

`POST /api/v1/auth/participant/password`

```json
{
  "currentPassword": "1234",
  "newPassword": "new-password-2026"
}
```

This endpoint requires the participant bearer token. A new password must be at least 8 characters and cannot be `1234`.

Successful response: `200 OK`

```json
{
  "status": "completed"
}
```

## Planned password-reset email

Password reset will use one dedicated Gmail SMTP account as its initial delivery service. The backend will create a one-time reset token, enqueue the email in `notification_jobs`, and have the worker send it through Gmail SMTP. The Gmail address, app password, and reset base URL are CloudType Secrets; the normal Gmail password is never used by the backend.