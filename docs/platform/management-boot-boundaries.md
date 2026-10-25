# Xen·dom0·domU 부팅과 종료

이 문서는 전원이 들어온 뒤 플랫폼이 준비되고 VM이 자동 시작되는 과정과,
부분 실패·재시도·정상 종료 때의 책임을 설명한다. 개별 요청의 수명은
[관리 구조](management-boundaries.md), 정확한 명령·필드는
[CLI·Manager 명세](cli-manager-spec.md#contract-boot)를 따른다.

## 목차

- [부팅의 세 경계](#부팅의-세-경계)
- [플랫폼 서비스와 준비 의존성](#플랫폼-서비스와-준비-의존성)
- [최초 등록과 매 부팅 준비](#최초-등록과-매-부팅-준비)
- [자동 시작과 부분 실패](#자동-시작과-부분-실패)
- [VM 프로세스의 소유](#vm-프로세스의-소유)
- [종료와 재시작](#종료와-재시작)
- [시간 예산과 확인 방법](#시간-예산과-확인-방법)

## 부팅의 세 경계

1. Raspberry Pi 펌웨어·U-Boot가 Xen과 dom0 Linux를 적재한다. Xen 인자와 dom0 Linux
   인자는 별개다. 이미지 제작의 부팅 입력·DT·해시는 [빌드 가이드](build-guide.md)에서 고정한다.
2. dom0 Linux의 systemd가 저장소, 최초 등록, Xen 도구와 플랫폼 서비스를 준비한다.
3. 플랫폼이 VM별 부팅 정책과 현재 준비 상태를 확인하고 domU domain을 생성한다.
   domain 실행, guest OS의 IP/SSH 준비, 앱/Agent 준비는 같은 완료 조건이 아니다.

게스트 장애가 dom0의 진단·관리 경로를 불필요하게 막지 않도록, 모든 VM의 앱 준비를
부팅 서비스의 선행 조건으로 만들지 않는다. Wi-Fi·외부망/NTP 성공도 VM 부팅의 조건이 아니다.

## 플랫폼 서비스와 준비 의존성

| 서비스·자원 | 역할과 시작 조건 |
|---|---|
| `xenova-data-prepare.service` → `srv-xenova.mount` | 데이터 파티션 준비 후 `/srv/xenova` ext4 마운트 |
| `xenova-first-boot.service` | 마운트된 저장소에서 기존 factory 등록을 검증·재개. 새 신원은 최초 identity plan으로 확정 |
| `xenova-xen-ready.service` | Xen toolstack과 기본 dom0 상태 검사 |
| `xenova-cpu-prepare.service` | 최초 정책 게시/현재 boot의 dom0 affinity 적용·검증. 정책 미설정 설치본은 호환 모드 |
| 내부 bridge·`xenova-dhcp.service` | Linux/Network Starter가 쓰는 격리 내부망. networked VM의 실제 시작 경로에서도 검사 |
| `xenova-vm-shutdown.service` | backend/toolstack·저장소가 남아 있는 동안 guest를 정리할 독립 종료 guard |
| `xenova-vms.service` | 등록 VM들의 autostart 판정과 시작을 한 batch로 실행 |
| `xenova-host-admission.service` | autostart 뒤 이번 host의 admission barrier 초기화. 종료 시 새 시작·복구 차단 |

unit의 `After/Before`는 순서를 지정하고 `Requires/Wants`는 함께 시작할 의존성을
지정한다. ordering만으로 준비 성공을 보장하지 않는다. vms는 저장소·first-boot·Xen ready·
종료 guard를 Requires+After로 요구하고, CPU 준비는 Wants+After로 연결한다.
CPU 정책이 활성화되면 모든 수동/자동 start·reboot·recover_once가 현재 host 준비와
실제 배치를 다시 검사하므로, 준비 서비스의 active 표시만으로 시작을 허가하지 않는다.

CPU 준비 서비스에는 VM으로 중지 전파를 일으키는 PartOf/BindsTo나 ExecStop이 없다.
재시작이 실행 중 VM을 종료하거나 affinity를 초기화하지 않는다. 네트워크가 필요 없는
게스트와 진단을 외부 네트워크 준비 뒤로 일괄 직렬화하지 않는다.

## 최초 등록과 매 부팅 준비

최초 설치는 seed·카탈로그·identity plan으로 UUID/MAC와 자산을 확정한다. 중단 후에는
같은 plan을 재개한다. 서비스 재시도나 최신 코드 설치를 이유로 신원을 다시 발급하거나
기존 템플릿·자산·사용자 의도를 덮어쓰지 않는다.

매 부팅에는 새 host_boot_id로 실행·관측·CPU 준비 근거를 만든다. 이전 boot의 domid,
incarnation, monotonic deadline과 ready 기록은 현재 근거로 쓸 수 없다. 영구 자산·UUID·
autostart와 desired_state는 보존하고 기존 부팅 정책으로 실행 여부를 판정한다.
같은 host에서 명시한 stop을 서비스 재시작으로 뒤집지 않는다.

CPU 프리셋은 최초 identity plan에서 확정된 A/B UUID에 연결한다. 기존 plan에 활성화
항목을 소급 추가하지 않는다. 기존 설치의 활성화·복원은 [CPU 배치 절차](cpu-layout.md)를 따른다.

## 자동 시작과 부분 실패

autostart는 CLI 진입에서 만든 하나의 batch deadline을 registry·준비 검사·DHCP·모든
VM의 start에 전달한다. VM마다 새 전체 예산을 만들지 않는다. OS/앱 준비까지 기다리지 않는다.

`/run/xenova/autostart-results.json`은 VM 내부 이름별 결과다. 각 항목의 host와 deadline을
읽고 아래 상태를 구분한다. 정확한 필드는 [autostart 명세](cli-manager-spec.md#cmd-autostart)에 있다.

| 상태 | 의미·재시도 |
|---|---|
| running/blocked 등 관측 상태 | 이미 시작된 실행. 다른 VM의 실패 때문에 임의 롤백하지 않음 |
| skipped | 부팅 정책상 시작하지 않음. admission/정책 근거 확인 |
| not-started | 기한이 끝나 start를 제출하지 못한 VM. 이전 성공 VM과 구분 |
| failed | start 호출 경계의 실패. 생성 효과 없음으로 단정하지 않고 원 실행과 대조 |
| pending/starting | batch가 중단됐을 수 있음. 성공/부재로 꾸미지 않고 journal과 Xen 확인 |

서비스 재시도는 기존 실행에 대해 같은 공통 start의 신원·CPU 확인을 수행하므로
중복 생성하지 않는다. 원 control/recovery 요청의 key나 실행 기한을 재발급하는 동작은 아니다.
시작 실패는 unit 실패로 남긴다. 실패를 성공으로 분류하거나 광범위한 shutdown-all을
실패 후처리에 넣어 성공 VM을 정리하지 않는다.

## VM 프로세스의 소유

Xen domain과 dom0의 libxl monitor/guest qdisk는 다른 자원이다. 새 domain 생성은
`xenova-vm-<VM UUID>-<execution UUID>.scope`에서 실행하여 지속 프로세스를 시작 batch와
다른 cgroup에 둔다. scope 명령이 없거나 생성이 실패하면 직접 xl 실행으로 우회하지 않는다.
결과가 미확정이면 원 실행을 대조한다.

일시 xl 명령은 호출자 사망 때 PDEATHSIG로 종료되지만 fork된 지속 프로세스에는 그
설정이 상속되지 않는다. batch 실패·timeout에서 일시 프로세스 정리와 성공 VM의 지속
프로세스 보존을 구분한다. Linux toolstack reboot는 기존 monitor scope를 유지하고,
Zephyr 협조 reboot의 새 create는 새 실행 scope를 사용한다. 구형 실행은 자동 이동하지 않는다.

개별 VM stop은 먼저 신원과 domain 종료를 확인한 뒤 해당 실행의 소유 scope에 TERM을
보내 정상 정리를 확인한다. 다른 VM·공용 backend를 정리하지 않는다. 기한 후 제한된
재확인에서는 신호를 보내지 않는다. scope cleanup 실패를 자동 KILL/destroy로 숨기지 않는다.

## 종료와 재시작

동시에 stop되는 unit은 시작 ordering의 역순으로 종료된다.

1. host-admission의 host-quiesce가 새 시작·복구를 차단한다.
2. 정상 활성 vms unit의 ExecStop이 shutdown-all로 기존 VM별 종료 정책을 적용한다.
3. 독립 vm-shutdown guard가 guest 정리 경계를 유지한다.
4. 실행 scope·backend/toolstack·저장소의 후속 정리가 진행된다.

scope의 `Before=xenova-vm-shutdown.service`는 종료 때 guard가 먼저 실행되게 한다.
guard의 backend/toolstack/mount 의존성은 guest 정리 동안 필요한 기반을 유지한다.
개별 scope 정리는 stop job의 역순 대기를 피하도록 신호와 최종 상태 확인을 사용한다.

| 상황 | 종료 계약 |
|---|---|
| 시작 실패한 vms batch | 실패 oneshot에는 ExecStop이 실행되지 않음. 성공 실행 scope는 batch cleanup과 분리되고 독립 guard는 host 종료용으로 남음 |
| 정상 `systemctl stop/restart xenova-vms.service` | 제품 ExecStop의 shutdown-all이 실행됨. VM을 보존하는 단순 batch 중단 명령이 아님 |
| 개별 `xenova-vm stop` | 그 VM과 소유 실행 프로세스만 정리. 자산·UUID·CPU 예약 유지 |
| host 종료 | 전체 시작/복구 gate 차단 후 기존 guest별 graceful/refuse/force-allowed 정책과 종료 순서 적용 |

실패·기한 초과를 자동 force로 바꾸지 않는다. host 종료 guard 자체의 실패/timeout 뒤
systemd 종료는 계속될 수 있어 남은 backend가 정리될 수 있다. 이 경우까지 guest 보존을
보장하지 않는다. 단순 시험 batch의 stop 결과를 제품 서비스나 host 종료에 확대하지 않는다.

## 시간 예산과 확인 방법

| 서비스 | 바깥 unit 상한 | 내부 예산 |
|---|---|---|
| data prepare / first-boot | 각각 300초 | 최초 저장소·등록 검사. control의 90초와 별개 |
| 데이터 mount | mount 15초, job 330초 | 선행 준비와 mount를 구분 |
| Xen ready | 30초 | check-xen 전체 25초 |
| CPU prepare | 30초 | 기본 15초. factory 정책 검증부터 실제 적용까지 같은 잔여 기한 |
| DHCP | 15초 | service 준비와 호출자의 관리 deadline도 적용 |
| autostart | 150초 | batch 전체 140초 |
| vms/vm-shutdown 종료 | 각각 110초 | guest 정리 전체 90초 |
| admission 종료 | 10초 | 새 시작·복구 차단 |

unit 상한은 모든 선행 unit을 포함한 전원 부팅 상한이 아니다. fsync·커널 I/O를
엄밀하게 선점하는 보장도 아니다. 개별 호출의 상위 대기/반환 예산은
[시간 계약](cli-manager-spec.md#time-budgets)과 구분한다.

dom0에서 아래 읽기 명령으로 현재 상태·실제 ordering과 실패 로그를 확인한다.

```sh
cat /proc/sys/kernel/random/boot_id
systemctl is-active xenova-vms.service xenova-host-admission.service
systemctl show xenova-vms.service -p ActiveState -p Result -p After -p Requires -p ExecStop
journalctl -b -u xenova-first-boot.service -u xenova-cpu-prepare.service -u xenova-vms.service --no-pager
xenova-vm list --short
xenova-vm cpu-layout --json
```

실제 power-cycle·host 종료의 실패 경로와 제품 전체 자동 시작 수락은 별도다.
로컬 unit 시험과 특정 보드 결과의 근거는
[검증 이력](../verification/platform-management-boundaries-history.md)에 구분해 보존한다.
