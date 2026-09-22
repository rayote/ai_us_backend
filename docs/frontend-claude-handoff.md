# 프론트엔드 AI 작업 인수인계

이 문서는 Claude 등 다른 AI가 `ai_us` 프론트엔드를 수정할 때 지켜야 할 현재 계약을 정리한다. 화면 구조와 로컬 확인 방법은 frontend `README.md`, 정확한 요청·응답은 backend `docs/api-contract.md`, 배포 설정은 `docs/cloudtype-deployment-checklist.md`를 우선한다.

## 사용 방법

1. 새 프론트 작업 시 이 문서의 공통 원칙과 관련 현재 계약, 필요한 향후 작업 후보를 함께 제공한다.
2. 작업 전 frontend와 backend의 현재 branch·dirty state를 확인하고 사용자 변경을 보존한다.
3. API 변경이 필요하면 schema, route, service, worker, 테스트, `api-contract.md`를 함께 갱신한다.
4. 완료 후 변경 파일, 실행한 검증, 배포 필요 여부를 전달한다. 요청 없이 branch나 commit을 만들지 않는다.

## 공통 작업 원칙

- 대상 저장소: `ai_us`. 주요 파일은 `index.html`, `researcher.html`이다.
- 순수 HTML/CSS/JavaScript와 inline `<script>` 구조를 유지한다. 외부 라이브러리와 프론트 빌드 도구를 추가하지 않는다.
- 기존 디자인, 이미지, 문구, 화면 배치, CSS는 요구사항에 필요한 범위에서만 바꾼다.
- MongoDB URI, API 비밀값, JWT secret, 연구자 계정 비밀번호를 프론트에 넣지 않는다.
- API 기본 URL은 CloudType에 배포된 해당 환경의 backend 공개 URL만 사용한다. 개발과 운영의 URL, 비밀값, 데이터는 분리한다.
- `AI_US_API_BASE`는 `window.location.hostname`의 정확한 매핑으로 선택한다. 개발 frontend `web-ai-us-mtmm0sg55da4c824.sel3.cloudtype.app`은 개발 backend `https://port-0-ai-us-backend-mtmm0sg55da4c824.sel3.cloudtype.app`, 운영 frontend `web-ai-us-mu2jfq4sfccf2f38.sel3.cloudtype.app`은 운영 backend `https://port-0-ai-us-backend-mu2jfq4sfccf2f38.sel3.cloudtype.app`을 사용한다. 알 수 없는 hostname의 fallback은 운영 backend로 둔다.
- API 실패 시 성공 화면이나 완료 상태로 이동하지 않는다. `detail` 오류 메시지를 우선 표시한다.
- 서버 네트워크 오류나 5xx 응답은 기존의 `일시적인 연결 문제가 있어요` full-modal로 안내한다. 409/422는 해당 화면의 입력 오류 영역 또는 업무 맥락에 맞는 modal, 401/403은 로그인 만료/권한 흐름으로 처리한다.
- 오래 걸리는 저장·다운로드는 진행 메시지와 spinner가 있는 full-modal을 사용한다. 진행 중에는 확인 버튼을 두지 않고, 작업과 후속 목록 재조회가 끝난 뒤 자동으로 닫는다.
- 구현 뒤 `index.html`과 `researcher.html`의 inline JavaScript가 파싱되는지 확인하고, 기존 흐름을 훼손하지 않는다.

## 현재 구현 계약

### 참여 신청과 로그인

- `POST /api/v1/applications`에 성별, 학교급·학년, 본인/보호자 휴대폰 번호, 이메일, 동의를 보낸다.
- backend는 `documentRead`, `survey`, `participant`, `guardian` 동의가 모두 `true`인 신청만 받는다. `chat`은 선택 동의다.
- 수동 승인 시 응답은 `status: "pending"`이며, 프론트는 기존 승인 대기 완료 화면을 보인다.
- 자동 승인 시 응답은 `status: "approved"`, `accessToken`, `role: "participant"`, `needsPasswordChange: true`, `audience`, `chatConsent`를 포함한다. 프론트는 토큰과 참여자 정보를 일반 로그인과 동일하게 `sessionStorage`에 저장하고, 승인 대기 화면 대신 최초 비밀번호 변경 모달을 즉시 연다.
- 참여자 로그인은 `POST /api/v1/auth/participant/login`에 `audience`를 함께 보낸다. 초등 UI 값은 `elementary`, 중고등 UI 값은 `secondary`로 변환한다.
- 참여자 access token은 파트 1·2와 대화문 제출까지 같은 세션에서 완료할 수 있도록 기본 240분 유효하다. 연구자 token의 기본 60분 설정과 분리한다.
- 로그인 성공 시 새 token으로 기존 `sessionStorage` 값을 반드시 교체한다. 참여자 화면은 5분마다 남은 시간을 확인하고 30분 미만이면 `POST /api/v1/auth/participant/refresh`로 갱신하며, 정상 인증 API 통신 후에도 같은 확인을 수행한다.
- 로그인 응답의 실제 `audience`가 현재 UI와 다르면 기존 커스텀 전환 모달을 보인 뒤 해당 대상 UI로 전환한다.
- `needsPasswordChange: true`이면 설문 화면을 열기 전에 `POST /api/v1/auth/participant/password`로 최초 비밀번호를 변경한다.
- 비밀번호 재설정은 `POST /api/v1/auth/participant/password-reset`에 본인 휴대폰과 보호자 휴대폰을 보내며, 성공하면 비밀번호는 `1234`로 초기화된다.
- 신청 정보는 본인 휴대폰과 보호자 휴대폰을 필수로 받고, 이메일과 SNS는 선택사항으로 보낸다. SNS 예시는 `인스타그램 @insta_user, 페이스북 faceuser`처럼 안내한다.
- 비밀번호 재설정은 `phone`과 `guardianPhone` 조합을 사용한다. 운영 frontend는 이메일을 비밀번호 복구 조건으로 사용하지 않는다.

### 연구자 회원 신청 관리

- `GET /api/v1/researcher/applications`와 `POST /api/v1/researcher/applications/approve`는 `admin`, `researcher` 모두 사용한다.
- `GET /api/v1/researcher/application-settings`는 두 역할 모두 `autoApproval` 상태를 읽는다.
- `PUT /api/v1/admin/application-settings`는 `admin`만 `{ "autoApproval": true | false }`로 변경할 수 있다. 연구자 화면은 일반 `researcher`에게 현재 상태를 읽기 전용으로 표시한다.
- 자동 승인을 켜도 기존 `pending` 신청은 그대로 유지한다. 새 신청에만 적용된다.
- CSV 등록은 `POST /api/v1/researcher/participants/imports`의 `file` field로 UTF-8 CSV를 전송한다. 필수 열은 `이름,휴대폰번호,보호자휴대폰,학교급,학년`이며 `이메일,SNS`는 선택 열이다. 연구자 신청/참여자 표와 CSV에는 이메일과 SNS를 함께 표시한다.

### 설문 정의와 제출

- 실제 설문 spec은 `index.html`의 `SURVEY_SETS`가 원본이다. `surveyRound`, `surveyVersion`, `audience`, `part`, `_meta.title`, 문항 key와 선택지 value는 연구진 확정값만 사용한다.
- 현재 수집용 정의는 `t1-elem-part1-v2-0916`, `t1-elem-part2-v2-0916`, `t1-secondary-part1-v2-0916`, `t1-secondary-part2-v2-0916`이다. 기존 `v1-draft` 정의와 응답은 삭제하거나 덮어쓰지 않고 버전별로 병행 보존한다.
- v2 파트 2 마지막에는 `followup.researchConsent`(예=1, 아니오=2)를 추가한다. 기존 파트 2 응답에는 이 key가 없을 수 있으므로, 연구진 승인 후 backend의 `scripts/backfill_followup_consent.py`로 `2`를 보수적으로 backfill한다.
- 조건부로 숨겨지는 문항은 answers에서 생략될 수 있다. Backend는 등록되지 않은 top-level key만 거부하며 required/type/조건부 존재 여부는 frontend 설문 엔진이 검증한다.
- Composite 답변은 부모 key 아래 객체로 저장한다. 평면 설문 정의에는 composite field와 `otherText` key도 등록하고, CSV export는 객체 내부 값을 해당 하위 열로 펼친다.
- 설문 작성 모달은 장시간 입력 화면이므로 바깥 backdrop click이나 Escape로 닫지 않는다. 명시적인 닫기 버튼만 사용하며, 임시 저장 기능과 별개로 진행 중 입력을 우발적으로 잃지 않게 한다.
- 모바일 Likert 선택지는 가로 스크롤이나 화면 밖 넘침 없이 선택지 수에 맞춘 동일 폭 grid로 렌더링한다. 긴 라벨은 각 선택지 안에서 줄바꿈한다.
- v2의 `usage.q13`처럼 정해진 AI 도구 목록을 묻는 순위 문항은 styled dropdown을 사용한다. `기타 (직접 입력)`을 선택하면 해당 순위에서만 텍스트 입력칸을 표시하고, 그 입력값을 답변으로 저장·복원한다. 2·3순위의 `없음` 선택도 문자열 값으로 보존한다.
- frontend 변경 후 backend 담당자가 `scripts/register_frontend_surveys.py` 또는 개발 DB 동기화 스크립트로 `survey_definitions`를 갱신한다. Claude는 MongoDB에 직접 접속하거나 definition을 직접 넣지 않는다.
- 최종 설문 제출은 `POST /api/v1/survey-responses`에 `surveyRound`, `surveyVersion`, `answers`, `submissionId`를 전송한다. `GET /api/v1/submission-jobs/{submissionId}`가 `completed`일 때만 임시 저장을 삭제한다.
- 서버에서 완료된 같은 회차·버전의 설문은 참여자가 수정하거나 재제출할 수 없다. frontend도 완료 상태에서 입력과 제출을 다시 열지 않는다.
- 기기 간 파트 완료 상태는 `GET /api/v1/participant/survey-progress?survey_round=1`에서 복원한다. 브라우저 `localStorage` 완료 marker는 임시 최적화로만 사용하고, 서버에 저장된 완료 응답이 파트 2 잠금 해제의 기준이다.
- 설문 답변 key는 spec의 일반 문항 `key`, grid `rows[].key`, 조건부 `detail.field.key`/`other.field.key`를 그대로 사용한다.
- 연구자 설문 버전 목록은 `GET /api/v1/researcher/survey-definitions`로 채운다. 결과 조회/CSV는 선택된 `surveyRound`와 `surveyVersion`을 그대로 전송한다.
- 연구자 페이지의 미참여자·결과 다운로드 설문 selector는 기본적으로 현재 운영 `v2-0916`만 표시한다. 과거 `v1-draft` 정의와 응답은 DB/API에 보존하며, `과거 v1 포함` 옵션을 켠 경우에만 selector에 표시한다.
- 참여 현황 bar chart는 설문 버전별로 나누지 않고 회차별 고유 참여자 수를 집계하는 기존 workflow를 유지한다. 따라서 v1을 별도 bar로 노출하지 않는다.
- 파트 1 제출 완료 후 파트 2 definition이 있으면 파트 2를 자동으로 열고, 파트 2 제출 완료 후 `chatConsent`가 true이면 대화문 제출 탭으로 전환한다.

### AI 대화문 제출

- 대화문 제출은 AI 대화문 제출 동의(`chatConsent`)가 있는 참여자에게만 열어 준다. 로그아웃 시 `chatConsent`도 삭제한다.
- 링크/붙여넣기 본문은 `POST /api/v1/chat-submissions`에 `submissionPoint`, `sourceType`(`link` 또는 `text`), `rawInput`, `submissionId`를 보낸다.
- 공유 링크는 원문을 보존하지만 crawler나 AI로 자동 수집하지 않는다. 서비스별 접근 차단과 높은 오류 가능성 때문에 링크를 추출 완료 대화문처럼 표시하지 않는다.
- ZIP 또는 이미지 첨부는 `POST /api/v1/chat-submissions/uploads`에 multipart `FormData`로 보낸다. fields는 `files`, `tool`, `submissionPoint`, `sourceType`(`file` 또는 `image`), `submissionId`다.
- ZIP은 한 개만, 이미지는 JPG/PNG/WEBP/HEIC 최대 20개까지 허용한다. 업로드는 파일당 25MB, 요청 전체 100MB 제한이다. Gemini inline 파싱은 별도로 이미지 한 장당 20MB 미만만 지원하므로 20MB 이상인 이미지는 업로드 후 파싱 단계에서 크기 오류가 난다.
- 첨부 원본은 backend가 MongoDB GridFS `chat_uploads` bucket에 저장한다. 업로드 완료와 파싱 완료는 별개다. 연구자의 파싱 요청 이후 ZIP은 서비스별 adapter, 이미지는 Gemini screenshot parser로 비동기 처리하며 frontend는 Queue 상태를 polling한다.
- 대화문 탭의 `제출 내역`은 `GET /api/v1/chat-submissions/mine`으로 최신순 목록을 표시한다. 목록에는 도구, 제출 방식, 파일명, 상태만 표시하며 GridFS file ID나 원문은 표시하지 않는다.
- 참가자가 `삭제 요청`을 확인하면 `POST /api/v1/chat-submissions/{submissionId}/deletion-request`를 호출하고 상태를 `삭제 요청됨`으로 회색 표시한다. `삭제 요청 취소`는 `POST /api/v1/chat-submissions/{submissionId}/restore`를 호출한다.
- 연구자 파일 관리 화면은 `GET /api/v1/researcher/chat-submissions/files?status_filter=deletion_requested`에서 요청 항목을 조회한다. `파일 삭제`는 되돌릴 수 없다는 확인 뒤 `DELETE /api/v1/researcher/chat-submissions/files/{submissionId}`를 호출한다. 실제 삭제가 끝난 항목은 참가자 내역에 표시하지 않는다.
- 연구자 대화문 CSV는 `GET /api/v1/researcher/exports/chat-submissions`에 `submission_point`, 필요 시 `school_level`을 전송한다. 검토 상태·메모·수정 시각·연구자도 CSV에 포함된다.
- 결과 다운로드 미리보기는 `GET /api/v1/researcher/chat-submission-previews`로 실제 DB와 동기화한다. 기본 `review_filter=unreviewed`이며 `all`, `reviewed` 또는 개별 검토 결과로 필터링할 수 있다.
- 관리 상태는 `incentive_paid`, `excluded`, `duplicate`, `other` 중 하나이며 기존 `chat_submissions.review`에 저장한다. 모든 상태에서 메모는 선택이고 `other`에서만 필수다. 상태 변경과 미검토 복귀는 모두 `저장` 버튼을 눌러야 확정한다. 미검토 복귀는 같은 URL의 `DELETE`를 사용한다.
- `other` 메모가 비어 있으면 native `alert()` 대신 확인 버튼이 있는 `reviewSaveDialog`를 표시하고 닫은 뒤 메모 입력으로 포커스를 돌려준다. 저장 시에는 같은 dialog를 버튼 없는 spinner 상태로 열고 PATCH/DELETE와 `loadPreview()`가 모두 끝난 뒤 자동으로 닫는다.
- 행 체크박스는 다운로드 선택 전용이다. 명시적으로 고른 `submissionIds`는 `reviewFilter`를 우회하지만 현재 `submissionPoint`와 `schoolLevel` 조건은 유지한다. `현재 조건 전체 선택`은 빈 ID 목록과 세 필터를 모두 보낸다.
- 개별 첨부는 `GET /api/v1/researcher/chat-submissions/files/{submissionId}/download`로 받고, 선택 원본 ZIP은 `POST /api/v1/researcher/chat-submissions/download-jobs`, 선택 대화문 CSV ZIP은 `POST /api/v1/researcher/chat-submissions/transcript-download-jobs`로 생성한다. 두 작업 모두 상태 polling 후 `downloadUrl`을 사용하며 삭제 요청 상태는 기본 다운로드에서 제외한다.
- 개별 파싱은 `POST /api/v1/researcher/chat-submissions/{submissionId}/parse` 후 run 상태를 polling한다. 완료 결과는 `latest-parse/download?format=json|csv`로 받는다. 파싱 불가 항목은 성공으로 꾸미지 않고 warning/error를 표시한다.

## 운영 절차

### 설문 정의 동기화

1. `ai_us`에서 `git fetch` 후 로컬 `HEAD`와 `origin/main`을 비교하고 `git pull --ff-only`로 최신 `SURVEY_SETS`를 받는다.
2. `python scripts/sync_frontend_survey_definitions.py --dry-run`으로 네 정의가 파싱되는지와 flattened field 수를 확인한다.
3. 문항 key 또는 값 의미가 바뀌면 기존 버전을 replace하지 않고 새 `surveyVersion`을 개발 DB에 등록한다. 동일 버전의 문구·보기만 고치는 경우에만 검토 후 replace한다.
4. 개발 DB를 timestamp backup collection에 복제하고, 기존 응답 key 호환성과 frontend/DB exact match를 확인한다.
5. 참여자 제출, Queue `completed`, 연구자 미리보기, CSV 다운로드를 개발 환경에서 확인한다. Composite `otherText`와 배열 값도 실제 CSV에서 확인한다.
6. Backend 변경이 있으면 먼저 배포한 뒤 운영 admin API로 새 정의를 등록한다. 운영 정의 목록의 field 수와 CSV header를 재확인한다.

### 환경 분리

- 개발과 운영은 별도의 CloudType backend, MongoDB, Secret, JWT secret을 사용한다.
- 개발 DB 접근 정보는 Git ignore된 backend `.env`에만 둔다. 운영 MongoDB 접속정보는 CloudType Secret으로 관리한다.
- 운영 설문 definition 동기화는 배포 backend의 admin API를 우선 사용한다.
- 현재 v2 flattened field 수는 초등 파트 1/2 `230/209`, 중고등 파트 1/2 `230/234`이다.

## 향후 작업 후보

### FH-016: 참가자 개인 리포트

상태: 보류. 분석 기준, 척도별 채점 규칙, 차트, 해석 문구가 확정된 뒤 구현한다.

- 설문 제출 요청에서 즉시 생성하지 않고 별도 Queue job으로 `participant_reports` snapshot을 생성한다.
- 참가자는 본인 JWT로만 조회하며, `pending`/`processing`은 대기 안내, `ready`만 리포트를 표시한다.
- 리포트 기준 변경을 위해 `reportVersion`과 원본 설문 버전을 보존한다.

### FH-018: 회차별 설문 운영 보완

상태: 운영 절차 적용 중.

- 설문 문항을 같은 `surveyRound`/`surveyVersion`으로 교체해야 할 때는 backend 담당자가 검증된 replace 절차를 사용한다.
- 이미 운영 응답이 존재할 때 문항 key나 CSV 열이 바뀌면 새 `surveyVersion`을 사용해야 하는지 연구진과 먼저 결정한다.

### AI 대화문 후처리 확장

상태: 보류.

- 아직 지원하지 않는 AI 서비스의 ZIP export adapter와 공유 링크 수집 정책은 샘플·서비스 약관·연구 기준이 확보된 뒤 추가한다.
- 대용량 원본과 생성 ZIP이 장기간 누적되면 GridFS 보존 기간과 백업 비용을 검토하고 S3 호환 object storage 이전을 검토한다.
- 기존 GridFS 파일 제출의 `tool`/`status` 메타데이터가 없는 경우 backend 담당자는 `scripts/backfill_chat_submission_metadata.py`로 GridFS metadata에서 도구 값을 보정한다.

## 현재 기준선

- 신청·인증·승인·CSV 등록, 설문 Queue 제출, 서버 기반 파트 완료 복원은 운영 흐름으로 확정됐다.
- 연구자 현황·미완료자·어뷰징 검토·설문 결과·대화문 원본/파싱 결과 다운로드는 실제 API를 사용한다.
- ZIP/이미지 첨부, GridFS 저장, Gemini screenshot parser, 관리 상태와 검토 필터는 구현 완료 상태다.
- 향후 기능은 위 `향후 작업 후보`에만 기록하고, 완료된 기능의 세부 이력은 Git history를 사용한다.
