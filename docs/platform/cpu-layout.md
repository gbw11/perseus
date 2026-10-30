# Xen vCPU·pCPU 배치 운영

vCPU는 게스트에 제공하는 가상 CPU이고 pCPU는 Xen이 실행을 배치하는 물리 CPU다.
이 문서는 정적 전용·공유 배치를 설정하고 현재 적용 상태를 확인·복구하는 절차다.
입력·필드·오류는 [CPU 명령 명세](cli-manager-spec.md#cpu-contract)에 정의한다.

## 목차

- [지원 모델과 경계](#지원-모델과-경계)
- [운영자 작업 네 가지](#운영자-작업-네-가지)
- [조회 결과 읽기](#조회-결과-읽기)
- [설치·부팅·중단 복구](#설치부팅중단-복구)
- [예약과 VM 수명주기](#예약과-vm-수명주기)
- [성능 측정과 수락 범위](#성능-측정과-수락-범위)

## 지원 모델과 경계

hard affinity는 Xen이 **각 vCPU를 실행할 수 있는 pCPU 집합**이다.
`xl vcpu-list`의 `CPU` 열은 현재 실행 CPU이며 affinity가 아니다.
이 구현은 Xen 4.21의 `Affinity (Hard / Soft)` 중 Hard만 읽는다.

기본 factory 프리셋은 Zephyr A→1, B→2, dom0·나머지 VM→{0,3}이다.
각 VM의 기존 vCPU 수는 보존한다. Linux 4-vCPU VM이면 네 vCPU가 각각 {0,3}을 사용한다.
Xen cfg의 scalar `cpus = '0,3'`은 전체 vCPU에 같은 마스크를 적용한다.
`cpus = ['0', '3']`처럼 vCPU별 매핑을 의미하는 문법으로 바꾸지 않는다.

운영 정책 원본은 `/srv/xenova/cpu-policy.json` 하나다. 전용 예약은 영구 UUID로 지정한다.
표시 이름, domid, template_id, Spring role, AUTO/MANUAL, ACTIVE/STANDBY를 배치 입력으로 해석하지 않는다.
전용 예약은 최대 두 개이며, 등록된 1-vCPU Zephyr만 신규 지정할 수 있다.
공유 집합·전용 코어는 운영자가 정지 조건에서 바꿀 수 있다. 빈 전용 예약 목록도 허용한다.
코어 교환은 정책 변경으로 처리한다. VM별 혼합 예외나 실행 중 재배치는 지원하지 않는다.

Xen `xl info`의 `nr_cpus`, `max_cpu_id`와 CPU pool의 실제 CPU 집합을 대조한다.
첫 버전은 max_cpu_id=3인 RPi4, 모든 온라인 CPU가 속한 단일 Credit2 pool을 지원한다.
다중 pool, 미할당 온라인 CPU, scheduler 변경·pool 전환 중 상태는 명시적으로 거절한다.
pool이나 scheduler를 자동 변경하지 않는다. dom0 Linux의 CPU 목록으로 Xen pCPU를 추정하지 않는다.

`exclusive_verified=true`는 관측 시점에 다른 도메인의 모든 vCPU hard affinity와 겹치지 않았다는 뜻이다.
dom0, 미등록 도메인, paused/blocked 도메인도 검사한다. IRQ·캐시·메모리·I/O 격리나 실시간 기한 보장이 아니다.
dom0 프로세스에 `taskset`을 적용하는 Linux affinity와 dom0 **vCPU의 Xen affinity**는 별개다.
Gateway 프로세스의 Linux affinity를 이 기능이 변경하지 않는다.
외부 root/xl 호출과 원자적으로 직렬화하지 않는다. 두 번의 전체 관측 사이에 도메인 집합·incarnation·affinity가 바뀌면 오류를 반환한다.

## 운영자 작업 네 가지

모든 예제의 실행 위치는 **dom0의 root 셸**이다. UUID는 현재 `xenova-vm list --json`에서 선택한다.
CPU 정책 변경 예제는 실행 중 관리 VM과 미완료 작업이 없는 유지보수 창에서 사용한다. 자원 수량 변경은 대상 Linux가 정지한 조건을 따른다.
명령이 기존 VM을 자동 정지하지 않는다.

### 1. 자원 수량 변경: 기존 update-resources

정지한 Linux의 vCPU 수·메모리 변경은 계속 `update-resources`만 사용한다.

```sh
VM_UUID='<Linux 영구 UUID>'
xenova-vm get "$VM_UUID" --json
# get의 generation, configuration_sha256을 그대로 전달한다.
xenova-vm update-resources "$VM_UUID" --vcpus 4 --memory-mib 512 \
  --expected-lifecycle-generation '<조회한 generation>' \
  --expected-configuration-sha256 '<조회한 SHA256>' \
  --idempotency-key linux-resources-01 --execute --json
```

CPU 정책이 활성화돼 있으면 저장할 자원 수량과 정책 문맥의 조합을 검사한다.
정책이 Linux의 vCPU 수·메모리·UUID를 바꾸지 않으며, 다음 시작 시 모든 구성 vCPU에 공유 마스크를 적용한다.

### 2. CPU 정책 변경: cpu-policy-set 하나

Manager 호출:

```python
from xenova_management import Manager

spec = {
    "expected_policy_revision": None,
    "shared_pcpus": [0, 3],
    "exclusive": [
        {"vm_id": "<Zephyr A 영구 UUID>", "pcpu": 1},
        {"vm_id": "<Zephyr B 영구 UUID>", "pcpu": 2},
    ],
}
manager = Manager()
plan = manager.call("cpu-policy-set", spec, idempotency_key="cpu-maintenance-01")
result = manager.call("cpu-policy-set", spec, execute=True,
                      idempotency_key="cpu-maintenance-01")
```

CLI는 같은 spec JSON과 같은 검증·journal 코드를 사용한다.

```sh
AUTO_UUID='<Zephyr A 영구 UUID>'
MANUAL_UUID='<Zephyr B 영구 UUID>'
export AUTO_UUID MANUAL_UUID
umask 077
python3 - <<'PY'
import json, os
from pathlib import Path
spec = {
    "expected_policy_revision": None,
    "shared_pcpus": [0, 3],
    "exclusive": [
        {"vm_id": os.environ["AUTO_UUID"], "pcpu": 1},
        {"vm_id": os.environ["MANUAL_UUID"], "pcpu": 2},
    ],
}
Path('/root/cpu-policy-request.json').write_text(json.dumps(spec)+'\n')
PY
xenova-vm cpu-policy-set --spec-file /root/cpu-policy-request.json \
  --idempotency-key cpu-maintenance-01 --json
xenova-vm cpu-policy-set --spec-file /root/cpu-policy-request.json \
  --idempotency-key cpu-maintenance-01 --execute --json
```

초기 등록의 expected_policy_revision은 null, 변경 시에는 `cpu-layout`에서 읽은 정수다.
오래된 revision은 TARGET_CHANGED다. 같은 요청 재전달은 원래 key를 유지한다.
새 정책 변경은 새 key와 현재 revision을 사용한다. 완료 key는 과거 결과를 반환하며 정책을 다시 덮어쓰지 않는다.
계획 모드는 파일·affinity·intent를 변경하지 않는다. 계획에 표시된 operation UUID는 저장이 확정된 ID가 아니다.
실행 응답의 data.policy는 저장된 정책이며 data.host_preparation_required=true는 아직 현재 호스트 적용을 완료하지 않았다는 뜻이다.
`pin-vcpu`, `set-affinity`, `reserve-core` 같은 별도 공개 변경 명령은 제공하지 않는다.

### 3. 실제 배치 조회: cpu-layout

```sh
xenova-vm cpu-layout --json
xenova-vm cpu-layout "$AUTO_UUID" --timeout-ms 15000 --json
```

```python
from xenova_management import Manager
result = Manager().call("cpu-layout", {}, request_id="cpu-host-status")
result = Manager().call("cpu-layout", {"vm_id": "<영구 UUID>", "timeout_ms": 5000},
                        request_id="cpu-vm-status")
```

입력은 선택적 vm_id와 timeout_ms뿐이다. timeout_ms 기본값은 15000, 범위는 1..15000이다.
전역 operation_lock은 **대기 없이** 획득을 시도한다. 경합하면 OPERATION_IN_PROGRESS다.
deadline은 잠금 시도·registry 읽기 전부터 계산한다. Xen 명령별 timeout도 남은 전체 deadline으로 제한한다.
VM 한 개만 요청해도 전용성 검사는 전체 도메인을 관측한다.
dom0-manager의 SerializedManager는 그 앞에서 프로세스 내부 mutex를 최대 5초 별도로 기다릴 수 있다.
하위 timeout_ms에는 이 상위 대기 시간이 포함되지 않는다. 제품 조회를 연결할 때는 [시간 예산 계약](cli-manager-spec.md#time-budgets)의 상위 대기와 기존 조회 계약을 고려해 잔여 timeout_ms를 전달해야 한다. 하위 기본값으로 HTTP 기한이 보장되는 것은 아니다.
조회는 정책·준비 기록·affinity를 수정하거나 VM을 시작하지 않는다.
operation_id=null, replayed=false이며 하위 명령 출력이 stdout JSON에 섞이지 않는다.

### 4. 기존 시작·재부팅·복구 사용

```sh
xenova-vm start "$AUTO_UUID" --idempotency-key auto-start-01 --json
xenova-vm reboot "$AUTO_UUID" --idempotency-key auto-reboot-01 --json
xenova-vm cpu-layout "$AUTO_UUID" --json
```

별도 pin 명령을 호출하지 않는다. 공통 start, autostart, Zephyr 협조형 reboot 및 recover_once에서
승인된 정책·현재 boot 준비 상태와 실제 배치를 검사한다. Linux 일반 reboot의 기존 toolstack 재생성 방식도 유지하며,
새 실행의 bind_launch에서 동일 정책과 실제 affinity를 검사한다.
이미 실행 중인 start도 실제 배치를 확인한다. 불일치하면 중복 생성하거나 몰래 repin하지 않는다.
recover_once의 외부 요청 형태는 그대로다. 하위 수락 journal에 cpu_policy 문맥을 추가한다.

## 조회 결과 읽기

`cpu-layout`의 ok=true는 관측 성공이다. host.ready와 VM별 실제 mask·affinity_matches를
함께 확인한다. 전용 VM의 exclusive_verified는 다른 모든 domain의 vCPU와 중복이 없는지
관측한 결과이며, 지연 상한이나 IRQ/I/O 격리를 보장하지 않는다.

정지한 VM의 실제 실행 식별자·affinity와 비교값은 null이다. 공유 VM의 전용성도 null이다.
미설정 정책은 configured=false이고 전용 배치 완료가 아니다. 손상·유실·읽기 실패가
발생한 활성 정책을 미설정 호환 모드로 바꾸지 않는다. 정상 관측의 불일치는 issues로,
관측 자체의 실패는 ok=false로 구분한다.

필드·전체 envelope·오류 코드·권장 HTTP 분류의 정식 원문은
[cpu-layout 명세](cli-manager-spec.md#cmd-cpu-layout)와
[CPU 제품 연결](cli-manager-spec.md#cpu-http-integration)에 있다.
제품 HTTP의 CPU 조회 연결은 현재 별도 구현이 필요하다.

## 설치·부팅·중단 복구

### 신규 최초 설치

recipe가 `/etc/xenova/cpu-policy-preset.json`, xenova_cpu.py, xenova-cpu-prepare.service를 설치한다.
첫 identity plan 생성 시 프리셋의 instance_id를 **이미 계획한 영구 UUID**에 연결해
`.first-install-state.json`의 cpu_policy_activation에 저장한다. 신원을 다시 발급하지 않는다.
기존 identity plan에 활성화 항목을 소급 추가하지 않는다.

부팅 순서는 first-boot 등록·Xen ready 완료 → CPU prepare → VM autostart → 기존 host admission이다.
prepare는 factory ticket을 같은 cpu-policy-set 저장 코드로 최초 게시하고, dom0의 모든 실제 vCPU를 공유 집합으로 제한·검증한다.
호스트 준비 영수증은 `/run/xenova/cpu-host.json`에 저장한다. 다음 host_boot_id에는 새 준비가 필요하다.
첫 등록 의도가 running이어도 실제 도메인이 아직 없고 진행 중 실행 작업이 없으면 준비할 수 있다.

vms unit은 CPU prepare를 Wants+After로 순서 지정한다. Requires/PartOf/BindsTo나 ExecStop으로 결합하지 않는다.
CPU prepare의 중지/재시작이 VM 정지·affinity 초기화로 전파되지 않는다.
prepare가 실패해도 수동 시작을 포함한 모든 공통 시작 경로에서 실제 준비 상태를 다시 검사해 새 실행을 차단한다.
준비 서비스를 재시작해 실행 중 게스트를 임의 재배치하지 않는다.

### 기존 설치 활성화와 내부 준비 명령

정책·활성화 anchor·publication history가 모두 없는 설치본은 과거 호환 모드다.
코드만 업데이트해도 정책이 자동 활성화되지는 않는다. 유지보수 창에서 관리 게스트의 정지와 미완료 작업 부재를 확인한 뒤
위 CPU 정책 변경 절차로 명시적으로 등록한다. 실행 중 VM이나 미확정 작업이 있으면 변경하지 말고 먼저 정지·복구 상태를 확인한다.

설치 담당자/부팅 서비스 전용 준비 호출:

```sh
# 게스트가 없는 유지보수 창. 계획 조회 후 실행.
xenova-vm cpu-host-prepare --json
xenova-vm cpu-host-prepare --execute --json
xenova-vm cpu-layout --json
```

```python
Manager().call("cpu-host-prepare", {"timeout_ms":15000}, execute=True)
```

일반 사용자가 VM 시작 때마다 수행하는 명령이 아니다. 일반 조회/start가 dom0 affinity를 대신 변경하지 않는다.
준비는 별도 내부 action이며 공개 CPU 정책 변경 action은 cpu-policy-set 하나다.
정책 파일 저장과 host.ready는 별개다. 사용자·unit 모두 Manager의 공통 잠금과 같은 준비 코드를 사용한다.

### 저장 중단·부분 적용

정책 publication journal: `/srv/xenova/cpu-policy-operations/<operation_id>.json`.
정책 저장은 prepared journal → activation anchor → 정책 원자 게시 → completed 순서다.
원래 spec/key 재전달로 준비 또는 게시 경계를 마무리한다. 완료 key는 과거 결과만 재생한다.
활성화 anchor나 history가 있는데 정책이 없으면 CPU_POLICY_INVALID다.
유효한 과거 정책 파일을 복사해 덮어쓴 경우에도 최신 완료 journal revision·활성화 소유자를 대조해 rollback으로 거절한다.
정책·anchor·history를 함께 지우는 외부 root 행위를 탐지하는 tamper-proof 저장소라고 주장하지 않는다.
임의 journal/lock 삭제나 새 key로 중단 단계를 우회하지 않는다.

dom0 적용은 여러 `xl vcpu-pin` 호출이므로 원자적이지 않다.
각 vCPU 적용 전 현재 정책·dom0 incarnation을 확인하고 적용 뒤 진행 상황을 기록한다.
영수증에는 원래 전체 마스크, 완료 vCPU, phase=applying/failed/ready/restoring/restored를 남긴다.
부분 실패 상태는 ready가 아니며 새 guest 시작이 차단된다.
관리 게스트가 없고 boot/revision/incarnation이 같은 상태에서 cpu-host-prepare --execute를 재전달하면 적용을 재개한다.
현재 마스크가 원래값 또는 예정값과 다르면 외부 변경으로 보고 중단한다.

관리자가 원래 dom0 마스크로 되돌릴 때:

```sh
xenova-vm cpu-host-prepare --restore --json
xenova-vm cpu-host-prepare --restore --execute --json
```

복원도 원래 영수증의 동일 boot·정책·dom0 실행을 요구하며 게스트를 정지하지 않는다.
복원 후 정책은 유지되고 ready=false다. 게스트를 시작하려면 다시 준비해야 한다.
CPU 정책 변경은 부분 적용을 먼저 재개 또는 복원한 뒤 수행한다.
호스트를 실제 재부팅하면 이전 boot의 runtime 영수증을 재사용하지 않는다.

## 예약과 VM 수명주기

stop/reboot/recovery는 CPU 예약을 해제하지 않는다. retire/restore/purge도 예약을 자동 변경하지 않는다.
restore 후 같은 UUID는 같은 전용 배치를 받는다. purge 뒤에도 코어 예약이 남으며
운영자가 cpu-policy-set으로 명시적으로 해제하거나 새 등록 UUID로 이전해야 한다.
미등록/보관 UUID를 새로운 전용 대상처럼 등록할 수는 없다.
전용 UUID를 포함한 기존 정책은 표시 이름·차량 모드·제어권 변경과 무관하게 유지된다.

CPU 준수 실패와 실행 신원은 별개다. stop/shutdown은 기존 UUID/name/domid/xen_unique_id 확인을 계속 요구한다.
CPU 오류 때문에 이 확인을 우회하거나 실행 불확실성을 임의로 제거하지 않는다.
불변 Zephyr 펌웨어의 RAM/GIC/단일 vCPU 프로필, 바이너리, manifest를 CPU 배치 때문에 수정하지 않는다.

## 성능 측정과 수락 범위

기능 수락은 실제 Xen affinity·신원·정책 준비를 확인하는 것이다. 성능 효과는 같은
입력·펌웨어·broker·vCPU/메모리 조건에서 별도로 측정한다. 비교할 때는 전용 예약이
없는 전체 공유 정책과 전용 정책을 명시적으로 등록하며 정책/anchor/journal을 삭제하지 않는다.

각 배치에서 Debian 유휴/CPU 부하 조건을 비교한다. guest 계산 주기와 Gateway
수신·출력 지연을 구분해 p95/p99·관측 최대·deadline miss·오류/손실·표본 수를 기록한다.
관측 최대를 보장 상한으로 사용하지 않고 주행 앱이 준비되지 않았다면 실제 주행 성능으로
수락하지 않는다. 저장된 정책이나 unit active만으로 재부팅/복구 후 실제 배치를 대신하지 않는다.

host 재부팅 전후 검증에는 새 host_boot_id, cpu-layout, 준비 로그, 기존 UUID·자산 해시가
필요하다. 특정 후보·실보드 결과와 남은 검증은
[검증 이력](../verification/platform-management-boundaries-history.md)에 보존한다.
