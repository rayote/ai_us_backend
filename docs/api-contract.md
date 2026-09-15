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

This endpoint requires an `admin` bearer token. It registers the fixed CSV column order for a single `surveyRound` and `surveyVersion` before responses are collected. It accepts both the simple flat question format and the richer scale-based survey spec used for real questionnaire drafts.

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

For a scale-based questionnaire, send `audience` and `scales`. The backend preserves the original JSON under `spec` in MongoDB and creates the flat `questions` list from `scales[].questions[]` for submission validation and CSV export. In the current collaboration workflow, `spec` is the questionnaire source structure prepared in the frontend `SURVEY_SETS` by the research team or Claude. It is intentionally preserved so question text, options, scale metadata, review notes, scoring hints, and future report inputs remain traceable after registration.

```json
{
  "surveyRound": 1,
  "audience": "elementary",
  "surveyVersion": "t1-elem-v1-draft",
  "scales": [
    {
      "scaleId": "motive",
      "order": 4,
      "scaleName": "AI 활용동기",
      "questions": [
        {
          "key": "motive.q1",
          "no": "1",
          "text": "나는 가족, 친구, 다른 문제에서 벗어나려고 AI를 사용한다.",
          "type": "likert",
          "required": true
        }
      ]
    }
  ]
}
```

When `questions` is omitted, each scale question must include stable answer keys. For normal questions, the registered key is the question `key`. For `grid` questions, the backend registers each `rows[].key` because the frontend submits row-level answers. Conditional `detail.field.key` and `other.field.key` values are also registered as additional CSV/export columns. The backend generates `csvColumn` values from `scaleName | no | text` plus row or field labels and assigns sequential `order` values. Additional fields such as `type`, `options`, `logic`, `sensitive`, and `scoring` remain in the preserved `spec`; current final submission validation still checks only registered answer keys.

The same round and version cannot be registered more than once. Register a new `surveyVersion` when the questionnaire changes.

The backend reads both the current `csvColumn` field and the legacy MongoDB `csv_column` field for definitions registered during early development. New definitions are stored with `csvColumn`.

## List survey definitions

`GET /api/v1/researcher/survey-definitions`

This endpoint accepts an `admin` or `researcher` bearer token and returns registered survey definition summaries for researcher UI filters.

```json
[
  {
    "surveyRound": 1,
    "surveyVersion": "t1-elem-part1-v1-draft",
    "audience": "elementary",
    "part": 1,
    "title": "청소년 생성형 AI 사용 경험 연구 · 1회차 파트1 (초등)",
    "questionCount": 217,
    "createdAt": "2026-09-14T00:00:00Z"
  }
]
```

`title` and `part` are populated from the preserved survey definition `spec` when available. The researcher page uses this list to populate survey version selections instead of hardcoding version IDs. When a new round is added, the frontend `SURVEY_SETS` should include the correct `surveyRound`, `surveyVersion`, `audience`, `part`, and `_meta.title`; the backend registration helper preserves those values instead of requiring direct MongoDB edits.

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

This endpoint accepts an `admin` or `researcher` bearer token and returns a UTF-8 BOM CSV file. It includes `아이디(휴대폰)`, `학교급`, and `학년` before the round, version, submission time, and registered question columns. Phone values are exported as Excel text formulas so their leading `0` is preserved when the CSV is opened in Excel. The optional `school_level` query accepts `초등`, `중등`, or `고등`; missing answers remain blank.

## Preview survey responses

`GET /api/v1/researcher/survey-response-previews?survey_round=2&survey_version=2026-round-2-v2`

This endpoint accepts an `admin` or `researcher` bearer token and returns at most 10 matching MongoDB response records. Each preview includes the participant phone identifier, school level, grade, survey round, survey version, and submission time. The optional `school_level` query accepts `초등`, `중등`, or `고등`. Internal MongoDB participant object IDs are not returned to the researcher UI. It returns an empty array when no response matches the selected conditions.

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

The frontend keeps its `localStorage` draft until the status endpoint reports `completed`. It then deletes the draft and locks that survey round/version against another automatic submission. A future explicit resubmission feature requires a separate update policy; the current response storage treats a participant, round, and version as one final response.

When part 1 completes, the frontend may immediately open part 2 when that definition is available. When part 2 completes, it may switch to the chat submission tab for participants whose `chatConsent` is true.

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

## Upload AI chat files

`POST /api/v1/chat-submissions/uploads` accepts `multipart/form-data` and requires a participant bearer token with AI chat submission consent. It stores original file bytes in the MongoDB GridFS bucket `chat_uploads`; `chat_submissions` keeps only the submission metadata and GridFS file IDs.

Required form fields are `files`, `tool`, `submissionPoint`, `sourceType`, and `submissionId`. Use `sourceType=file` for one ZIP file and `sourceType=image` for one or more screenshots. ZIP files must have a `.zip` extension and ZIP signature. Allowed screenshots are JPG, PNG, WEBP, and HEIC.

- ZIP: exactly one file
- Images: at most 20 files
- Each file: at most 25 MB
- Total request files: at most 100 MB

The endpoint returns `202 Accepted` with the supplied `submissionId` and `completed` status when the files and metadata have been stored. Attached original files are retained for researcher review; transcript extraction and OCR are not performed yet.

## Manage AI chat submissions

`GET /api/v1/chat-submissions/mine` returns the authenticated participant's submissions in descending submission-time order. The response contains only `submissionId`, `submissionPoint`, `sourceType`, `tool`, filenames, `submittedAt`, and `status`; it does not expose GridFS file IDs or transcript content.

The participant can request deletion with `POST /api/v1/chat-submissions/{submissionId}/deletion-request`. This changes only the metadata status to `deletion_requested`; GridFS files remain available for researcher review. `POST /api/v1/chat-submissions/{submissionId}/restore` cancels that request before physical deletion.

Both endpoints require the participant who owns the submission. The participant UI displays `deletion_requested` as `삭제 요청됨`; after research staff physically delete it, the item no longer appears in the participant history.

`GET /api/v1/researcher/chat-submissions/files?status_filter=deletion_requested` lets an `admin` or `researcher` list file submissions for management. `DELETE /api/v1/researcher/chat-submissions/files/{submissionId}` physically deletes only a `deletion_requested` submission and all of its linked GridFS files. This operation cannot be restored.

## Export AI chat transcripts

`GET /api/v1/researcher/exports/chat-submissions?submission_point=afterRound1`

This endpoint accepts an `admin` or `researcher` bearer token. It returns a UTF-8 BOM CSV containing active submissions only: participant ID, name, school level, grade, original input, normalized transcript, parser status, parser version, and parser warnings. The optional `submission_point` is `afterRound1` or `afterRound4`; the optional `school_level` is `초등`, `중등`, or `고등`.

`GET /api/v1/researcher/chat-submission-previews` returns active chat submissions for the researcher preview table, filtered by optional `submission_point` and `school_level` parameters. It includes participant phone, school level, grade, tool, source type, filenames, attachment count, and submitted time.

`GET /api/v1/researcher/chat-submissions/files/{submissionId}/download` downloads one submission's original attachments as a ZIP. `POST /api/v1/researcher/chat-submissions/download-jobs` creates an asynchronous ZIP job for selected `submissionIds` or the submitted filters. Poll `GET /api/v1/researcher/chat-submissions/download-jobs/{jobId}`; when `completed`, download the archive from its `downloadUrl`. Bulk jobs use the `chat_download` queue type and preserve each submission in its own folder.

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

## Participant contact and password reset

Participant applications accept optional `email` and `sns` strings. `guardianPhone` remains required. Password reset accepts `{ "phone": "...", "guardianPhone": "..." }` and resets the password only when both participant and guardian phone numbers match the participant account. The old email reset fields are retained only as a compatibility path for existing clients.

Participant CSV import requires `이름`, `휴대폰번호`, `보호자휴대폰`, `학교급`, and `학년`; `이메일` and `SNS` are optional columns.

## Researcher application management

Both endpoints accept either an `admin` or `researcher` bearer token.

### Automatic approval setting

`GET /api/v1/researcher/application-settings` returns the current automatic approval state for both `admin` and `researcher` roles.

```json
{
  "autoApproval": false
}
```

`PUT /api/v1/admin/application-settings` requires an `admin` bearer token and changes the state.

```json
{
  "autoApproval": true
}
```

The setting is stored in MongoDB and defaults to `false` when it has not yet been set. When enabled, a new application that satisfies all required consent fields is immediately approved, receives a participant account with initial password `1234`, and the application response contains a participant bearer token with `needsPasswordChange: true`. The frontend must immediately show the first-password-change screen before permitting survey access. Existing pending applications remain pending.

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

## Researcher participation reports

Both endpoints require an `admin` or `researcher` bearer token.

`GET /api/v1/researcher/participation-status`

Returns participant totals by elementary, middle, high, and total, plus unique completed participant counts by survey round.

`GET /api/v1/researcher/nonparticipants?survey_round=1&survey_version=demo-v1`

Returns the participants without a completed response for the specified survey round and version, with counts by school level. The frontend development dashboard currently uses the `demo-v1` version; replace it when the real questionnaire definition is registered.

## Import participants from CSV

`POST /api/v1/researcher/participants/imports`

This endpoint accepts an `admin` or `researcher` bearer token and a `multipart/form-data` file field named `file`. It currently accepts UTF-8 CSV files only. The required columns are `이름`, `휴대폰번호`, `학교급`, `학년`, and `이메일`.

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

Each valid row creates a participant account with the hashed initial password `1234`, the registered email used for simplified password reset, and requires the first password change. Existing phone numbers are skipped. Invalid rows do not stop other valid rows from importing.

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

The frontend may call this endpoint once on page load to detect temporary backend deployment or connection problems. If `/health` fails at the network level or returns a 5xx response, the frontend shows a non-blocking full-modal message such as `일시적인 연결 문제가 있어요`. User-input errors such as 409 or 422 are not treated as health failures.

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

- `409 Conflict`: the participant phone number already has an application, or it already belongs to a registered participant account. The response `detail` distinguishes these cases.
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
  "needsPasswordChange": true,
  "audience": "elementary",
  "requestedAudience": "secondary",
  "audienceSwitched": true,
  "chatConsent": true
}
```

`audience` is required: use `elementary` for the elementary-school mode and `secondary` for the middle/high-school mode. If the selected audience does not match the participant's registered school level, the backend still logs in after password verification and returns the registered audience in `audience`, the submitted value in `requestedAudience`, and `audienceSwitched: true`. `chatConsent` reflects whether the approved participant consented to AI chat transcript submission. The frontend sends the access token in the `Authorization: Bearer <JWT>` header for authenticated requests. When `needsPasswordChange` is `true`, it must show the first-password-change screen before opening the survey panel.

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

## Reset participant password

`POST /api/v1/auth/participant/password-reset`

```json
{
  "phone": "010-1234-5678",
  "email": "participant@example.com"
}
```

This public endpoint resets a participant password to the initial password `1234` only when the registered phone number and email address match. The next participant login returns `needsPasswordChange: true`, so the frontend must show the first-password-change screen before opening the survey panel. No Gmail, SMTP, or email link is used in this simplified flow.

Successful response: `200 OK`

```json
{
  "status": "completed"
}
```

Failure response:

- `404 Not Found`: the phone number and email do not match a participant account.