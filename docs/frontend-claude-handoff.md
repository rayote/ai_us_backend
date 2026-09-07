# 프론트엔드 Claude 전달 기록

이 문서는 `ai_us` 프론트엔드 저장소를 작업하는 Claude에게 전달할 변경 요청을 버전별로 관리한다. 백엔드 API가 준비된 뒤 해당 버전 항목만 전달한다.

## 사용 방법

1. 프론트 작업을 요청할 때 이 문서의 전달 ID와 전체 해당 항목을 Claude에 제공한다.
2. Claude가 작업한 뒤 프론트 저장소의 커밋 ID를 해당 전달 ID 아래에 기록한다.
3. 새 요청은 기존 항목을 수정하지 않고 다음 전달 ID로 추가한다.
4. 이 문서의 기준 프론트 커밋과 다른 버전에서 작업할 때는 먼저 차이를 확인한다.

## 공통 작업 원칙

- 대상 저장소: `ai_us`
- 기준 프론트 커밋: `f8f5e830d38bc88d3ead512a41805b7622984345`
- 대상 파일: `index.html`, `researcher.html`
- 기존 작업의 변경 추적 혼선을 줄이고 후속 Claude 작업의 연속성을 유지하기 위해, 순수 HTML/CSS/JavaScript 구조와 inline `<script>` 블록을 보존한 상태로 구현한다.
- 디자인, 이미지, 문구, 화면 배치, 기존 CSS를 필요 없이 변경하지 않는다.
- 외부 라이브러리와 프론트 빌드 도구를 추가하지 않는다.
- MongoDB URI, API 비밀값, 연구자 계정 비밀번호는 프론트에 넣지 않는다.
- API 기본 URL은 CloudType에 배포된 해당 환경의 backend 공개 URL만 사용한다. 개발용 무료 환경과 운영용 유료 환경의 URL·비밀값·데이터는 서로 분리한다.
- API 오류 시 기존 화면 흐름을 유지하고, 성공하지 않은 작업을 성공한 것처럼 표시하지 않는다.
- 참여자 로그인 요청에는 상단 토글의 UI 값(`kid` 또는 `teen`)을 API 분류값(`elementary` 또는 `secondary`)으로 변환한 `audience`를 함께 보낸다. backend가 계정 학교급과 비교하므로 프론트에서 임의로 우회하지 않는다.

## FH-001: API 연동 준비 사항

상태: 병합 완료. 프론트 작업 브랜치 `backend/fh-001-api-integration`의 신청·로그인·신청 목록/승인·CSV 등록 연동이 `main`에 반영됐다.

### 변경 대상

| 파일 | 현재 위치 | 요청 |
| --- | --- | --- |
| `index.html` | `AppStore.submitApplication` | 로컬 배열 저장 대신 참여 신청 API를 호출한다. |
| `index.html` | 참여자·연구자 로그인 제출 처리 | 입력 검증 뒤 로그인 API를 호출하고, 성공한 역할에 따라 기존 화면 이동을 실행한다. |
| `index.html` | `PasswordReset.sendResetLink` | 즉시 성공하는 데모 Promise 대신 비밀번호 재설정 요청 API를 호출한다. |
| `researcher.html` | `applications` 배열과 `approve()` | 신청 목록 API를 불러오고, 선택/전체 승인을 승인 API로 처리한다. |
| `researcher.html` | `confirmBtn` 파일 등록 | 선택한 원본 CSV 파일을 연구자 CSV 등록 API로 전송한다. |
| `researcher.html` | 결과 다운로드 버튼 | 현재는 데모 상태 유지. 화면의 전체·학교급 필터와 backend의 단일 `surveyRound`·`surveyVersion` CSV 계약을 맞춘 뒤 별도 요청으로 연결한다. |

참여자 로그인 API는 구현되었으며, 성공 응답의 `needsPasswordChange`가 `true`이면 설문 패널을 열기 전에 `FH-004`의 비밀번호 변경 화면을 표시한다. 연구자 로그인과 신청 목록·승인 API도 구현되었지만, 실제 CloudType backend URL을 정한 뒤에 함께 연결한다. 연구자 화면의 기존 관리 기능은 `admin`과 `researcher` 모두 사용할 수 있으며, 연구자 계정 생성은 backend의 admin 전용 API로만 처리한다.

비밀번호 재설정 이메일은 전용 Gmail 1계정의 backend SMTP 발송으로 처리한다. 프론트에는 Gmail 계정, 앱 비밀번호, SMTP 설정을 넣지 않으며, `PasswordReset.sendResetLink`는 backend 요청 성공 여부만 처리한다.

Gmail 기반 비밀번호 재설정 API는 아직 구현·활성화하지 않는다. `PasswordReset.sendResetLink`는 데모 상태를 유지하고 별도 전달 ID로 연동한다.

CSV 등록은 `POST /api/v1/researcher/participants/imports`에 `file` 필드로 UTF-8 CSV를 전송한다. 현재 프론트의 CSV 양식인 `이름,휴대폰번호,학교급,학년`을 유지하며, Excel 업로드는 backend에서 아직 지원하지 않는다. 응답의 `createdCount`, `skippedCount`, `errors`를 기존 등록 완료 안내에 표시한다.

### 참여 신청 요청에 포함할 값

현재 신청 화면은 동의를 화면에서 확인하지만, 요청 객체에는 일부 값만 넣는다. API 연동 때 아래 값을 모두 전송한다.

- 성별, 학교급·학년, 참여자 휴대폰 번호, 보호자 휴대폰 번호, 이메일
- 설명문 전체 확인 여부
- 설문 참여 동의 여부
- AI 대화문 제출 동의 여부
- 본인 동의와 보호자 동의 여부

정확한 JSON 필드명과 API URL은 백엔드의 API 계약 문서 확정본을 따른다.

참여자 로그인 API의 응답 `needsPasswordChange`가 `true`이면 설문 패널을 열지 않고, `FH-004`의 최초 비밀번호 변경 화면을 먼저 표시한다.

### 이번 전달에서 제외할 항목

- 설문 문항 화면과 설문 최종 제출 UI
- `localStorage` 임시 저장 및 재로그인 복원
- 설문 Queue 제출 상태 조회
- AI 대화문 입력 화면
- 첫 로그인 비밀번호 변경 화면
- 대화문 제출 형식 결정

위 항목은 현재 프론트에 구현되어 있지 않으므로, 화면·API 계약이 준비된 뒤 별도 전달 ID로 요청한다.

### 완료 기준

- 기존 신청, 로그인, 연구자 관리 화면의 디자인과 이동 흐름이 유지된다.
- 요청 중에는 같은 버튼을 반복 제출할 수 없다.
- API 실패 시 기존 성공 화면이나 성공 상태로 이동하지 않는다.
- 성공 시에만 현재의 완료 화면, 목록 갱신, 파일 다운로드를 수행한다.
- 브라우저 개발자 도구에 MongoDB 접속 정보나 비밀값이 노출되지 않는다.

### 작업 완료 기록

- 프론트 작업 커밋: `3e21a19 Connect application management to backend API`
- 검토 일자: 2026-09-07
- Pull Request: `#1` 병합 완료 (`backend/fh-001-api-integration` → `main`), merge commit `8dfaee6e9274741da4ac5d5bf3dc15759021f14b`
- 비고: backend 공개 URL을 사용해 신청·참여자/연구자 로그인·신청 목록/승인·참여자 CSV 등록을 연결했다. 설문/대화문 결과 다운로드, 최초 비밀번호 변경 화면, 비밀번호 재설정은 후속 요청으로 남긴다.

## 다음 전달 예정 항목

- `FH-002`: 설문 문항 화면, `localStorage` 임시 복원, Queue 제출·완료 상태 확인
- `FH-003`: AI 대화문 입력·제출 화면과 Queue 연동
- `FH-004`: 병합 완료. 공통 초기 비밀번호 `1234`로 로그인한 참여자에게만 표시하는 첫 로그인 비밀번호 변경 화면

`FH-002`에서는 설문 최종 제출과 `localStorage` 키에 설문 회차 `surveyRound`와 설문 버전 `surveyVersion`을 함께 사용한다. 설문지가 다음 회차에 업데이트돼도 기존 임시 저장과 결과 데이터를 구분하기 위한 값이다.

설문 문항이 확정되면 backend의 `admin`이 먼저 같은 `surveyRound`와 `surveyVersion`의 설문 정의를 등록한다. Claude는 정의의 문항 `key`를 최종 제출 요청의 `answers` 객체 키로 사용하며, CSV 열 순서는 backend의 설문 정의가 관리한다.

문항이 많은 경우 설문 정의는 CSV 또는 Excel 원본에서 일괄 등록한다. Claude는 backend가 검토·등록한 설문 정의의 문항 `key`를 사용하며, Word·PDF 원본을 직접 추정해 문항 키를 만들지 않는다.

설문 최종 제출은 `POST /api/v1/survey-responses`로 전송한다. `202 Accepted` 응답의 `submissionId`로 `GET /api/v1/submission-jobs/{submissionId}`를 확인하고, 상태가 `completed`일 때만 해당 `localStorage` 임시 저장을 삭제한다.

### FH-002: 더미 설문 연동 확인

- 프론트 작업 브랜치: `backend/fh-002-dummy-survey-sync`
- 프론트 작업 커밋: `9448063 Add dummy survey queue integration`
- Pull Request: `#6` 병합 완료 (`backend/fh-002-dummy-survey-sync` → `main`), merge commit `5ce5b44e6e7c3f31c38faf80bff1a7e23a530b84`
- 후속 프론트 작업 커밋: `7233b83 Lock completed dummy survey`
- 후속 Pull Request: `#7` 병합 완료 (`frontend/lock-completed-dummy-survey` → `main`), merge commit `230052552bbc89832227cfe538588b13f147bc19`
- 선행 backend 커밋: `5edcd64 Fix survey definition MongoDB storage`
- **TODO - 실제 설문으로 교체:** 현재 `설문 연동 확인` 모달과 `surveyRound: 1`, `surveyVersion: "demo-v1"`의 3문항은 개발 환경 확인용 더미 설문이다. 실제 연구 설문 문항·디자인이 준비되면 이 모달과 더미 문항을 제거하고, 확정된 회차·버전·문항 키를 사용한 실제 설문 화면으로 대체한다.
- **중요 - 프론트 설문 완성만으로 MongoDB 저장은 완료되지 않는다.** 실제 설문 UI·문항을 만든 뒤에는 회차, 버전, 문항 키, CSV 열 이름, 순서가 담긴 문항 정의를 backend 담당자에게 전달한다. backend 담당자가 이를 `survey_definitions`에 등록·검증한 뒤, 프론트의 최종 제출 요청을 실제 MongoDB 저장 Queue와 연결한다.
- 프론트 Claude는 실제 설문을 만들 때 임의의 더미 문항, 임의 `surveyRound`·`surveyVersion`, 임의 문항 `key`를 추가하거나 기존 더미 값을 실제 연구 데이터에 사용하지 않는다. 확정 문항 정의의 값을 받은 뒤에만 `answers` 객체와 제출 요청을 완성한다.
- 인계 순서: 실제 설문 UI 초안 → 연구진 문항 확정 → 문항 정의 CSV/Excel 또는 JSON 전달 → backend 설문 정의 등록·검증 → 프론트 최종 제출 연동 → 개발 환경 통합 확인.
- 실제 설문 화면으로 교체할 때도 아래 임시 저장·Queue 완료 확인 계약은 유지한다. 디자인·문항 UI만 교체하고 `surveyRound`, `surveyVersion`, `answers`, `submissionId` 요청 구조는 backend API 계약에 맞춘다.
- 임시 저장 키는 참여자 휴대폰 번호, `surveyRound`, `surveyVersion`으로 구분한다.
- 입력 변경과 30초 간격으로 `localStorage`에 저장하고, 다시 열면 임시 내용을 복원한다.
- 최종 제출은 Queue에 접수한 뒤 `completed`일 때만 임시 저장을 삭제한다.
- `completed` 뒤에는 해당 더미 설문의 입력과 제출 버튼을 잠가 30초 임시 저장 타이머나 중복 클릭이 새 제출을 만들지 않게 한다.
- 현재는 동일 참여자·회차·버전의 최종 응답을 1건으로 제한한다. “다시 최종 제출하기”는 기존 응답 갱신 정책을 확정한 뒤 별도 요청으로 구현한다.
- backend의 `demo-v1` 설문 정의 등록은 `csvColumn` alias 저장 수정이 배포된 뒤 수행한다.

### FH-003: AI 대화문 제출

대화문 제출 화면은 `submissionPoint`가 `afterRound1` 또는 `afterRound4`인 링크 또는 본문 입력을 `POST /api/v1/chat-submissions`로 전송한다. API 응답의 `submissionId`로 기존 제출 상태 조회 API를 확인한다. 이 기능은 대화문 제출 동의 참여자에게만 표시한다.

- 요청 필드: `submissionPoint`, `sourceType` (`link` 또는 `text`), `rawInput`, 브라우저 생성 `submissionId`
- 공유 링크는 현재 서비스별 parser가 없으므로, backend가 `placeholder` 상태로 보관한다. 프론트에서 추출 완료로 표시하지 않는다.
- 복사 본문은 원문 그대로 전송한다. backend가 정규화와 경고 기록을 처리한다.
- 기존 HTML·inline script 구조와 화면 디자인을 유지한다.

### FH-004: 최초 비밀번호 변경

- 프론트 작업 브랜치: `backend/fh-004-first-password-change`
- 프론트 작업 커밋: `b29e70a Add first login password change flow`
- Pull Request: `#2` 병합 완료 (`backend/fh-004-first-password-change` → `main`), merge commit `5b17062e6e20a9dfa37233e2cdfda3a81cf24f79`
- 참여자 로그인 응답의 `needsPasswordChange`가 `true`이면 기존 설문 화면을 열지 않고 비밀번호 변경 모달을 표시한다.
- 모달은 현재 비밀번호, 8자 이상 새 비밀번호, 확인 값을 입력받아 `POST /api/v1/auth/participant/password`로 전송한다.
- backend 성공 응답 뒤에만 모달을 닫고 설문 화면으로 이동한다.

### FH-005: 학교급 검증과 승인 오류 표시

- 프론트 작업 브랜치: `backend/fh-005-audience-validation`
- 프론트 작업 커밋: `1dd00d6 Validate participant school audience`, `2d975cb Use explicit school audience values`
- Pull Request: `#3` 병합 완료 (`backend/fh-005-audience-validation` → `main`), merge commit `7f5786f35ae95d68bd41a6e3058152ca109ea55a`
- 선행 backend 커밋: `9052351 Validate participant school audience`, `e23f530 Use explicit school audience values`
- 참여자 로그인 요청에 초등학생 토글은 `audience: "elementary"`, 중고등학생 토글은 `audience: "secondary"`를 포함한다.
- backend가 등록 학교급과 다른 모드를 거부하면 해당 오류 메시지를 기존 로그인 오류 영역에 표시한다.
- 연구자 페이지의 API helper는 JSON body를 `JSON.stringify()`로 전송한다. 승인 요청 오류 객체나 배열을 그대로 `alert()`에 전달하지 않고, backend의 `detail` 또는 검증 메시지를 표시한다.

### FH-006: 세션 종료와 중복 승인 처리

- 프론트 작업 브랜치: `backend/fh-006-session-and-approval-fixes`
- 프론트 작업 커밋: `5017731 Fix session handling and approval errors`
- Pull Request: `#4` 병합 완료 (`backend/fh-006-session-and-approval-fixes` → `main`), merge commit `3f02934cb3d73907d4b9f00bd550a9c991d3f3de`
- 선행 backend 커밋: `6acbb30 Fix session handling and duplicate approvals`
- 참여자 로그아웃은 `ai_us_access_token`과 `ai_us_role`을 `sessionStorage`에서 삭제한 뒤 홈 화면으로 돌아간다.
- `researcher.html`은 토큰이 없거나 `admin`·`researcher`가 아닌 역할이면 홈으로 이동한다. 정적 HTML 자체의 직접 접근은 막을 수 없지만, 민감 데이터 API는 backend가 JWT 역할을 검증한다.
- 연구자 승인 API가 `409`과 이미 등록된 휴대폰 번호 메시지를 반환하면, 해당 신청을 자동 승인하지 않고 오류를 표시한다.
- CSV 등록 참여자는 `participants`에 직접 생성되므로 회원 신청 관리 목록에는 나타나지 않는다. 웹 신청 참여자만 신청 목록에서 검토·승인한다.

### FH-007: 연구자 로그아웃과 휴대폰 입력

- 프론트 작업 브랜치: `frontend/fix-logout-and-phone-caret`
- 프론트 작업 커밋: `1716b64 Fix logout and phone input caret`
- Pull Request: `#5` 병합 완료 (`frontend/fix-logout-and-phone-caret` → `main`), merge commit `45724115ec2191cd83104f2faf516bbcfac692ce`
- 연구자 로그아웃은 access token과 역할을 삭제한 뒤 홈으로 이동한다.
- 참여자 휴대폰 번호 자동 하이픈 처리 중 입력 커서 위치를 유지한다.
- CSV 등록은 이미 `participants.phone_normalized` 고유 인덱스와 `DuplicateKeyError` 처리로 중복 휴대폰 번호를 생성하지 않으며, 결과를 `skippedCount`로 반환한다.

### FH-008: 연구자 관리 화면 실제 데이터 연동

- 프론트 작업 브랜치: `backend/fh-008-researcher-reporting`
- 프론트 작업 커밋: `71a9350 Connect researcher reporting to backend`
- Pull Request: `#8` 병합 완료 (`backend/fh-008-researcher-reporting` → `main`), merge commit `13b5726e42b6fd422de230ea5ae91bbb082d50e1`
- 선행 backend 커밋: `ce226b7 Add researcher participation reporting API`
- `참여 현황`은 `GET /api/v1/researcher/participation-status`를 사용해 MongoDB의 참여자 학교급별 인원과 회차별 완료 인원을 표시한다.
- `미참여자`는 `GET /api/v1/researcher/nonparticipants?survey_round=<round>&survey_version=<version>`을 사용한다. 현재 개발 화면은 `1`회차와 `demo-v1`을 대상으로 하며, 실제 설문 정의 등록 뒤 해당 버전 선택 UI로 대체한다.
- 설문 결과 다운로드는 회차와 설문 버전을 명시해 `GET /api/v1/researcher/exports/survey-responses`의 CSV 응답을 내려받는다.
- 설문 결과 미리보기는 `GET /api/v1/researcher/survey-response-previews`로 최근 최대 10건의 실제 MongoDB 응답 메타데이터를 표시한다. 선택 조건에 결과가 없으면 더미 행 대신 “해당 조건의 설문 응답이 없습니다”를 표시한다.
- AI 대화문 결과 다운로드는 `GET /api/v1/researcher/exports/chat-submissions`에 제출 시점과 학교급 필터를 전송해 실제 CSV를 내려받는다.
- 실제 설문 버전 목록 UI는 후속 작업이다.

### FH-009: AI 대화문 결과 다운로드

- 프론트 작업 브랜치: `backend/fh-009-chat-export`
- 프론트 작업 커밋: `9fa5145 Connect chat export to backend`
- Pull Request: `#9` 병합 완료 (`backend/fh-009-chat-export` → `main`), merge commit `a8adf19fe3dd365efe0d304c162b228fbc5a0bce`
- 선행 backend 커밋: `b98205c Add filtered chat transcript exports`
- 연구자 화면의 대화문 1/2 선택은 각각 `afterRound1`/`afterRound4`로 변환하고, 학교급 선택은 `school_level` query로 전송한다.
- backend CSV에는 참여자 ID, 이름, 학교급, 학년, 원본 입력, 정규화 대화문, parser 상태·경고가 포함된다.

### FH-010: 설문 응답 미리보기

- 프론트 작업 브랜치: `backend/fh-010-survey-preview`
- 프론트 작업 커밋: `c43e335 Preview survey responses from backend`
- Pull Request: `#10` 병합 완료 (`backend/fh-010-survey-preview` → `main`), merge commit `7068766efcdaa0397636d3da33e73a4cb5b99f2e`
- 선행 backend 커밋: `be18b3e Add survey response preview API`
- 결과 다운로드 화면의 설문 응답 미리보기는 선택된 회차·설문 버전·학교급 조건의 실제 MongoDB 응답을 최대 10건 표시한다. 내부 MongoDB ObjectId 대신 연구자에게 익숙한 `아이디(휴대폰)`, `학교급`, `학년`을 표시한다.
- 설문 CSV 다운로드에도 `아이디(휴대폰)`, `학교급`, `학년`을 포함하고 화면에서 선택한 학교급을 `school_level` query로 전송한다.
- 응답이 없으면 더미 행 대신 “해당 조건의 설문 응답이 없습니다”를 표시한다.

### FH-011: 설문 결과 참여자 프로필

- 프론트 작업 브랜치: `backend/fh-011-survey-profile-export`
- 프론트 작업 커밋: `74df11c Show participant profiles in survey results`
- Pull Request: `#11` 검토 중 (`backend/fh-011-survey-profile-export` → `main`)
- 선행 backend 커밋: `9d83ccc Include participant profiles in survey exports`
- 설문 결과 미리보기와 CSV에는 내부 MongoDB ObjectId 대신 `아이디(휴대폰)`, `학교급`, `학년`을 포함한다.
- 화면의 학교급 선택을 preview와 CSV export API의 `school_level` query에 전송한다.