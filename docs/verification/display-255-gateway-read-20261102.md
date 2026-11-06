# 255 주행 상태 읽기·표시 연결 — 2026-11-02

## 결과와 범위

### 최신 Git과 오래된 실보드의 구분

사용자가 보드는 최신 배포본이 아니므로 Git 기준으로 판단하도록 정정했다. 이후 최신 platform **01ca838**을 fetch해 다시 조사했다. 아래 보드 점검은 당시 배포 상태의 관측 이력이며, 최신 소스의 기능 공백이나 새 송신 기능 개발 요구로 사용하지 않는다.

- GatewayProvider.context와 GatewayClient.context는 최신 platform에도 있다. 기준 e82fa66 이후 해당 Gateway runtime·display 소스 차이는 없다.
- __main__은 warm 프로필에서 기존 GatewayServer를 기동한다. gateway.env.example에 warm 및 /run/xenova-gateway/control.sock이 있다. verified/legacy만 기동해서는 이 UDS를 제공하지 않는다.
- Yocto 레시피는 client 전용 RPM을 분리하고 서버의 의존성으로 포함한다. 기존 이미지 레시피도 xenova-gateway를 설치 대상으로 포함한다. 실제 이미지 빌드·배포 결과와는 구분한다.
- systemd unit과 환경 예시는 준비돼 있지만 SYSTEMD_AUTO_ENABLE=disable이며, 운영 UUID·인증·권위·차량 설정이 필요한 점은 그대로다. 소스가 준비됐다고 현재 보드에서 실행 중이라고 주장하지 않는다.

따라서 **최신 Git 기준 Gateway 제공 기능은 준비돼 있고, 이번 미커밋 표시 수신·UI 구현을 같은 배포에 연결하는 단계**다. 별도 표시 MQTT 송신·ACL 구현이나 Gateway 상태 결정 기능의 재구현은 필요하지 않다. 실제 수신 증거는 최신 묶음 배포 후 별도로 남긴다.

사용자가 기존 Gateway 정보를 직접 읽고 선택된 자율/수동 모드가 미허가 상태이면 ‘구동 준비 중’으로 표시하도록 선택했다. platform e82fa66에서 feat/platform/S15P31E110-255-drive-state-integration 브랜치를 분리해 구현했다.

**로컬 연결 구현·검증은 수행했지만 실제 보드 Gateway 수신은 완료하지 못했다.** 읽기 전용 보드 점검에서 기존 표시 서비스는 active이나 Gateway unit·패키지·UDS를 찾지 못했다. 따라서 새 수집기·프론트·설정을 보드에 덮어쓰거나 주행 Gateway를 설치/기동하지 않았다. 보드·VM·펌웨어·MQTT·Tailscale·표시 서비스 변경은 없다.

## 구현

- 기존 GatewayClient.context()만 단일 1초 poll로 읽는다. 기본 호출 budget 0.5초, 3초 관측 만료이며 실패는 즉시 미확인이다.
- 기존 client의 root UDS 권한·peer UID·host boot 검증을 재사용한다. Gateway 제어 요청·새 Xen 조회·MQTT 발행·Authority 직접 파일 읽기는 없다.
- 등록 두 VM·형식·generation 감소/동세대 선택 충돌을 검증한다. 신선도는 호출 시작 시각을 사용하며 오래 걸린 응답으로 유효기간을 늘리지 않는다.
- gateway 모드는 별도 표시 MQTT를 구독/수락하지 않는다. 기존 MQTT 기본 동작·Agent/앱 수신은 유지한다. 장애 시 다른 경로로 자동 전환하지 않는다.
- Gateway의 선택 VM·모드·phase·출력 허가·저장 미확정 정보를 authority에 분리한다. active_vm_id는 유효 binding·APPLIED·출력 허가가 확인된 AUTO/MANUAL에만 노출한다.
- 두 HDMI의 준비 중·정상 모드·미확인 표시를 연결했다. API의 출력 허가는 실제 차량 이동·장치 적용의 증명이 아니다. 기존 공개 REST/WS 인증을 유지한다.
- [연결 계약](../contracts/display-gateway-read-v1.md)과 환경 예시에 배포 전제·수집기/프론트 동시 배치·복원·기존 MQTT 이력 경계를 정리했다.

## 로컬 검증

- 관련 수집기 62개 검사 통과 후 최종 전체 수집기 **217개 통과·1개 skip**를 확인했다. skip은 WSL에 Chromium 실행 파일이 없는 기존 viewer stall 브라우저 시험이며 새 Gateway UDS 검사는 실제 실행해 통과했다. [JUnit](evidence/display-255-gateway-read-20261102/collector-tests.xml)에 기록한다. 프론트 화면 검사는 별도 Windows Chrome에서 수행했다.
- Gateway 전용 28개 통과: 선택/출력 허가, 독립 VM·모드 변경, 다른 host/UUID·잘못된 타입, 조회 budget, 만료·실패·복귀, 세대 역행/충돌, MQTT 혼입 차단, 인증된 REST/WS/presentation 전달, 유효 설정 검증.
- Linux 실제 UDS에서 기존 client의 wire request가 오직 version=1/action=context임을 확인했다. 서버 응답은 합성 context이며 Gateway 주행 엔진·실보드 수락 증거가 아니다. private 디렉터리 권한 위반 시 조회가 거부된다.
- 프론트 표시/모델 33개 통과, vm-screens 타입 검사 포함 production build, 변경 TypeScript ESLint·Prettier 통과.
- 실제 production build를 로컬 headless Chrome 1280×720에서 열어 준비 중·적용·저장 미확정 3가지 × 두 화면의 6개 화면을 확인했다. 문구·상단/하단·가로/세로 overflow를 검사하고 준비 중에는 현재 구동 VM1이 보이지 않음을 확인했다. 글꼴 로딩·두 RAF 후 캡처했으며 캡처에 LOCAL SYNTHETIC DATA를 표시했다.
- git diff --check와 새 문서 상대 링크·UTF-8·코드 fence를 확인한다. 이 문서·로컬 시험은 실제 주행 보고·HDMI 적용 완료로 해석하지 않는다.

## 보드 읽기 전용 조회

[표준 경로 점검](evidence/display-255-gateway-read-20261102/gateway-preflight-255-20261102.json):

| 항목 | 결과 |
| --- | --- |
| xenova-display-feed / kiosk / graphics | 모두 active |
| xenova-gateway.service | inactive, FragmentPath 비어 있음, MainPID=0 |
| /run/xenova-gateway/control.sock | 없음 |
| 기본 호스트 Python의 xenova_gateway 패키지 | 없음 |

[대체 경로 조회](evidence/display-255-gateway-read-20261102/gateway-alternate-255-20261102.json)에서도 gateway 이름의 loaded unit/unit file, Gateway/control.sock listener, /opt·/run·/etc/xenova 바로 아래 Gateway 후보 디렉터리를 찾지 못했다. 임의 깊이의 모든 사용자 배치를 조사한 것은 아니므로 ‘다른 이름의 사용자 프로세스가 절대로 없다’고 주장하지 않는다. 다른 배포 경로가 있다면 담당이 제공한 정식 socket/client 경로로 확인한다.

두 SSH 확인 세션은 종료했다. 인증정보를 증거에 저장하지 않았다. 보드에 시험 프로세스·서비스·환경 파일을 새로 만들지 않았으므로 원복할 변경도 없다.

## 남은 작업

1. Gateway 담당에게 **현재 보드의 provider UDS 배포 여부 또는 실제 경로**를 확인한다. Git의 제공 기능이 실행 중이라는 가정으로 배포를 강행하지 않는다.
2. 정식 Gateway/client가 준비되면 실제 VM pair·host boot·권한·응답을 읽기 전용으로 대조한다.
3. 새 수집기·web build와 명시적 gateway 설정을 함께 적용한다. 기존 인증·VM·주행 서비스는 보존한다.
4. 실제 보고에서 VM/모드·준비/출력 허가·조회 단절/복귀를 확인하고 HDMI·외부 API/WS 상태를 비교한다. 제어 변경이 필요한 시험은 담당 범위와 조율한다.

이 결과로 전체 255나 기존 251·253·257을 완료 처리하지 않는다. 이번 변경은 로컬 구현과 배포 준비이며 코드 커밋·푸시·MR·Jira 변경은 별도 요청 시 진행한다.

## 증거

- [로컬 6개 화면 요약](evidence/display-255-gateway-read-20261102/local-visual-summary.json)
- [계기판 준비 중](evidence/display-255-gateway-read-20261102/monitor-1-preparing.png) / [인포테인먼트 준비 중](evidence/display-255-gateway-read-20261102/monitor-2-preparing.png)
- 같은 evidence 폴더의 읽기 전용 점검 스크립트·시각 검증 스크립트·SHA256 목록. 점검용 SSH helper는 기존 board_prepare의 connect/execute와 공개 host-key pin만 분리해 보존한다. 비밀번호는 실행 시 입력하며 파일에 저장하지 않는다.
