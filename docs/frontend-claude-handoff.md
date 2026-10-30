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

### 문서 점검 기준 (2026-10-30)

- 동의 관리 구현은 frontend `21ce3a9`, backend `dc41ddb`로 각 `main`에 푸시됐다. 이후 로컬 백엔드 수정과 이번 문서 보완은 별도 미커밋 상태이며, 최신 코드의 CloudType 배포 여부를 이 기록만으로 판단하지 않는다. Git push와 수동 배포는 별개다.
- 날짜가 붙은 아래 T2 초기 검증·계획은 당시 이력이다. 현재 API는 `api-contract.md`, 현재 화면은 frontend `README.md`를 우선하며, 이미 구현된 진행 저장·가입 차단·설문 접수 기능을 다시 미구현으로 취급하지 않는다.

### 참여자 안내·입력·세션 보존

- 첫 화면과 신청·로그인의 참여 안내는 실제 단계에 맞춰 표시하며 연구자·설문 작성 화면에서는 숨긴다. `?preview=guide`는 API·분석 이벤트·실제 세션 갱신을 차단하는 검토 모드이며, 일반 로컬 hostname의 운영 API fallback과 구분한다.
- AI 순위는 같은 도구의 중복 선택을 막되 `없음` 반복과 서로 다른 기타 도구명은 허용한다. 최대 선택 피드백은 거부된 선택지 옆에 표시한다. 조건부 답 복원·숨긴 답 제거·필수 이유·정수 금액·T2 기프티콘 필수 선택을 보존한다.
- 참여자 token은 기본 240분이며 만료 30분 전부터 기존 주기로 갱신한다. 요청 전·탭 복귀·복원에서도 확인하되 네트워크/5xx를 실제 만료로 취급하지 않는다. 현재 token의 401은 열린 답을 계정별 로컬 초안에 보존한 뒤 만료 모달로 안내한다.
- 같은 계정으로 다시 로그인해도 원래 버전·접수 상태·선행 파트 조건을 확인한 후 복원한다. 이전 계정/token의 늦은 응답이 새 세션을 덮어쓰지 못하게 한다. 별도 4시간 무입력 로그아웃이나 10분 전 경고는 추가하지 않았다.

### 참여 현황 정렬 인덱스 (2026-10-26)

- 연구자 참여 현황은 대화문 제출 상태도 조회한다. `chat_submissions` 전체 목록의 `submitted_at` 정렬을 지원하는 비고유 인덱스 `chat_submissions_submitted_at`이 필요하다. 기존 `participant_id/submission_point/submitted_at` 복합 인덱스만으로는 전체 목록 정렬을 지원하지 못한다. 대화문 데이터 누적으로 MongoDB 32MB 정렬 한도를 초과하면 참여 현황·대화문 목록·다운로드·파싱이 함께 실패할 수 있다.
- 서버 시작 시 정렬 인덱스를 백그라운드 생성하도록 반영했고, 운영에도 해당 인덱스만 추가했다. 적용 전 대화문 72건의 BSON/메타데이터 백업을 검증했고, 적용 후 기존 인덱스·문서 해시가 보존됨을 확인했다. 기존 실패 조회의 `explain`은 `SORT` 없이 `IXSCAN`을 사용해 성공했다. 백업과 적용 기록은 `/data/ai_us_joint_research/backups/ai_us/2026-10-26_pre_chat_sort_index_prod_20261026T181321+0900/`에 보관한다. 복원 시험은 하지 않았다.
- 관련 DB·참여 현황·연구자·대화문 회귀 테스트 37개가 통과했다. 단순 재시작이나 일부 필드 제외만으로 해결됐다고 판단하지 말고 실제 조회의 실행 계획을 확인한다. 과거 실패한 파싱 작업은 원본 형식 문제도 섞여 있으므로 일괄 재시도하지 않는다.

### T2 참여 사례 선택 추가 (2026-10-26)

- 공통 파트 2의 기존 `followup` 바로 앞, 같은 3페이지에 필수 단일 선택 `reward.gifticon`을 추가했다. 값은 1=GS25, 2=신세계상품권, 3=다이소, 4=네이버페이, 5=올리브영이다. 기존 다음 조사 참여·조건부 불참 이유의 문구, 선택지, 키는 보존했다.
- 기존 제출자는 연구진의 종전 지급 방식으로 처리하며 재제출을 요구하지 않는다. 추가 후 수집된 선택값은 해당 기프티콘을 우선 지급하는 데 사용한다. 기존 응답·오래 열린 설문에서 선택값이 없더라도 임의의 기본값을 넣지 않는다. 지급 자동화는 추가하지 않았다.
- 버전은 `t2-part1-v9-1022`/`t2-part2-v9-1022`를 유지한다. 파트 1은 256개 응답 필드/270개 CSV 열로 불변이며 파트 2는 96→97개 필드, 110→111개 CSV 열이다. 화면 순서와 달리 `csvAppend: true` 문항은 평면 정의 맨 끝에 배치하므로 기존 CSV 열·순서·값은 그대로이고 새 열만 끝에 붙는다. 과거 응답의 새 열은 빈칸이다.
- 개발 DB `ai_us_development`에 적용했고 실제 변경은 파트 2 정의 한 건뿐이다. 나머지 10개 정의와 참여자·최종 응답·임시저장·접수 설정의 전체 내용 해시 불변을 확인했다. 검증한 정의 전용 BSON/메타데이터 백업과 manifest, 적용 기록은 `/data/ai_us_joint_research/backups/ai_us/2026-10-26_pre_T2_reward_dev_20261026T100614+0900/`에 있다. 복원 시험은 하지 않았다.
- backend 44개·참여자 frontend 24개 회귀 테스트와 로컬 미리보기 1440/390px 검증을 통과했다. `python scripts/add_t2_reward_question.py --dry-run`은 기존 필드 보존과 재실행 멱등성을 확인하며 DB를 쓰지 않는다. 이번 작업에서 운영 DB 접근·CloudType 배포는 하지 않았다. Git push와 CloudType 수동 배포는 별개다. 같은 버전의 열린 화면/설정 캐시는 이전 문항을 유지할 수 있으므로 개발 검수는 새로고침한 세션에서 진행한다. 기존 완료자 재제출이나 초안 초기화는 금지한다.

### T2 로컬 구현 상태 (2026-10-23)

- 기존 두 저장소에 T2 공통 문항·8/3페이지 엔진, 전화번호 두 개만 고정하는 파트별 독립 입력, 서버 완료 기반 잠금·집계, 별도 revision 진행 저장, preview 격리, 가입 차단·승인 재검증을 구현했다. T1 정의와 과거 응답을 수정하지 않았다.
- 연구자 T2/T3 대화문 필터·집계·다운로드를 확장했고, 최종 응답에서 분리된 부분 응답 JSON/CSV와 `부분 응답 CSV` 버튼을 추가했다. 최종 저장 후 진행 완료 표시가 실패해도 재시도로 복구하며 다른 최종 답을 덮어쓰지 않는다.
- 관련 backend 회귀 77개, 참여자 frontend 12개, 관리자 다운로드 10개가 통과했다. 참여자 테스트에는 preview 토큰 갱신이 실제 세션을 읽거나 네트워크 요청을 보내지 않는 검증도 포함한다. HTML 진단과 `git diff --check`도 통과했다. 기존 Python W503/미사용 import 경고는 별도이며 이번 작업에서 정리하지 않았다.
- 모의 API/미리보기 브라우저에서 조건부 분기·숨긴 답 삭제·배타 선택·최대 3개·필수 이유·정수 금액·파트 간 독립 입력·전체 문항 완료를 확인했다. 가상 전화번호만 잠기고 기존 세션/초안은 보존됐다. 모바일 390px에서 7점 척도 넘침 없음과 진행 표시 고정, 예상 시간·중간 안내를 확인했다. 사회적 지지 아이콘 3개는 128px 원본이 정상 로드됐다.
- S12 브라우저 검수를 재개해 일반 모드의 검사 3 저장 후 새로고침 시 검사 4·기존 답 복원, 서버 역할의 모의 API에서 더 최신인 답의 명시적 복원, 오래된 revision의 409 차단을 확인했다. 이전 네트워크 harness 대기는 해결했다. 실제 휴대폰·서로 다른 물리 기기·개발 CloudType 종단 검수와 구분한다.
- 중지됐던 개발 서비스를 재시작한 뒤 `APP_ENV=development`, `DATABASE_NAME=ai_us_development`의 연결을 확인했다. **개발 DB 백업·T2 두 버전 등록·정확한 readback을 완료했다.** 백업은 `/data/ai_us_joint_research/backups/ai_us/development_pre_T2_20261023T053722Z/`에 gzip JSON과 SHA256/T1 해시 manifest로 보관하며 비공개 권한을 적용했다. 복원 대조 시 BSON datetime은 원본 Mongo 읽기와 같은 naive UTC로 decode한다. 정의는 9개에서 11개로 늘었고 T1 9개 해시는 모두 불변이다. T2 답변 필드 256/96개, 전체 CSV 열 270/110개와 원본 spec이 일치하며 재등록은 unchanged다. 운영 DB에는 연결하거나 쓰지 않았다.
- 실제 MongoDB의 임시 개발 컬렉션으로 동시 revision 저장 1건 성공/1건 충돌, 숨긴 답 제거, 본인 격리, 페이지 이력, 최종 응답 재시도 멱등성, 최종 완료 후 진행 재개 금지를 검증했다. 테스트 컬렉션은 삭제했고 실제 참여자의 응답·진행 기록은 변경하지 않았다.
- 초기 점검 당시 개발 frontend/backend는 HTTP 200이었지만 이전 배포였다. 이후 T2 코드 푸시와 개발 재배포 후 신규 경로·문항을 확인했다. 이 문단은 당시 이력이지 최신 배포 상태가 아니다. 후속 변경마다 사용자 수동 배포와 공동연구진 종단 검수를 별도로 확인하며, 서비스 재시작만으로 새 코드가 반영됐다고 판단하지 않는다.
- 등록 명령은 `python scripts/sync_frontend_survey_definitions.py --survey-round 2 --insert-only --expect-database ai_us_development`이며 먼저 `--dry-run`으로 확인한다. 기존 T2 정의가 다르면 덮어쓰지 않고 중단한다. `.env.example`에 `T1_SUBMISSIONS_OPEN=true`를 추가했으며 실제 환경값은 바꾸지 않았다. 개발 검수에서 T1 종료를 확인하려면 승인된 시점에 개발 CloudType만 `false`로 설정한다.
- T2 구현 커밋은 frontend `3e028da`, backend `b2f9bac`이며 기존 `main`을 사용한다. 2026-10-23 사용자 확인에 따라 이 프로젝트의 CloudType 배포는 수동이다. Git push는 소스만 갱신하며, 사용자가 CloudType에서 저장소를 가져와 Docker 빌드·컨테이너 배포를 실행해야 반영된다. 단순 서비스 재시작과 새 코드 배포를 구분하고, 승인된 push 전에 자동 배포 설정을 다시 확인하도록 요구하지 않는다. 개발 backend, frontend 순서로 수동 재배포한 뒤 종단 검수하며 연구진 승인 전 운영 전환은 금지한다. 상세 절차는 `docs/cloudtype-deployment-checklist.md`를 따른다. 가입·승인 대조는 재검증이지만 Mongo 다중 컬렉션 트랜잭션이나 번호 소유 인증은 아니므로 동시 생성의 전역 원자성까지 보장한다고 설명하지 않는다.

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

- 연구자 사이드바의 `미완료자 관리` 아래 `설문 접수 관리`는 `회차 상태 → 설문 구성 → 제출 허용 → 설문기간 종료 → 변경 정보` 순서다. `GET /api/v1/researcher/survey-round-settings`는 관리자/연구자 공용이며, 관리자만 `PUT /api/v1/admin/survey-round-settings/{round}`에 `configurationId`(문자열 또는 null), `isOpen`(제출 허용), `periodEnded` 중 한 항목씩 보낸다. 선택 가능한 완성 스키마가 없는 T3/T4는 구성과 토글을 모두 비활성화한다. 조회 실패는 미등록으로 간주하지 않는다.
- 자동 승인과 접수 변경은 동일한 테마 확인창을 사용하며 버튼은 `예`, `아니오` 순서다. 취소 시 요청하지 않고, 성공 응답 전까지 이전 상태를 유지한다. 저장 중 중복 요청·닫기를 막고 실패하면 같은 창에 오류를 표시한다. 일반 연구자는 조회 전용이며 조회 실패 시 변경을 막는다.
- 관리·참여자 화면은 `surveyRoundStates`의 `preparing/open/closed`를 동일한 `준비 중/접수 중/접수 종료` 뱃지로 표시한다. 준비 중 구성을 선택하면 제출 허용이 켜져 있어도 준비 중이다. 선택된 구성에서 `periodEnded=true`면 접수 종료이며, 나머지는 제출 허용이 켜져야 접수 중이다. 제출 허용만 끄면 준비 중이지 기간 종료가 아니다. 종료 처리·해제는 모두 제출 허용을 끄며, 자동 재개하지 않는다. 개인의 제출 완료/완료 횟수는 별도 보존한다.
- 참여자 `survey-status`의 `surveyConfigurations`에 맞춰 `GET /api/v1/participant/survey-configurations/{round}`에서 실제 DB 문항 spec을 읽고 파트/대상별로 적용한다. 로딩 실패 시 시작을 막는다. 1~4회차 버튼과 안내 문구에 상태를 반영하며, 기간 종료일 때만 `참여 기간이 끝났어요`를 쓴다. 구 backend 응답은 기존 호환 동작을 유지하므로 backend를 먼저 배포한다. 미리보기는 내장 문항만 사용하고 API를 호출하지 않는다.
- 초기 DB 선택값이 없을 때 기존의 완성된 T1 v2-0916/T2 v9-1022 구성만 호환 선택하며 다른 신규/초안 버전은 자동 선택하지 않는다. T1 종료 환경값 false와 기존 DB 접수 설정을 보존한다. T3/T4는 준비 중이다. 이후 관리자 설정에는 재배포가 필요 없다. 종료해도 기존 응답·완료 기록·조회/다운로드와 종료 전 접수된 작업은 유지되며, 이미 제출된 파트는 수정할 수 없다. 사용자별 임시 재개방·유예 종료는 아직 구현하지 않았다.

- 문항 저작·등록 원본은 `index.html`의 내장 설문이며, 실제 참여 시에는 관리자가 선택한 DB 버전의 spec을 사용한다. `surveyRound`, `surveyVersion`, `audience`, `part`, `_meta.title`, 문항 key와 선택지 value는 연구진 확정값만 사용한다.
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
- 참여 현황 bar chart와 설문 미완료자 목록은 등록된 definition의 `part: 2` 응답만 회차 완료로 인정한다. 파트 1만 제출한 참여자는 미완료이며, part 2 version이 여러 개여도 같은 참여자는 회차별 한 명으로 집계한다.
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
- 연구자 파일 관리 화면의 `대화문 강제 등록` 서비스 value는 참여자 화면의 `CHAT_TOOLS`와 동일한 ChatGPT/chatgpt, Gemini/gemini, Claude/claude, Grok/grok, 제타/zeta, 크랙/crack, 기타 캐릭터AI 챗봇/other를 사용한다. 별도 입력 방식은 선택하지 않으며 서비스가 ZIP 또는 이미지 형식을 결정한다.
- 강제 등록은 휴대폰 번호, 제출 시점, 서비스, 파일을 받아 파일별 `POST /api/v1/researcher/chat-submission-imports` 세션을 만든다. 응답의 `chunkSize`대로 파일을 나눠 numbered chunk endpoint에 순차 전송하고 실제 완료 chunk 수를 진행률로 표시한다. 각 chunk는 최대 3회 재시도하며, 모든 파일이 저장된 뒤 선택 순서의 `uploadIds`로 `/complete`를 한 번 호출한다.
- ZIP은 최대 1GB이고 이미지는 최대 20장, 장당 20MB 미만, 합계 100MB다. backend가 참여자 존재 여부와 대화문 제출 동의, 파일 signature, 서비스별 내보내기 구조, 동일 참여자의 중복 파일 묶음을 검증한다. 브라우저는 MongoDB/GridFS ID를 만들거나 DB 자격 증명을 다루지 않는다. `/complete`가 성공하기 전에는 화면에서 등록 완료로 표시하지 않는다.
- 연구자 대화문 CSV는 `GET /api/v1/researcher/exports/chat-submissions`에 `submission_point`, 필요 시 `school_level`을 전송한다. 검토 상태·메모·수정 시각·연구자도 CSV에 포함된다.
- 결과 다운로드 미리보기는 `GET /api/v1/researcher/chat-submission-previews`로 실제 DB와 동기화한다. 기본 `review_filter=unreviewed`이며 `all`, `reviewed` 또는 개별 검토 결과로 필터링할 수 있다.
- 관리 상태는 `incentive_paid`, `excluded`, `duplicate`, `other` 중 하나이며 기존 `chat_submissions.review`에 저장한다. 모든 상태에서 메모는 선택이고 `other`에서만 필수다. 상태 변경과 미검토 복귀는 모두 `저장` 버튼을 눌러야 확정한다. 미검토 복귀는 같은 URL의 `DELETE`를 사용한다.
- `other` 메모가 비어 있으면 native `alert()` 대신 확인 버튼이 있는 `reviewSaveDialog`를 표시하고 닫은 뒤 메모 입력으로 포커스를 돌려준다. 저장 시에는 같은 dialog를 버튼 없는 spinner 상태로 열고 PATCH/DELETE와 `loadPreview()`가 모두 끝난 뒤 자동으로 닫는다.
- 행 체크박스는 다운로드 선택 전용이다. 명시적으로 고른 `submissionIds`는 `reviewFilter`를 우회하지만 현재 `submissionPoint`와 `schoolLevel` 조건은 유지한다. `현재 조건 전체 선택`은 빈 ID 목록과 세 필터를 모두 보낸다.
- 개별 첨부는 `GET /api/v1/researcher/chat-submissions/files/{submissionId}/download`로 받고, 선택 원본 ZIP은 `POST /api/v1/researcher/chat-submissions/download-jobs`, 선택 대화문 CSV ZIP은 `POST /api/v1/researcher/chat-submissions/transcript-download-jobs`로 생성한다. 두 작업 모두 상태 polling 후 `downloadUrl`을 사용하며 삭제 요청 상태는 기본 다운로드에서 제외한다.
- 개별 파싱은 `POST /api/v1/researcher/chat-submissions/{submissionId}/parse` 후 run 상태를 polling한다. 완료 결과는 `latest-parse/download?format=json|csv`로 받는다. 파싱 불가 항목은 성공으로 꾸미지 않고 warning/error를 표시한다.

## 관리자 분석 분리

- 어뷰징 전수 비교는 API가 아니라 같은 컨테이너의 `app.analysis_worker` 프로세스가 전용 `analysis_jobs` 큐에서 처리합니다. 기존 제출 워커와 잠금·큐·생존 신호가 다릅니다. API 내부에서 `AbuseReviewService.report()`를 다시 호출하지 않습니다.
- 새 작업 요청·진행 조회·강제 새로고침·5분 캐시·페이지네이션 계약은 `api-contract.md`의 Queued Researcher Analysis를 따릅니다. 조회 중 연결이 끊기면 기존 작업 번호를 유지하고 재조회합니다. 상태 변경 요청은 자동 재전송하지 않습니다.
- 관리자 상단의 `설문 Worker`와 `관리자 Worker`는 각 워커의 생존 신호이고, 메뉴 내부의 대기/계산/완료/실패는 개별 작업 상태입니다. `관리자 Worker`의 현재 담당 기능은 관리자 분석이며 계산 중에도 상태등은 정상으로 유지됩니다.
- 어뷰징은 메뉴 진입 시에만 요청하고 활성/유입 통계는 해당 메뉴가 표시될 때만 갱신합니다. 참가자 로그인·설문 제출·기존 워커 정책은 그대로 유지합니다.
- backend 시작 스크립트와 frontend를 함께 반영해야 합니다. 추가 컨테이너·서비스·Secret은 없으며 운영 배포나 DB 변경은 별도 승인 없이 실행하지 않습니다.

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

### MongoDB 백업

- 변경 전 운영 MongoDB 전체를 날짜가 있는 `mongodump`로 Git 밖의 접근 제한 경로에 보관한다. 대상은 `ai_us`라는 단일 컬렉션이 아니라 `ai_us_production` DB이며 GridFS도 포함한다. 결과·검증·복구 시험 여부를 기록하고, 실제 운영 전환 직전에도 새 백업을 남긴다. 무중단 논리 백업은 컬렉션 간 단일 시점 스냅샷으로 간주하지 않는다.
- 2026-10-23 백업 완료: `/data/ai_us_joint_research/backups/ai_us/2026-10-23_pre_T2_20261023T120407+0900/`에 gzip BSON·메타데이터와 `manifest.json`을 보관했다. 전체 19개 컬렉션, 5,312개 문서, 압축 파일 합계 105,073,197 bytes이며 BSON/메타데이터 해독·컬렉션 목록 대조를 통과하고 SHA256을 기록했다. 여러 번의 dump로 완성한 온라인 논리 백업이며 운영 DB 쓰기·복원은 하지 않았다. 실제 복원 시험은 미실시다.

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

#### T2 통합 계획 갱신 (2026-10-23)

상태: 아래는 2026-10-23 당시 승인된 통합 계획의 보존 기록이다. 진행 저장·가입 차단·T2 정의·설문 접수 설정 등 구현 완료 항목은 위 현재 계약과 `api-contract.md`를 따른다. 아래의 당시 현재형 설명이나 미구현 표현을 새 작업 지시로 해석하지 않는다. 후속 배포·실환경 검수·운영 전환은 각각 별도 확인한다.

##### 근거 자료와 검토 범위

- 원본은 `/data/ai_us_joint_research/tmp/temp_T2/`의 `index_T2_v9.html`, `index_T2_v9_개발_확인사항.md`, `T2 설문 응답 로직 검수 가이드.docx`이며 보존한다. 공동연구진의 최신 회신에서 확정된 합의를 기존 검수 가이드의 기술적 가정보다 우선한다.
- T2 공통 정의 `t2-part1-v9-1022`, `t2-part2-v9-1022`를 새로 등록하고 T1 정의·응답은 덮어쓰지 않는다. 문항 key·보기 값·분기·채점 메타를 승인본과 대조한다.
- 검수본의 여섯 결함은 API를 모의 처리한 로컬 브라우저에서 재현했다. S1~S14 전체 종단 검수, 실제 모바일 검수와 개발 서버 통합 검수는 아직 하지 않았다.

##### 참여 정책과 T1 표시

- T2는 신규 계정, T1 미시작·임시작성·파트 1만 완료·전체 완료 계정 모두 진입할 수 있게 한다. T2 파트 2는 T2 파트 1의 서버 저장 완료 후 연다.
- T1의 기존 UI와 파트 1·파트 2 하위 항목을 보존하고 시작·제출 버튼만 비활성화한다. 종료 안내는 공동연구진 원문인 `참여 기간이 끝났어요`를 사용한다.
- 접수 종료와 제출 완료는 별도 상태다. 각 파트에 서버 제출 이력에 따른 `제출 완료` 또는 `미제출`을 표시한다. 기존 버전도 회차·파트로 매핑하며 임시저장은 완료로 인정하지 않는다. 파트 1만 제출했다면 파트 1만 완료로 표시한다.
- T1 종료 후에도 제출 이력 확인과 연구자 집계·내보내기는 유지한다. 접수 종료와 T2 파트 선행 조건은 서버에서도 검사한다. 종료 시각·유예·접수된 Queue 작업은 아래 확인 대기 정책을 따른다.
- 완료 회차 수는 실제 서버 기록으로 계산한다. T2 진입만으로 T1을 완료 처리하지 않고, T2부터 시작한 참여자의 진입 회차와 T1 응답 부재를 집계에서 구분한다.
- 대화문은 `chatConsent`와 T1~T4 중 한 회차의 완료를 요구하는 방향으로 링크·본문·파일 제출의 UI와 서버를 맞춘다. 완료 기준은 현재와 같은 파트 2 최종 저장 완료를 기본안으로 두되 최종 확인한다. 기존 T1/T4 기록은 재분류하지 않고 T2/T3 제출 시점·관리자 필터·CSV·다운로드·등록 도구를 호환 확장한다.

##### 문항 병합과 진행 저장

- 공동연구진 HTML을 통째로 교체하지 않고, 현재 frontend에 T2 공통 문항과 필요한 엔진 기능을 병합한다. T1 정의·응답, 서버 기반 완료 복원, 입력 최적화, heartbeat 오류 비차단, 활동 추적과 Queue 저장 완료 판정은 보존한다.
- 현재 등록 경로는 공통 대상 값을 거부하고 파트 2에 T1의 `followup.researchConsent`를 자동 추가한다. 공통 대상 표현을 정규화하고 T1 전용 보정의 적용 범위를 제한한 뒤 정의 등록·조회·집계·CSV를 검증한다. `SURVEY_SETS.common`의 그룹 이름을 그대로 audience에 넣지 않는다.
- 부분 진행용 `PUT/GET /api/v1/survey-progress`는 기존 완료 조회 `/api/v1/participant/survey-progress?survey_round=...`와 구분해 추가한다. 본인·회차·버전 기준으로 조회·저장하고 부분 응답은 최종 응답이나 회차 완료로 집계하지 않는다.
- 서버 revision 등으로 다중 탭·기기·역순 요청의 오래된 덮어쓰기를 방지한다. 답변 개수나 기기별 `seq`만으로 최신 상태를 결정하지 않는다. 뒤로 이동·분기 변경 시 현재 답과 이탈 분석용 페이지 이력을 구분하며 숨긴 답이 되살아나지 않게 한다.
- 부분 저장 실패는 작성을 막지 않고 로컬 초안과 재전송을 유지한다. 해당 경로만 비차단 처리하고 일반 제출의 오류 안내는 보존한다. 본인 접근 권한과 개인정보 보존 범위도 정의한다.
- 202 접수만으로 완료 처리하지 않는다. Queue의 실제 저장 완료 후에만 초안을 정리하고 진행 기록을 완료 표시하며 다음 파트를 연다. 완료 기록과 이탈 분석 이력은 보존한다.

##### 확정된 인적사항 재입력 정책

- 목적은 가입 정보·T1·T2 파트 간 인적사항을 대조할 수 있도록 연구용 원응답을 독립적으로 수집하는 것이다. `reenter`를 T2 수집 정책으로 확정하며 `prefill-confirm` 자동 입력 모드는 이번 운영 범위에서 사용하지 않는다. 초기 입력 정책의 적용 지점과 버전 메타데이터는 분리해 향후 변경에 대비한다.
- 고정하는 값은 `demo.contact` 안의 `demo.contact_phone`과 `demo.guardianContact` 안의 `demo.guardianContact_phone` 두 개뿐이다. 로그인한 참여자의 DB 회원정보로 자동 입력하고 수정은 막으며, 서버도 요청값과 무관하게 해당 계정의 번호를 적용한다. 설문 응답으로 계정 연락처를 변경하지 않는다.
- 나머지 인적사항은 각 파트에서 대조용으로 다시 입력받는다. 성별·나이·재학 여부·학교명·학교급/학년·지역 등을 가입 정보나 파트 1 응답으로 자동 입력하거나 잠그지 않는다. 선택사항인 이메일/SNS도 전화번호와 함께 복사하지 않으며 기존 선택 여부는 유지한다.
- 현재 `applyProfilePrefill`과 `_merge_participant_profile_answers`는 학년까지 계정 값으로 고정·덮어쓴다. T2에서는 이 동작을 전화번호 두 개로 한정하고, 학교급/학년의 설문 원응답을 그대로 보존하도록 프런트와 서버를 함께 변경한다. 기존 T1 처리와 과거 응답은 소급 변경하지 않는다.
- 계정의 학교급/학년은 로그인·대상 UI 정보로 유지하고 설문에서 다시 입력한 값과 구분한다. 차이가 있다는 이유만으로 계정 값으로 보정하거나 재입력을 막지 않도록 기존 학년/대상 일치 확인 동작을 검토한다. 문항 자체의 유효 범위 검증은 유지한다.
- 파트 2 학업동기는 파트 2에서 다시 입력한 재학 여부와 공부 여부로 분기한다. 파트 1 원본은 변경하지 않는다. 같은 파트의 기존 초안은 복원하되 고정 전화번호는 DB를 우선하며, 새 설문을 열 때 다른 파트 답을 초안처럼 채우지 않는다.
- DB로 파트 1을 읽는 것은 기술적으로 가능하지만 재입력 정책의 자동 입력에는 사용하지 않는다. 연락처용 localStorage carry와 계정정보 덮어쓰기 경로를 제거·제한하고 다른 날·다른 기기에서도 계정 전화번호만 동일하게 제공한다. 인적사항 재사용용 API를 만들기 위해 전체 답변을 참여자에게 노출하지 않는다.
- 적용 정책과 정책 버전을 초안·최종 응답 메타데이터에 기록한다. 기존 초안의 정책 전환과 과거 수집값은 임의로 소급 변경하지 않는다. 대조 결과의 불일치만으로 거짓 응답으로 확정하거나 자동 정지·원응답 수정을 하지 않는다.

##### 회원가입 어뷰징 방지 요구사항

- 현재 `ApplicationCreate`는 숫자로 정규화한 본인·보호자 번호의 동일 여부를 서버에서 거부하고, frontend 가입 흐름도 같은 번호를 차단한다. 이 기존 방어는 유지한다. 사후 어뷰징 검토와 가입 시 차단은 별개다.
- 신규 조건은 신청서의 보호자 전화번호가 이미 가입된 다른 참여자의 로그인 전화번호와 일치하면 가입을 차단하는 것이다. 현재 가입 API에는 이 대조가 없으므로 추가한다. 기준은 기존 참여자 계정의 정규화된 로그인 번호이며, 기존 보호자 번호와 비교하는 규칙이 아니다.
- 안내 문구는 `이미 가입된 사용자 번호는 보호자 번호로 입력할 수 없어요. 연락처를 다시 확인하세요.`로 고정한다. 가입 화면의 보호자 번호와 연결해 오류를 보여 주고 입력값은 보존한다. 원시 DB 오류나 다른 계정 정보는 노출하지 않는다.
- 프런트 검증만으로 끝내지 않고 신청 저장·자동 승인보다 앞선 서버 경로에서 검사한다. 위반 시 신청/계정 저장이나 성공 화면·토큰 발급으로 진행하지 않으며, 자동 승인 켜짐/꺼짐 모두 적용한다. 서버 오류 코드와 frontend 표시를 API 계약에 함께 정의한다.
- 신청 후 수동 승인까지 계정 목록이 바뀌거나 동시 요청이 발생하는 경우에도 규칙이 우회되지 않도록 승인 시 재검증·일관성 확보 방식을 설계한다. 가입 API 우회 요청도 테스트한다.
- 형제자매가 같은 실제 보호자 번호를 공유한다는 이유만으로 차단하지 않는다. 본인 번호와 보호자 번호는 서로 달라야 하지만 보호자 번호 자체를 전역 unique로 만들지 않는다. 뒷자리만 같다는 이유로도 차단하지 않는다.
- 이번 규칙은 다른 참여자의 번호 재사용을 줄이는 장치이며, 실제 보호자 소유 여부·친구 번호·미개통/허위 번호를 증명하지는 못한다. 통신사/SMS 본인 인증은 이번 합의 범위가 아니며 추가하지 않는다.
- 기존 가입자를 소급 정지하거나 연락처를 일괄 수정하지 않는다. 승인 대기 신청 번호까지 차단 범위에 넣는 정책, 기존 보호자 번호를 새 로그인 번호로 쓰는 역방향 차단, 연구자 CSV 등록의 적용/예외 정책은 별도 확인 전 임의 확대하지 않는다.

##### 통합 전에 수정할 재현 결함

| 항목 | 확인된 문제 | 수정·회귀 검수 기준 |
|---|---|---|
| 조건부 초안 복원 | 파트 2 재진입 시 공부 여부·학업동기 답 누락 | 의존 답을 복원한 뒤 분기를 계산하고 활성 답을 복원한다. 로컬·서버 초안 모두 확인한다. |
| S11 필수 이유 | 참여의사 3번의 이유 없이 최종 제출 통과 | 필수 `other`의 공백을 거부한다. 사용 계기·도움 요청 기타 입력도 함께 확인한다. |
| C1 최대 3개 | 네 번째 선택 취소 전 4개가 저장돼 재진입 시 복원·검증 통과 | 제한 적용 후 저장하고 복원·최종 검증에서도 개수 제한을 확인한다. |
| 숨긴 학교명 | 학교 밖으로 변경해도 `demo.name`이 answers에 잔존 | 숨김 시 지우고 초안·최종 제출에서 제외한다. |
| A12·B9 기타 복원 | 체크만 복원되고 직접 입력 텍스트 누락 | 기타 텍스트까지 저장·복원하며 배타 선택·분기 변경도 확인한다. |
| 유료 금액 정수 | `usage.q17_amount=0.5` 검증 통과 | 빈값·음수·소수를 거부하고 0 이상 정수만 허용한다. |

- 이 여섯 항목은 현재 로컬 코드에서 수정하고 기존 T1 회귀 검사를 유지했다. 개발 배포 후 원본 S1~S14 및 실환경 재검수는 별도 게이트다.

##### Preview와 검수 기준

- 공동연구진 검수용 preview는 보존하되 가상 회원정보만 사용한다. preview에서 실제 로그인 정보·연락처·초안을 읽거나 변경하지 않으며, 진행 저장·최종 제출·heartbeat·분석 추적 등 실제 API 쓰기를 하지 않는다. preview 임시저장도 실제 참여자 저장 영역과 분리한다.
- 원 검수 가이드의 연락처 수정 허용·같은 기기에서 직후에만 이어받는다는 설명을 수정한다. 운영과 preview 모두 가상/실제 계정에 해당하는 전화번호 두 개만 고정하고, 나머지 인적사항은 파트별 빈칸에서 시작하는지 검수한다. 재진입한 동일 파트 초안의 복원과 구분한다.
- B9 조건, 학업동기 13/13/0개 분기, 효능감 18개 유지, S6 학업동기 제거, A12 배타 선택과 사회적 지지 아이콘 3개는 국소 확인됐다. 이를 전체 검수 완료로 확대 해석하지 않는다.
- S1~S14 전체와 신규 진입 유형, 학년 재입력/계정값 차이 보존, 두 번호 수정 불가·서버 변조 방어, 파트 간 인적사항 미복사, 창 닫기·다른 기기·동시 탭·오프라인·토큰 갱신·Queue 실패/재시도를 개발 환경에서 확인한다.
- 가입 검수에는 본인=보호자(서식만 다른 동일 번호 포함), 보호자=기존 참여자 로그인 번호, 정상 신규 번호, 형제자매의 보호자 공유 허용, 뒷자리만 같은 번호 허용, 수동/자동 승인과 승인 전 계정 변동·서버 직접 요청을 포함한다. 위반 시 새 신청/계정/토큰이 생기지 않는지 확인한다.
- 모바일/PC의 고정 진행 표시·텍스트 넘침·아이콘과 저성능 입력 지연, 관리자 완료/미완료·부분응답·CSV·대화문 제출/다운로드 회귀도 확인한다. 검수 URL·가상 테스트 계정·문항 비교표·결과표와 frontend/backend commit을 연구진에 제공한다.

##### 구현 순서와 운영 전환

1. 원본·확정 회신을 기준으로 문항·분기·회원정보 정책을 고정하고 가입 차단의 오류 계약을 정한다. 기존 가이드의 상충 설명은 운영용 검수표에서 수정한다.
2. 공통 정의 등록, 재입력/전화번호 고정, 부분 저장 충돌 제어, 가입 차단과 승인 재검증, 서버 접수·제출 자격을 구현한다. schema·route·service·worker·API 계약·관련 테스트를 함께 갱신한다.
3. 여섯 결함을 수정하며 현재 frontend에 T2를 병합하고 T1 UI·진행률·가입·대화문·관리자 흐름을 연결한다. 기존 참여자·관리자·가입 테스트를 유지한다.
4. 별도 Git 저장소를 만들지 않는다. 승인된 변경을 기존 저장소에 push하고 사용자가 개발 CloudType backend/frontend만 수동 재배포한다. 개발 DB에만 새 정의를 등록해 공동연구진 검수를 진행한다. 별도 branch가 필요하면 먼저 승인받는다.
5. 운영은 기존 배포를 유지한다. 이 프로젝트의 CloudType은 사용자 수동 배포이므로 승인된 Git push만으로 운영 컨테이너가 교체되지 않는다. 개발 서비스만 수동 빌드·배포하고, 로컬·개발 테스트가 운영 API fallback이나 운영 DB에 연결되지 않게 한다. 검수 승인 전 운영 배포·정의 등록·접수 설정 변경도 하지 않는다.
6. 전체 검수와 운영 전환 승인 후 위 MongoDB 백업 절의 경로에 새 백업을 남긴다. 사용자가 backend 재배포, 새 정의 등록, frontend 재배포와 접수 정책 전환을 순서대로 진행하고 대상 commit을 기록한다.
7. 이상 시 서비스 버전·접수 설정을 되돌리되 이미 수집한 응답·정의는 삭제하지 않는다. 새 데이터/설정의 이전 버전 호환성과 이미 접수한 Queue 작업의 처리를 복구 절차에서 확인한다.

##### 남은 확인 사항

- 인적사항 재입력과 전화번호 두 개 고정은 확정됐으므로 더 이상 연구진 답변 대기 항목이 아니다. 기존 운영 회원정보의 오류 정정 경로는 설문 입력과 분리하며 임의 정정하지 않는다.
- T1 종료 시각(KST), 작성 중 유예와 종료 전 접수된 Queue 작업의 저장 보장 정책, 대화문 자격의 회차 완료 기준을 최종 확인한다.
- 공통 문구 승인과 문항 수 산정 기준을 확정한다. 파트 1의 243문항 표기와 실제 문항 객체 240개는 화면 문항·조건부 문항·복합 하위 필드·제출 key·CSV 열의 기준을 구분해 대조한다.
- 개인정보·부분응답 보존 기간과 대조 메타데이터 범위, 가입 차단의 별도 정책 확장이 필요한지 확인한다. 연구진 검수 승인 전 운영 수집이나 추가 신원 인증을 시작하지 않는다.

### AI 대화문 후처리 확장

상태: 보류.

- 아직 지원하지 않는 AI 서비스의 ZIP export adapter와 공유 링크 수집 정책은 샘플·서비스 약관·연구 기준이 확보된 뒤 추가한다.
- Claude ZIP은 `conversations.json`의 `uuid`, `chat_messages[]`, `human|assistant` sender 구조로 식별한다. parser는 JSON의 `\uXXXX` escape를 표준 decode한 뒤 사용자에게 표시된 `chat_messages[].text`만 정규화하고 thinking/tool 내부 content와 첨부 파일 본문은 포함하지 않는다.
- Grok ZIP은 파일명이 아니라 최상위 `conversations[]`와 각 항목의 `conversation`, `responses[]` 구조로 식별한다. 실제 export 형식이 변경되면 fixture를 먼저 추가해 validator와 adapter를 함께 갱신한다.
- 대용량 원본과 생성 ZIP이 장기간 누적되면 GridFS 보존 기간과 백업 비용을 검토하고 S3 호환 object storage 이전을 검토한다.
- 기존 GridFS 파일 제출의 `tool`/`status` 메타데이터가 없는 경우 backend 담당자는 `scripts/backfill_chat_submission_metadata.py`로 GridFS metadata에서 도구 값을 보정한다.

## 대화문 동의 철회·재동의 및 자료 복원

- 연구자 콘솔의 동의 관리 > 대화문 동의 관리에서 본인 휴대전화 11자리로 계정을 조회한다. 좌측 메뉴에서 참여 현황 바로 다음에 배치한다. CSV 등록 계정도 신청서 생성 없이 같은 경로를 사용한다. 승인 목록의 동의 버튼도 이 패널로 이동한다.
- 흰색 패널의 `조회 결과` 띠 안에 연락처·이름·학교급·학년과 동의·제출·숨김 건수를 묶어 표시한다. 변경 이력은 처리 시각·참여자·동의 변경·기존 자료·처리자·사유의 6열이며 참여자 정보는 현재 조회 계정 기준이지 처리 당시 학적 snapshot이 아니다. 높이 240px 제한·고정 헤더·키보드 접근·모바일 내부 가로 스크롤을 유지한다.
- `POST /api/v1/researcher/participants/chat-consent/lookup`은 `{phone}`을 받아 현재 동의, revision, 제출/숨김 건수와 변경 이력을 반환한다. 번호는 URL이나 부분 검색에 사용하지 않는다.
- `PUT /api/v1/researcher/participants/{participantId}/chat-consent` 요청은 `{chatConsent, expectedRevision, restoreExisting, reason}`이다. 연구자/관리자 인증이 필요하며 사유는 필수다. 계정 한 문서에서 현재 상태·자료 제한 기준 시각·revision·이력을 원자적으로 저장한다. 버전 충돌은 409이며 재조회가 필요하다.
- 철회는 신규 제출을 차단하고 기존 자료를 숨김·이용 제한한다. 원본, 파싱 기록, 사례금/검토 메모를 삭제하거나 덮어쓰지 않는다. 최초 신청 동의도 보존하며 신청 목록은 계정의 현재 값을 표시한다. 기존 신청서 기반 변경 API도 같은 필수 요청 계약과 계정 저장 처리를 사용한다.
- 재동의의 기본 범위는 향후 제출뿐이다. 이전 제한 시각까지의 자료는 계속 숨긴다. 본인이 기존 자료 복원도 요청한 경우에만 `restoreExisting=true`로 동의 철회에 의한 제한을 해제한다. 삭제 요청 상태나 다른 검토 상태는 복원하지 않는다. 모든 변경 이력은 유지한다.
- 대화문 목록의 `consent_restricted` 필터는 내용·파일명 없이 제한 메타데이터만 보여 준다. 일반/전체 목록, 개별 원본·JSON·CSV, 명시적 ID 선택, 일괄 ZIP, 파싱 요청/조회와 worker 실행에 제한을 적용한다. 자료 제한은 인증 오류와 구분해 409로 반환한다. 제출 현황 CSV 등 내용 없는 관리 메타데이터는 유지된다.
- 새 ZIP에는 실제 포함된 제출 ID manifest를 기록하고 수신 직전 동의 상태를 다시 확인한다. 배포 전에 만든 manifest 없는 ZIP 또는 이후 제한/삭제된 자료가 든 ZIP은 409로 거절되므로 새 다운로드를 요청한다. 이미 브라우저로 전달·저장된 사본은 회수할 수 없으며 별도 연구진 처리 절차가 필요하다.
- 저장 중에는 공통 진행 모달로 중복·상충 조작을 막고 30초로 요청을 제한한다. 응답 불명확/실패 시 자동 재시도하지 않고 계정 재조회를 요구한다. 백엔드와 설문 Worker를 함께 재배포한 뒤 프런트엔드를 배포한다. 이 변경 자체는 기존 운영 자료를 일괄 수정하거나 삭제하지 않는다.
- 보존 기간과 별도 삭제 요청 처리는 승인된 IRB·연구 운영 기준을 따른다. 동의 토글에 자동 삭제를 연결하지 않는다.

## 현재 기준선

- 로컬 운영 모니터 `127.0.0.1:4199`는 별도 읽기 전용 보조 프로세스다. 30초 갱신, 종료 시 확인·재기동, 자동 시작 미설정 사항은 `cloudtype-deployment-checklist.md`의 로컬 운영 절차를 따른다. 모니터의 활동 0명만으로 운영 배포를 승인하지 않는다.
- 신청·인증·승인·CSV 등록, 설문 Queue 제출, 서버 기반 파트 완료 복원은 운영 흐름으로 확정됐다.
- 연구자 현황·미완료자·어뷰징 검토·설문 결과·대화문 원본/파싱 결과 다운로드는 실제 API를 사용한다.
- ZIP/이미지 첨부, GridFS 저장, Gemini screenshot parser, 관리 상태와 검토 필터는 구현 완료 상태다.
- 향후 기능은 위 `향후 작업 후보`에만 기록하고, 완료된 기능의 세부 이력은 Git history를 사용한다.
