# Linux 자원·부팅 정책·Zephyr 네트워크/종료 계약

기존 Manager envelope, operation_lock, 실행 의도/신원, recovery journal과 G1을
재사용한다. 6a7d21a1eaccd78aa3bbbf2b33954b0a13b08280에서 분리해 구현한 후보를
실보드에서 검증하고 정식 소스에 반영했다. native/대역, ARM64 부분 타깃 빌드,
현재 보드의 부분 적용과 새 이미지 수락은 서로 다른 근거다.
최종 dom0 이미지 빌드·조립·SD 기록은 별도 단계다.

## Linux 정지 중 자원 변경

Manager action은 update-resources다. execute=False는 계획만 반환한다.
다음 예의 UUID, generation, configuration hash는 현재 get 응답에서 가져온다.

    {
      "vm_id": "<현재 영구 UUID>",
      "vcpus": 2,
      "memory_mib": 768,
      "expected_lifecycle_generation": 0,
      "expected_configuration_sha256": "<data.vm.configuration_sha256>"
    }

    xenova-vm update-resources <UUID 또는 내부 이름> --vcpus 2 --memory-mib 768 \
      --expected-lifecycle-generation <현재 generation> \
      --expected-configuration-sha256 <현재 configuration_sha256> \
      --idempotency-key <이번 요청 전용 키> --execute --json

허용 범위는 기존 Linux renderer와 같은 1..4 vCPU, 128..8192 MiB다.
boolean은 정수로 받지 않는다. 물리 메모리 예약·과할당 보장·hotplug는 제공하지 않는다.
REGISTERED Linux의 현재 lifecycle에 적용되는 stopped 의도와 실제 Xen 부재를
동시에 요구한다. present/paused/crashed/preserved, absent-running, unknown 의도,
관측 오류, 현재 host의 미해결 control/recovery/launch, 미완료 자산 작업은 거절한다.
이전 host의 물리 실행자는 새 host에 계속 실행 중이라고 간주하지 않는다.
영구 자산 작업은 메타데이터 게시 여부를 원래 키로 재판정해야 한다.

기존 operations journal에 before/after와 기대 조건을 먼저 저장하고 instance.json을
한 번에 바꾼다. 원본 creation_spec, UUID/name, 디스크/키, MAC/IP, autostart, 실행 의도와
intent_revision은 바꾸지 않는다. journal 뒤·metadata 뒤 중단은 원래 키로 재호출한다.
게시된 after와 현재 metadata가 같으면 metadata를 다시 게시하지 않고 완료한다.
이미 완료된 키는 original_result와 current를 구분한다. 이후 새 변경을 되돌리지 않는다.
같은 키의 다른 요청은 IDEMPOTENCY_CONFLICT, 오래된 조건은 TARGET_CHANGED다.
필수 영구 기록 실패는 RECOVERY_REQUIRED이며 성공으로 숨기지 않는다.

## 승인된 새 배포 부팅 정책

execution-policy.json의 새 기본값은 autostart_stopped=keep-stopped,
manual_running=keep-stopped, force_restart_vm_ids=[]다. CONFFILES로 관리자 override를
보호한다. 기존 null/명시 start를 임의로 덮어쓰는 migration은 없다.

| autostart | 적용 가능한 의도 | 새 host의 자동 시작/admission |
|---|---|---|
| true | running | 허용 |
| true | stopped | 거절 |
| false | running | 거절, 저장된 running은 유지 |
| false | stopped | 거절 |

현재 host의 명시 start는 autostart=false여도 현재 host 복구 admission을 만들 수 있다.
새 host에서는 표를 다시 적용한다. 같은 host의 최신 stop, R1의 commit/admission 중단
복원, unknown/이전 lifecycle 호환 경계는 유지한다. autostart-set은 즉시 실행 명령이 아니다.

## 기존 A/B 전환과 새 설치

기존 Starter, source.lock.json, starter.lock.json, G1 앱/소스는 보존한다.
별도 network-starter.lock.json과 xenova-zephyr-network-starter-1.1.0 번들을 사용한다.
공개 카탈로그 ID zephyr-xenvm-starter-arm64의 새 배포 버전은 1.1.0이다.
기본 Debian은 그대로이며 새 factory 기본 A/B는 이 새 immutable 번들을 선택한다.
최종 이미지에서 실제 새 번들을 지정해야 하며 이전 firmware manifest만 바꾸지 않는다.

Manager action firmware-transition은 다음 필드를 정확히 받는다.

    {
      "vm_id": "<기존 A 또는 B UUID>",
      "bundle": "/canonical/absolute/candidate-bundle",
      "manifest_sha256": "<firmware.json SHA256>",
      "expected_lifecycle_generation": 0,
      "expected_configuration_sha256": "<현재 get hash>",
      "board_shutdown_policy": "graceful"
    }

CLI도 같은 필드의 --bundle, --sha256, --expected-lifecycle-generation,
--expected-configuration-sha256, --board-shutdown-policy, --idempotency-key,
--execute를 사용한다. UUID/내부 이름 모두 공통 Manager로 간다.
적용은 stopped 의도/실제 부재/자산·작업 소유 확인 후에만 한다.
journal이 MAC/IP 예약과 이전/다음 metadata를 먼저 보존하고 새 revision을 독립 복사한다.
이후 instance.json 한 번의 원자 게시로 활성 firmware/profile/capability/policy를 바꾼다.
UUID, creation_origin, 최초 template 출처와 lifecycle generation은 바꾸지 않는다.
DHCP는 기존 원자 게시·0644·reload를 사용하며 다른 예약을 보존한다.

MAC/IP는 기존 allocator와 retired/pending/lease 예약을 대조한다.
현재 보드의 A/B 주소를 .2/.3 등으로 가정하지 않는다. 기존 시험 VM도 예약 소유자다.
이전 bundle로 같은 action을 호출하면 rollback할 수 있다. 이전 board policy를 명시하고
현재 hash/generation과 새 키를 쓴다. 새 MAC/IP는 legacy rollback 후에도 예약으로
남기며 VIF/DHCP capability만 꺼진다. firmware/기존 UUID를 purge/create하지 않는다.
미완료 전환은 원래 키로 재호출한다. 다른 키로 예약을 재선택하지 않는다.

새 catalog create도 network capability를 확인해 MAC/IP를 할당하며 stopped로 생성한다.
factory seed의 주소는 기존 allocator에서 고르고 per-card MAC/UUID는 첫 등록의 영구
신원 계획에서 만든다. 재등록은 그 신원을 유지한다. seed placeholder MAC은 운영 MAC이 아니다.
Ubuntu·Debian 카탈로그와 기존 Starter 버전은 삭제하지 않는다.
기본 선택 lock은 공개 ID마다 현재 버전 하나만 선택한다. 이전 release/Starter lock은 보관하되 기본 선택 목록에 같은 ID를 중복 넣지 않는다.

## Xen netfront와 공용 Store

xen-runtime은 G1 client를 하나의 bounded mutex/session으로 공유한다.
네트워크와 shutdown은 별도 thread/watch를 사용하고 callback/ISR에서 동기 Store를
호출하지 않는다. watch는 절대 domain 경로이며 등록 뒤 현재 값을 다시 읽는다.
수신 thread가 자기 응답을 기다리는 구조를 만들지 않는다.

표준 Zephyr Ethernet/net_if·IPv4·ARP·ICMP·DHCP를 사용한다. 1 VIF, 1 TX/RX queue pair,
공유 event channel, MTU 1500, RX-copy, 방향당 8 packet page다. ring ABI는 256 entries다.
descriptor/producer/id generation/offset/length/flag를 검사한다. TX completion 전 페이지를
재사용하지 않는다. unsupported extra-info/fragment/checksum-blank는 수용하지 않는다.
checksum offload는 feature-no-csum-offload=1 등으로 명시한다.
IRQ는 wake만 수행하고 worker에서 복사·completion·상위 stack 전달을 한다.

netfront 프로필은 upstream XEN_GRANT_TABLE을 끄고 제한된 grant-v1 owner를 사용한다.
고정 upstream의 end-access 반환/해제 경계를 그대로 재사용하지 않는다.
영구 RAM의 VA→GPA helper로 변환한 뒤 GFN을 만든다. busy revoke는 ref/page를 보존한다.
reconnect 실패 시 link-down/자원 격리로 끝내며 동일 실행에서 임의 hotplug 재연결은 제공하지 않는다.
Store·PV console의 빌린 page/event를 소유 grant처럼 반환하지 않는다.
Store/shutdown만 사용하는 앱은 기존 grant driver와 함께 빌드할 수 있다.

## 협조 stop/reboot/복구

shutdown.h의 hook은 별도 thread에서 호출되며 같은 장치의 uptime deadline을 받는다.
0은 앱의 quiesce 및 필요한 마지막 상태 ACK 완료, -EBUSY는 거절, -ETIMEDOUT은 기한,
그 밖의 음수는 실패다. hook은 기한을 지켜야 한다. 일반 작업·출력 억제와 최종 상태/ACK의
구체 의미는 앱 담당자가 구현한다. demo에는 주행 상태/차량 출력이 없고 이를 명시한다.

순서는 요청 수신 → 빈 문자열 ACK(수신만 의미) → hook → TX drain →
backend Closing/Closed → 소유 grant/event 회수 → feature 철회/Store close →
SCHEDOP_shutdown이다. 네트워크는 hook의 최종 상태 전송까지 살아 있다.
반환한 hypercall은 rc=0이어도 종료 성공으로 선언하지 않는다.
실제 제거의 최종 판정은 dom0의 기존 신원/제거 관측이다.

내부 thread priority와 resource cleanup budget은 Kconfig의 잠정 앱 설정이다.
host CPU pinning/RT 보장·차량 safety timeout·수락 latency 기준으로 쓰지 않는다.
기본 15s cleanup은 Manager의 원래 90s 요청 기한 안에서 평가한다.
hook 실패 후 자동 재무장/일반 업무 재개는 하지 않으며 feature를 철회한다.

엄격한 활성 bundle/profile와 해당 incarnation의 feature-poweroff/reboot 쌍이 모두
준비돼야 graceful을 받는다. legacy Starter에는 false capability를 붙이지 않는다.
Linux on_poweroff=destroy/on_reboot=restart/on_crash=preserve는 유지한다.
새 Zephyr는 destroy/destroy/preserve이며 Manager가 한 번만 명시 재생성한다.

일반 Zephyr reboot는 control-requests에 request-accepted, shutdown-requested,
old-removed, new-launching, new-observed, completed 경계를 남긴다.
기대 옛 실행·설정·host/lifecycle·intent·소유·원 deadline을 보존한다.
shutdown-requested 재전달은 명령을 다시 보내지 않고 관측한다.
old-removed에서 확실히 launch 전이면 남은 원 기한 안에서 재개한다.
new-launching은 실제 launch/child가 있을 수 있으므로 부재만 보고 재생성하지 않는다.
확인된 owner의 새 실행만 완료로 결합한다. durable new-observed는 이후 사용자 stop 뒤에도
과거 실행 결과로 완료할 수 있으며 최신 stop/실행 의도를 덮지 않는다.
RECONCILE_REQUIRED는 새 키로 무조건 재시도하라는 뜻이 아니다.
control-operation의 원 결과와 현재 get/probe를 따로 조회한다.

사용자 reboot의 intent revision은 한 번만 증가한다.
recover_once graceful-restart는 running intent/revision을 유지한다.
force-restart UUID 허용 목록은 [] 그대로이며 board force-allowed를 확대 해석하지 않는다.
execution_id는 실행 대응용 read-only 문맥이며 인증/차량 제어권 토큰이 아니다.
재시작 완료는 APP_READY/STANDBY_READY/ACTIVE가 아니고 app_readiness는 unknown이다.

## 부분 빌드·후속 이미지

build-xenova-network-starter.py는 고정 Zephyr/SDK와 ABI·앱 lock을 검사한다.
binary/ELF/map/config/DTS, compiler·toolchain·입력 hash를 별도 output에 남긴다.
package-xenova-network-starter.py는 실제 입력 일치 후 새 후보 bundle만 만든다.
기존 release를 덮지 않는다. 실제 보드 수락 뒤 별도 publication 경로로 게시한다.
runtime cfg verifier는 network Zephyr의 정확한 VIF/MAC/xenbr0와 무디스크 조건을 검사한다.
recipe·조립 source matching에는 새 Python 모듈이 포함된다.

이 앱의 timer test RNG는 DHCP/ping identifier용 비암호 난수다.
인증·개인키·보안 신원에 사용할 수 없다. 암호 기능을 가진 실제 앱의 entropy는 별도 계약이다.
실제 ARM64/SD/보드 수락 없이 소프트웨어 시험으로 성공을 소급하지 않는다.
Spring/FastAPI의 새 HTTP route, Agent/Worker/Gateway, 주행 mirroring은 구현하지 않는다.

## 앱 담당자 후속 책임

돌발 crash는 quiesce hook을 실행하지 못할 수 있다. 종료 직전 한 번의 ACK를 유일한 복구 근거로 삼지 않는다. 실제 앱은 지속 상태 동기화, 새 guest/app session, 데이터 freshness와 재합류 판정을 별도로 구현한다. 플랫폼 execution_id와 앱 세션 대응은 read-only 문맥이며 인증·제어권 증거가 아니다.

## 실환경 경계와 종료 소유권

공용 Store transaction wrapper는 G1의 필수 attempt counter를 전달한다. transaction
게시 실패는 Connected 성공으로 바꾸지 않는다. 템플릿 조회도 검증한 bundle의 profile과
capability를 사용하므로 legacy와 network Starter의 기능을 구분한다.

도메인 제거 중 unique_id 조회가 실패하면 새로운 성공 목록 두 번으로 실제 부재를
다시 확인한다. 조회 오류 자체는 부재가 아니다. 도메인이 남거나 목록이 깨지면 원래
오류/신원 변경을 반환한다. 이미 제출한 reboot의 원 기한이 지난 경우에도 제한된 읽기로
제거 경계를 닫을 수 있지만, 만료한 요청으로 새 실행을 생성하지 않는다.

libxl monitor와 per-domain QEMU/qdisk는 autostart를 수행한 xenova-vms.service cgroup을
상속한다. 이 unit의 ExecStop에서도 기존 shutdown-all을 실행하여 VM 제거를 systemd의
cgroup cleanup보다 앞세운다. 기존 xenova-vm-shutdown.service는 후속 제거 확인을
유지하며 공용 xen-qemu-dom0-disk-backend.service보다 먼저 종료된다. 정상 완료 뒤의
두 번째 호출은 이미 부재인 대상을 재종료하거나 사용자 의도를 바꾸지 않는다.
xenova-vms.service의 stop/restart는 VM 종료를 수반하는 플랫폼 작업이다.

정상 systemd 종료 성공 경로와 hook 실패 시 일회성 VM 복구의 거절은 별도 수락 항목이다.
ExecStop 실패만으로 진행 중인 PID 1 shutdown transaction이 취소된다고 보장하지 않는다.
운영 host 명령은 기존 prepare/finish를 연결한 xenova-vm reboot-board/poweroff 경로로
사전 조건을 검사한다. 거절/timeout 상태의 실제 host 종료와 전원 상실은 별도 수락이다.

보드 시험 결과는 외부 실행 증거와 함께 인계한다. frame 크기·제한된 burst·반복 재시작의
성공은 장시간 부하, 잘못된 netback 응답의 실물 주입, 차량 시간 기준의 수락을 뜻하지 않는다.
native의 악성 descriptor/grant busy/반환 hypercall 시험은 실제 하이퍼바이저 실패 주입과
구분한다. 앱의 quiesce/ACK·세션 freshness·운행 허가 판단은 앱 담당자의 책임이다.

## 2026-10-22 실보드 확인과 후속 빌드

현재 이미지에 백업 후 필요한 플랫폼 모듈·unit·펌웨어만 적용한 결과다.

| 범위 | 직접 확인한 결과 |
|---|---|
| Linux 자원 | stopped+absent에서 2 vCPU·768 MiB 적용, 실제 guest 확인 후 1 vCPU·512 MiB 복원; running/absent-running 변경 거절 |
| A/B 신원·통신 | 한 대씩 전환, UUID/generation/creation_origin 유지; 독립 MAC/DHCP, dom0/Linux/A/B 양방향 ping, payload 1472와 제한된 burst 성공 |
| 협조 종료·복구 | stop/reboot, graceful-restart/start-if-absent, 원 요청 재전달·오래된 실행/host 거절, 실제 중단 3지점, A의 legacy 프로필 롤백과 복귀 |
| 거절·timeout | 앱 hook 거절/실패/timeout에서 자동 force·재생성 없음; 별도 명시 개발용 정리는 이 결과와 구분 |
| 새 host 부팅 | 실제 정상 systemd 재부팅 2회에서 부팅 정책 4조합·실행 신원 교체 확인; Wi-Fi 수동 조작 없이 재연결 |
| mixed 종료 | 최초에는 startup cgroup cleanup이 기본 Debian의 backend를 먼저 종료함; ExecStop/종료 순서 보완 후 Debian/A/B graceful 제거와 toolstack 종료 전 Domain-0만 남음 확인 |

실환경 보완은 공용 Store transaction의 필수 attempt counter 전달, 제거 중 신원 조회
경쟁의 재관측, 검증된 템플릿 capability 표시, startup cgroup 정리 전 VM 종료다.
Wi-Fi 입력 표시·scan 설정 보존과 DHCP 예약 파일 0644는 기존 정식 소스 보완을 유지한다.

검증된 network Starter 1.1.0의 firmware.json SHA256은
`b540e67932762992a990cb6109aca1d1c36ea60116676d6a98ac1ffc82a9dedb`,
Image SHA256은
`9c91ebf50839ecec395bb5cf15bc98cc2fee710a77647af9f7090a9da6f969c3`다.
기존 Linux 템플릿과 legacy Starter/G1 source lock은 보존한다. 새 bundle을 별도
release로 게시하고 기본 선택 lock과 일치시킨 뒤 factory/최종 이미지 수락을 수행한다.
게시된 원본 경로에 임의 덮어쓰기하지 않는다.

악성 backend/실물 grant busy·반환 hypercall 주입, new-launching 중단의 실보드 주입,
장시간 부하, 실패 hook 상태의 실제 host 종료·전원 상실은 별도 수락 항목이다.
보드의 부분 적용 성공을 새 이미지의 최초 설치·자동 연결·차량 복구 완료로 확대하지 않는다.
빌드 준비와 후속 확인은 [dom0 재빌드 가이드](../../docs/build-guides/xenova-dom0-rebuild.md)를 따른다.
