# 담당자 요청 문서

다른 담당자 영역에 필요한 작업을 정리해 둔 곳입니다. 명시적으로 합의한 최소 연결 수정은 각 요청서에 범위를 구분하고, 그 밖의 작업은 담당자에게 넘깁니다. 문서 작성과 실제 전달·담당자 구현은 구분합니다.

- 파일 이름: `YYYY-MM-DD-<주제>-<대상 part>.md`. 대상 담당자가 그 작업을 시작할 때 처음 읽는다고 가정하고 씁니다.
- 처리되면 문서 맨 위에 `✅ 처리됨 (날짜, MR/커밋)`을 적습니다. 지우지 않습니다.

## 현재 담당자에게 전달할 요청서

2026-11-01 6-A에서 HTTP·Worker·Linux·Gateway의 현재 본문과 이력을 분리했다. 각 본문에는 수정 이유/위치·하위 제공물·정상/실패 수락·선행조건·담당 회신이 있다. [배포 준비·공동 결정·7번 입력표](../platform/deployment-readiness.md)는 플랫폼 원본이며 실제 배포나 담당 제품 완료를 뜻하지 않는다.

2026-10-29의 주행·복구 확장 요청은 아래 `20261029-request-*` 문서에서 시작합니다. 각 문서의 **현재 할 일 → 함께 읽을 파일 → 최소 수정과 나머지 작업** 순서로 읽습니다. 기존 D01·D02·D03·D08 요청의 ID·상세 조건·미완료 상태는 아래 원문에 보존하며, 새 문서가 완료 상태를 대신하지 않습니다.

| 현재 요청서 | 담당 | 이번 최소 연결 밖의 주요 작업 |
|---|---|---|
| [Gateway 직접 구현·공동 연동](20261029-request-gateway.md) | 박신영 / 플랫폼·Gateway | 직접 구현의 후속 계획과 ESP32·입력·Worker 공동 연결. 타 담당자에게 Gateway 전체를 위임하는 요청이 아님 |
| [Recovery Worker](20261029-request-recovery-worker.md) | Worker·Engine | D01 건강 분류·episode 회복, D03 원 결과 수습·전체 복구 연동 |
| [Linux·Agent](20261029-request-linux-agent.md) | Linux 엔지니어·Agent | D01-L01~L05 자원·서비스 진행·로컬 감독·설치 |
| [HTTP·Spring·화면](20261029-request-http-spring-ui.md) | FastAPI·Spring·프론트 | Linux 준비 중 조회/실제 정지 연결, 원 key·pending 종단 연결, 수동 입력 세션·명시적 모드/복귀 |
| [Jetson](20261029-request-jetson.md) | 인지·지도·영상 | 실제 센서·지도/위치·바이너리 snapshot·보존/복원·영상 분리 |
| [ESP32·차량](20261029-request-esp32.md) | 장치·차량 제어 | 실제 피드백·적용/정지 확인·차량 보정·출력 연속성 |

후속 변경은 해당 문서의 현재 작업 카드를 개정하고, 바뀐 요구와 당시 결과는 이력에 옮깁니다. 이미 있는 요청 ID를 재발급하거나 같은 상세 계약을 여러 문서에 복제하지 않습니다. 아래 표는 원 요청의 상세 내용과 이력을 찾는 색인입니다.

아래에서 담당 문서 하나를 고른 뒤, 그 문서 앞의 **현재 할 일 → 함께 읽을 파일과 순서**를
따릅니다. 공통 정의는 연결된 계약 문서에 두고, 담당자 요청과 과거 이력을 구분합니다.
문서 분리·요청서 작성은 담당자 구현 완료를 뜻하지 않습니다.

| 문서 | 대상 | 현재 할 일 | 상태 |
| --- | --- | --- | --- |
| [Linux·Agent 건강 연결](2026-10-27-linux-health-agent.md) | Linux 엔지니어·Agent | D01-L01~L05: 자원·서비스/앱 진행·Agent 전달·로컬 감독·배포 | 요청 작성·플랫폼 근거 제공 완료, 담당자 구현·연동·실보드 수락 전 |
| [Worker 건강 분류·복구·재합류](2026-10-27-recovery-health-ctrl.md) | Recovery Worker·Engine(ctrl) | D01-W01~W05 및 D03-W01~W04: 건강 입력·행동·원 결과 재확인·receipt 재사용·준비 연결 | 하위 제공물 준비, 담당자 구현·수락 전. D01 대기 유지 |
| [Gateway Authority 수습](2026-10-27-authority-recovery-ctrl.md) | 박신영 / 플랫폼·Gateway | D03-G01~G04: 원본/백업 최신성·단일 소유·제한적 복원·재무장·저장 실패 | 기존 수습 과제 보존, 직접 구현·운영/장치 수락 전 |
| [HTTP·Spring·VM 관리 화면 연동](../platform/management-integration-request.md) | FastAPI·Spring·화면 | D02·D08: 원 요청 key·pending/완료·시간 예산·후속 조회/이행 | 하위 구현 마감, 상위 연동 미구현·종단 수락 전 |

## 담당 회신 양식

현재 작업 카드의 기존 ID마다 아래를 채운다. 이번 문서화에서 담당자 회신이나 제품 구현을 수집·완료 처리하지 않았다.

| 회신 항목 | 담당자 작성 |
|---|---|
| 후보·수정 위치 | commit+diff/새 파일, 실제 파일·기호 |
| 정상·실패·회복 | 실행 명령/입력·설정 원천, 실제 assertion·결과, 오류 뒤 정상 진행 |
| 제공물·미결정 | package/hash/entrypoint/의존성/unit/권한, 결정 담당과 그 결정이 막는 기능 |
| 설치·종단 상태 | 문서 준비 / 로컬 구현·시험 / 패키지 / 실제 설치 / 제품·보드 수락을 각각 표시 |
| 미수행 | 필요한 환경·제공물, 이유, 다음 담당/인수 조건 |

## 기존 요청·과거 이력

작성 당시의 요청 ID·담당·상태를 보존합니다. 현재 담당자 업무는 위 요청서를 먼저 읽고,
아래 문서는 원 요청의 배경과 남은 플랫폼 제공 사항을 확인할 때 참조합니다.

| 문서 | 대상 | 요청 | 상태 |
| --- | --- | --- | --- |
| [2026-10-20-zephyr-drive-network-platform.md](2026-10-20-zephyr-drive-network-platform.md) | 박신영·주소정 (platform) | Zephyr 게스트 vchan 연결 지원 (D3 = vchan + dom0 중계로 결정) | 대기 |
| [2026-10-20-manual-app-gateway-ctrl.md](2026-10-20-manual-app-gateway-ctrl.md) | ctrl, Gateway | S15P31E110-155·237·124, D7·D8의 원 요청·당시 상태. Gateway는 공병우 소유 | 과거 기록 보존. D01-W01~W05의 미완료 요청은 위 Worker 문서로 이동 |
| [2026-10-20-manual-app-ci-deploy.md](2026-10-20-manual-app-ci-deploy.md) | 정수환 (deploy) | CI에 앱 테스트 단계 추가 | 대기 |
| [2026-10-22-recovery-worker-platform.md](2026-10-22-recovery-worker-platform.md) | platform 파트 | Zephyr 템플릿 capability `app_heartbeat` 펌웨어별 구분, Xen 관측 없는 의도 조회, Recovery Worker 패키징 (S15P31E110-279) | 대기 |
