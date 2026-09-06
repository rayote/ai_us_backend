# AI 마음 탐사대 백엔드 구현 계획

## 1. 확정된 전제

- 프론트엔드는 별도 `ai_us` 저장소에서 유지한다. 기존 HTML, CSS, 이미지, inline JavaScript 구조는 변경하지 않는다.
- 백엔드는 별도 `ai_us_backend` 저장소에서 Python과 MongoDB로 구현한다.
- 프론트와 백엔드는 HTTPS JSON API로만 통신한다. 브라우저에는 MongoDB 접속 정보나 관리자 비밀값을 두지 않는다.
- 설문 진행 중 임시 저장은 브라우저 `localStorage`에서 약 30초 간격으로 처리하고, 최종 제출 시에만 응답 전체를 API로 전송한다. 같은 브라우저와 기기에서 다시 로그인하면 임시 저장을 복원한다.
- 최종 제출 API는 MongoDB에 직접 동기 저장하지 않고 영속 대기열에 먼저 접수한다. 별도 작업자가 대기열의 데이터를 MongoDB에 순차 저장한다.
- Queue는 최종 제출·외부 알림 발송·대용량 파일 생성처럼 지연 허용 작업에만 사용한다. 로그인, 신청 저장, 개별 승인, 목록 조회는 즉시 결과가 필요한 동기 API로 처리한다.
- MongoDB 기반 Queue는 제출 폭주 완화와 작업자 재시도용이다. MongoDB 전체 장애 중에는 새 작업 접수가 불가하므로, MongoDB 백업·복구와 프론트 임시 저장을 별도로 유지한다.
- CloudType에서 프론트엔드 컨테이너, backend 컨테이너, MongoDB 컨테이너의 3개 서비스로 운영한다. 프론트와 백엔드 서비스는 각 GitHub 저장소를 참조해 CloudType이 배포하고, MongoDB는 CloudType 사전구성 컨테이너를 사용한다.
- 개발용 무료 CloudType 계정과 실제 운영용 유료 CloudType 계정은 같은 서비스 구성을 사용하되, 데이터베이스와 비밀값, 공개 URL을 완전히 분리한다.
- 로컬 개발 서버에는 Docker나 MongoDB를 설치하지 않는다. 실제 서비스 연동이 필요한 시점에 CloudType 개발 환경을 구성한다.
- 이번 범위에서는 별도의 스트레스 테스트를 수행하지 않는다. 기존 회의에서 공유된 결과를 용량 계획의 참고 자료로만 사용한다.

## 2. 실제 프론트엔드 통합 기준

- 현재 `AppStore.submitApplication`과 `PasswordReset.sendResetLink`만 명시적인 데모 함수로 분리되어 있다. 로그인, 연구자 승인, CSV 등록, CSV 다운로드는 inline script 안에서 바로 데모 동작을 하므로 API 호출로 교체할 지점을 별도 지정해야 한다.
- 설문 문항 화면, 설문 최종 제출 버튼, 대화문 입력 화면은 아직 구현되어 있지 않다. `localStorage` 임시 복원, 최종 제출 Queue, 제출 상태 조회는 이 화면들이 추가될 때 함께 연결한다.
- 신청 화면은 동의 체크를 검증하지만 현재 전송 객체에는 `joinSurvey: true`와 `joinChat`만 포함한다. 실제 신청 API에는 설명문 확인, 본인 동의, 보호자 동의의 확인값과 확인 시각을 함께 전송하도록 inline script를 보완한다.
- 연구자 화면의 신청 목록, 참여 현황, 미참여자, 결과 CSV는 모두 예시 배열과 예시 CSV를 사용한다. 서버 API 연동 전에는 실제 연구 데이터로 보이지 않도록 데모 상태를 유지한다.
- 연구자 로그인은 현재 입력값 검증 뒤 바로 `researcher.html`로 이동한다. 참여자 로그인도 바로 설문 패널을 열며, 두 흐름 모두 인증 API 연동 시 교체한다.

## 3. 구현 범위와 우선순위

### 1단계: 계정 신청과 연구자 승인

프론트의 `AppStore.submitApplication`을 API 호출로 대체한다.

- 참여 신청 저장: 성별, 학교급/학년, 참여자 휴대폰 번호, 보호자 휴대폰 번호, 이메일, 설명문 확인, 설문 참여 동의, AI 대화문 제출 동의, 본인/보호자 동의 여부와 확인 시각
- 중복 신청 방지: 휴대폰 번호를 정규화한 값에 고유 인덱스를 둔다.
- 연구자 신청 목록, 학교급 필터, 선택 승인과 전체 승인
- 승인은 계정 생성 및 첫 로그인 비밀번호 변경 필요 상태로 처리한다.
- 연구자 CSV 등록 화면은 원본 CSV 파일을 백엔드에 전송하고, 서버에서 열 이름과 행별 유효성을 검증한 뒤 참여자 계정을 일괄 등록한다.
- 연구자용 신청 결과 CSV 다운로드

### 2단계: 로그인과 비밀번호 관리

기존 참여자/연구자 로그인 화면을 유지하고 API만 연결한다.

- 참여자 로그인: 휴대폰 번호와 비밀번호
- 연구자 로그인: `admin` 또는 `researcher` 역할을 가진 별도 연구자 계정
- CloudType Secret의 초기 연구자 계정은 첫 backend 기동 시 `admin` 역할로 한 번만 생성하며, 이후 재기동에서 덮어쓰지 않는다. `admin`은 공동연구 실무자용 `researcher` 계정을 추가한다.
- 로그인 성공 시 짧은 수명의 access token과 역할 정보를 반환
- 승인 시 참여자 초기 비밀번호는 공통값 `1234`로 설정하되, 평문이 아닌 단방향 해시로만 저장한다.
- 공통 초기 비밀번호로 로그인한 참여자는 설문과 대화문에 접근하기 전에 비밀번호 변경 화면으로 이동하도록 강제한다.
- 로그인과 비밀번호 재설정 요청에는 반복 시도 제한을 적용한다.
- `PasswordReset.sendResetLink`는 1회용 재설정 토큰을 동기 생성하고, 이메일 또는 SMS 알림 발송 작업은 Queue로 처리한다. 외부 발송 지연·재시도가 로그인 화면을 막지 않도록 한다.
- SMS 발송은 초기 구현의 필수 의존성이 아니다. 참여 안내 또는 비밀번호 재설정에 필요해지면 외부 SMS 공급자, 발신번호, 수신 동의 절차를 확정한 뒤 Queue 작업으로 추가한다.

### 3단계: 설문 응답과 대화문 제출

- 설문 1건은 회차별 최종 제출 때 하나의 작업으로 대기열에 접수하고, 작업자가 하나의 MongoDB 문서로 저장한다.
- 제출 API는 대기열 접수 성공 뒤 `202 Accepted`와 제출 추적 ID를 반환한다. MongoDB 저장이 끝난 뒤에만 제출을 완료 상태로 전환한다.
- 프론트는 `202 Accepted`만으로 임시 저장을 지우지 않고, 제출 상태 조회 API가 `completed`를 반환한 뒤에만 해당 회차의 임시 저장을 삭제한다. 네트워크 단절 시에는 같은 제출 식별자로 재전송할 수 있어야 한다.
- 대기열은 메모리만 사용하는 방식이 아니라 MongoDB에 저장하는 영속 작업 컬렉션으로 구현한다. 서버 재시작 또는 작업자의 일시적 저장 오류 뒤에도 미처리 작업을 재시도할 수 있어야 한다.
- 작업자는 `queued`, `processing`, `completed`, `failed` 상태와 재시도 횟수를 관리한다. 저장 작업이 일시 실패하면 최대 3회까지 재대기하며, daemon 재시작 시 `processing`에 남은 작업을 `queued`로 복구한다. 중복 제출 식별자를 고유 인덱스로 두어 같은 최종 제출이 여러 번 저장되지 않게 한다.
- 서버는 참여자 ID, 회차, 동의 상태, 응답 전체, 서버 제출시각을 기록한다.
- 같은 참여자와 회차의 중복 제출 정책은 `초안/최종` 또는 `최종 1회 후 수정 불가` 중 연구진 결정에 따라 확정한다.
- 설문 1차 및 4차 뒤의 AI 대화문 제출은 대화문 동의 참여자만 허용한다.
- 대화문 입력 형식은 링크, 본문, 또는 둘 다 허용 중 연구진 결정 후 API 필드를 확정한다.

### 4단계: 연구자 관리와 결과 다운로드

- 신청 현황, 회차별 참여 현황, 미참여자 목록의 조회 API
- 조건별 설문 결과와 대화문 자료 CSV 생성 및 다운로드. 작은 결과는 동기로 반환하고, 파일 생성 시간이 길어질 때만 Queue 작업으로 전환한다.
- 연구자 권한이 있는 토큰만 이 기능에 접근하도록 제한
- `admin`과 `researcher`는 신청·참여·결과 관리 기능을 함께 사용하고, 연구자 계정 생성은 `admin`만 수행한다.

### Queue 적용 기준

| 기능 | 처리 방식 | 목적 |
| --- | --- | --- |
| 설문 최종 제출 | MongoDB 영속 Queue | 동시 제출을 평준화하고 저장 재시도 |
| AI 대화문 최종 제출 | MongoDB 영속 Queue | 본문 데이터의 비동기 저장과 재시도 |
| 비밀번호 재설정 알림 | MongoDB 영속 Queue | 이메일 또는 SMS 발송 서비스 지연·실패 격리 |
| 대용량 CSV 생성 | 필요할 때만 MongoDB 영속 Queue | 긴 파일 생성이 연구자 화면을 막지 않게 처리 |
| 로그인, 비밀번호 변경, 신청 저장, 개별 승인, 조회 | 동기 API | 사용자에게 즉시 확정 결과 제공 |

## 4. API 초안

API의 실제 URL과 JSON 필드명은 프론트 소스를 받은 뒤 기존 함수와 대조하여 확정한다.

| 기능 | 메서드와 경로 | 기존 프론트 연결 지점 |
| --- | --- | --- |
| 참여 신청 | `POST /api/v1/applications` | `AppStore.submitApplication` |
| 참여자 로그인 | `POST /api/v1/auth/participant/login` | 로그인 모달 |
| 연구자 로그인 | `POST /api/v1/auth/researcher/login` | 연구자 로그인 모달 |
| 연구자 계정 생성 | `POST /api/v1/admin/researchers` | 관리자 전용 기능, 연구자 화면 추가 시 연동 |
| 비밀번호 재설정 요청 | `POST /api/v1/auth/password-reset-requests` | `PasswordReset.sendResetLink` |
| 참여자 최초 비밀번호 변경 | `POST /api/v1/auth/participant/password` | 첫 로그인 비밀번호 변경 화면 |
| 신청 목록 | `GET /api/v1/researcher/applications` | `applications` 배열 |
| 신청 승인 | `POST /api/v1/researcher/applications/approve` | `approve()` |
| CSV 참여자 등록 | `POST /api/v1/researcher/participants/imports` | 파일 업로드의 `confirmBtn` |
| 설문 제출 | `POST /api/v1/survey-responses` | 설문 최종 제출 버튼 |
| 설문 제출 상태 | `GET /api/v1/submission-jobs/{submission_id}` | 제출 완료 확인 및 임시 저장 삭제 |
| 대화문 제출 | `POST /api/v1/chat-submissions` | 대화문 제출 화면 |
| 연구 결과 CSV | `GET /api/v1/researcher/exports/survey-responses` | 결과 다운로드 |
| 대화문 CSV | `GET /api/v1/researcher/exports/chat-submissions` | 결과 다운로드 |
| 상태 확인 | `GET /health` | CloudType health check |

## 5. MongoDB 컬렉션 초안

- `applications`: 신규 신청과 승인 상태. 휴대폰 번호 정규화 값에 고유 인덱스.
- `participants`: 승인된 참여자 계정, 역할, 비밀번호 해시, 최초 비밀번호 변경 필요 여부.
- `researchers`: 연구자 계정과 역할.
- `survey_responses`: 참여자 ID와 회차, 응답 전체, 제출시각. `participant_id + wave` 복합 인덱스.
- `submission_jobs`: 최종 설문 제출 대기열. 제출 추적 ID, 멱등성 키, 상태, 작업 데이터, 재시도 횟수, 오류 사유, 생성/처리 시각을 저장한다. 처리 상태와 생성 시각의 복합 인덱스.
- `chat_submissions`: 참여자 ID, 제출 시점(1차 후/4차 후), 형식, 링크 또는 본문, 제출시각.
- `notification_jobs`: 비밀번호 재설정 등 이메일 또는 SMS 발송 대기열. 발송 채널, 수신 대상, 템플릿 유형, 상태, 재시도 횟수, 오류 사유, 생성/처리 시각을 저장한다.
- `password_reset_tokens`: 만료시각을 가진 일회용 토큰. TTL 인덱스.
- `audit_logs`: 연구자 승인, CSV 내보내기 같은 민감한 관리자 작업의 기록.

보호자 연락처, 이메일, 대화문 등은 개인정보 또는 민감 가능 데이터이므로 최소 권한 원칙을 적용한다. 백업·보존 기간·삭제 절차는 연구 윤리 및 기관 정책에 맞춰 연구진이 확정한다.

## 6. FastAPI 내부 구조

```text
app/
  api/        # 인증, 참여 신청, 설문, 연구자 API 라우터
  core/       # 환경변수, CORS, 토큰, 비밀번호 해시 설정
  db/         # MongoDB 클라이언트와 컬렉션/인덱스 초기화
  schemas/    # API 요청/응답 Pydantic 모델
  services/   # 승인, 인증, 설문 저장, CSV 생성 로직
tests/        # API와 서비스 단위 테스트
deploy/       # Dockerfile 및 CloudType 실행 설정
docs/         # 프론트 연동 계약과 운영 문서
```

## 7. CloudType 운영 구성

- 개발 환경: 무료 CloudType 계정에 `ai_us` GitHub 저장소를 참조하는 프론트 서비스, `ai_us_backend` GitHub 저장소를 참조하는 backend 서비스, CloudType 사전구성 MongoDB 컨테이너를 만든다.
- 운영 환경: 별도 유료 CloudType 계정에 동일한 3개 서비스 구성을 만들고, 개발 환경과 별도의 MongoDB 데이터와 Secret을 사용한다.
- 프론트: CloudType이 GitHub의 정적 HTML을 현재 구조 그대로 배포한다.
- 백엔드: CloudType이 GitHub 저장소에서 container를 배포하며, 하나의 backend 컨테이너에서 Uvicorn FastAPI와 대기열 작업자 daemon을 별도 OS 프로세스로 실행한다.
- 대기열 작업자: 같은 backend 컨테이너에서 설문·대화문 저장과 알림 발송을 처리한다. FastAPI 프로세스 안에서 임시 task로 실행하지 않으며, MongoDB 작업 상태를 원자적으로 바꿔 중복 처리를 막는다.
- MongoDB: CloudType 사전구성 컨테이너를 사용하고 외부 공개를 피한다. backend 서비스에서만 접속하도록 설정하며, MongoDB용 GitHub 저장소는 만들지 않는다.
- 환경변수: `MONGODB_URI`, `DATABASE_NAME`, `JWT_SECRET`, `FRONTEND_ORIGINS`, 이메일 또는 SMS 발송 설정을 CloudType Configure 패널의 Secret으로 관리한다. CloudType이 backend 프로세스 환경변수로 주입하고 `Settings.from_environment()`가 직접 읽는다. `.env.example`은 이름 목록일 뿐이며 실제 `.env` 파일은 배포에 사용하거나 저장소에 커밋하지 않는다.
- backend 서비스 생성 시 CloudType이 제공하는 Dockerfile 템플릿과 필요한 build/start 초기화 명령을 배포 설정에 추가한다. 템플릿의 실제 내용은 CloudType 설정 단계에서 확정한다.
- CORS: 배포된 참여자/연구자 프론트 도메인만 허용한다.
- `/health`를 CloudType 상태 점검 경로로 등록한다.
- API와 작업자의 상태 점검은 각각 분리하고, 실패 작업 수와 가장 오래된 대기 작업 시간을 운영 지표로 확인한다.
- 운영 배포 전 MongoDB 백업과 복구 절차를 문서화하고 실제 복구를 1회 확인한다.

## 8. 프론트 연동 작업 순서

1. `/data/ai_us_joint_research/ai_us`에 프론트 저장소를 clone한다.
2. `AppStore.submitApplication`, `PasswordReset.sendResetLink`, 로그인 처리, `approve()`, `confirmBtn`, `downloadBlob`, `CHAT_CONSENT`와 예시 배열을 확인한다.
3. 각 함수가 기대하는 입력·성공·실패 화면 상태를 표로 기록한다.
4. 신청 동의값을 API 요청에 추가하고, 참여자 로그인 성공 뒤 `needsPasswordChange`가 `true`이면 비밀번호 변경 화면을 먼저 표시한다.
5. 로그인·승인·CSV 등록·다운로드의 데모 동작을 각 API 호출로 교체한다.
6. 설문 문항과 대화문 입력 화면이 추가되면 같은 참여자와 회차의 `localStorage` 임시 저장을 재로그인 뒤 복원하고, 제출 작업 상태가 `completed`일 때만 자동 삭제하도록 연결한다.
7. 해당 계약에 맞춘 FastAPI 요청/응답 모델과 MongoDB 스키마를 확정한다.
8. 백엔드 최소 기능부터 구현하고, 프론트 변경은 inline script의 API 호출부에 한정한다.

## 9. 연구진 확인이 필요한 결정

- AI 대화문: 링크, 본문, 또는 둘 다 허용 여부
- 설문 최종 제출 뒤 수정/재제출 허용 여부
- 첫 로그인 비밀번호 변경 화면의 프론트 구현 방식
- 비밀번호 재설정에 이메일을 유지할지, SMS 발송을 추가할지와 외부 발송 서비스
- 연구자 계정 생성·권한 부여 절차
- 개인정보처리방침, 데이터 보존 기간, 삭제·백업 정책
- 참여자와 연구자 도메인의 최종 분리 방식