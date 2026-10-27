# 담당자 요청 문서

다른 담당자 영역에 필요한 작업을 정리해 둔 곳입니다. 요청한 사람은 그 영역의 파일을 직접 고치지 않고, 여기 문서와 Mattermost로 넘깁니다.

- 파일 이름: `YYYY-MM-DD-<주제>-<대상 part>.md`. 대상 담당자가 그 작업을 시작할 때 처음 읽는다고 가정하고 씁니다.
- 처리되면 문서 맨 위에 `✅ 처리됨 (날짜, MR/커밋)`을 적습니다. 지우지 않습니다.

| 문서 | 대상 | 요청 | 상태 |
| --- | --- | --- | --- |
| [2026-10-20-zephyr-drive-network-platform.md](2026-10-20-zephyr-drive-network-platform.md) | 박신영·주소정 (platform) | Zephyr 게스트 vchan 연결 지원 (D3 = vchan + dom0 중계로 결정) | 대기 |
| [2026-10-20-manual-app-gateway-ctrl.md](2026-10-20-manual-app-gateway-ctrl.md) | ctrl, 복구 서비스 | 기존 D8·원 요청 보존. D01 per-VM 전달·건강 분류·복구/재합류 근거 W01~W05 누적. Gateway는 공병우 소유 | D01 요청 작성, 담당자 구현·수락 대기 |
| [2026-10-20-manual-app-ci-deploy.md](2026-10-20-manual-app-ci-deploy.md) | 정수환 (deploy) | CI에 앱 테스트 단계 추가 | 대기 |
| [2026-10-22-recovery-worker-platform.md](2026-10-22-recovery-worker-platform.md) | platform 파트 | Zephyr 템플릿 capability `app_heartbeat` 펌웨어별 구분, Xen 관측 없는 의도 조회, Recovery Worker 패키징 (S15P31E110-279) | 대기 |
| [플랫폼 연동 누적 요청](../platform/management-integration-request.md) | FastAPI·Spring·화면 / Linux 엔지니어·Agent | D02·D08 기존 연동 요청 및 D01-L01~L05 자원·앱/Agent 진행·감독·배포 요청 | D02·D08 하위 완료, 상위·Linux 연결 미구현 |
