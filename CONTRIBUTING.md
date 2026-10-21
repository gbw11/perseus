# Git Convention

Xenova(S15P31E110) 프로젝트의 브랜치·커밋·MR 규칙입니다.
작업 시작 전에 한 번 읽어주세요.

---

## 1. 브랜치 구조

```text
master                ← 통합 데모·발표용 안정 버전
└── develop           ← 전 파트 통합 브랜치 (기본 브랜치)
    ├── backend       ← 기능별 develop 브랜치
    │   ├── feat/backend/vm-lifecycle-api
    │   ├── feat/ctrl/xen-driver
    │   └── chore/deploy/gitlab-ci-pytest
    ├── frontend
    │   └── feat/frontend/vm-list-card
    ├── platform
    │   ├── feat/platform/xen-bringup
    │   └── feat/agent/heartbeat
    ├── vehicle
    │   └── feat/app/drive-control
    └── docs/common/openapi-v1   ← 여러 파트에 걸친 변경은 develop에서 직접
```

| 브랜치 | 역할 | 규칙 |
| --- | --- | --- |
| `master` | 통합 데모·발표 기준 버전 | develop에서만 MR로 머지, 통합 데모 직전에만 반영 |
| `develop` | 모든 파트의 작업이 합쳐지는 통합 브랜치 | 기능별 develop 브랜치(또는 `common` 작업 브랜치)에서 MR로만 머지 |
| `backend` `frontend` `platform` `vehicle` | 기능별 develop 브랜치 | 작업 브랜치에서 MR로만 머지, 동작하는 단위로 develop에 MR |
| `{type}/{part}/*` | 개인 작업용 단기 브랜치 | 자기 기능별 develop 브랜치에서 생성, 1~3일 내 머지, 머지 후 삭제 |

**part → 기능별 develop 브랜치**

| 기능별 develop 브랜치 | part |
| --- | --- |
| `backend` | `backend`, `ctrl`, `deploy` |
| `frontend` | `frontend` |
| `platform` | `platform`, `agent`, `display` |
| `vehicle` | `app` |
| `develop` (직접) | `common` |

- `master`, `develop`, 기능별 develop 브랜치는 직접 push하지 않습니다. 반드시 MR로 머지합니다.
- `feat`처럼 type 이름만으로 된 브랜치나, `backend/...`처럼 기능별 develop 브랜치 이름 아래의 브랜치는 만들지 마세요. Git 구조상 같은 이름이 브랜치와 폴더를 겸할 수 없습니다.

---

## 2. 폴더 구조

```text
S15P31E110/
├── backend/                  # Spring Boot VM 관리 서버 (테스트: src/test/)
├── frontend/                 # React (npm workspaces, 테스트: frontend/ 안)
│   ├── apps/                 # admin-console(웹 관리 콘솔), vm-screens(모니터 화면)
│   └── packages/ui/          # 공통 컴포넌트·디자인 토큰
├── platform/                 # 보드(dom0·domU) 위에서 돌거나 보드 이미지를 만드는 것
│   ├── yocto/ boot/ first-install/ guests/ network/   # Yocto·Xen·U-Boot, 부팅 환경, domU 템플릿, 네트워크
│   ├── services/
│   │   ├── dom0-manager/     # Python 제어 계층 (하이퍼바이저 추상화·복구 엔진)
│   │   ├── vm-agent/         # VM 에이전트
│   │   └── display/          # dom0 표시 서비스 (화면 라우터 수신측·키오스크)
│   ├── apps/                 # VM 애플리케이션 (RC카 시나리오 앱, 폴더 이름 = MQTT app_id)
│   │   ├── autonomous/       # 자율주행 VM
│   │   ├── manual/           # 수동주행 VM
│   │   └── message-board/    # 메시지 보드 VM
│   ├── deploy/               # dom0 서비스 등록·배포 설정
│   ├── scripts/              # 빌드·검증 스크립트
│   └── tests/                # 플랫폼 테스트·픽스처
├── vehicle/                  # 젯슨(차량 쪽, Jetson Nano ADAS ECU) 코드
├── docs/                     # 설계·가이드 문서
│   └── contracts/            # VM 간 메시징·에이전트·관리 API 계약 (OpenAPI 포함)
└── Jenkinsfile
```

최상위 폴더는 `backend/`, `frontend/`, `platform/`, `vehicle/`, `docs/` 다섯 개만 둡니다. 새 폴더는 이 중 하나 아래에 만듭니다.
테스트는 루트에 모으지 않고 각 폴더가 자기 테스트를 가집니다.
CI 설정(`Jenkinsfile`)은 모든 브랜치에서 동작해야 하므로 루트에 둡니다.

---

## 3. 브랜치 네이밍

```text
{type}/{part}/{설명}
{type}/{part}/{Jira키}-{설명}     # Jira 이슈가 있는 경우 (권장)
```

**part**

| part | 대상 폴더 |
| --- | --- |
| `platform` | `platform/` (아래 services·apps·deploy 제외), `platform/scripts/`, `platform/tests/` |
| `ctrl` | `platform/services/dom0-manager/` |
| `agent` | `platform/services/vm-agent/` |
| `display` | `platform/services/display/` |
| `backend` | `backend/` |
| `frontend` | `frontend/` |
| `app` | `platform/apps/`, `vehicle/` |
| `deploy` | `platform/deploy/`, `Jenkinsfile` |
| `common` | `docs/`, `docs/contracts/`, 루트 설정 등 여러 파트에 걸친 변경 |

**type**: `feat`, `fix`, `refactor`, `test`, `docs`, `chore` (커밋 타입과 동일)

**설명**: 영어 소문자 + 하이픈(kebab-case), 짧게

```text
feat/platform/S15P31E110-5-xen-bringup
feat/agent/S15P31E110-18-heartbeat
feat/backend/S15P31E110-12-vm-lifecycle-api
fix/backend/S15P31E110-33-websocket-reconnect
feat/frontend/S15P31E110-20-vm-list-card
feat/display/hdmi-router
chore/deploy/gitlab-ci-pytest
docs/common/openapi-v1
```

---

## 4. 작업 흐름

```bash
# 1) 자기 기능별 develop 브랜치 최신화 (1장 표 참고, common 은 develop)
git switch backend
git pull origin backend

# 2) 기능 브랜치 생성
git switch -c feat/backend/S15P31E110-12-vm-lifecycle-api

# 3) 작업 & 커밋
git add .
git commit

# 4) MR 올리기 전 기능별 develop 브랜치 변경사항 반영
git fetch origin
git rebase origin/backend      # 충돌 시 해결 후 git rebase --continue

# 5) push 후 GitLab에서 MR 생성 (target: backend)
git push -u origin feat/backend/S15P31E110-12-vm-lifecycle-api
```

rebase 후 이미 push했던 브랜치를 다시 올릴 때는 `git push --force-with-lease`를 사용합니다.
(`--force`는 쓰지 않습니다. 본인 기능 브랜치에만 사용합니다.)

### 기능별 develop 브랜치 → develop

- 기능별 develop 브랜치에서 기능이 동작하면 develop으로 MR을 올립니다. 통합 데모 전에는 최소 1번 반드시 올립니다.
- 이 MR은 **Squash 없이 Merge commit**으로 머지하고, 소스 브랜치는 **삭제하지 않습니다**.
- develop의 변경(다른 파트 작업)은 GitLab에서 **develop → 기능별 develop 브랜치 MR**(Merge commit)로 받아옵니다. 여러 명이 쓰는 브랜치라 rebase·force push는 하지 않습니다.
- 충돌이 나면 작업 브랜치를 하나 따서 해결한 뒤 그 브랜치로 MR을 올립니다.

```bash
git switch -c chore/backend/sync-develop origin/backend
git merge origin/develop       # 충돌 해결 후 커밋
git push -u origin chore/backend/sync-develop   # MR target: backend
```

### Jira 이슈로 브랜치 만들기 (Claude Code)

Claude Code에서 `/branch {Jira키}`를 실행하면 1)~2) 단계를 대신합니다.
이슈 제목의 접두어로 part를, 이슈 내용으로 type과 영어 설명을 정하고, Jira 이슈를 '진행 중'으로 바꿉니다.

```text
/branch S15P31E110-56
→ feat/platform/S15P31E110-56-rpi4-xen-boot
```

| Jira 접두어 | part |
| --- | --- |
| `[PLATFORM]` | `platform` |
| `[CTRL]` | `ctrl` |
| `[AGENT]` | `agent` |
| `[DISPLAY]` | `display` |
| `[BE]` | `backend` |
| `[FE]` | `frontend` |
| `[APP]` | `app` |
| `[DEPLOY]` | `deploy` |
| `[COMMON]` | `common` |
| `[TEST]` | 대상 폴더의 part (type은 `test`) |

---

## 5. 커밋 메시지

[Conventional Commits](https://www.conventionalcommits.org/) 형식을 따릅니다.

```text
{type}({part}): {제목}

{본문 - 선택, 무엇을 왜 바꿨는지}

{Jira키 - 선택}
```

| type | 의미 |
| --- | --- |
| `feat` | 새로운 기능 |
| `fix` | 버그 수정 |
| `refactor` | 기능 변경 없는 구조 개선 |
| `test` | 테스트 추가·수정 |
| `docs` | 문서 변경 |
| `chore` | 빌드·설정·의존성 등 기타 |

규칙

- 제목은 한국어 가능, 50자 이내, 마침표 없이
- scope에는 브랜치의 part를 씁니다
- 한 커밋에는 하나의 변경 의도만 담습니다

```text
feat(backend): VM 시작·정지·재부팅 API 구현
feat(agent): heartbeat 5초 주기 송신
fix(frontend): 메트릭 차트 깜빡임 수정
test(backend): 자동 복구 정책 엔진 테스트 추가
docs(common): 에이전트 프로토콜 v1 확정
chore(deploy): GitLab CI에 pytest 단계 추가
```

커밋 템플릿 적용 (클론 후 1회):

```bash
git config commit.template .gitmessage
```

---

## 6. Merge Request

- target: 작업 브랜치는 **자기 기능별 develop 브랜치**(1장 표), `common` 작업 브랜치와 기능별 develop 브랜치는 `develop` (통합 데모 반영 시에만 `develop → master`)
- 최소 **1명 리뷰 승인** + **CI 통과** 후 머지
- 하루~이틀 분량으로 작게 올립니다. 기능 여러 개를 한 MR에 넣지 않습니다
- 작업 브랜치 MR은 **Squash merge**를 기본으로 합니다. MR 제목이 커밋 메시지가 되므로 커밋 규칙과 같은 형식으로 씁니다
- 작업 브랜치는 머지 후 삭제합니다. 기능별 develop 브랜치 → develop MR은 Merge commit으로 머지하고 소스 브랜치를 남깁니다 (4장)
- MR 작성 시 템플릿(`Default`)을 사용합니다

### Jira 자동 완료

이슈 작성 규칙은 [docs/jira-convention.md](docs/jira-convention.md)를 참고합니다.

GitLab–Jira 연동은 **develop(기본 브랜치)에 머지될 때만** 이슈를 자동으로 '완료'로 바꿉니다.
그래서 `Closes`는 develop으로 들어가는 MR에 씁니다.

| MR | 설명에 적을 것 |
| --- | --- |
| 작업 브랜치 → 기능별 develop 브랜치 | 이슈 키만 (`S15P31E110-56`) — Jira에 MR 링크가 남습니다 |
| 기능별 develop 브랜치 → develop | 이번에 끝난 이슈마다 `Closes {Jira키}` |
| `common` 작업 브랜치 → develop | `Closes {Jira키}` |

```text
Closes S15P31E110-56
Closes S15P31E110-57      # 여러 이슈를 함께 끝낼 때는 줄마다 하나씩
```

- 작업이 끝나지 않은 이슈는 `Closes` 없이 키만 적습니다.
- 브랜치·커밋·MR에 Jira 키가 들어가면 Jira 이슈의 개발 정보에 자동으로 연결됩니다.

### 공통 계약 변경

`docs/`의 OpenAPI 명세·이벤트/메트릭 스키마·에이전트 프로토콜 변경은 영향받는 파트에서 최소 1명씩 리뷰합니다.

| 변경 대상 | 리뷰 필요 파트 |
| --- | --- |
| OpenAPI 명세 | backend + frontend |
| 에이전트 프로토콜 | agent(platform) + backend |
| 이벤트·메트릭 스키마 | backend + frontend |
| 이미지 레이아웃 | platform + backend |

---

## 7. 통합 데모와 태그

매 5일차(5·10·15·20·25·30일) 통합 데모 직전에 `develop → master` MR을 머지하고 태그를 답니다.

```bash
git switch master
git pull origin master
git tag -a demo-day05 -m "5일차 통합 데모"
git push origin demo-day05
```

데모 중 문제가 생기면 직전 태그로 돌아가 시연합니다.

```bash
git switch --detach demo-day05
```

최종 발표 버전은 `v1.0.0` 태그를 답니다.

---

## 8. 금지 사항

- `master`, `develop`에 직접 push
- 다른 사람의 브랜치에 force push
- 비밀번호·토큰·키 파일 커밋 (`.env`는 `.gitignore`, 예시는 `.env.example`로)
- 빌드 산출물(이미지 파일, `node_modules`, `__pycache__` 등) 커밋
- 의미 없는 커밋 메시지 (`수정`, `ㅇㅇ`, `final`, `asdf`)
