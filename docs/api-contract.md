# API Contract

Base URL: the public backend URL deployed by CloudType for the current environment.

All request and response bodies use JSON unless an endpoint explicitly returns a file.

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

## Export survey responses

`GET /api/v1/researcher/exports/survey-responses?survey_round=2&survey_version=2026-round-2-v2`

This endpoint accepts an `admin` or `researcher` bearer token and returns a UTF-8 BOM CSV file. The response columns follow the registered question order; missing answers remain blank.

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
  "password": "1234"
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

The frontend sends the access token in the `Authorization: Bearer <JWT>` header for authenticated requests. When `needsPasswordChange` is `true`, it must show the first-password-change screen before opening the survey panel.

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