# 담당자 요청 문서

다른 담당자 영역에 필요한 작업을 정리해 둔 곳입니다. 요청한 사람은 그 영역의 파일을 직접 고치지 않고, 여기 문서와 Mattermost로 넘깁니다.

- 파일 이름: `YYYY-MM-DD-<주제>-<대상 part>.md`. 대상 담당자가 그 작업을 시작할 때 처음 읽는다고 가정하고 씁니다.
- 처리되면 문서 맨 위에 `✅ 처리됨 (날짜, MR/커밋)`을 적습니다. 지우지 않습니다.

## 현재 담당자에게 전달할 요청서

아래에서 담당 문서 하나를 고른 뒤, 그 문서 앞의 **현재 할 일 → 함께 읽을 파일과 순서**를
따릅니다. 공통 정의는 연결된 계약 문서에 두고, 담당자 요청과 과거 이력을 구분합니다.
문서 분리·요청서 작성은 담당자 구현 완료를 뜻하지 않습니다.

| 문서 | 대상 | 현재 할 일 | 상태 |
| --- | --- | --- | --- |
| [Linux·Agent 건강 연결](2026-10-27-linux-health-agent.md) | Linux 엔지니어·Agent | D01-L01~L05: 자원·서비스/앱 진행·Agent 전달·로컬 감독·배포 | 요청 작성·플랫폼 근거 제공 완료, 담당자 구현·연동·실보드 수락 전 |
| [Worker 건강 분류·복구·재합류](2026-10-27-recovery-health-ctrl.md) | Recovery Worker·Engine(ctrl) | D01-W01~W05: 대상별 전달·건강 입력·행동·원 작업/예산·공동 수락 | 요청 작성·플랫폼 근거 제공 완료, 담당자 구현·수락 전 |
| [HTTP·Spring·VM 관리 화면 연동](../platform/management-integration-request.md) | FastAPI·Spring·화면 | D02·D08: 원 요청 key·pending/완료·시간 예산·후속 조회/이행 | 하위 구현 마감, 상위 연동 미구현·종단 수락 전 |

## 기존 요청·과거 이력

작성 당시의 요청 ID·담당·상태를 보존합니다. 현재 담당자 업무는 위 요청서를 먼저 읽고,
아래 문서는 원 요청의 배경과 남은 플랫폼 제공 사항을 확인할 때 참조합니다.

| 문서 | 대상 | 요청 | 상태 |
| --- | --- | --- | --- |
| [2026-10-20-zephyr-drive-network-platform.md](2026-10-20-zephyr-drive-network-platform.md) | 박신영·주소정 (platform) | Zephyr 게스트 vchan 연결 지원 (D3 = vchan + dom0 중계로 결정) | 대기 |
| [2026-10-20-manual-app-gateway-ctrl.md](2026-10-20-manual-app-gateway-ctrl.md) | ctrl, Gateway | S15P31E110-155·237·124, D7·D8의 원 요청·당시 상태. Gateway는 공병우 소유 | 과거 기록 보존. D01-W01~W05의 미완료 요청은 위 Worker 문서로 이동 |
| [2026-10-20-manual-app-ci-deploy.md](2026-10-20-manual-app-ci-deploy.md) | 정수환 (deploy) | CI에 앱 테스트 단계 추가 | 대기 |
| [2026-10-22-recovery-worker-platform.md](2026-10-22-recovery-worker-platform.md) | platform 파트 | Zephyr 템플릿 capability `app_heartbeat` 펌웨어별 구분, Xen 관측 없는 의도 조회, Recovery Worker 패키징 (S15P31E110-279) | 대기 |
