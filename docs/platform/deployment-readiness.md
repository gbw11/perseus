# 플랫폼 배포 준비와 7번 인수

6-A/6-B의 배포 원본이다. 당시 기준 `8e3232a`는 이력으로 보존한다. Gateway 이력/실제 주행앱의
현재 후보는 platform `cdcb619` + `feat/platform/gateway-history-driving-app` diff/manifest다.
실제 실행 결과·파일 hash는 [최신 검증 보고](../verification/recovery-scenario-closure-20261030.md#history-drive-final)에 연결한다.
이번 작업은 운영 설치·서비스 교체·7번 수락을 실행하지 않는다.

2026-11-02 CLI·조회 후속은 위 제공물 위에 `f8a8663` + 현재 diff/새 파일로 식별한다.
현재 조회/원 기록 reader의 검증·패키지 근거는 [CLI·조회 마감](../verification/recovery-scenario-closure-20261030.md#cli-query-consistency)이다.
앞선 Gateway/firmware 제공 archive를 제자리 수정하거나 그 시험을 이번 모듈의 설치 증거로 쓰지 않는다.

## 제공 수준

담당자 현재 지시는 [요청 색인](../requests/README.md)에서 읽는다. 하위 create의 의미 비교·기한·소유·수습과
OS 공통 정적 CPU 배치는 이전 검증 범위를 유지한다. 기존 full HTTP 호출은 그대로 지원하고 optional 상위 DTO는 담당 제품 확장으로 남는다.
문서 준비, 계약 결정, 제품 구현, 패키지 검증, 실제 설치·종단 수락을 각각 기록한다.

<a id="cohort"></a>
## 배포 단위와 실제 설치 경로

아래 recipe는 `platform/yocto/meta-xenova/` 기준이다. 버전 번호가 같다는 이유로 소스 후보가 같다고 판단하지 않는다.

| 구성·생산 원천 | recipe → 설치 경로 | 함께 확인할 소비자·호환 |
|---|---|---|
| 하위 Manager·CLI·Execution·creation/preparation·lifecycle·CPU·resources·관측·trace | `recipes-core/xenova-platform/xenova-platform_1.0.bb` → `/usr/lib/xenova/*.py`, `/usr/sbin/xenova-vm`, `/usr/sbin/xenova-domain-identity`, `/usr/libexec/xenova-domain-identity-native` | 같은 저장·잠금·owner를 쓰는 CLI, first-boot/CPU/admission/autostart, FastAPI가 import한 Manager, Worker·Bootstrap의 하위 호출. wrapper·native helper·Python 모듈을 같은 후보로 교체하고 장기 프로세스의 import를 확인 |
| 공용 Python MQTT | `services/vm-agent/mqtt-client/xenova_mqtt` → `recipes-core/xenova-mqtt-client` → target Python site-packages | Gateway·dom0-manager/Worker가 정식 envelope/binding/세션 계약을 공유. 개발 PC PYTHONPATH 없이 설치 경로에서 해석돼야 함. guest Agent의 `/opt` 설치는 별도 template 경로 |
| Gateway | `services/gateway/xenova_gateway` → 새 `recipes-core/xenova-gateway/xenova-gateway_0.1.0.bb` → Python site-packages, `/usr/lib/systemd/system/xenova-gateway.service` | 기존 `python3 -m xenova_gateway` 사용. pyproject·비활성 env 예시는 `/usr/share/xenova/`. unit은 disable preset, 활성 설정·Authority·재무장은 생성하지 않음 |
| Gateway client | 같은 recipe의 `xenova-gateway-client` subpackage → site-packages `xenova_gateway/{__init__,client,ipc_contract}.py` | 표준 라이브러리만 요구. 외부 client wire v1과 내부 Authority writer 배포는 별개. Worker 제품의 구현/재시작은 아직 미수행 |
| FastAPI·Worker | 기존 `recipes-core/xenova-dom0-manager` → site-packages의 `dom0_manager`, API·Worker unit | 제품 소스는 이번에 미변경. 기본 API/Worker RDEPENDS와 현재 환경 공급을 대조. 일반 full HTTP 호출자와 구형 내부 모듈을 계속 import하는 프로세스는 다른 호환 문제 |
| Linux Agent·guest MQTT | `services/vm-agent/agent/deploy/install_into_rootfs.py` → guest `/opt/xenova/agent`, `/opt/xenova/mqtt-client`, Agent unit | Agent 담당이 후보·의존성·smoke를 제공. `guests/common/guest_agent_template.py:prepare`가 다음 template에 포함하고 receipt 기록. 일반 `prepare_instance`와 seed `prepare_deployment_seed`의 identity/drop-in을 구분 |

하위의 지원 journal 1.0/1.1/1.2 혼재 읽기와 CPU 정책 1.0/2.0 처리는 정식 계약의 범위다.
일반 호출자는 journal 버전을 선택·변환하거나 raw 파일을 해석하지 않는다.
구형 내부 writer와 새 모듈을 같은 저장소에서 혼합 실행하는 배포는 지원하지 않는다.
외부 HTTP/Spring 소비자까지 무조건 같은 commit으로 동시에 교체할 필요는 없다. 공개 CPU 버전/필드는 해당 소비자가 정식 계약대로 처리한다.

## 의존성과 포장

고정 layer는 [layers.lock](../../platform/yocto/layers.lock)을 따른다. 현재 target은 Python 3.14.7,
Paho 2.1.0, pyserial 3.5다. 새 라이브러리 버전이나 pip 설치를 도입하지 않는다.
Gateway의 `RDEPENDS`는 실제 provider `python3-paho-mqtt`, `python3-pyserial`, `python3-protobuf`, 표준 모듈 분할 패키지와
`xenova-mqtt-client`, `xenova-platform`을 선언한다. Python의 logging.handlers/copy/queue/threading은 실제 pkgdata 및 target import로 대조한다.
README의 배포판 명칭 `python3-serial`과 Yocto recipe 이름 `python3-pyserial`을 혼동하지 않는다.

Gateway 소스는 site-packages에 설치하며 tests는 runtime payload에 넣지 않는다. 소유 fixture임을 표시한 실행 예제는 `/usr/share/xenova/gateway-examples`에 설치한다.
warm 진입점이 실제 UDS 수신부를 실행한다. unit은 RuntimeDirectory=xenova-gateway/0700, root·socket0600을 사용한다.
이 임시 디렉터리에는 socket만 두고 내구 Authority·원 요청 기록을 넣지 않는다. 두 번째 Gateway는 기존 lease/활성 endpoint를 침범하지 않는다.
기본 serial profile은 write-only다. feedback-1은 호환 firmware와 승인한 기한·차량 설정을 명시 공급해야 한다.
지도형 차량 입력을 쓰는 feedback-2 역시 capability·누적 encoder/장치 시계·보정값의 명시 공급이
필요하다. 구형 firmware에 새 프레임을 자동 전송하거나 package 설치만으로 선택하지 않는다.
서버·공용 MQTT·기존 Authority/lifecycle 모듈은 같은 내부 후보로 교체하고 client는 wire 계약으로 호환한다.
새 Authority optional 기록을 모르는 구형 내부 writer와 혼합 설치하거나 자동 downgrade하지 않는다.
현재 Worker unit은 root·AF_UNIX 허용이며 PrivateTmp/ProtectSystem을 사용한다. endpoint는 `/tmp`가 아닌 `/run/xenova-gateway`다.
수신부의 0700/0600·peer UID 조건과 container/namespace의 동일 pathname을 실제 설치본에서 확인한다.
Worker가 없어도 Gateway 보호는 진행하고 Gateway가 없으면 client는 유한 unavailable/unknown을 반환한다.
하위 플랫폼의 private 모듈은 `/usr/lib/xenova`에 설치하고 기존 entrypoint가 그 경로를 사용한다.
native identity helper는 기존 `${CC}`로 빌드하는 ABI 경계를 유지한다.
bringup 이미지에는 Gateway 패키지를 포함하도록 연결하지만, 이번에 전체 이미지/rootfs를 만들거나 서비스 enable을 실행하지 않는다.

### CLI·조회 모듈의 부분 교체

`xenova-platform`의 기존 SRC_URI/install 목록은 이번 변경의 `xenova_vm`, `xenova_management`,
`xenova_management_cli`, `xenova_execution`, `xenova_recovery`, `xenova_catalog`와 wrapper를 이미 포함한다.
신규 runtime 모듈·의존성·unit·설정·저장 형식을 추가하지 않아 recipe는 변경하지 않았다.
`intents`와 원 key CLI, 독립 probe/execution-context/templates/recovery-operation reader는 이 모듈 집합으로 제공한다.
같은 내부 helper를 import하는 프로세스는 기존 drain/import 교체 절차를 따른다. 외부 HTTP DTO의 동시 변경은 필수가 아니다.

고정 환경에서 이 recipe만 빌드해 do_install·package QA·정규 RPM 생성을 수행하고 변경 파일의
source→image→RPM payload 바이트와 Python0644/wrapper0755 모드를 대조했다.
target Python3.14.7/QEMU에서 설치 경로의 help·metadata 의도 조회·원 key 회수를 실행했다.
별도 native private mount namespace/chroot에서는 실제 `/usr/sbin/xenova-vm` wrapper를 빈 환경으로
실행했다. task 소유 fixture만 `/srv/xenova`와 `/run/xenova`에 보이게 했으며 host의 실제 경로는 조작하지 않았다.
QEMU smoke의 mount guard 대역과 native wrapper의 실제 fixture mount 검사는 보고서에서 구분한다.
이 결과는 전체 이미지·실제 Xen/보드·API 서비스 교체 수락이 아니다.

Worker는 `PlatformObserver._refresh_intents`의 live list 의도 취득을 `intents`로 연결하고,
FastAPI의 `SerializedManager.call/fork`, `_BUDGET_ACTIONS/call_with_budget`는 실제 소비 action의
독립 요청 객체·잔여 예산을 연결해야 한다. 해당 제품 코드는 이번에 수정하지 않았다.
CLI `status`를 자산/시각 종합 진단으로 쓰던 소비자는 `diagnose --details`로 전환한다.
새 경량 응답이나 과거 원 key 결과는 현재 VM 변경/복구의 승인 토큰이 아니다.

<a id="history-drive-install"></a>
### 실제 지도형 제공물과 고정 설치 검증

같은 Gateway recipe가 `apps/drive/world/python/xenova_drive_world`의 codec/generated schema를
site-packages에 설치한다. source tree의 PYTHONPATH로만 해석되는 상태를 제품 설치로 세지 않는다.
고정 target Python 3.14.7·Paho 2.1.0·protobuf 6.33.6(upb)·pyserial 3.5를 사용하며
큰 binary 수신 경계는 이 Paho 구현을 기준으로 검증한다. 기본 Gateway와 최소 client의
외부 wire 호환은 별개다. client subpackage가 server/Authority/Paho/protobuf를 요구하지 않는다.

`build-xenova-drive-app.py --app drive --profile warm-lattice-lab`은 고정 SDK의 실제
planner/tracker·nanopb를 ARM ELF에 링크한다. 같은 output에 `.config`·ELF/map·stack audit·
firmware 번들과 `BUILD_RECEIPT.json`을 남긴다. receipt의 schema/primitive/source hash,
AUTO stateful/MANUAL stateless와 실제 16MiB 배치를 대조한다. Nanopb zlib와 Zephyr 라이선스를
번들에 포함한다. 합성 `lattice-lab-1`은 운영 차량값이 아니다. A/B는 이 번들을 공유하되
UUID·boot/execution/app instance·MAC·계정·승인 CPU 배치는 각각 유지한다.

world 입력은 root provisioning credential의 선택 `world` 객체에서 다섯 평면 Bootstrap 필드로
전달한다. Pi/Jetson 주소·계정은 독립이다. 일부만 공급하거나 실행 중 다른 source/credential로
바꾸면 무효이며 앱 instance 교체만으로 조용히 새 broker를 채택하지 않는다.
Gateway의 `--drive-link`, `--world-trust`, `--odometry-config`, `--history-policy`는 비활성
env example의 선택 입력이다. 파일/차량/시간 승인값은 설치 담당이 별도로 공급한다.
없거나 잘못된 mapper trust는 AUTO만 제한하며 일반 VM 관리·정상 MANUAL에 전역 gate를 추가하지 않는다.

이 후속에서 정규 Gateway/runtime RPM 생성·package QA, RPM payload 바이트/0644 모드·
비활성 unit/preset과 ARM Python/QEMU installed import를 대조한다. 최소 client 설치와
설치된 client→실제 UDS→설치 Gateway 코드의 submit/get/cancel도 구분해 검증한다.
전체 이미지 부팅·Zephyr ARM 실행·실제 Xen/USB/장치·운영 enable을 의미하지 않는다.
각 최종 해시·명령·직접 수행/미수행은 [동일 후보 보고](../verification/recovery-scenario-closure-20261030.md#history-driving-app)가 원본이다.

## 설정 공급과 기능별 준비

| 입력·공급 담당 | 실제 원천·권한 | 없거나 실패할 때 |
|---|---|---|
| 생성 시간 정책 / 플랫폼·운영 승인 | `/etc/xenova/creation-policy.json`을 승인값으로 별도 공급. `/usr/share/xenova/creation-policy.json.example`의 null은 활성값이 아님 | 신규 Linux create 운영 수락 대기. 기존 완료 회수·지원 수습·조회/stop까지 전역 차단하지 않음. 승인 없는 fallback 숫자 없음 |
| CPU / 플랫폼·운영 | `/etc/xenova/cpu-policy-preset.json`은 최초 공급, 현재 `/srv/xenova` 정책·anchor와 이번 host receipt는 기존 CPU 경로가 소유 | empty placements도 정상 현재 정책. 과거 factory가 현재 해제를 덮지 않음. 준비 오류는 새 start/recover 허가를 제한하고 정확한 기존 실행 stop은 별도 guard |
| Gateway / 플랫폼·Gateway·차량 | `/usr/share/xenova/gateway.env.example`에서 승인한 설정만 root:root 0600 `/etc/xenova/gateway.env`로 별도 공급. 실제 UUID 쌍·profile·broker/ACL·device·root 소유 차량 한계 필요 | 필수 env 부재는 unit 시작 실패. 미승인 예시를 active로 복사하지 않음. 해당 차량 기능만 대기 |
| Authority·binding / Gateway·신원 공급 | 기존 `/srv/xenova/drive-control`, `/run/xenova/drive-bindings.json`, 단일 Gateway lease와 현재 host 검증 | package hook의 initialize/backup 복원/자동 이동 없음. 설치·프로세스 생존이 재무장·출력 허가가 아님 |
| MQTT·네트워크 / 플랫폼·계정 운영 | 기존 `xenova-mqtt-broker` 계정/ACL 세대, 보호된 env의 credential. API token과 broker 계정은 별도 | 익명·`#` ACL·전역 raw 수집·bridge 추가로 우회하지 않음. broker 준비와 각 제품 인증/수신은 별도 |
| Worker·Agent / 제품 담당 | 기존 unit 환경/override와 runtime 경로, Agent별 identity·필수 앱 제공물 | `/run`의 host 재부팅 보존 한계·episode 정책을 영속 이관/SQLite/새 상수로 바꾸지 않음 |
| 콘솔·진단 / 플랫폼·각 제품 | 기존 optional recorder·trace와 공개 CLI/PTY, 각 제품 logging 설정 | 선택 진단과 필수 원장·Authority·receipt를 분리. 인증/console lease·필수 프로토콜/원 PTY를 보존 |

## unit과 backend 수명

부팅 원본은 [부팅·종료 책임](management-boot-boundaries.md)이다.
storage 준비→mount→first-boot/Xen ready→CPU 준비·autostart→host admission의 기존 흐름을 유지한다.
MQTT·외부망·차량 준비가 모든 VM 시작/조회/stop의 전역 readiness가 되지 않는다.

새 Gateway unit은 root·UMask=0077, 기존 env 파일과 entrypoint, `Restart=no`를 사용한다.
mount는 Requires, first-boot/MQTT/autostart는 After, admission은 Before로 연결한다.
After는 준비 성공/인증 ACK가 아니다. 프로세스는 실제 Authority/binding·연결 계약으로 검증한다.
shutdown에서는 admission 차단→Gateway 종료→autostart의 guest 정리→독립 shutdown guard→mount 후속 순서다.
운영 재시작/재무장 정책을 package에서 새로 결정하지 않았다.

기존 `xenova_guest_process.py:scope_command/command`는 real runner에서 VM/execution UUID scope를 만들고
`Before=xenova-vm-shutdown.service`로 backend 수명을 batch와 분리한다. scope 실패는 직접 xl fallback으로 숨기지 않는다.
FastAPI가 실제 Platform 객체를 호출할 때도 이 경계를 사용하지만, 설치 API cgroup·kernel/systemd·libxl의 실제 귀속은 7번 공동 수락 전이다.
API unit의 KillMode 완화나 운영 backend 이동을 이번에 수행하지 않는다.
batch 일부 실패는 성공 VM scope를 죽이지 않고, 개별 stop은 신뢰한 domain 부재 뒤 자기 scope만 정리한다.
host drain은 수락 전 lifecycle lease·활성 owner·상속 FD/helper·잔존 자원을 포함한다. lock 해제나 timeout만으로 종료/unmount/예약 반환을 확정하지 않는다.

<a id="replacement"></a>
## 설치·교체·복구 절차

다음은 운영 실행 전 검토할 절차이며 이번에 실행한 명령이 아니다.

1. 배포 후보 manifest/RPM hash, 현재 module `__file__`, wrapper/native 경로·모드, unit/유효 설정과 그 파일을 읽고 쓰는 프로세스를 확인한다. 비밀 내용은 수집하지 않는다.
2. 기존 host gate의 지원 경로로 새 하위 변경 수락을 막고 원 owner/helper를 drain한다. 원 operation·자산을 삭제하지 않는다. gate는 조회/stop·외부 소비자 자체의 중지와 다르며, 관계없는 guest를 일괄 정지시키지 않는다.
3. 실제 API backend cgroup/scope 수명 선행조건을 확인한다. 미확인 상태에서 서비스 재시작이 안전하다고 선언하지 않는다. API/Worker 담당과 장기 import 종료·교체 시점을 합의한다.
4. 위 내부 cohort의 Python·native/helper·wrapper를 일관된 묶음으로 교체하고 해당 프로세스의 새 import 경로·hash를 확인한다. env/정책/credential·데이터는 패키지 갱신과 별도 공급한다.
5. 승인한 소유 fixture로 기존 full 호출·과거/최근 원 결과 회수·선택 생략과 CPU 공개 조회를 smoke한다. 운영 생성·삭제·재무장을 rollout 승인 없이 실행하지 않는다.
6. 실패하면 중단 phase·원 기록·잔존 자원과 지원 재확인 경로를 남긴다. 옛 binary가 새 저장 형식을 읽는지 확인하지 않고 downgrade하지 않는다. 소스 원복과 데이터 복구는 별도 판단이다.

hash/import 검사는 설치·시험 경계에서 수행한다. 매 요청 전체 설치 검증이나 범용 updater/capability 서비스를 추가하지 않는다.

<a id="decisions"></a>
## 공동 결정과 실제 대기 범위

| 결정·담당 | 필요한 제공물 | 이것 때문에 대기하는 기능 |
|---|---|---|
| Worker–Gateway / Worker 소비 담당 | 제공된 client/UDS와 Engine 원 attempt 저장·조회·현재 Manager guard·finish/cancel 연결 | 실제 Worker 보호→복구→새 binding→종결의 제품/설치본 수락. 전송 미구현 대기는 해소 |
| Agent/앱·Worker·계약 담당 | 필수 업무 완료·idle·서비스 수명·로컬 복구 wire | 해당 서비스 건강·승격 판정 |
| 플랫폼·운영 | 지원 템플릿/SD 측정에 따른 승인 create 시간 정책 | 신규 Linux create의 운영 수락 |
| FastAPI·Spring | 실제 인증·원 요청 소유·허용 결과를 갖춘 명시 수습 공개 경로 | 제품 UI/HTTP 수습 노출 |
| Gateway·장치·차량 | 실측 차량 한계·장치 적용/정지 피드백·연속성 계약 | 실제 주행·물리 정지·연속 승계 |
| 플랫폼·API / Agent 제공 담당 | API backend 귀속 수락 / Agent L05 후보·의존성·smoke | 실제 API 교체 / 해당 다음 guest template 제품 수락 |

이 미정은 기본 조회·정지와 무관한 문서·포장 작업을 막지 않는다. 새 topic/endpoint/운영 수치를 확정된 계약처럼 만들지 않는다.

<a id="stage7"></a>
## 7번 최소 입력표 — 미실행

보드 FastAPI 직접 호출과 EC2/Spring/화면은 별도 시작점으로 수락한다.
fixture의 실제 UUID/template/version/key·기존 예산·관측 지점과 정리 담당을 승인한 뒤 실행한다.

| 기존 ID·담당·시작점 | 입력/관측·정상 진행 | 실패·교차 / 정리 책임 |
|---|---|---|
| D02·D08·L05 / 플랫폼·API·Linux / CLI 및 보드 API | Debian/Ubuntu/Zephyr의 `xenova-vm create/get/start/stop/delete/restore/purge` 지원 입력은 CLI 계약. API의 기존 POST/GET/DELETE `/vms` 계열은 VM HTTP 계약. 등록/현재 domain·구성/실측을 대조 | Agent 부재·OS별 협조 실패·backend/disk/vif. 플랫폼은 소유 VM fixture, API는 원 요청 추적을 정리하며 자동 force/purge 없음 |
| D02·D08 / API·Spring·화면 / 각 공개 시작점 | full HTTP 201와 기존 실행 중 POST 409, 선택 optional DTO, 같은 원 자원값 양방향 교차 | 다른 값·null·기본값 변경·현재 구성 변경. 원 기록/UUID/기한 보존, 승인한 fixture만 명시 수습 |
| S3/S4/P55·D02 / 플랫폼·API | A 긴 create/restore/firmware·usage·DHCP + B get/list/stop. 수락·실제 종료·원 결과 저장 각각 시각/횟수 관측 | 실제 상위 mutex/threadpool·polling·오류/만료 후 회복. barrier 해제와 원 owner 종료는 시험 소유자가 책임 |
| S5·D02·D03 / 플랫폼·API·Worker | `operation`·`control-operation`, 공개 GET `/vms/{vm_id}/control-operations?request_key=...`, 명시 `operation-reconcile`는 하위 계약과 사용자 승인 | 응답 유실·fsync 미확정·잔존 helper·무권한·늦은 결과. 자동 삭제/새 key 우회 없이 원 이행·명시 수습 후 별도 신규 생성 |
| C-T·E-T / 플랫폼 / CLI | `cpu-layout/cpu-policy-set/cpu-prepare`, 각 정식 spec-file. pin/unpin·정지 vCPU 수 변경·재부팅 후 실제 전체 vCPU 확인 | empty placements+factory 이력·Linux 부하·live 변경 거절. 정책/affinity 변경 fixture는 사전 승인과 정식 반환 절차 필요 |
| D01-W/L·D03 / 각 제품 | 유효 관측→허용 행동→같은 원 복구→receipt/binding→Standby 합류 | 첫 무수신·Agent/앱 분리·공통 기반 단절·user stop·누적 예산. 각 owner의 원 pending/사건 보존 |
| D02·L05·S3 / 플랫폼·API / 설치본 | RPM payload와 실제 import, API 정상 재시작 중 Linux disk/network 유지 | batch 일부 실패·host shutdown·drain 실패. guest/backend/서비스 작업은 별도 공동 승인으로만 실행 |
| DEC/ARCH·D03-G / Gateway·입력·장치 | 승인한 profile/세션·전환/재무장과 실제 장치 적용을 각 단계에서 관측 | 새 generation의 가짜 진행·옛 입력/백업 복원·미승인 재무장·미적용. 실제 STOP/장치 안전 종료는 해당 owner 책임 |

현장에서 DB/env/권한·기본 VM을 임의 변경해 통과시키지 않는다. 시간·횟수의 기준은 기존 계약/승인 시험 계획을 사용한다.
