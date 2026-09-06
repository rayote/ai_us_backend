# API Contract

Base URL: the public backend URL deployed by CloudType for the current environment.

All request and response bodies use JSON unless an endpoint explicitly returns a file.

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