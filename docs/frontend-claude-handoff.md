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
- Pull Request: `#3` 검토 중 (`backend/fh-005-audience-validation` → `main`)
- 선행 backend 커밋: `9052351 Validate participant school audience`, `e23f530 Use explicit school audience values`
- 참여자 로그인 요청에 초등학생 토글은 `audience: "elementary"`, 중고등학생 토글은 `audience: "secondary"`를 포함한다.
- backend가 등록 학교급과 다른 모드를 거부하면 해당 오류 메시지를 기존 로그인 오류 영역에 표시한다.
- 연구자 페이지의 API helper는 JSON body를 `JSON.stringify()`로 전송한다. 승인 요청 오류 객체나 배열을 그대로 `alert()`에 전달하지 않고, backend의 `detail` 또는 검증 메시지를 표시한다.