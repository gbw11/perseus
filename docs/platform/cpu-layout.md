# 정적 CPU 배치와 dom0-manager 인계 계약

## 목차

- [지원 모델과 경계](#지원-모델과-경계)
- [운영자 작업 네 가지](#운영자-작업-네-가지)
- [조회 응답](#조회-응답)
- [오류와 HTTP 연결](#오류와-http-연결)
- [설치·부팅·중단 복구](#설치부팅중단-복구)
- [예약과 VM 수명주기](#예약과-vm-수명주기)
- [검증과 실보드 인계](#검증과-실보드-인계)

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
아래 변경 예제는 실행 중 VM이 없고 미완료 관리 작업이 없는 유지보수 창에서 사용한다.
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
하위 timeout_ms에는 이 상위 대기 시간이 포함되지 않는다. 별도 HTTP 자원 조회를 연결할 때는
현재 조회 5초·Spring 대기 6초 계약을 고려해 상위 대기를 줄이거나 전체 예산에서 대기 시간을 빼고
남은 timeout_ms를 전달해야 한다. 하위 기본값 15초를 그대로 사용해 HTTP 기한이 보장된다고 해석하지 않는다.
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

## 조회 응답

모든 정상·오류 응답은 기존 envelope를 사용한다. 실행 가능한 전체 JSON 예제는
[cpu-layout-responses.json](../../platform/tests/fixtures/cpu-layout-responses.json)에 있다.
`running`, `stopped`, `unconfigured`, `mismatch`, `observation_failure` 각각이 완전한 envelope다.

| 필드 | 의미 |
|---|---|
| configured | 유효하게 활성화된 영구 정책의 존재 |
| policy_revision | 저장된 정책 revision. 미설정은 null |
| host_boot_id | 이번 관측의 host 부팅 UUID |
| observed_monotonic_ms | 그 host_boot_id에 속한 단조 밀리초. UTC/Unix 시간이 아님 |
| host.online_pcpus | Xen과 pool에서 대조한 실제 온라인 pCPU |
| host.pool_name / scheduler | 실제 Xen pool과 scheduler. 자동 변경하지 않음 |
| host.desired_dom0_pcpus | 정책의 공유 집합. 미설정은 null |
| host.observed_dom0_hard_affinity | 실제 dom0 vCPU별 마스크. vCPU 개수는 실제 조회값 |
| host.ready | 현재 boot·revision·정책 해시·dom0 incarnation에 맞는 준비 영수증과 실제 배치가 일치 |
| reservations | 정책에 남은 전용 UUID·코어 목록. 보관·purge 이후에도 명시적 변경까지 유지 |
| vms[].vcpus / desired_hard_affinity | 영구 VM 설정의 수량 / 정책으로 계산한 배치 |
| vms[].allocation | exclusive/shared. 정책 미설정은 null |
| vms[].execution_id | 현재 Xen incarnation과 일치하는 플랫폼 실행 ID. 미관리 실행·정지는 null |
| vms[].observed | 이번 호출에서 읽은 실제 Xen 값 |
| vms[].affinity_matches | 실제 vCPU 수와 각 hard mask가 예정값과 정확히 같음 |
| vms[].exclusive_verified | 전용 VM의 예정 마스크 일치 및 다른 모든 도메인의 모든 vCPU와 겹침 없음 |
| issues | AFFINITY_MISMATCH, EXCLUSIVE_CONFLICT, POLICY_PUBLICATION_PENDING 또는 CPU_NOT_READY 진단. 충돌 domain/코어 포함 |

정지 VM의 presence는 absent이며 domid·xen_unique_id·vcpus·hard_affinity는 모두 null이다.
정지 VM의 affinity_matches와 exclusive_verified도 null이다. 정지를 불일치로 보고하지 않는다.
공유 VM의 exclusive_verified는 null이다. 미설정 정책에서는 allocation·desired_hard_affinity와 두 비교값이 null이며 host.ready=false다.
정책 미설정은 제한 없는 과거 호환 동작을 뜻하며 전용 배치 수락이 아니다.
vm_id 조회는 현재 등록된 UUID를 받는다. 보관/purge한 예약은 호스트 조회의 reservations에서 확인한다.

정상 관측으로 불일치를 찾으면 **ok=true**와 issues/false 비교 결과를 반환한다.
ok=true는 조회 성공이다. 관측 명령 실패·누락·파싱 실패·신원 변경·deadline 초과는 ok=false다.
실패를 빈 affinity 배열이나 정상 상태로 바꾸지 않는다. uint64 xen_unique_id는 문자열로 전달한다.

정상 실행 전체 envelope 예(실보드 결과가 아닌 회귀 fixture):

```json
{
  "schema_version": "1.0",
  "ok": true,
  "request_id": "01422a2c-862f-442b-ba94-e5ddf14b6503",
  "operation_id": null,
  "replayed": false,
  "data": {
    "configured": true,
    "policy_revision": 1,
    "host_boot_id": "10000000-1111-4222-8333-123456789abc",
    "observed_monotonic_ms": 0,
    "host": {
      "online_pcpus": [
        0,
        1,
        2,
        3
      ],
      "max_cpu_id": 3,
      "pool_name": "Pool-0",
      "scheduler": "credit2",
      "desired_dom0_pcpus": [
        0,
        3
      ],
      "observed_dom0_hard_affinity": [
        {
          "vcpu": 0,
          "pcpus": [
            0,
            3
          ]
        },
        {
          "vcpu": 1,
          "pcpus": [
            0,
            3
          ]
        },
        {
          "vcpu": 2,
          "pcpus": [
            0,
            3
          ]
        },
        {
          "vcpu": 3,
          "pcpus": [
            0,
            3
          ]
        }
      ],
      "ready": true
    },
    "reservations": [
      {
        "vm_id": "732239a7-1bc3-4b2c-b833-a8bc09009a0c",
        "pcpu": 2
      },
      {
        "vm_id": "e158a936-304e-42b8-bbc1-e2353175f43e",
        "pcpu": 1
      }
    ],
    "vms": [
      {
        "vm_id": "e158a936-304e-42b8-bbc1-e2353175f43e",
        "execution_id": "bca58491-0ee9-4342-800d-0632c20ef511",
        "vcpus": 1,
        "allocation": "exclusive",
        "desired_hard_affinity": [
          {
            "vcpu": 0,
            "pcpus": [
              1
            ]
          }
        ],
        "observed": {
          "presence": "present",
          "domid": 1,
          "xen_unique_id": "1",
          "vcpus": 1,
          "hard_affinity": [
            {
              "vcpu": 0,
              "pcpus": [
                1
              ]
            }
          ]
        },
        "affinity_matches": true,
        "exclusive_verified": true,
        "issues": []
      }
    ],
    "issues": []
  },
  "error": null
}
```

오류 envelope 예:

```json
{
  "schema_version": "1.0",
  "ok": false,
  "request_id": "cpu-status-01",
  "operation_id": null,
  "replayed": false,
  "data": null,
  "error": {
    "code": "OBSERVATION_UNAVAILABLE",
    "message": "cannot parse or identify complete Xen CPU state",
    "retryable": false,
    "details": {"cause_type": "ValueError"}
  }
}
```

실행 기록의 cpu_policy에는 configured, policy_revision, policy_sha256, allocation, desired_hard_affinity를 저장한다.
control/recovery/resource journal에도 수락한 정책 문맥을 보존한다. 정책 파일 변경을 instance.json 해시만으로 판단하지 않는다.
policy_sha256은 key 정렬·공백 없는 UTF-8 JSON(ensure_ascii=false)의 SHA256이며, 보기 좋게 들여쓴 파일 자체의 sha256sum과 구분한다.
생성 후 검증 실패는 phase=observed인 실행 신원을 유지하면서 cpu_verification=incomplete와 cpu_error를 남긴다.
응답 CPU_APPLY_INCOMPLETE는 도메인 존재 가능성을 명시한다. 기존 복구/협조형 reboot의 uncertain 경계에서는 RECONCILE_REQUIRED와 details.cause로 원래 CPU 오류를 보존한다. 원래 key 재전달은 create/destroy/repin을 반복하지 않는다.
CPU 정책 손상·불일치는 기존 신원 검증을 거친 stop/shutdown을 막지 않는다.

## 오류와 HTTP 연결

아래 HTTP 값은 **dom0-manager 담당자에게 권장하는 신규 매핑**이다. 이번 변경이 HTTP endpoint나 mapper를 수정하지 않는다.

| code | 뜻 | 권장 HTTP | 처리/재시도 |
|---|---|---|---|
| INVALID_SPEC | 허용하지 않은 필드·UUID·CPU·중복·revision 형식 | 400 | 입력 수정 |
| VM_NOT_FOUND | 조회/예약 대상 등록 UUID 없음 | 404 | 목록 다시 조회 |
| RESOURCE_UNSUPPORTED | 1-vCPU Zephyr 제약, offline CPU, 지원 밖 pool/scheduler/topology | 400 | 환경·정책 검토, 자동 변경 금지 |
| VM_NOT_STOPPED | 관리 게스트가 실행 중 | 409 | 승인된 정지 후 새 시도 |
| IDEMPOTENCY_CONFLICT | 같은 key에 다른 요청 | 409 | 원 요청 유지 또는 새 변경 key |
| OPERATION_IN_PROGRESS | 잠금·미완료 자산 작업 경합 | 503 | retryable=true, 제한된 backoff |
| TARGET_CHANGED | 오래된 revision·incarnation/set/정책 변경 | 409 | 새 조회와 문맥 확인. 변경 요청을 무조건 재전달하지 않음 |
| RECONCILE_REQUIRED | 미완료 launch/control/recovery/policy, 불확실한 물리 결과 | 503 | 원 journal 확인. 새 key로 우회 금지 |
| CPU_POLICY_INVALID (신규) | 활성 정책 유실·손상·읽기 실패·publication 불일치 | 503 | 관리자 복구, 무제한 fallback 금지 |
| CPU_NOT_READY (신규) | 이번 boot/revision의 dom0 준비 미완료/불일치 | 503 | 설치/유지보수 준비 절차 확인 |
| CPU_APPLY_INCOMPLETE (신규) | 도메인이 존재하나 CPU 검증 미완료, 충돌 또는 부분 적용 | 409 | 실행 신원·journal·cpu-layout 조사. 자동 destroy/repin 금지 |
| OBSERVATION_UNAVAILABLE | 실제 Xen 조회/파싱 실패 | 503 | 원인 조사 후 새 읽기 시도 |
| DEADLINE_EXCEEDED | 전체 관측 기한 만료 | 504 | 새 조회 가능. 변경은 journal 먼저 확인 |
| STORAGE_UNAVAILABLE / RECOVERY_REQUIRED | 필수 저장 실패/미확정 변경 | 503 | 저장소 및 원 operation 확인 |

CPU 신규 오류의 retryable 기본값은 false다. 관리자 조치 없이 쓰기 호출을 자동 반복하라는 의미가 아니다.
제안의 policy_revision은 단조 증가 **정수**로 확정했다. 원자 비교와 expected_policy_revision 타입을 맞추기 위해서다.
allocation·execution_id·reservations 및 실제 pool/scheduler 필드를 추가해 실행·예약·실제 배치를 구분한다.

CLI 종료 코드는 기존 규약을 유지한다: 성공 0, 입력 2, 기존 conflict 코드 3, 실행/CPU 오류 4.

dom0-manager 담당자는 `Manager.call("cpu-layout", spec, request_id=...)`의 data를 **별도 자원 조회 응답**으로 연결하면 된다.
기존 4필드 Vm DTO에 억지로 추가하지 않는다. 현재 `platform/services/dom0-manager/dom0_manager/drivers/platform.py`의 `_CODE_MAP`에는
RESOURCE_UNSUPPORTED→InvalidArgument(400), OPERATION_IN_PROGRESS·RECONCILE_REQUIRED·
OBSERVATION_UNAVAILABLE→PlatformUnavailable(503), DEADLINE_EXCEEDED→DriverTimeout(504)가 이미 있다.
추가할 항목은 CPU_POLICY_INVALID·CPU_NOT_READY→PlatformUnavailable(503),
CPU_APPLY_INCOMPLETE→InvalidState(409) 세 개다. 현재는 미등록 코드가 DriverError(500)로 처리되므로
담당자가 이 매핑과 별도 조회 응답 연결을 추가해야 한다. 현재 raise_for_envelope는 원본 code를 platform_code로, message를 사람용 detail 문자열로 옮기지만
retryable/details를 구조화된 필드로 전달하지 않는다. 별도 CPU 자원 조회 어댑터에서는 이 원본 정보를 보존할 방법을
정해야 한다. 기존 Vm DTO·오류 응답을 이번 하위 구현이 임의로 확장하지 않는다.
503이라는 이유만으로 변경 요청을 새 key로 자동 반복하지 않는다.
일반 start/stop/reboot와 recover_once 요청을 변경하거나 pin_vcpu()를 구현할 필요가 없다.
CPU 정책 변경을 위한 새 HTTP API는 이번 범위에 없다.

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
위 cpu-policy-set 절차로 명시적으로 등록한다. **이 작업은 별도 실보드 승인 후 수행한다.**

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

## 검증과 실보드 인계

소스 회귀:

```sh
python3 -B -m unittest discover -s platform/tests -p 'test_xenova_cpu.py' -v
bash platform/scripts/check-xenova-platform.sh
bash platform/scripts/check-xenova-platform.sh --metadata
```

전체 회귀의 실제 dom0 unit 그래프 검사는 프로젝트의 systemd 259 loader 환경을 사용한다.
--metadata는 격리된 설정에서 BitBake parse/-e만 수행한다. 이미지나 타깃 바이너리를 빌드하지 않는다.
조립 검증기는 새 CPU 모듈·프리셋·unit을 소스와 바이트 단위로 비교한다. G1 인계 묶음도 새 import를 포함한다.

실보드 affinity 변경·부하 주입·guest/host reboot는 이번 구현 요청으로 실행하지 않았다.
별도 승인 후 다음 증거를 수집한다. 실보드 배치 수락과 성능 수락을 별도로 판정한다.

1. 현재 이미지 영수증, Xen 버전, host_boot_id, 원본 cpu-layout, xl info,
   xl cpupool-list / -c, xl list -c, xl vcpu-list, 정책·runtime 영수증 해시를 PC에 보관한다.
2. UART 확보·관리 Wi-Fi 보존·기본 VM/UUID/자산 목록을 확인한 뒤 승인된 정지 창에서 정책 등록·host 준비를 수행한다.
3. A/B/기본 Debian/추가 Linux를 기존 start로 기동하고 cpu-layout의 모든 실제 마스크와 예약 충돌 부재를 확인한다.
   Linux vCPU 수를 임의로 줄이지 않았는지 함께 확인한다.
4. A/B 협조형 reboot, Linux 일반 reboot, recover_once, 이미 실행 중 start 후 새 execution_id와 동일 policy revision·마스크를 수집한다.
5. 승인된 host reboot 전후 host_boot_id 교체, 새 준비 영수증, 자동 시작 배치를 확인한다.
   CPU prepare stop/restart만으로 기존 VM 정지·마스크 초기화가 전파되지 않는지도 별도 승인 창에서 확인한다.
6. 동일 펌웨어·주행 입력·broker/Gateway 설정으로 pinning 전/후 × Debian 부하 없음/있음의 네 조건을 비교한다.
   부하는 기존 도구를 사용해 공유 코어 집합에서만 실행한다. 조건마다 vCPU 마스크·빈도·실행 시간·샘플 수를 기록한다.
7. guest 계산 주기/명령 간격과 Gateway 수신→출력 지연을 구분한다.
   두 계측 지점을 연결하려면 동기 시각 또는 correlation ID의 기준을 명시한다.
   p95/p99·관측 최대·deadline miss·오류·손실을 기록하고 원시 표본을 보관한다.
   실제 주행 앱이 없다면 타이밍 harness 결과만 보고하며 실제 주행 성능 PASS로 표시하지 않는다.

보드 적용과 새 이미지 배포는 별도 단계다. FakeXen 회귀를 실제 affinity 또는 성능 검증으로 대체해 보고하지 않는다.

### 별도 승인 후 사용할 실보드 명령

다음은 **인계 명령**이며 이번 구현 중 실행하지 않았다. 실제 guest/host reboot와 부하 주입은 별도 승인한다.

dom0에서 관측 증거를 수집한다. 동일 명령을 시작·재부팅·복구의 각 전후에 실행하고 출력 파일명만 단계별로 바꾼다.

```sh
umask 077
evidence=/root/cpu-layout-acceptance-01
mkdir -m 700 "$evidence"
cat /proc/sys/kernel/random/boot_id > "$evidence/host-boot-id"
cat /proc/uptime > "$evidence/dom0-monotonic"
xenova-vm cpu-layout --json > "$evidence/cpu-layout.json" 2> "$evidence/cpu-layout.stderr"
xl info > "$evidence/xl-info.txt"
xl cpupool-list > "$evidence/pools.txt"
xl cpupool-list -c > "$evidence/pool-cpus.txt"
xl list -v > "$evidence/domains.txt"
xl list -c > "$evidence/domain-pools.txt"
xl vcpu-list > "$evidence/vcpu-affinity.txt"
sha256sum /srv/xenova/cpu-policy.json /run/xenova/cpu-host.json > "$evidence/files.sha256"
```

복구는 새 실행 문맥으로 요청을 만든다. `TARGET_UUID`에는 현재 실행 중인 시험 대상 UUID를 직접 지정한다.
기존 stop 의도가 있는 VM을 이 명령으로 running으로 바꾸지 않는다.

```sh
TARGET_UUID='<현재 running 의도를 가진 시험 VM UUID>'
export TARGET_UUID
PYTHONPATH=/usr/lib/xenova python3 - <<'PY'
import json, os, time
from pathlib import Path
from first_install_contract import uuid4
from xenova_management import Manager
target = os.environ['TARGET_UUID']
response = Manager().call('probe', {'vm_id':target, 'timeout_ms':15000})
assert response['ok'], response['error']
o = response['data']['observation']
assert o['intent_applicable'] and o['desired_state'] == 'running'
present = o['presence'] == 'present'
if present:
    assert o['execution_status'] == 'managed' and o['execution_id']
request = {
    'vm_id':target, 'recovery_attempt_id':uuid4(),
    'expected_host_boot_id':o['host_boot_id'],
    'expected_lifecycle_generation':o['lifecycle_generation'],
    'expected_intent_revision':o['intent_revision'],
    'expected_execution':({'execution_id':o['execution_id']} if present else
                          {'absent_observation_revision':o['observation_revision']}),
    'mode':'graceful-restart' if present else 'start-if-absent',
    'deadline_monotonic_ms':int((time.monotonic()+90)*1000),
}
path = Path('/root/cpu-recovery-request.json')
assert not path.exists(), 'use a fresh owned request path'
path.write_text(json.dumps(request)+'\n')
PY
xenova-vm recover-once --spec-file /root/cpu-recovery-request.json --json
xenova-vm recover-once --spec-file /root/cpu-recovery-request.json --execute --json
xenova-vm cpu-layout "$TARGET_UUID" --json
```

동일 시도의 재전달에는 파일 내용·attempt ID를 유지한다. 다음 장애에는 새 probe와 새 요청 파일을 만든다.
호스트 재부팅 후에는 이전 요청 파일의 host_boot_id/deadline을 재사용하지 않는다.

부하 시험은 **시험 Debian guest 안**에서 실행한다. 새 패키지 없이 기존 Python 표준 라이브러리만 사용하며 60초로 제한한다.
guest의 모든 vCPU는 이미 Xen 공유 집합으로 제한돼 있어야 한다. 전용 주행 VM이나 dom0에서 아래 부하를 실행하지 않는다.

```sh
python3 - <<'PY'
import json, multiprocessing as mp, os, time
def burn(end):
    value = 1
    while time.monotonic() < end:
        for _ in range(10000):
            value = (value * 1664525 + 1013904223) & 0xffffffff
if __name__ == '__main__':
    started = time.monotonic()
    workers = max(1, os.cpu_count() or 1)
    children = [mp.Process(target=burn, args=(started+60,)) for _ in range(workers)]
    for child in children: child.start()
    for child in children: child.join(70)
    for child in children:
        if child.is_alive():
            child.terminate()
            child.join()
    print(json.dumps({'guest_workers':workers, 'elapsed_s':time.monotonic()-started,
                      'exit_codes':[child.exitcode for child in children]}))
PY
```

pinning 전 기준선은 활성화 전에 확보하거나, 동일 정지 조건에서 `exclusive=[]`,
`shared_pcpus=[0,1,2,3]`인 명시적 비교 정책을 cpu-policy-set으로 등록·준비해 수집한다.
그다음 새 revision으로 A/B 전용 정책을 적용한다. 정책/anchor/journal을 삭제해 legacy 모드를 만드는 방식으로 비교하지 않는다.
각 정책에서 Debian 부하 없음/있음을 각각 측정한다. 펌웨어·vCPU 수·메모리·broker·입력은 유지한다.
주행 명령 표본은 실제 앱/Gateway의 기존 계측 도구를 사용한다. 아직 없는 이벤트명이나 타임스탬프 필드를 이 구현이 가정하지 않는다.

host reboot 수락 시에는 위 증거를 PC에 회수하고 관리 Wi-Fi/UART 복원 조건을 확인한 뒤,
승인된 창에서 dom0의 `xenova-vm reboot-board`를 사용한다. 재접속 후 새 host_boot_id,
`xenova-cpu-prepare.service`의 완료 로그와 cpu-layout, 기존 VM UUID·자산 해시를 비교한다.
