# 관리 작업과 부팅·종료 경계

이 문서는 `feat/platform/runtime-lifecycle-diagnostics`의 하위 플랫폼 변경과
실제 systemd unit을 기준으로 dom0 준비, domU 자동 시작, 종료의 경계를 설명한다.
U-Boot·Xen·dom0 커널·DT·하드웨어 설정은 변경하지 않는다.

## 준비 의존성과 완료 의미

| 단계/실제 진입점 | 선행 조건과 의존성 | 완료가 의미하는 것 |
|---|---|---|
| `xenova-data-prepare.service` → `xenova-first-boot grow` | `systemd-remount-fs.service`를 Requires+After, udev trigger 뒤 | 기존 부팅 매체의 마지막 데이터 파티션 확인·확장. 새 디스크 생성/포맷이 아님 |
| `srv-xenova.mount` | data-prepare Requires+After | 검증된 ext4를 `/srv/xenova`에 마운트 |
| `xenova-first-boot.service` → `register` | 데이터 mount Requires+After | 최초 등록 재개 또는 기존 설치 확인. 이미 확정된 UUID·자산·사용자 autostart 선택을 덮지 않음 |
| `xenova-xen-ready.service` → `check-xen` | xenstored·xenconsoled·xen-init-dom0 Requires+After | dom0 제어 인터페이스·xl·Domain-0 확인. guest/app 준비가 아님 |
| `xenova-cpu-prepare.service` → `cpu-host-prepare --execute --json` | mount와 first-boot·xen-ready 뒤. 후자 둘은 Wants | 현재 host/policy의 dom0 실제 affinity 확인·적용. CPU 정책 미설정 설치는 기존 호환 경로 |
| `xenova-vm-shutdown.service`의 `/bin/true` | first-boot·mount·xen-ready Requires+After, toolstack/backend 뒤 | 종료 때 guest를 먼저 정리할 서비스 경계 설치. VM 시작은 하지 않음 |
| `xenova-vms.service` → `autostart` | first-boot·mount·xen-ready·vm-shutdown Requires+After, cpu-prepare Wants+After | 등록·부팅 정책에 따라 선택한 VM의 Xen 실행 및 필수 신원/CPU 검증. guest OS·앱 준비는 별도 |
| networked guest의 `prepare_dhcp` | `xenova-dhcp.service` start/검증, DHCP는 bridge Requires+After | 내부 bridge/예약/DHCP 확인. Wi-Fi·외부 DNS·NTP에 부팅을 묶지 않음 |
| `xenova-host-admission.service` → `host-init` | vms Wants+After | 현재 host의 admission 초기화. 기존 quiescing barrier를 무조건 다시 열지 않음 |

`After`는 순서만 뜻한다. `Requires`와 함께일 때 선행 start 실패가 서비스 시작을
막는다. CPU 준비는 `Wants+After`이므로 legacy 설치의 호환 동작을 유지하고,
정책 활성화 시에는 **각 공통 start/reboot/recovery 경로가 실제 현재 준비 상태를
검사**한다. service의 active 문자열이나 전 부팅의 ready 파일만으로 허가하지 않는다.
CPU 준비 서비스에는 `ExecStop`, guest에 대한 `PartOf/BindsTo/Requires` 전파를
추가하지 않았다. CPU 준비 재시작이 실행 중 VM을 종료하거나 affinity를 초기화하지 않는다.

내부 bridge/DHCP는 networked guest가 사용할 때도 검사한다. 네트워크 장애가
network 없는 guest 또는 dom0 진단 경로를 불필요하게 직렬화하지 않도록 기존
조건부 진입을 유지한다. 기존 factory 등록과 매 부팅 실행 준비는 별개다.

## 시간 예산

`TimeoutStartSec`는 해당 service의 실행 상한이며 모든 선행 unit을 포함한
보드 전체 부팅 상한이 아니다. reboot의 90초를 부팅 전체에 적용하지 않는다.

| 실행 | unit의 바깥 상한 | 내부 경계 |
|---|---|---|
| data-prepare / 최초 등록 | 각각 300초 | 기존 설치 단계·저장 검증. 이번 변경으로 90초를 적용하지 않음 |
| 데이터 mount | mount 15초, JobTimeoutSec 330초 | 선행 data-prepare와 실제 mount를 구별 |
| check-xen | 30초 | CLI 진입에서 **25초** 한 번; 모든 xl 조회가 남은 예산 공유 |
| CPU host prepare | 30초 | 기존 CPU preparation의 15초 관측/적용 예산. 서비스 실패는 완료 증거가 아님 |
| DHCP | 15초 | 서비스 내부 ExecStartPre/ExecStartPost 및 기존 준비 확인. 호출자는 남은 관리 예산으로도 제한 |
| autostart | 150초 | **140초 batch 하나**. 잠금 시도 전 생성하고 registry·Xen 준비·DHCP·모든 VM start가 공유 |
| vms/vm-shutdown의 shutdown-all | 각각 110초 | 기존 90초 전체 guest 정리 기한. admission gate 종료 뒤 순서 유지 |
| host-admission quiesce | 10초 | 새 시작/복구 차단. guest 정리를 대신하지 않음 |

autostart의 10초 차이는 결과 저장·프로세스 종료를 위한 여유다. fsync·프로세스
생성·커널 대기를 엄밀하게 선점한다는 보장은 아니다. 개별 VM마다 140초를 새로
주지 않는다. 기존 공통 작업 잠금의 충돌 즉시 거절 의미도 유지한다.

## autostart 부분 성공과 재시도

`/run/xenova/autostart-results.json`의 기존 VM 내부 이름별 map을 유지한다.
각 항목에 이번 `host_boot_id`, batch의 `deadline_monotonic_ms`를 붙인다.
기한은 해당 부팅의 단조 시계 값이며 UTC가 아니다.

- 정상 완료 항목의 `result`·`xen_state`는 실제 관측 상태를 유지한다.
- 시간이 끝나기 전에 처리된 skip은 기존 boot admission/정책 근거를 유지한다.
- 아직 start를 호출하지 못한 VM은 `result="not-started"`, `start_invoked=false`로 기록한다.
- start 호출 뒤 실패는 `result="failed"`, `start_invoked=true`로 구별한다.
  실패가 도메인 생성 효과 없음이나 안전한 자동 재시도라는 뜻은 아니다.
- 이미 시작한 VM을 다른 VM의 실패/기한 만료 때문에 임의로 종료하지 않는다.
  호출은 실패로 끝나도 각 VM의 원 결과·자산·실행 기록은 남는다.
- 서비스 재시도는 새로운 부팅 batch다. 기존 실행에 대해 신원·CPU 정책을
  확인하는 공통 start를 사용하므로 중복 생성하지 않는다. 원 VM 제어/recovery의
  멱등 키·실행 기한을 재발급하는 동작이 아니다.
- 작업 중 강제 중단이면 마지막 `starting` 또는 `pending`이 남을 수 있다.
  이를 성공/부재로 해석하지 않고 기존 실행·journal과 대조한다.

새 host에서는 이전 execution/관측/CPU ready 근거를 현재 것으로 사용하지 않는다.
영구 desired_state와 autostart의 충돌은 기존 승인된 boot policy가 판단한다.
동일 host의 명시적 사용자 stop을 서비스 재시작으로 뒤집지 않는다.

## 종료 순서

동시에 종료되는 unit의 `After`는 반대 순서로 적용된다.

1. host-admission의 `ExecStop=host-quiesce`가 새 시작·복구를 차단한다.
2. vms의 `ExecStop=shutdown-all`이 기존 VM별 종료 정책으로 guest를 정리한다.
3. 별도 vm-shutdown 경계가 guest 부재를 확인하며 backend/toolstack·저장소보다 먼저 종료된다.
4. 그 뒤 toolstack/backend·mount 등의 기존 systemd 정리가 진행된다.

협조 종료 실패를 자동 force로 바꾸지 않는다. CPU 정책 오류도 신원으로 확인된
guest의 stop/shutdown을 새로 막아서는 안 된다. xen domain 생성, guest OS 준비,
앱 준비는 서로 다른 완료 단계이며 앱 readiness를 이 경로에 추가하지 않는다.

## 검증과 실보드 인계

로컬 `test_xenova_boot_boundaries.py`는 실제 Platform/Execution/CPU와 작은
자산·FakeXen·가짜 시계를 사용한다. 실제 systemd loader로 정상 그래프와 주입한
의존성 순환을 검사하고, 등록 중단 재개·기존 등록 보존·동일 실행 재사용·현재 host
CPU 준비·실제 affinity 변경·공유 예산 소진·부분 성공을 확인한다.
기존 lifecycle/recovery/first-install 회귀도 함께 실행한다.

이 결과는 전원 부팅 수락이 아니다. 보드 후보 설치·서비스 교체·CPU 정책 변경·
guest 제어는 이후 별도 승인으로 G5의 개별 협조 reboot까지 검증했다.
host 재부팅은 수행하지 않았다. 후속 전원 시험 시
전후 host_boot_id, 등록 UUID·자산 hash, 영구 intent, autostart-results,
CPU policy/ready 문맥, 실제 전체 domain/affinity, unit journal을 함께 회수한다.
정상 VM을 임의 rollback하지 않고 pending/failed 항목을 구분해 인계한다.

### unit 실패와 cgroup 소유 관계의 남은 수락

함수의 부분 성공 보존과 systemd의 프로세스 정리는 서로 다른 경계다.
2026-10-25 실제 보드의 `xenova-vms.service`는 `KillMode=control-group`,
`SendSIGKILL=yes`였다. WSL의 별도 oneshot unit에서 같은 cgroup의 비-VM
monitor/backend 모형을 먼저 준비한 후 launcher 실패/timeout을 주입했다.
두 경우 모두 자식이 SIGTERM으로 종료됐다. 실제 Xen guest 실패 주입은 하지 않았다.

따라서 autostart batch의 함수 결과·journal이 부분 성공을 보존해도, 실패한
unit이 소유한 libxl monitor/qdisk까지 보존한다고 결론내릴 수 없다.
`KillMode`를 바꾸거나 전체 서비스 순서를 재배치하지 않았으며, 이 unit 전체
수락은 별도로 남긴다. 이번 보드의 수동 복원 VM들은 sshd cgroup에서 실행됐으므로
그 생존도 autostart unit의 실패 수락을 대신하지 않는다.
격리 증거·실제 cgroup·보드 reboot 결과의 범위는
[G5 수락 경계](management-boundaries.md#g5-실보드-결과와-별도-수락-경계)에 있다.
