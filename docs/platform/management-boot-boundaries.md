# Xen·dom0·domU 부팅과 종료

이 문서는 전원이 들어온 뒤 플랫폼이 준비되고 VM이 자동 시작되는 과정과,
부분 실패·재시도·정상 종료 때의 책임을 설명한다. 개별 요청의 수명은
[관리 구조](management-boundaries.md), 정확한 명령·필드는
[CLI·Manager 명세](cli-manager-spec.md#contract-boot)를 따른다.

## 목차

- [부팅의 세 경계](#부팅의-세-경계)
- [부팅 진행·지연·실패 표시](#부팅-진행지연실패-표시)
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

## 부팅 진행·지연·실패 표시

화면을 사용할 수 있게 된 뒤 `xenova-boot-status.service`가 systemd와 현재 부팅의
보조 진행 기록을 읽어 로그인 전 안내문을 갱신한다. VM을 시작·정지하거나 준비 여부를
판정하는 서비스가 아니다. 표시기 없이도 기존 부팅과 로그인이 진행된다. 커널·PID1 정지나 표시기 실행 전 문제를
이 서비스가 관측할 수 있다고 가정하지 않는다.

### 무엇을 표시하는가

| 표시 대상 | 실제 출처와 완료 의미 | 정보가 없을 때 |
|---|---|---|
| 저장소 준비·데이터 mount | 해당 unit의 실행 상태·Result·exit status. mount 완료와 저장소 준비 명령 완료를 구분 | 대기·선행 실패·관측 불가를 구분 |
| 최초 등록·기존 등록 검증 | first-boot의 실제 identity plan 판정 지점에서 게시한 모드와 unit 결과 | verification/resume으로 표시하며 최초 설치 여부를 추측하지 않음 |
| Xen 관리 기능 | `xenova-xen-ready.service`의 실제 check-xen 결과 | unit 실패 이유와 journal 조회 안내 |
| CPU 준비 | 같은 unit invocation이 게시한 정책 미설정 호환 모드 또는 준비 검증 완료 | service active만으로 현재 affinity 일치·전용성을 주장하지 않음 |
| 내부망 | bridge와 DHCP 각각의 unit 상태·필수 의존성. guest 연결을 대신 검증하지 않음 | 선행 실패·관측 불가·미설치를 구분 |
| VM 자동 시작 | 실제 정책 선택, 네트워크 준비, 공통 start, 필수 결과 저장 직후의 보조 기록 | 선택 전 분모 미확정. 중단된 진행은 결과 미확정 |
| host admission·관리 서비스 | 해당 unit 실행 상태. API·외부 서비스 기능 검사는 아님 | 미설치·미시작·조회 실패를 구분 |

Wi-Fi·NTP·MQTT·앱 신호는 추가 부팅 조건으로 만들지 않는다. **VM 실행 검증 완료,
guest OS 준비, 앱 준비는 다르다.** 이 표시기는 OS 준비를 관측하지 않고 앱 readiness
소비 경로도 연결하지 않는다. 화면에 각각 `unobserved`, `unconnected`로 명시한다.
앱 readiness를 연결할 때는 별도 소비자가 검증한 실행 신원과 근거를 제공해야 한다.

`complete`는 그 행에 정의된 사실의 완료다. `progress`는 실제 unit 또는 코드 단계의 진행,
`waiting`은 아직 시작되지 않은 상태다. 확인된 필수 선행 unit 실패는 `deferred`,
사용자 부팅 정책에 따른 VM 제외는 `excluded`다. 실행 결과를 확인하지 못하면
`uncertain` 또는 `unknown`이며 성공·부재로 바꾸지 않는다.

`delayed`는 unit 시작 뒤 **20초**가 지난 표시 주의 상태다. 실행 timeout이나 고장 판정이
아니며, 단조 시계의 실제 경과시간과 마지막 보조 기록 변경 시각을 보여준다.
기록 변화가 없다는 사실만으로 실제 작업이 멈췄다고 단정하지 않는다. 관측된 실패는
같은 부팅의 제한된 이력에 남겨, 재시도 성공과 과거 실패를 구분한다.

### HDMI·로그인·UART의 관계

dom0 Linux의 `console=hvc0`는 Xen PV 콘솔 경로이며 Xen의 `console=dtuart` 인자와
별개다. 기본 systemd의 오류 표시는 `/dev/console` 쪽이고, 이것만으로 HDMI
진행 화면이 생기지는 않는다. dom0의 부팅 인자 `loglevel=4`는 우선순위가 4보다 작은
`KERN_ERR(3)`까지의 초기 콘솔 필터다. 이후 서비스가 바꾼 현재 필터는
`cat /proc/sys/kernel/printk`로 확인한다. Xen이 UART에 직접 쓰는 경고에는 이 필터가
적용되지 않는다. 전체 커널·서비스 기록은 기존 journal에 남는다.
정상 U-Boot 경로는 `/chosen` 전체를 출력하지 않는다. `xenova_debug=1`일 때만
Xen·dom0의 bootargs 두 속성을 출력하며 firmware 난수 seed는 덤프하지 않는다.

HDMI의 텍스트 VT는 기존 `getty@tty1`과 agetty가 소유한다. 표시기는 VT를 직접 열거나
전환하지 않고 `/run/issue.d/90-xenova.issue`와 agetty의 reload trigger만 갱신한다.
agetty는 입력 전의 로그인 안내문만 다시 읽고, 사용자 이름의 첫 문자를 읽으면 갱신을
멈춘다. 암호 입력·로그인 셸에 별도 출력을 주입하지 않는다. 키 입력을 기다리는
새 부팅 장벽도 없다. SSH 명령 출력과 셸 초기화는 변경하지 않는다.

초기 VT의 한글 폰트 지원을 전제하지 않으므로 로그인 전 안내는 ASCII다. 상세 명령은
UTF-8 표시 이름을 보존한다. 한글 렌더러가 없는 VT에서 실제 한글 모양을 그리는
기능을 추가한 것은 아니다. hvc0의 기존 agetty에도 같은 평문 안내가 표시될 수 있다.
표시기는 ANSI 재출력·강제 VT 전환·PAM·계정·mount namespace를 변경하지 않는다.

별도로 설치한 kmscon이나 그래픽 키오스크는 자신의 화면을 계속 소유한다. 이 표시기는
그 화면을 덮거나 전환하지 않는다. 해당 렌더러에서 issue를 읽지 않으면 자동 안내가
보이지 않으며, 기존 로그인·SSH에서 아래 명령으로 조회한다. 실제 HDMI 인계 수락은
설치된 renderer·getty 구성과 물리 화면을 함께 확인해야 한다.

고정 getty는 sysinit 뒤에 시작한다. 따라서 그 이전 저장소 준비가 오래 걸리면 보조
진행 기록은 쌓여도 HDMI에는 getty 시작 후에 표시된다. 이 구간의 실시간 HDMI 진행은
현재 지원 범위가 아니며 hvc0의 기본 오류 경로를 사용한다. 표시기를 일찍 실행하는 것과
화면 renderer가 일찍 준비되는 것은 별개다.

`systemd.show_status=true`는 간결한 unit 시작·종료 상태 전체를, `error`는 실패 상태를
표시한다. `auto`는 평소 조용히 있다가 상당한 부팅 지연이 생기면 표시를 켠다.
현재 기본값은 `error`를 유지하며 이 선택은 journal 수집 수준과 다르다.
의미의 근거는 [systemd 문서](https://www.freedesktop.org/software/systemd/man/latest/systemd.html#systemd.show_status)와
[agetty 구현](https://github.com/util-linux/util-linux/blob/v2.41.5/term-utils/agetty.c)의
입력 전 issue 갱신 경계다.

### 자세히 확인하기

```bash
xenova-boot-status
xenova-boot-status --details
xenova-boot-status --json
journalctl -b -u xenova-vms.service --no-pager -n 60
```

기본 출력은 완료된 서비스를 한 줄로 묶고 진행 중인 실제 대상만 보여준다. 선택 전의
대상 수는 `selection unknown`으로 남기며 실패·지연·미확정은 생략하지 않는다.
`--details`는 원 단계·판정 근거·unit·전체 VM UUID와 journal 조회 명령을 보여준다.
요약의 `prepared for this boot`는 해당 부팅의 CPU 준비 기록을 확인했다는 뜻이며,
현재 affinity를 새로 조회한 결과는 `xenova-vm cpu-layout --json`으로 확인한다.

명령은 현재 unit 상태를 한 번 읽는다. 표시 파일을 수정하거나 Manager·Xen 전체 조회,
공통 실행 잠금, VM 제어를 수행하지 않는다. 예산·필드·종료 코드의 정식 정의는
[명령 명세](cli-manager-spec.md#cmd-boot-status)에 있다.

보조 파일은 host_boot_id·unit invocation ID·batch ID로 구분한다. 이전 boot나 이전
unit 실행의 성공을 현재 것으로 사용하지 않는다. `/run/xenova/boot-progress/`는
진행 사실, `boot-display.json`은 표시기가 본 요약과 최대 16개 실패 이력이다.
둘 다 관리 원장이나 영구 보관소가 아니다. 진행 게시 실패는 기존 필수 journal·결과
저장·JSON stdout을 바꾸지 않는다. 원인 문자열 전체나 인증 설정은 화면에 싣지 않는다.

표시 서비스는 5초 간격으로 최대 600초 동안 관측하고, 상태 변화에 안내문을 갱신한다.
지연의 경과시간만 바뀌면 최대 30초 간격으로 갱신한다. 종료 뒤에도 마지막 안내는
남지만 현재 상태의 보장이 아니므로 상세 명령으로 다시 읽는다. 실패 요약을 남기기
위해 로그인이나 정상 VM을 막지 않는다. 표시용 데이터가 불완전하면 그 한계를 표시한다.

표시만 중지하려면 `systemctl disable --now xenova-boot-status.service`를 사용한다.
기존 VM·부팅 unit·getty에는 중지 의존성이 없다. 안내 파일까지 없애려면 표시기를
중지한 뒤 **표시기 소유 파일** `/run/issue.d/90-xenova.issue`만 제거하고
`agetty --reload`를 실행한다. `/etc/issue`나 기존 로그인 설정은 건드리지 않는다.


배포 파일·의존성·설정 공급·내부 reader/writer 교체와 Gateway의 비활성 unit은 [배포 준비](deployment-readiness.md)를 따른다.

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
| `mosquitto.service` | 내부 bridge·검증된 데이터 마운트와 최초 계정이 준비되면 broker 시작. 외부 네트워크 연결·소비자 인증은 VM 시작 조건이 아님 |
| `tailscaled.service` | 검증된 `/srv/xenova`가 마운트된 뒤 원격 관리 데몬 시작. Tailnet 인증·인터넷 연결은 VM 시작 조건이 아님 |

unit의 `After/Before`는 순서를 지정하고 `Requires/Wants`는 함께 시작할 의존성을
지정한다. ordering만으로 준비 성공을 보장하지 않는다. vms는 저장소·first-boot·Xen ready·
종료 guard를 Requires+After로 요구하고, CPU 준비는 Wants+After로 연결한다.
CPU 정책이 활성화되면 모든 수동/자동 start·reboot·recover_once가 현재 host 준비와
실제 배치를 다시 검사하므로, 준비 서비스의 active 표시만으로 시작을 허가하지 않는다.

CPU 준비 서비스에는 VM으로 중지 전파를 일으키는 PartOf/BindsTo나 ExecStop이 없다.
재시작이 실행 중 VM을 종료하거나 affinity를 초기화하지 않는다. 네트워크가 필요 없는
게스트와 진단을 외부 네트워크 준비 뒤로 일괄 직렬화하지 않는다.

### MQTT의 저장소·부팅 경계

`xenova-mqtt-broker` 설정 패키지가 기존 `mosquitto.service`의 운영 설정과 enable을
소유한다. Mosquitto 단독 패키지는 계속 기본 disable 상태다. 전체 unit override로
외부 `network-online.target` 대기를 제외하며, 내부 bridge와 `/srv/xenova`만 요구한다.
VM·CPU 준비·admission unit은 broker에 새 성공 의존성을 갖지 않는다.

인증 자료는 공통 이미지에 넣지 않는다. 최초 계정이 없으면 broker 시작을 건너뛰고,
운영자가 [계정 초기화](cli-manager-spec.md#cmd-broker)를 수행한 뒤 서비스를 시작한다.
시작 전 검사는 실제 writable ext4 데이터 장치·계정 파일 권한·manifest를 확인한다.
검증 실패를 익명 접속이나 rootfs의 새 계정으로 대체하지 않는다.

Mosquitto listener는 localhost·내부 bridge에만 연다. EC2의 외부 접속은 보드의 기존
`tailscaled`가 Tailnet IP의 1883·8081을 localhost MQTT·FastAPI로 전달한다.
최초 인증 뒤 명시적으로 설정하는 [background Serve](cli-manager-spec.md#cmd-tailscale-serve)를
사용하며 PC·SSH 터널·추가 daemon에 의존하지 않는다. 표시 피드 8082와 VM 사설망은 공개하지 않는다.
broker 실행과 Agent/주행/Telemetry 소비자의 인증·실제 수신은 별도로 확인한다.
서비스 재시작은 저장된 current 계정 세대를 재사용하며 VM 신원·의도·CPU 정책을 바꾸지 않는다.
계정 작성은 xenova-broker 하나가 소유한다. 완성한 세대의 passwd·ACL을 고정 경로로 읽으며,
저장 완료와 SIGHUP 요청·실제 인증을 구분한다. 과거 세대와 중단 기록을 지워 최초 상태로
만들지 않는다. 기존 schema 1 또는 수동 배포 계정은 명시적인 전환 뒤 새 runtime을 시작한다.
Tailnet이 늦다는 이유로 broker를 재시작하거나 주소를 반복 대기하지 않는다.

### Tailscale의 저장소·부팅 경계

Tailscale은 외부 원격 관리 경로다. `tailscaled`의 `RequiresMountsFor`·`BindsTo`·`After`는
`srv-xenova.mount`에만 연결하며, 기존 VM·내부 bridge/DHCP·admission unit은 Tailscale을
요구하지 않는다. `network-online.target`이나 Tailnet 로그인 완료를 기다리지 않는다.
데이터 마운트가 중지될 때 데몬을 먼저 중지하되 기존 guest 종료 순서는 유지한다.

데몬의 `--statedir`와 `--state`는 `/srv/xenova/tailscale/`을 사용한다. 시작 전 검사에서
실제 writable ext4 mount의 장치 번호가 기존 data-prepare의 `/run/xenova/data-device`와
같은지 확인한다. 마운트 실패·다른 장치·심볼릭 링크·중단된 복원이 있으면 시작을 거절하며
rootfs에 대체 인증 상태를 만들지 않는다. 디렉터리는 root 0700, state 파일은 0600이다.
기본 `/var/lib/tailscale`용 `StateDirectory`는 해제하며 daemon restart는 저장된 설정을 재사용한다.
종료 후의 상위 `--cleanup`도 같은 저장 경로와 mount 검사를 거쳐 실행한다. 마운트가 없을 때
cleanup이 기본 rootfs 상태 디렉터리를 새로 만드는 경로는 사용하지 않는다.

미인증 데몬 실행, Tailnet 인증, 다른 장치에서의 SSH 성공은 서로 다른 확인 단계다.
인증 URL 발급은 운영자가 `tailscale up --accept-dns=false`를 실행할 때만 요청한다.
연결 단절 시에는 Tailscale 자체 재연결을 사용하며 별도 감시·인증 루프를 추가하지 않는다.
기존 OpenSSH 인증을 그대로 쓰고 Tailscale SSH·subnet router·exit node·Funnel을 자동 활성화하지 않는다.
MQTT·FastAPI의 background Serve 설정도 같은 장치 상태에 보존한다. Serve 연결이나 인증이
실패해도 broker·관리 API·guest 부팅을 기다리게 하거나 해당 서비스를 재시작하지 않는다.

VM factory UUID·실행 의도와 Tailscale 장치 키는 별개의 신원이다. 일반 재부팅·서비스
재시작과 **데이터 파티션을 실제로 보존한** 이미지 갱신은 상태를 재사용한다.
`xenova-first-install.img`를 SD 전체에 쓰면 데이터 파티션도 초기화되므로 보존되지 않는다.
이 경우 같은 물리 보드의 암호화 백업을 복원하거나 재인증한다. 이미지 파티션 배치와
factory seed·초기화/grow 정책은 이 통합으로 바꾸지 않는다.
사용법·백업/복원과 중단 복구는 [접속 명세](cli-manager-spec.md#cmd-tailscale)를 따른다.

## 최초 등록과 매 부팅 준비

일반 생성과 factory의 자격/등록-only 통지는 [공급 계약](../contracts/vm-provisioning-v1.md)을 따른다.
factory의 UUID 계획 뒤 대상 lease를 유지하고 해시/guest I/O 동안 공통 관리 잠금을 반환한다.
local 등록 완료 뒤 선택 로컬 FastAPI 후속을 시작하며 HTTP 응답을 부팅/다른 VM 제어의 선행조건으로 두지 않는다.
현재 first-boot는 complete 기회 소비 후 common/대상 FD를 반환하고 helper를 띄운다. helper는 최대 15초
API `/openapi.json` 준비를 확인한 뒤 UUID notify POST를 1회만 시도한다. API/Spring 부재로 first-boot 종료나
autostart가 기다리거나 다음 boot의 재송신 큐가 생기지 않는다. Spring 송신·토큰은 FastAPI가 소유한다.
[설정·교체·기동 경계](deployment-readiness.md#registration-api)를 따른다.
완료 boot/replay는 자동 통지 기회를 보충하지 않는다. broker는 첫 등록 뒤 기존 bridge/mount 조건으로 기동한다.

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

1. host-admission의 host-quiesce가 새 시작·복구·Linux 준비 채택을 차단하고, 공통 실행 잠금 밖에서 활성 준비를 drain한다.
2. 정상 활성 vms unit의 ExecStop이 shutdown-all로 기존 VM별 종료 정책을 적용한다.
3. 독립 vm-shutdown guard가 guest 정리 경계를 유지한다.
4. 실행 scope·backend/toolstack·저장소의 후속 정리가 진행된다.

scope의 `Before=xenova-vm-shutdown.service`는 종료 때 guard가 먼저 실행되게 한다.
guard의 backend/toolstack/mount 의존성은 guest 정리 동안 필요한 기반을 유지한다.
개별 scope 정리는 stop job의 역순 대기를 피하도록 신호와 최종 상태 확인을 사용한다.

Linux 준비의 슬롯과 자식에게 전달된 원 실행 FD가 남아 있으면 저장소 해제를 진행하지 않는다.
수락 전 lifecycle 검사도 `asset-<VM UUID>.lock` 대상 보호에 포함한다. `ensure_quiet/drain`은
retire/restore/purge 실행자와 상속된 helper FD가 끝나기 전 저장소 해제를 거절한다.
gate가 닫혔다 재열려도 검사 당시 barrier 사본과 달라진 실행은 늦게 게시하지 않는다.
대상 lease → 공통 잠금 → 짧은 admission 순서와 실제 검증 범위는
[lifecycle 소유 경계](management-boundaries.md#lifecycle의-대상-소유와-공통-잠금)를 따른다.
슬롯이 풀려도 미완료 workspace의 자원 기록이 정리 미확정을 나타내면 거절한다.
`xenova-vm poweroff/reboot-board/shutdown-all`의 준비 drain과 VM 정리는 같은 기존 90초
예산을 사용한다. 준비 종료를 기다리며 공통 잠금을 계속 잡아 두지 않는다.
이 guard는 미게시 자산을 자동 정리하거나 실패 operation을 종결하는 기능이 아니다.

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
| admission 종료 | 10초 | gate 차단과 Linux 준비 drain. 내부 9초로 반환 여유 1초를 남기며 unit 상한을 늘리지 않음 |

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
