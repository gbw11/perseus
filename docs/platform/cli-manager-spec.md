# Xenova CLI·Manager 기능 명세서

Xenova는 Raspberry Pi 4의 Xen dom0에서 VM 등록·실행·관측·복구·자산 관리와 관리용 Wi-Fi 접속을 제공한다. 이 문서는 설치된 명령의 사용법과 공통 Manager 계약을 함께 설명한다. 이미지 제작은 [빌드 가이드](build-guide.md)를 따른다. 기능이나 절차가 바뀌면 이 문서를 갱신한다.

소스 대조 기준: [통합 소스 `347d935`](https://lab.ssafy.com/s15-final/S15P31E110/-/tree/347d935cb7ef09e07f3082a13af1733bb6acd2cb). 예제는 해당 소스의 CLI·Manager 계약을 따른다.

<a id="toc"></a>
## 목차

- [읽는 방법과 공통 사용법](#usage)
- [명령어 색인](#command-index)
- [1. VM 등록·조회](#registration)
- [2. VM 실행 제어](#control)
- [3. 실행 관측](#observation)
- [4. 작업 이력](#history)
- [5. 조건부 일회성 복구](#recovery)
- [6. 자원·Zephyr firmware 변경](#changes)
- [7. 자동 시작·VM 정책·설정](#policies)
  - [CPU 정책 변경·실제 배치 조회](#cpu-contract)
- [8. 자산 수명주기](#assets)
- [9. Wi-Fi·콘솔·SSH](#access)
- [10. 운영 진단·설치 도구](#diagnostics)
- [11. host gate·보드 종료·서비스 내부 명령](#host)
- [공통 계약 A. 응답·오류·멱등성](#contract-response)
- [공통 계약 B. 신원·실행 의도·필드 사전](#contract-identity)
- [공통 계약 C. 복구·journal·중단 복원](#contract-journal)
- [공통 계약 D. 부팅·종료·배포·저장 경로](#contract-boot)
- [연동 명세. Python Manager·HTTP·WebSocket](#integration)
- [구현 근거](#sources)

<a id="usage"></a>
## 읽는 방법과 공통 사용법

**실행 위치는 별도 표시가 없으면 보드 dom0의 root 셸이다.** 관리 PC·Linux domU 내부에서 실행하는 예시는 따로 표시한다. VM 자산은 쓰기 가능한 ext4 `/srv/xenova`에 있으며 초기화 표식·자산 신원 검사를 통과해야 한다.

각 명령은 **인자 → 사용 조건 → 실행 예시 → 정상 응답 → 오류 응답 → 확인 방법** 순서로 설명한다. 예시의 `read` 프롬프트에는 자신이 관리하는 시험 대상을 입력한다. 다른 절의 변수나 이전 명령 실행을 전제로 하지 않는다. 게시 버전은 `templates`에서 확인하고 예시의 버전과 다르면 실제 게시 버전을 입력한다.

### 대상과 작업 키

- `TARGET`은 영구 UUID 또는 **등록된 내부 이름**이다. 표시 이름으로 검색하지 않는다. 보관된 VM과 자동 연동에는 UUID를 사용한다.
- 공통 CLI에서 위치 TARGET 대신 `--vm-id UUID`를 쓸 수 있다. 둘 다 주면 `--vm-id`가 우선한다.
- 새 작업의 `KEY`는 예시에서 UUID로 한 번 발급한다. **같은 작업 재전달은 원 KEY·인자 전체를 유지**하고, 새 작업은 새 키를 사용한다. 원 키와 응답의 `operation_id`를 작업 기록에 보관한다. 셸을 다시 열어 키를 새로 발급하는 것은 재전달이 아니다.
- create는 별도 규칙으로 `create:<정규화한 VM UUID>`를 사용한다. 복구는 `recovery_attempt_id`와 원 요청 파일로 중복을 판정한다.
- `request_id`는 호출 추적용이며 중복 실행 방지 키가 아니다. CLI가 자동 발급하고 Python 호출자는 지정할 수 있다.

### 출력과 실제 변경

`--json`은 출력 형식뿐 아니라 **공통 Manager 경로를 선택하는 옵션**이다.

| 인터페이스 | 출력·주의점 |
|---|---|
| `xenova-vm … --json` | stdout에 Manager envelope 하나. 하위 도구 출력은 stderr |
| Manager 전용 명령 | `--json` 없이도 Manager로 호출하며 JSON을 들여써 출력 |
| 운영 CLI 전용 명령 | 자체 JSON·문자열·도구 로그·대화형 콘솔. **`--json`을 붙이지 않음** |
| `xenova-wifi` | 자체 JSON/오류·종료 코드. `--json` 옵션 없음 |
| Python `Manager.call` | 동기 dict 반환. stdout에 쓰지 않음 |
| HTTP/WebSocket | 별도 모델과 상태 코드. Manager envelope와 다름 |

Manager 전용 명령은 `templates, list, get, create, retire, delete, restore, purge, operation, update-resources, firmware-transition, recover-once, recover_once, recovery-operation, control-operation, probe, snapshot, execution-context, host-resume, cpu-policy-set, cpu-layout, cpu-host-prepare`다. `status --json`은 `list` 별칭이다. 나머지 공통 제어·설정은 `--json`으로 Manager를 선택한다.

| 변경 구분 | 명령 |
|---|---|
| `--execute` 생략 시 계획, 지정 시 실행 | `create, retire/delete, restore, purge, update-resources, firmware-transition, recover-once, cpu-policy-set, cpu-host-prepare`. 운영 `firmware-replace`도 자체 계획 옵션 사용 |
| **`--execute`와 무관하게 즉시 실행** | `start, stop, reboot, destroy, autostart-set, board-shutdown-set, config, host-resume` |
| **즉시 운영 동작** | `autostart, host-init, host-quiesce, shutdown-all, poweroff, reboot-board` 및 Wi-Fi configure/activate |
| 읽기 전용 실제 CPU 조회 | `cpu-layout`. 정책·affinity·VM 상태를 변경하지 않음 |
| Xen 조회·runtime 관측 게시 | `get, list, probe, execution-context` |
| 캐시만 읽음 | `snapshot`. Xen·공통 operation lock 없이 조회 |

**응답 예시는 형식을 설명하는 예이며 실측 로그가 아니다.** 명령별 JSON은 별도 표시가 없으면 정상 `data` 또는 오류 `error`의 **주요 필드 발췌**다. 전체 필드는 뒤쪽 공통 계약에서 정의한다. UUID·domid·revision·IP는 설명용 값이다. 오류 `message`는 상세 진단 문자열이므로 코드·details를 기준으로 처리한다.

정상 `get`의 envelope 예(내부 vm은 주요 필드 발췌):

```json
{
  "schema_version": "1.0",
  "ok": true,
  "request_id": "11111111-1111-4111-8111-111111111111",
  "operation_id": null,
  "replayed": false,
  "data": {
    "vm": {
      "vm_id": "22222222-2222-4222-8222-222222222222",
      "desired_state": "stopped",
      "domid": null,
      "execution_id": null,
      "app_readiness": "unknown"
    }
  },
  "error": null
}
```

위 `vm` 객체도 VMView의 주요 필드 발췌다. 실패 시 `ok=false, data=null`이고 `error={code,message,retryable,details}`다. Manager CLI는 성공 `0`, 입력/대상 검증 `2`, 충돌 `3`, 실행·저장·관측 오류 `4`를 반환한다. [전체 오류표](#error-codes)를 함께 본다.

<a id="command-index"></a>
## 명령어 색인

[VM 필드 사전](#vmview) · [관측 필드 사전](#observation-fields) · [복구 요청 필드](#recovery-spec) · [부팅 판정표](#boot-policy) · [오류 코드](#error-codes)

| 명령 | 용도 |
|---|---|
| [`xenova-vm templates`](#cmd-templates) | 게시 템플릿 조회 |
| [`xenova-vm list`](#cmd-list) | 등록·보관 VM 목록 |
| [`xenova-vm get`](#cmd-get) | VM 한 대의 현재 상태·설정 조회 |
| [`xenova-vm status`](#cmd-status) | 운영 상태와 Manager 목록 별칭 |
| [`xenova-vm create`](#cmd-create) | 독립 VM 등록 |
| [`xenova-vm start`](#cmd-start) | 등록 VM 시작 |
| [`xenova-vm stop`](#cmd-stop) | VM 협조형 정지 |
| [`xenova-vm reboot`](#cmd-reboot) | 같은 자산으로 새 실행 시작 |
| [`xenova-vm destroy`](#cmd-destroy) | 명시적 강제 도메인 제거 |
| [`xenova-vm execution-context`](#cmd-execution-context) | 현재 실행 신원 조회 |
| [`xenova-vm probe`](#cmd-probe) | 제한시간을 둔 실제 Xen 관측 |
| [`xenova-vm snapshot`](#cmd-snapshot) | 공통 작업을 기다리지 않는 캐시 조회 |
| [`xenova-vm operation`](#cmd-operation) | 자산 작업 이력 |
| [`xenova-vm control-operation`](#cmd-control-operation) | 사용자 제어 이력 |
| [`xenova-vm recovery-operation`](#cmd-recovery-operation) | 조건부 복구 이력 |
| [`xenova-vm recover-once`](#cmd-recover-once) | 현재 문맥이 일치할 때 한 번 복구 |
| [`xenova-vm update-resources`](#cmd-update-resources) | 정지 Linux의 vCPU·메모리 변경 |
| [`xenova-vm firmware-transition`](#cmd-firmware-transition) | Zephyr 활성 firmware·프로필 전환 |
| [`xenova-vm firmware-replace`](#cmd-firmware-replace) | 같은 Zephyr 프로필의 활성 firmware 교체 |
| [`xenova-vm autostart-set`](#cmd-autostart-set) | VM 자동 시작 플래그 저장 |
| [`xenova-vm board-shutdown-set`](#cmd-board-shutdown-set) | 보드 종료 시 VM 정책 저장 |
| [`xenova-vm cpu-policy-set`](#cmd-cpu-policy-set) | 정지 조건에서 UUID 기반 CPU 정책 등록·변경 |
| [`xenova-vm cpu-layout`](#cmd-cpu-layout) | 실제 Xen hard affinity·전용성 조회 |
| [`xenova-vm config`](#cmd-config) | 검증된 runtime xl 설정 생성 |
| [`xenova-vm autostart`](#cmd-autostart) | 등록 VM 전체의 시작 판정·실행 |
| [`xenova-vm retire`](#cmd-retire) | 정지 자산 보관 |
| [`xenova-vm delete`](#cmd-retire) | retire 별칭 |
| [`xenova-vm restore`](#cmd-restore) | 보관 자산 복원 |
| [`xenova-vm purge`](#cmd-purge) | 보관 자산 영구 제거 |
| [`xenova-wifi configure`](#cmd-wifi-configure) | SSID·PSK와 국가 저장 |
| [`xenova-wifi activate`](#cmd-wifi-activate) | 저장한 Wi-Fi 활성화 |
| [`xenova-wifi status`](#cmd-wifi-status) | 인증·주소·내부망 중복 조회 |
| [`xenova-vm console`](#cmd-console) | Xen PV 콘솔 접속 |
| [`xenova-vm check-xen`](#cmd-check-xen) | dom0 Xen 제어 기반 확인 |
| [`xenova-vm check-network`](#cmd-check-network) | 격리된 내부 브리지 검사 |
| [`xenova-vm diagnose`](#cmd-diagnose) | 부팅 인자·가동 시간·시각 진단 |
| [`xenova-domain-identity`](#cmd-domain-identity) | libxenmanage로 incarnation 조회 |
| [`xenova-verify-artifacts`](#cmd-verify-artifacts) | 생성 cfg·Xen/QEMU 설치 내용 검사 |
| [`update_guest_ssh.py`](#cmd-update-guest-ssh) | 정지 Linux rootfs의 SSH service 정책 갱신 |
| [`xenova-vm cpu-host-prepare`](#cmd-cpu-host-prepare) | 설치/부팅 내부용 dom0 CPU 준비·원복 |
| [`xenova-vm host-init`](#cmd-host-init) | 이번 부팅의 host barrier 초기화 |
| [`xenova-vm host-quiesce`](#cmd-host-quiesce) | 전역 시작·복구 gate 닫기 |
| [`xenova-vm host-resume`](#cmd-host-resume) | 현재 barrier가 일치할 때 gate 재개 |
| [`xenova-vm shutdown-all`](#cmd-shutdown-all) | 등록 게스트 전체 종료 |
| [`xenova-vm poweroff`](#cmd-poweroff) | 조율 후 보드 전원 종료 |
| [`xenova-vm reboot-board`](#cmd-reboot-board) | 조율 후 보드 재부팅 |
| [`xenova-bridge-up`](#cmd-bridge-up) | 내부 bridge 생성·주소 준비 |
| [`xenova-vm dhcp-ready`](#cmd-dhcp-ready) | dnsmasq의 UDP/67 준비 확인 |
| [`xenova-first-boot grow/register`](#cmd-first-boot) | 저장소 확장·factory 등록 |

별칭: [`delete → retire`](#cmd-retire) · [`recover_once → recover-once`](#cmd-recover-once) · [`status --json → list`](#cmd-status). `xenova-first-boot` 항목은 grow/register를 함께 설명한다.


<a id="registration"></a>
## 1. VM 등록·조회

등록은 자산과 신원을 준비하는 작업이다. `create` 완료 후 `start`를 별도로 호출한다.

<a id="cmd-templates"></a>
### `xenova-vm templates` — 게시 템플릿 조회

**인자**

선택: `--json`. 대상 인자 없음.

**사용 조건**

설치된 카탈로그·원본 자산을 읽을 수 있어야 한다.

**실행 예시**

```bash
xenova-vm templates --json
```

**정상 응답**

`data` 예: `{"templates":[{"template_id":"debian-13-arm64","template_version":"1.0.2","guest_kind":"linux","default_resources":{"vcpus":1,"memory_mib":512}}]}`. [Template 전체 필드](#template-fields)를 참조한다.

**오류 응답**

카탈로그/manifest 무결성 오류는 `ARTIFACT_INVALID` 등, 종료 `4`. 빈 목록을 특정 템플릿의 설치 성공으로 해석하지 않는다.

**확인 방법**

원하는 `template_id`와 정확한 `template_version`, `resource_limits`·`capabilities`·`directory`를 확인한다.

**Manager 매핑:** `templates / {}`

[명령어 색인으로](#command-index)

<a id="cmd-list"></a>
### `xenova-vm list` — 등록·보관 VM 목록

**인자**

선택: `--lifecycle REGISTERED|RETIRED|PURGING|PURGED|all`, 기본 `REGISTERED`. `--json`.

**사용 조건**

전체 대상에 대한 live 관측이 포함된다. 한 대상의 관측 실패도 호출 오류가 될 수 있다.

**실행 예시**

```bash
xenova-vm list --lifecycle all --json
```

**정상 응답**

`data` 예: `{"vms":[{"vm_id":"22222222-2222-4222-8222-222222222222","name":"시험 Debian","lifecycle":"REGISTERED","domid":null}]}`.

**오류 응답**

잘못된 lifecycle은 `INVALID_SPEC`, 종료 `2`. 공통 작업 경합은 `OPERATION_IN_PROGRESS`, 종료 `3`.

**확인 방법**

`lifecycle`로 자산 상태를, `desired_state`로 의도를, `xen_state/domid`로 현재 실행을 각각 읽는다.

**Manager 매핑:** `list / {"lifecycle":"all"}`. lifecycle 생략 가능.

[명령어 색인으로](#command-index)

<a id="cmd-get"></a>
### `xenova-vm get` — VM 한 대의 현재 상태·설정 조회

**인자**

`TARGET`: UUID 또는 등록 내부 이름. `--json`: Manager envelope 출력.

**사용 조건**

등록·보관·purge tombstone을 UUID로 조회한다. 현재 Xen 관측과 runtime 게시를 수행한다.

**실행 예시**

```bash
xenova-vm list --lifecycle all --json
read -r -p "조회할 VM UUID: " VM_ID
xenova-vm get "$VM_ID" --json
```

**정상 응답**

`data`는 `{"vm":VMView}`. 예: `{"vm":{"vm_id":"22222222-2222-4222-8222-222222222222","lifecycle":"REGISTERED","desired_state":"stopped","domid":null,"execution_id":null}}`.

**오류 응답**

미등록 UUID는 `error` 발췌 `{"code":"VM_NOT_FOUND","retryable":false}`, 종료 `2`. Xen 관측 실패는 `OBSERVATION_UNAVAILABLE` 등이며 부재로 바꾸지 않는다.

**확인 방법**

`configuration`과 `configuration_sha256`는 현재 설정이다. `initial_spec`은 최초 명세이므로 자원 변경 후 두 값을 혼동하지 않는다. 전체 항목은 [VMView](#vmview).

**Manager 매핑:** `get / {"vm_id":"UUID"}`

[명령어 색인으로](#command-index)

<a id="cmd-status"></a>
### `xenova-vm status` — 운영 상태와 Manager 목록 별칭

**인자**

인자 없음. `--json` 유무에 따라 반환 모델이 다르다.

**사용 조건**

운영 상태·자산 검사와 시각 진단을 읽는다.

**실행 예시**

```bash
xenova-vm status
xenova-vm status --json
```

**정상 응답**

옵션 없음: 자체 JSON `{"instances":[],"domains":[],"time":{}}` 형태이며 각 instance의 자산 오류·Xen flags·graceful 지원·유효 종료 정책을 포함한다. `--json`: `list / {}`의 `data.vms`.

**오류 응답**

운영 경로 실패: stderr `[xenova] ERROR: …`, 종료 `1`. `--json` 경로는 Manager 오류 envelope/종료 코드.

**확인 방법**

전체 등록 목록은 `list`, 개별 실행 신원은 `get/probe`로 확인한다. `status --json --lifecycle …`의 lifecycle은 별칭 경로에 전달되지 않으므로 필터가 필요하면 `list`를 쓴다.

**Manager 매핑:** `status --json`만 `list / {}`로 매핑. Python action `status`는 없음.

[명령어 색인으로](#command-index)

<a id="cmd-create"></a>
### `xenova-vm create` — 독립 VM 등록

**인자**

| 인자 | 조건 |
|---|---|
| `--vm-id UUID` | 필수, 신규 비-nil UUID |
| `--name TEXT` | 필수, 표시 이름 1..63문자. create 위치 이름도 허용 |
| `--template-id ID` / `--template ID` | 공개 템플릿 ID |
| `--template-version VERSION` | 정확한 게시 버전 |
| `--vcpus N / --memory-mib M` | 둘 다 필수, 템플릿 범위의 정수 |
| `--auto-ip` / `--ipv4 ADDRESS` | 상호 배타. 생략도 자동 할당 |
| `--execute / --json` | 계획 확인 후 실행 |

**사용 조건**

카탈로그·저장 용량·UUID/이름/MAC/IP 소유권 검사를 통과해야 한다. [생성·주소 계약](#creation-contract)을 따른다.

**실행 예시**

```bash
xenova-vm templates --json
read -r -p "게시된 Debian template_version: " VERSION
VM_ID="$(cat /proc/sys/kernel/random/uuid)"
printf '새 VM UUID: %s\n' "$VM_ID"
xenova-vm create --vm-id "$VM_ID" --name "시험 Debian" \
  --template-id debian-13-arm64 --template-version "$VERSION" \
  --vcpus 1 --memory-mib 512 --auto-ip --json
# 위 계획의 ok=true를 확인한 뒤 같은 입력으로 실행한다.
xenova-vm create --vm-id "$VM_ID" --name "시험 Debian" \
  --template-id debian-13-arm64 --template-version "$VERSION" \
  --vcpus 1 --memory-mib 512 --auto-ip --execute --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

계획은 `data.plan`을 포함한다. 최초 완료 `data`는 **VMView 자체**: `{"vm_id":"22222222-2222-4222-8222-222222222222","lifecycle":"REGISTERED","generation":0,"desired_state":"stopped","intent_revision":1}`. `{vm:…}`로 감싸지 않는다.

**오류 응답**

같은 UUID의 다른 명세는 `IDEMPOTENCY_CONFLICT`(3). 버전 없음은 `TEMPLATE_VERSION_NOT_FOUND`(2), 주소 충돌 `IP_CONFLICT`(3), 용량 부족 `INSUFFICIENT_STORAGE`(4).

**확인 방법**

`get`에서 UUID·내부 이름·MAC/IP·자원을 확인한다. 완료는 등록만 뜻하며 `domid`가 없어도 정상이다. 작업 조회는 `xenova-vm operation --idempotency-key "create:$VM_ID" --json`.

**Manager 매핑:** `create / {vm_id,name,template_id,template_version,vcpus,memory_mib,ipv4?}`. 키는 `create:<정규 UUID>`로 고정.

[명령어 색인으로](#command-index)

<a id="control"></a>
## 2. VM 실행 제어

아래 명령은 `--execute` 없이 즉시 실행한다. 수락하면 의도 revision이 증가하며 물리 동작 실패 후에도 수락한 의도는 유지한다. [수락·재전달 계약](#acceptance-contract)을 함께 확인한다.

<a id="cmd-start"></a>
### `xenova-vm start` — 등록 VM 시작

**인자**

`TARGET`: UUID 또는 등록 내부 이름. `--json`: Manager envelope 출력. `--idempotency-key KEY`는 선택이나 추적·재전달 시 권장한다.

**사용 조건**

REGISTERED·유효 자산·열린 host gate·Xen/내부망이 필요하다. CPU 정책 활성 시 이번 boot/revision의 host 준비와 실제 배치가 일치해야 한다. 이미 running/blocked인 분기도 실제 affinity를 확인하고 중복 생성·자동 repin하지 않는다.

**실행 예시**

```bash
xenova-vm list --json
read -r -p "start할 시험 VM UUID: " VM_ID
KEY="$(cat /proc/sys/kernel/random/uuid)"
printf '작업 키: %s\n' "$KEY"
xenova-vm start "$VM_ID" --idempotency-key "$KEY" --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

`data` 예: `{"vm":{"desired_state":"running","domid":6,"execution_id":"33333333-3333-4333-8333-333333333333","app_readiness":"unknown"}}`. envelope `operation_id`가 제어 journal을 가리킨다.

**오류 응답**

종료 gate는 `HOST_QUIESCING`(3). 수락 후 물리 실패는 `XEN_EXECUTION_FAILED`(4), 실행 불확실성은 `RECONCILE_REQUIRED`(4). `details.accepted/intent_revision/operation_id`를 읽고 새 키로 즉시 반복하지 않는다.

**확인 방법**

`get`에서 `desired_state=running`와 실제 `domid`를 확인한다. `control-operation --operation-id`로 원 결과를 조회한다. Xen 시작 성공은 앱 준비나 운행 허가가 아니다. CPU 정책 활성 시 `cpu-layout`의 host.ready·실제 마스크도 확인한다. CPU 관련 오류와 불완전 실행 기록은 [CPU 계약](#cpu-contract)을 따른다.

**Manager 매핑:** `start / {vm_id}`.

[명령어 색인으로](#command-index)

<a id="cmd-stop"></a>
### `xenova-vm stop` — VM 협조형 정지

**인자**

`TARGET`: UUID 또는 등록 내부 이름. `--json`: Manager envelope 출력. `--idempotency-key KEY`는 선택이나 추적·재전달 시 권장한다. `--force` 선택 시 즉시 제거 방식.

**사용 조건**

기본은 협조형 종료이며 기본 90초 안에 실제 제거를 확인한다. 이미 부재여도 stopped 의도를 수락한다. Network Starter는 활성 프로필과 현재 XenStore 협조형 기능이 모두 준비돼야 한다. legacy Starter는 명시적 `--force`가 필요하다.

**실행 예시**

```bash
xenova-vm list --json
read -r -p "stop할 시험 VM UUID: " VM_ID
KEY="$(cat /proc/sys/kernel/random/uuid)"
printf '작업 키: %s\n' "$KEY"
xenova-vm stop "$VM_ID" --idempotency-key "$KEY" --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

`data` 예: `{"vm":{"desired_state":"stopped","domid":null,"execution_id":null,"app_readiness":"unknown"}}`. envelope `operation_id`가 제어 journal을 가리킨다.

**오류 응답**

종료 gate는 `HOST_QUIESCING`(3). 수락 후 물리 실패는 `XEN_EXECUTION_FAILED`(4), 실행 불확실성은 `RECONCILE_REQUIRED`(4). `details.accepted/intent_revision/operation_id`를 읽고 새 키로 즉시 반복하지 않는다.

**확인 방법**

`get`에서 `desired_state=stopped`·도메인 부재를 확인하고 `control-operation --operation-id`로 원 결과를 조회한다. CPU 정책 오류가 있어도 기존 실행 신원을 확인한 종료는 허용한다. CPU 예약은 해제하지 않는다.

**Manager 매핑:** `stop / {vm_id,force?}`.

[명령어 색인으로](#command-index)

<a id="cmd-reboot"></a>
### `xenova-vm reboot` — 같은 자산으로 새 실행 시작

**인자**

`TARGET`: UUID 또는 등록 내부 이름. `--json`: Manager envelope 출력. `--idempotency-key KEY`는 선택이나 추적·재전달 시 권장한다. `--force` 선택 시 즉시 제거 방식.

**사용 조건**

현재 실행 중인 협조형 VM을 선택한다. 정지 VM은 거절한다. UUID·디스크/firmware·MAC/IP는 유지하고 실행 ID를 바꾼다. CPU 정책 활성 시 Zephyr 재시작뿐 아니라 Linux toolstack 재생성 후 `bind_launch`도 같은 정책과 실제 affinity를 검증한다. Network Starter는 활성 프로필과 현재 XenStore 협조형 기능이 모두 준비돼야 한다. legacy Starter는 명시적 `--force`가 필요하다.

**실행 예시**

```bash
xenova-vm list --json
read -r -p "reboot할 시험 VM UUID: " VM_ID
KEY="$(cat /proc/sys/kernel/random/uuid)"
printf '작업 키: %s\n' "$KEY"
xenova-vm reboot "$VM_ID" --idempotency-key "$KEY" --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

`data` 예: `{"vm":{"desired_state":"running","domid":6,"execution_id":"33333333-3333-4333-8333-333333333333","app_readiness":"unknown"}}`. envelope `operation_id`가 제어 journal을 가리킨다.

**오류 응답**

정지 VM은 사전 플랫폼 검사 오류로 거절한다. 종료 gate는 `HOST_QUIESCING`(3). 수락 후 물리 실패는 `XEN_EXECUTION_FAILED`(4), 실행 불확실성은 `RECONCILE_REQUIRED`(4). `details.accepted/intent_revision/operation_id`를 읽고 새 키로 즉시 반복하지 않는다.

**확인 방법**

전후 `execution_id`가 달라지고 UUID·설정·자산은 같아야 한다. `get`에서 `desired_state=running`와 실제 `domid`를 확인한다. `control-operation --operation-id`로 원 결과를 조회한다. Xen 시작 성공은 앱 준비나 운행 허가가 아니다. CPU 정책 활성 시 `cpu-layout`의 host.ready·실제 마스크도 확인한다. CPU 관련 오류와 불완전 실행 기록은 [CPU 계약](#cpu-contract)을 따른다.

**Manager 매핑:** `reboot / {vm_id,force?}`.

[명령어 색인으로](#command-index)

<a id="cmd-destroy"></a>
### `xenova-vm destroy` — 명시적 강제 도메인 제거

**인자**

`TARGET`: UUID 또는 등록 내부 이름. `--json`: Manager envelope 출력. `--idempotency-key KEY`는 선택이나 추적·재전달 시 권장한다. `--force` 필수.

**사용 조건**

`--force`가 필수다. 선택한 VM의 실행만 즉시 제거하며 디스크·등록은 보존한다. 대상 VM에 저장하지 않은 데이터가 있으면 사라질 수 있다.

**실행 예시**

```bash
xenova-vm list --json
read -r -p "destroy할 시험 VM UUID: " VM_ID
KEY="$(cat /proc/sys/kernel/random/uuid)"
printf '작업 키: %s\n' "$KEY"
xenova-vm destroy "$VM_ID" --force --idempotency-key "$KEY" --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

`data` 예: `{"vm":{"desired_state":"stopped","domid":null,"execution_id":null,"app_readiness":"unknown"}}`. envelope `operation_id`가 제어 journal을 가리킨다.

**오류 응답**

종료 gate는 `HOST_QUIESCING`(3). 수락 후 물리 실패는 `XEN_EXECUTION_FAILED`(4), 실행 불확실성은 `RECONCILE_REQUIRED`(4). `details.accepted/intent_revision/operation_id`를 읽고 새 키로 즉시 반복하지 않는다.

**확인 방법**

`get`에서 `desired_state=stopped`·도메인 부재를 확인하고 `control-operation --operation-id`로 원 결과를 조회한다. CPU 정책 오류가 있어도 기존 실행 신원을 확인한 종료는 허용한다. CPU 예약은 해제하지 않는다.

**Manager 매핑:** `destroy / {vm_id,force?}`에서 force=true 필수.

[명령어 색인으로](#command-index)

<a id="observation"></a>
## 3. 실행 관측

`get/execution-context/probe`는 현재 Xen을 확인한다. `snapshot`은 마지막 관측 캐시를 읽는다. 관측 오류·실제 부재·stale 캐시는 서로 다른 상태다.

<a id="cmd-execution-context"></a>
### `xenova-vm execution-context` — 현재 실행 신원 조회

**인자**

`TARGET`: UUID 또는 등록 내부 이름. `--json`: Manager envelope 출력.

**사용 조건**

현재 Xen incarnation과 플랫폼 launch 기록의 일치를 확인한다.

**실행 예시**

```bash
xenova-vm list --json
read -r -p "실행 신원을 볼 VM UUID: " VM_ID
xenova-vm execution-context "$VM_ID" --json
```

**정상 응답**

축약 객체가 아니라 `data.vm`의 전체 VMView다. 예: `{"vm":{"host_boot_id":"44444444-4444-4444-8444-444444444444","execution_id":null,"execution_context":null}}`.

**오류 응답**

미등록 UUID는 `error` 발췌 `{"code":"VM_NOT_FOUND","retryable":false}`, 종료 `2`. Xen 관측 실패는 `OBSERVATION_UNAVAILABLE` 등이며 부재로 바꾸지 않는다.

**확인 방법**

실행 중인데 `execution_id=null`이면 외부 생성·신원 불일치 등일 수 있다. `probe.execution_status`와 비교하고 domid만 보고 플랫폼 실행으로 채택하지 않는다.

**Manager 매핑:** `execution-context / {vm_id}`

[명령어 색인으로](#command-index)

<a id="cmd-probe"></a>
### `xenova-vm probe` — 제한시간을 둔 실제 Xen 관측

**인자**

`TARGET`: UUID 또는 등록 내부 이름. `--json`: Manager envelope 출력. `--timeout-ms N`: 정수 1..15000, 기본 15000.

**사용 조건**

공통 operation lock이 바쁘면 대기하지 않고 구조화 오류를 반환한다. Xen 호출과 snapshot 게시 잠금에 budget을 적용한다.

**실행 예시**

```bash
xenova-vm list --json
read -r -p "관측할 VM UUID: " VM_ID
xenova-vm probe "$VM_ID" --timeout-ms 5000 --json
```

**정상 응답**

`data` 예: `{"observation":{"presence":"absent","xen_state":"stopped","execution_status":"absent","domid":null,"execution_id":null,"observation_revision":2}}`. [전체 필드](#observation-fields).

**오류 응답**

잠금 경합 `OPERATION_IN_PROGRESS`(3), 기한 만료 `DEADLINE_EXCEEDED`(4), Xen 실패 `OBSERVATION_UNAVAILABLE`(4), 관측 중 교체 `TARGET_CHANGED`(3). 오류 snapshot 게시 실패가 원래 envelope를 가리지 않는다.

**확인 방법**

`ok`가 참인 뒤 `presence`를 판정한다. 실행 중이면 `execution_status=managed`와 기대 `execution_id`까지 확인한다. 오류를 absent로 해석하지 않는다.

**Manager 매핑:** `probe / {vm_id,timeout_ms}`. Python에서는 timeout_ms 필수.

[명령어 색인으로](#command-index)

<a id="cmd-snapshot"></a>
### `xenova-vm snapshot` — 공통 작업을 기다리지 않는 캐시 조회

**인자**

`TARGET`: UUID 또는 등록 내부 이름. `--json`: Manager envelope 출력. `--max-age-ms N`: 필수, 정수 1..120000.

**사용 조건**

UUID로 캐시를 읽는다. 등록 조회·Xen 호출·공통 operation lock을 사용하지 않는다.

**실행 예시**

```bash
read -r -p "캐시를 볼 VM UUID: " VM_ID
xenova-vm snapshot "$VM_ID" --max-age-ms 3000 --json
```

**정상 응답**

성공 관측 없음의 예: `{"snapshot":{"stale":true,"unknown":true,"age_ms":null,"last_success":null,"current":null}}`. 조회 `ok=true`도 현재 실행의 확인 성공을 뜻하지 않는다.

**오류 응답**

잘못된 max_age/UUID는 `INVALID_SPEC`(2). 유효 UUID에 캐시가 없는 경우는 일반적으로 unknown snapshot이며 VM_NOT_FOUND로 단정하지 않는다.

**확인 방법**

`stale=false, unknown=false, current!=null`을 함께 확인한다. `last_success`는 과거 증거다. 장기 복구 중 다른 VM의 snapshot 소비도 가능하다.

**Manager 매핑:** `snapshot / {vm_id,max_age_ms}`

[명령어 색인으로](#command-index)

<a id="history"></a>
## 4. 작업 이력

자산 작업·사용자 제어·복구는 서로 다른 journal을 쓴다. 아래 예시는 기존 이력이 있는 경우 파일명에서 ID를 골라 조회한다. 새 작업을 만들지 않는다.

<a id="cmd-operation"></a>
### `xenova-vm operation` — 자산 작업 이력

**인자**

`--operation-id UUID` 필수. 대신 `--idempotency-key KEY` 사용 가능. 두 선택자 중 정확히 하나. `--json`. 선택자 UUID는 소문자 하이픈 표준형.

**사용 조건**

조회할 작업이 존재해야 한다. `ls`에 파일이 없으면 아직 이력이 없는 것이다.

**실행 예시**

```bash
ls /srv/xenova/operations/*.json
read -r -p "조회할 operation UUID(파일명에서 .json 제외): " OP_ID
xenova-vm operation --operation-id "$OP_ID" --json
```

**정상 응답**

`data` 예: `{"operation":{"action":"create","status":"SUCCEEDED","phase":"complete"},"recovery_required":false,"effective_status":"SUCCEEDED"}`.

**오류 응답**

존재하지 않는 이력은 `VM_NOT_FOUND`(2), 비표준 선택자나 복수 선택자는 `INVALID_SPEC`(2).

**확인 방법**

`recovery_required/effective_status`와 phase·잔여 inventory를 읽는다. create는 `--idempotency-key "create:VM_UUID"`로도 조회한다. 잠금·journal 파일 삭제로 상태를 초기화하지 않는다.

**Manager 매핑:** `operation / {operation_id} 또는 {idempotency_key}`

[명령어 색인으로](#command-index)

<a id="cmd-control-operation"></a>
### `xenova-vm control-operation` — 사용자 제어 이력

**인자**

`--operation-id UUID` 필수. `--json`. 선택자 UUID는 소문자 하이픈 표준형.

**사용 조건**

조회할 작업이 존재해야 한다. `ls`에 파일이 없으면 아직 이력이 없는 것이다.

**실행 예시**

```bash
ls /srv/xenova/control-requests/*.json
read -r -p "조회할 operation UUID(파일명에서 .json 제외): " OP_ID
xenova-vm control-operation --operation-id "$OP_ID" --json
```

**정상 응답**

`data` 예: `{"operation":{"phase":"completed","error":null}}`.

**오류 응답**

존재하지 않는 이력은 `VM_NOT_FOUND`(2), 비표준 선택자나 복수 선택자는 `INVALID_SPEC`(2).

**확인 방법**

수락 revision·phase·result/error를 현재 `get` 결과와 구분한다. 잠금·journal 파일 삭제로 상태를 초기화하지 않는다.

**Manager 매핑:** `control-operation / {operation_id}`

[명령어 색인으로](#command-index)

<a id="cmd-recovery-operation"></a>
### `xenova-vm recovery-operation` — 조건부 복구 이력

**인자**

`--recovery-attempt-id UUID` 필수. `--json`. 선택자 UUID는 소문자 하이픈 표준형.

**사용 조건**

조회할 작업이 존재해야 한다. `ls`에 파일이 없으면 아직 이력이 없는 것이다.

**실행 예시**

```bash
ls /srv/xenova/recovery-attempts/*.json
read -r -p "조회할 attempt UUID(파일명에서 .json 제외): " OP_ID
xenova-vm recovery-operation --recovery-attempt-id "$OP_ID" --json
```

**정상 응답**

`data` 예: `{"attempt":{"phase":"completed","error":null}}`.

**오류 응답**

존재하지 않는 이력은 `VM_NOT_FOUND`(2), 비표준 선택자나 복수 선택자는 `INVALID_SPEC`(2).

**확인 방법**

원 request·host_boot_id·phase·result/error를 읽는다. 과거 host 시도도 이력 조회는 가능하다. 잠금·journal 파일 삭제로 상태를 초기화하지 않는다.

**Manager 매핑:** `recovery-operation / {recovery_attempt_id}`

[명령어 색인으로](#command-index)

<a id="recovery"></a>
## 5. 조건부 일회성 복구

복구 요청은 장애 판단 서비스가 읽은 **현재 host·의도·자산 세대·실행 신원**을 조건으로 기존 자산을 한 번 실행한다. 플랫폼이 재시작 횟수·대기·소진 정책이나 두 번째 Recovery Worker를 운영하지 않는다.

<a id="recovery-spec"></a>
### 복구 요청의 모양과 필드 출처

다음은 graceful-restart 요청의 형식 예다. 실제 파일은 아래 명령 예시에서 보드 관측으로 만든다.

```json
{
  "vm_id": "22222222-2222-4222-8222-222222222222",
  "recovery_attempt_id": "55555555-5555-4555-8555-555555555555",
  "expected_host_boot_id": "44444444-4444-4444-8444-444444444444",
  "expected_lifecycle_generation": 0,
  "expected_intent_revision": 3,
  "expected_execution": {
    "execution_id": "33333333-3333-4333-8333-333333333333"
  },
  "mode": "graceful-restart",
  "deadline_monotonic_ms": 120000
}
```

예시 deadline 숫자를 복사하지 않는다. **현재 보드의 단조 시각**으로 계산한다.

| 필드 | 형식·출처 |
|---|---|
| `vm_id` | 대상 영구 UUID |
| `recovery_attempt_id` | 새 시도에 한 번 발급한 UUID |
| `expected_host_boot_id` | 성공 probe의 `host_boot_id` |
| `expected_lifecycle_generation` | probe의 `lifecycle_generation`, 정수 ≥0 |
| `expected_intent_revision` | probe의 `intent_revision`, 정수 ≥1 |
| `expected_execution` | 아래 mode별 객체. 여분 필드 없음 |
| `mode` | 아래 세 값 중 하나 |
| `deadline_monotonic_ms` | 같은 host 부팅의 절대 단조 ms. 새 실행 판정 시 미래이며 120초 이내 |

| mode | expected_execution | 추가 조건 |
|---|---|---|
| `start-if-absent` | `{"absent_observation_revision":2}` | 최신 성공 probe가 실제 absent이며 revision이 일치 |
| `graceful-restart` | `{"execution_id":"UUID"}` | managed 실행 ID 일치·협조형 종료 지원 |
| `force-restart` | `{"execution_id":"UUID"}` | managed 실행 ID 일치·정책 `force_restart_vm_ids`에 해당 UUID 승인 |


<a id="cmd-recover-once"></a>
### `xenova-vm recover-once` — 현재 문맥이 일치할 때 한 번 복구

**인자**

`--spec-file FILE`: 위 8개 필드의 JSON. `--execute`: 실제 실행, 생략 시 계획/기존 시도 판정. `--json`. `recover_once` 별칭 허용.

**사용 조건**

REGISTERED·동일 세대·유효 running 의도·정확한 revision·동일 host의 allowed admission·열린 host gate·정상 기존 자산이 필요하다. 아래 예시는 **현재 running 의도인 협조형 시험 VM**용이다. 정지 의도를 복구로 우회하지 않는다.

**실행 예시**

```bash
xenova-vm list --json
read -r -p "복구할 협조형 시험 VM UUID: " VM_ID
export VM_ID
RECOVERY_FILE="$(mktemp /run/xenova/recovery-request.XXXXXX.json)"
export RECOVERY_FILE
PYTHONPATH=/usr/lib/xenova python3 - <<'PY'
import json, os, time
from pathlib import Path
from uuid import uuid4
from xenova_management import Manager

r = Manager().call("probe", {"vm_id": os.environ["VM_ID"], "timeout_ms": 5000})
assert r["ok"], r["error"]
o = r["data"]["observation"]
assert o["intent_applicable"] and o["desired_state"] == "running"
if o["presence"] == "absent":
    mode = "start-if-absent"
    expected = {"absent_observation_revision": o["observation_revision"]}
else:
    assert o["execution_status"] == "managed" and o["execution_id"]
    mode = "graceful-restart"
    expected = {"execution_id": o["execution_id"]}
spec = {
    "vm_id": os.environ["VM_ID"],
    "recovery_attempt_id": str(uuid4()),
    "expected_host_boot_id": o["host_boot_id"],
    "expected_lifecycle_generation": o["lifecycle_generation"],
    "expected_intent_revision": o["intent_revision"],
    "expected_execution": expected,
    "mode": mode,
    "deadline_monotonic_ms": int(time.monotonic() * 1000) + 60000,
}
Path(os.environ["RECOVERY_FILE"]).write_text(json.dumps(spec, indent=2) + "\n")
print("request:", os.environ["RECOVERY_FILE"])
print("attempt:", spec["recovery_attempt_id"])
PY
# Python이 오류 없이 완료했는지 확인한 다음 계획을 읽는다.
xenova-vm recover-once --spec-file "$RECOVERY_FILE" --json
# 계획 ok=true 확인 후 60초 기한 안에 원 파일로 실행한다.
xenova-vm recover-once --spec-file "$RECOVERY_FILE" --execute --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

`data`는 `{attempt,original_result,current_observation}`. 예: `{"attempt":{"phase":"completed"},"original_result":{"execution_id":"66666666-6666-4666-8666-666666666666","platform_elapsed_ms":2400,"app_readiness":"unknown"}}`. 원 결과와 현재 관측은 별개다.

**오류 응답**

의도 불일치 `INTENT_MISMATCH`(3), 이전 host `TARGET_CHANGED`(3), admission 불일치 `BOOT_ADMISSION_REQUIRED`(3), 미지원 mode `RECOVERY_MODE_UNSUPPORTED`(2), 만료 `DEADLINE_EXCEEDED`(4), 불확실한 launch `RECONCILE_REQUIRED`(4).

**확인 방법**

출력한 attempt ID로 `recovery-operation`을 조회하고 `get/probe`의 새 execution_id를 확인한다. 재전달은 위 Python을 다시 실행하지 않고 **원 RECOVERY_FILE로 실행 명령만 반복**한다. 새 장애는 최신 probe·새 attempt로 요청한다. 계획이 이후 실행을 예약하지 않는다. 만료한 원 파일의 deadline만 고치지 않는다.

**Manager 매핑:** `recover_once / 위 8개 필드`. [중복·만료·중단 판정](#recovery-reconciliation)을 따른다.

[명령어 색인으로](#command-index)

<a id="changes"></a>
## 6. 자원·Zephyr firmware 변경

이 기능들은 실행 중인 VM을 자동 정지하지 않는다. 대상은 **유효한 stopped 의도 + 실제 Xen 부재 + 자산 미사용**이어야 한다. 실행 의도·신원·미완료 작업 보호를 우회하지 않는다.

<a id="cmd-update-resources"></a>
### `xenova-vm update-resources` — 정지 Linux의 vCPU·메모리 변경

**인자**

| 인자 | 조건 |
|---|---|
| `TARGET` | REGISTERED Linux UUID/내부 이름 |
| `--vcpus N` | 필수, 정수 1..4 |
| `--memory-mib M` | 필수, 정수 128..8192 MiB |
| `--expected-lifecycle-generation G` | get의 현재 generation, 정수 ≥0 |
| `--expected-configuration-sha256 SHA` | get의 현재 **설정 객체** SHA256 |
| `--idempotency-key KEY` | 계획에도 필수 |
| `--execute / --json` | 계획/실행 및 출력 |

**사용 조건**

정지 Linux이며 generation·설정 해시가 정확히 일치해야 한다. pending 제어/복구·launching/uncertain 실행·Xen/loop/mount/열린 프로세스 연결이 없어야 한다.

**실행 예시**

```bash
xenova-vm list --json
read -r -p "정지한 Linux 시험 VM UUID: " VM_ID
xenova-vm get "$VM_ID" --json
# 위 응답의 stopped 의도·domid=null을 확인하고 같은 응답의 두 값을 입력한다.
read -r -p "data.vm.generation: " GENERATION
read -r -p "data.vm.configuration_sha256: " CONFIG_SHA
KEY="$(cat /proc/sys/kernel/random/uuid)"
printf '작업 키: %s\n' "$KEY"
xenova-vm update-resources "$VM_ID" --vcpus 2 --memory-mib 768 \
  --expected-lifecycle-generation "$GENERATION" \
  --expected-configuration-sha256 "$CONFIG_SHA" --idempotency-key "$KEY" --json
# 계획 ok=true 확인 후 같은 입력으로 실행한다.
xenova-vm update-resources "$VM_ID" --vcpus 2 --memory-mib 768 \
  --expected-lifecycle-generation "$GENERATION" \
  --expected-configuration-sha256 "$CONFIG_SHA" --idempotency-key "$KEY" --execute --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

계획 `data`: `{plan,action,before,after,configuration_sha256,starts_guest:false}`. 완료/완료 재전달: `{operation,original_result,current}`. 예: `{"original_result":{"before":{"vcpus":1,"memory_mib":512},"after":{"vcpus":2,"memory_mib":768},"starts_guest":false}}`.

**오류 응답**

실제 실행 중 `VM_NOT_STOPPED`(3), stopped 의도 아님 `INTENT_MISMATCH`(3), 오래된 해시/세대 `TARGET_CHANGED`(3), 종류/범위 `RESOURCE_UNSUPPORTED`(2), 사용 자산 `ASSET_IN_USE`(3).

**확인 방법**

get의 두 자원만 바뀌고 UUID·디스크·MAC/IP·의도 revision·generation·initial_spec이 유지되는지 비교한다. 다음 start에 새 자원이 적용된다. 범위 통과는 실제 host 메모리 예약을 뜻하지 않는다.

**Manager 매핑:** `update-resources / {vm_id,vcpus,memory_mib,expected_lifecycle_generation,expected_configuration_sha256}`

[명령어 색인으로](#command-index)

<a id="cmd-firmware-transition"></a>
### `xenova-vm firmware-transition` — Zephyr 활성 firmware·프로필 전환

**인자**

| 인자 | 조건 |
|---|---|
| `TARGET` | REGISTERED Zephyr |
| `--bundle ABS_DIR` | canonical 절대 경로, `..` 없음 |
| `--sha256 SHA` | bundle manifest의 소문자 64자리 SHA256 |
| `--expected-lifecycle-generation G` | get의 generation |
| `--expected-configuration-sha256 SHA` | get의 설정 객체 SHA256 |
| `--board-shutdown-policy POLICY` | 새 프로필에 유효한 graceful/refuse/force-allowed |
| `--idempotency-key KEY` | 계획에도 필수 |
| `--execute / --json` | 계획/실행 |

**사용 조건**

유효 stopped 의도·실제 부재·자산 미사용·일치하는 설정/세대가 필요하다. 이미 준비·검증된 읽기 전용 bundle을 사용한다. [bundle/신원 보존 계약](#firmware-contract)을 확인한다.

**실행 예시**

```bash
xenova-vm list --json
read -r -p "정지한 시험 Zephyr UUID: " VM_ID
read -r -p "준비된 Network Starter bundle 절대 경로: " BUNDLE
# 이 예시는 firmware.json을 가진 Network Starter bundle을 사용한다.
MANIFEST_SHA="$(sha256sum "$BUNDLE/firmware.json" | cut -d ' ' -f 1)"
xenova-vm get "$VM_ID" --json
read -r -p "data.vm.generation: " GENERATION
read -r -p "data.vm.configuration_sha256: " CONFIG_SHA
KEY="$(cat /proc/sys/kernel/random/uuid)"
printf '작업 키: %s\n' "$KEY"
xenova-vm firmware-transition "$VM_ID" --bundle "$BUNDLE" --sha256 "$MANIFEST_SHA" \
  --expected-lifecycle-generation "$GENERATION" --expected-configuration-sha256 "$CONFIG_SHA" \
  --board-shutdown-policy graceful --idempotency-key "$KEY" --json
# 계획 ok=true 확인 후 실행한다. graceful은 이 Network Starter 예시의 선택이다.
xenova-vm firmware-transition "$VM_ID" --bundle "$BUNDLE" --sha256 "$MANIFEST_SHA" \
  --expected-lifecycle-generation "$GENERATION" --expected-configuration-sha256 "$CONFIG_SHA" \
  --board-shutdown-policy graceful --idempotency-key "$KEY" --execute --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

계획 `data`: `{plan,action,before,after,address_reserved:false,starts_guest:false}`. 완료: `{operation,original_result,current}`. 예: `{"original_result":{"starts_guest":false,"app_readiness":"unknown"}}`. 원 결과에는 previous_firmware·active_firmware·configuration_sha256·retained_reservation도 포함한다.

**오류 응답**

잘못된 bundle `ARTIFACT_INVALID`(4), 실행 중 `VM_NOT_STOPPED`(3), 오래된 설정 `TARGET_CHANGED`(3), 예약 IP 소진 `IP_POOL_EXHAUSTED`(4).

**확인 방법**

active_firmware·프로필·capabilities·board policy를 확인한다. UUID·generation·initial_spec·creation_origin·이전 revision·다른 VM은 유지한다. legacy→Network는 MAC/IP/hostname을 한 번 예약하며 역전환도 기존 예약을 보존한다. 실제 시작/통신 검증은 별도 수행한다.

**Manager 매핑:** `firmware-transition / {vm_id,bundle,manifest_sha256,expected_lifecycle_generation,expected_configuration_sha256,board_shutdown_policy}`

[명령어 색인으로](#command-index)

<a id="cmd-firmware-replace"></a>
### `xenova-vm firmware-replace` — 같은 Zephyr 프로필의 활성 firmware 교체

**인자**

`INTERNAL_NAME`, `--bundle ABS_DIR`, `--sha256 MANIFEST_SHA` 필수. `--execute` 선택. **`--json` 없음**.

**사용 조건**

정지한 Zephyr의 기존 프로필·capabilities와 호환되는 bundle이어야 한다. 조건부 journal·프로필 전환은 firmware-transition을 사용한다.

**실행 예시**

```bash
xenova-vm status
read -r -p "정지한 시험 Zephyr 내부 이름: " INTERNAL_NAME
read -r -p "같은 프로필 bundle 절대 경로: " BUNDLE
read -r -p "검증된 manifest SHA256: " MANIFEST_SHA
xenova-vm firmware-replace "$INTERNAL_NAME" --bundle "$BUNDLE" --sha256 "$MANIFEST_SHA"
# 계획을 확인한 뒤 실행한다.
xenova-vm firmware-replace "$INTERNAL_NAME" --bundle "$BUNDLE" --sha256 "$MANIFEST_SHA" --execute
```

**정상 응답**

자체 JSON `{action,id,firmware,uuid,starts_guest:false}`. action은 계획 `plan-firmware-replace`, 실행 `firmware-replace`.

**오류 응답**

프로필 불일치·자산 검사 실패는 stderr `[xenova] ERROR: …`와 종료 `1`. Manager 오류 envelope가 아니다.

**확인 방법**

status/get에서 활성 revision만 변경됐는지 확인한다. UUID·creation_origin·이전 firmware 파일은 보존한다. 자동 시작하지 않는다.

**Manager 매핑:** 운영 CLI 전용. 공통 Manager action 없음.

[명령어 색인으로](#command-index)

<a id="policies"></a>
## 7. 자동 시작·VM 정책·설정

메타데이터 설정은 실행 의도를 직접 바꾸지 않는다. 새 host 부팅의 충돌 조합과 같은 host 사용자 의도 복원은 [부팅 판정표](#boot-policy)를 따른다.

<a id="cmd-autostart-set"></a>
### `xenova-vm autostart-set` — VM 자동 시작 플래그 저장

**인자**

`TARGET`와 `VALUE=on|off` 필수. `--json` 선택. `--execute`와 무관하게 저장.

**사용 조건**

등록된 VM을 선택한다. 현재 실행/의도는 바꾸지 않으며 다음 autostart 판정에 사용한다.

**실행 예시**

```bash
xenova-vm list --json
read -r -p "설정할 VM UUID: " VM_ID
xenova-vm autostart-set "$VM_ID" off --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

Manager `data`는 `{vm:VMView}`. `--json` 없음은 성공 시 별도 출력 없음.

**오류 응답**

허용되지 않은 값은 `INVALID_SPEC`(2), 게스트 종류에 맞지 않는 정책은 플랫폼 검사 오류로 거절한다. 저장 실패는 `STORAGE_UNAVAILABLE` 등(4).

**확인 방법**

get의 `configuration`에서 설정을 읽고 `desired_state/intent_revision/domid`가 의도치 않게 바뀌지 않았는지 확인한다.

**Manager 매핑:** `autostart-set / {vm_id,value}`

[명령어 색인으로](#command-index)

<a id="cmd-board-shutdown-set"></a>
### `xenova-vm board-shutdown-set` — 보드 종료 시 VM 정책 저장

**인자**

`TARGET`와 `VALUE=graceful|refuse|force-allowed` 필수. `--json` 선택. `--execute`와 무관하게 저장.

**사용 조건**

등록된 VM을 선택한다. Linux는 graceful만, legacy Starter는 refuse/force-allowed, Network Starter는 세 값이 유효하다. 실제 종료 해석은 [종료 계약](#shutdown-contract)을 따른다.

**실행 예시**

```bash
xenova-vm list --json
read -r -p "설정할 VM UUID: " VM_ID
xenova-vm board-shutdown-set "$VM_ID" graceful --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

Manager `data`는 `{vm:VMView}`. `--json` 없음은 자체 `{id,board_shutdown_policy,starts_guest:false}`.

**오류 응답**

허용되지 않은 값은 `INVALID_SPEC`(2), 게스트 종류에 맞지 않는 정책은 플랫폼 검사 오류로 거절한다. 저장 실패는 `STORAGE_UNAVAILABLE` 등(4).

**확인 방법**

get의 `configuration`에서 설정을 읽고 `desired_state/intent_revision/domid`가 의도치 않게 바뀌지 않았는지 확인한다.

**Manager 매핑:** `board-shutdown-set / {vm_id,value}`

[명령어 색인으로](#command-index)

<a id="cmd-config"></a>
### `xenova-vm config` — 검증된 runtime xl 설정 생성

**인자**

`TARGET` 필수. `--json` 선택. 즉시 파일 생성.

**사용 조건**

등록된 자산·프로필·디스크를 검증한다. VM을 시작하지 않는다.

**실행 예시**

```bash
xenova-vm list --json
read -r -p "설정을 생성할 VM UUID: " VM_ID
xenova-vm config "$VM_ID" --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

Manager: `{vm:VMView}`. 운영 `config INTERNAL_NAME`: `/run/xenova/INTERNAL_NAME.cfg` 경로 문자열.

**오류 응답**

잘못된 자산은 `ARTIFACT_INVALID`(4). 운영 경로는 stderr/종료 1. 생성 cfg를 임의 수정해 검증을 우회하지 않는다.

**확인 방법**

get의 configuration.id를 읽고 `/run/xenova/<id>.cfg`를 확인한다. 설치 artifact 검사에는 이 원 생성 cfg를 사용한다.

**Manager 매핑:** `config / {vm_id}`

[명령어 색인으로](#command-index)

<a id="cmd-autostart"></a>
### `xenova-vm autostart` — 등록 VM 전체의 시작 판정·실행

**인자**

인자 없음. **`--json` 없음**. 즉시 전역 동작.

**사용 조건**

factory 기본 등록과 host gate·카탈로그·자산·내부망이 준비돼야 한다. 전체 등록을 대상으로 하므로 한 VM 시험용 start 대신 사용하지 않는다.

**실행 예시**

```bash
xenova-vm status
xenova-vm autostart
cat /run/xenova/autostart-results.json
xenova-vm status
```

**정상 응답**

자체 `{autostart_results}`와 runtime 파일. VM별 pending/starting/skipped/실제 Xen 상태/failed를 기록하며 사람용 시작 로그가 섞일 수 있다.

**오류 응답**

사전 실패는 stderr/종료 `1`. 개별 결과의 failed·reason도 읽는다. 정책 null의 `BOOT_POLICY_PENDING`은 승인 누락 진단이며 임의 정책 선택을 뜻하지 않는다.

**확인 방법**

VM별 reason/admission·의도 revision을 확인한다. 같은 host 재호출이 최신 사용자 stop을 무시해 새 실행을 만들면 안 된다. 서비스 재시작은 전체 VM 재생성이 아니다.

**Manager 매핑:** 운영 CLI 전용. 한 번의 판정이며 자동 복구 루프가 아니다.

[명령어 색인으로](#command-index)

<a id="cpu-contract"></a>
### CPU 배치의 공통 의미

hard affinity는 각 Xen vCPU가 실행될 수 있는 pCPU 집합이다. 기본 프리셋은 A→1, B→2, dom0·나머지 VM→{0,3}이다. 기존 vCPU 수·RAM·펌웨어는 유지하며 Linux 4-vCPU VM이면 네 vCPU 모두 {0,3}을 사용한다. `cpus = '0,3'`은 공통 집합이고 `cpus = ['0','3']`은 vCPU별 매핑이므로 서로 바꾸지 않는다.

정책 원본은 `/srv/xenova/cpu-policy.json` 하나다. 전용 대상은 영구 UUID로 지정하며 template_id·표시 이름·domid·Spring role·AUTO/MANUAL로 역할을 추정하지 않는다. 최대 두 개의 등록된 1-vCPU Zephyr 전용 예약과 dom0·나머지 VM의 공통 공유 집합을 지원한다. 코어 교환·빈 전용 목록도 유효한 정책 입력이다. Xen에서 확인한 온라인 pCPU·단일 CPU pool·Credit2를 유지하고 자동 변경하지 않는다.

운영자 작업은 **수량 변경 `update-resources` / 배치 정책 변경 `cpu-policy-set` / 실제 조회 `cpu-layout` / 기존 start·reboot·recover-once 사용**의 네 가지다. 별도 pin 명령은 필요 없다. CPU 정책은 vCPU 수·메모리를 바꾸지 않으며 `update-resources` 후에도 모든 구성 vCPU에 공유 집합을 적용한다.

stop/reboot/recovery·retire/restore/purge는 CPU 예약을 해제하지 않는다. purge한 UUID의 예약도 명시적 정책 변경으로만 해제·이전한다. CPU 정책 오류는 신원을 확인할 수 있는 대상의 기존 stop/shutdown을 막지 않으며 UUID/name/domid/xen_unique_id 검사를 생략하지도 않는다.

<a id="cmd-cpu-policy-set"></a>
### `xenova-vm cpu-policy-set` — CPU 정책 등록·변경

**인자**

필수 `--spec-file PATH`, `--idempotency-key KEY`. `--execute` 생략 시 계획, 지정 시 저장. `--json` 선택. spec은 아래 세 필드만 받는다.

| 입력 | 조건 |
|---|---|
| expected_policy_revision | 최초 null, 변경 시 `cpu-layout`에서 읽은 양의 정수 |
| shared_pcpus | 0..3 중 중복 없는 비어 있지 않은 정수 배열 |
| exclusive | 최대 2개의 `{vm_id: 정규 UUID, pcpu: 정수}`. UUID·전용 코어 중복 및 공유 집합과 겹침 거절 |

**사용 조건**

모든 관리 게스트가 실제 정지했고 미완료 launch/reboot/control/recovery/자산 작업·uncertain 실행이 없어야 한다. 검사와 저장은 공통 잠금 안에서 진행한다. 명령이 게스트를 대신 정지하지 않는다. 미등록 도메인이 있으면 제안한 공유 집합을 이미 준수해야 한다.

**실행 예시**

dom0의 승인된 유지보수 창에서 실행한다. 같은 실행의 spec·key는 재전달 때 그대로 보관한다.

```bash
xenova-vm list --json
xenova-vm cpu-layout --json
read -r -p "전용 Zephyr A UUID: " AUTO_UUID
read -r -p "전용 Zephyr B UUID: " MANUAL_UUID
read -r -p "현재 policy_revision (최초는 null): " POLICY_REV
export AUTO_UUID MANUAL_UUID POLICY_REV
KEY="$(cat /proc/sys/kernel/random/uuid)"
REQUEST="/root/cpu-policy-$KEY.json"
export REQUEST
umask 077
python3 - <<'PY'
import json, os
from pathlib import Path
spec = {"expected_policy_revision": json.loads(os.environ["POLICY_REV"]),
        "shared_pcpus": [0, 3],
        "exclusive": [{"vm_id": os.environ["AUTO_UUID"], "pcpu": 1},
                      {"vm_id": os.environ["MANUAL_UUID"], "pcpu": 2}]}
with Path(os.environ["REQUEST"]).open("x") as stream:
    json.dump(spec, stream)
    stream.write("\n")
PY
printf '작업 키: %s\n요청 파일: %s\n' "$KEY" "$REQUEST"
xenova-vm cpu-policy-set --spec-file "$REQUEST" --idempotency-key "$KEY" --json
xenova-vm cpu-policy-set --spec-file "$REQUEST" --idempotency-key "$KEY" --execute --json
```

**정상 응답**

계획 data는 `{plan:true,policy:후보,host_preparation_required:true}`다. 파일·intent·affinity를 바꾸지 않고 계획 후보의 operation UUID도 저장 확정 ID가 아니다. 최초 실행 data는 `{policy:저장된_정책,host_preparation_required:true}`이며 envelope.operation_id가 publication journal을 가리킨다. 정책에는 schema_version, 단조 정수 policy_revision, operation_id, pool_name, shared_pcpus, exclusive가 저장된다.

완료 key 재전달은 `replayed=true`, data의 policy·current_policy·historical=true로 과거 결과와 현재 정책을 구분한다. 과거 결과가 현재 정책을 덮어쓰지 않는다. 정책 저장 성공은 현재 dom0 적용 성공이 아니다.

**오류 응답**

형식/겹침 `INVALID_SPEC`(2), 미등록 UUID `VM_NOT_FOUND`(2), online/pool/게스트 제약 `RESOURCE_UNSUPPORTED`(2), 실행 중 `VM_NOT_STOPPED`(3), 오래된 revision `TARGET_CHANGED`(3), 키 충돌 `IDEMPOTENCY_CONFLICT`(3). 미완료 실행/정책 게시·부분 dom0 적용은 `RECONCILE_REQUIRED`(4). 활성 정책 유실/손상은 `CPU_POLICY_INVALID`(4)이며 legacy 배치로 fallback하지 않는다.

**확인 방법**

`cpu-layout --json`의 policy_revision·reservations를 확인한다. 현재 host 적용은 [내부 CPU 준비](#cmd-cpu-host-prepare)에서 별도로 수행한다. 새 정책은 새 key와 최신 revision, 중단된 동일 정책은 원 spec/key로 재전달한다. journal·anchor를 삭제해 우회하지 않는다.

**Manager 매핑:** `cpu-policy-set / {expected_policy_revision,shared_pcpus,exclusive}`, `execute`·`idempotency_key`는 call 키워드 인자.

[명령어 색인으로](#command-index)

<a id="cmd-cpu-layout"></a>
### `xenova-vm cpu-layout` — 정책과 실제 Xen 배치 조회

**인자**

선택 `TARGET` 또는 `--vm-id UUID`, `--timeout-ms N`(기본 15000, 1..15000), `--json`. TARGET은 정규 UUID이며 이 명령은 내부 이름을 변환하지 않는다. 생략하면 전체 등록 VM을 조회한다.

**사용 조건**

실제 Xen domain identity·vcpu-list·CPU pool·scheduler를 읽을 수 있어야 한다. 공통 operation lock은 대기 없이 시도하며 경합은 즉시 오류다. deadline은 잠금 시도·registry 읽기 전 시작하고 각 Xen 호출에 남은 시간을 전달한다. 조회가 정책·준비 기록·affinity를 바꾸거나 VM을 시작하지 않는다.

**실행 예시**

```bash
xenova-vm cpu-layout --json
xenova-vm list --json
read -r -p "배치 조회 VM UUID: " VM_ID
xenova-vm cpu-layout "$VM_ID" --timeout-ms 5000 --json
```

**정상 응답**

operation_id=null, replayed=false. **ok=true는 조회 성공**이며 배치 준수 성공과 다르다. 한 VM 조회도 dom0·관리 VM·미등록 VM·paused/blocked 도메인의 모든 vCPU를 관측해 전용성을 검사한다. 두 전체 관측 사이에 domain set/incarnation/affinity가 바뀌면 오류다. `xl vcpu-list`의 현재 `CPU` 열을 hard mask로 읽지 않는다.

| 필드 | 의미 |
|---|---|
| configured / policy_revision | 유효한 활성 정책 여부 / 정수 revision, 미설정 null |
| host_boot_id / observed_monotonic_ms | 관측 host UUID / 해당 host의 단조 ms. UTC 아님 |
| host.online_pcpus / max_cpu_id / pool_name / scheduler | 실제 Xen topology·pool·scheduler. Linux CPU 수로 추정하지 않음 |
| host.desired_dom0_pcpus / observed_dom0_hard_affinity | 정책 공유 집합 / 실제 모든 dom0 vCPU 마스크 |
| host.ready | 현재 boot·revision·정책 해시·dom0 incarnation의 준비 영수증과 실제 배치 일치 |
| reservations | 명시적으로 유지되는 전용 UUID·코어. retire/purge 후에도 유지 |
| vms[].vm_id / execution_id | 영구 UUID / 현재 Xen incarnation과 결합한 실행 ID. 미관리·부재는 null |
| vms[].vcpus / allocation / desired_hard_affinity | 영구 수량 / exclusive·shared / 정책으로 계산한 각 vCPU 마스크 |
| vms[].observed | presence, domid, 문자열 xen_unique_id, 실제 vcpus·hard_affinity |
| affinity_matches | 실제 수량·각 마스크가 예정 배치와 정확히 일치 |
| exclusive_verified | 전용 VM의 마스크 일치 및 다른 모든 도메인 vCPU와 중복 없음 |
| issues | AFFINITY_MISMATCH / EXCLUSIVE_CONFLICT / CPU_NOT_READY / POLICY_PUBLICATION_PENDING. 충돌 domain·vcpu·코어 포함 |

정상 실행 전체 envelope 예(설명용 fixture, 실제 vCPU 수를 하드코딩하는 계약 아님):

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

정지·미설정·불일치·관측 실패의 [다섯 전체 응답 fixture](https://lab.ssafy.com/s15-final/S15P31E110/-/blob/347d935cb7ef09e07f3082a13af1733bb6acd2cb/platform/tests/fixtures/cpu-layout-responses.json)도 같은 envelope를 사용한다.

| 상태 | 응답 의미 |
|---|---|
| 정지 | observed.presence=absent. domid·xen_unique_id·실제 vcpus·hard_affinity와 두 비교값은 null. 정지를 불일치로 표시하지 않음 |
| 공유 | allocation=shared, exclusive_verified=null |
| 정책 미설정 | configured=false, policy_revision·desired/allocation·두 비교값 null, host.ready=false. 실제 관측값은 반환 |
| 불일치 | ok=true, affinity_matches=false와 issues. 전용 충돌이면 exclusive_verified=false |
| 관측 실패 | ok=false, data=null. 빈 마스크나 0으로 성공을 대체하지 않음 |

`exclusive_verified`는 관측 시점의 hard affinity 중복 검사다. IRQ·캐시·메모리·I/O 격리나 실시간 deadline 보장은 아니며 dom0 프로세스의 Linux taskset과도 다르다. 외부 root/xl 변경까지 원자적으로 통제하지 않는다.

**오류 응답**

```json
{"schema_version":"1.0","ok":false,"request_id":"cpu-status-01","operation_id":null,"replayed":false,"data":null,"error":{"code":"OBSERVATION_UNAVAILABLE","message":"cannot parse or identify complete Xen CPU state","retryable":false,"details":{"cause_type":"ValueError"}}}
```

입력 `INVALID_SPEC`(2), UUID 부재 `VM_NOT_FOUND`(2), 잠금 경합 `OPERATION_IN_PROGRESS`(3), 신원/set 변경 `TARGET_CHANGED`(3), 관측 실패 `OBSERVATION_UNAVAILABLE`(4), 기한 만료 `DEADLINE_EXCEEDED`(4), 손상 정책 `CPU_POLICY_INVALID`(4)를 구분한다.

**확인 방법**

host.ready와 각 대상 affinity_matches·exclusive_verified·issues를 함께 읽는다. stopped/shared/unconfigured의 null은 성공 true가 아니다. 이 조회와 기존 start/reboot 후 execution-context를 결합해 같은 실행인지 확인한다.

**Manager 매핑:** `cpu-layout / {vm_id?,timeout_ms?}`. 아래 예는 dom0 root 셸에서 실행한다.

```bash
PYTHONPATH=/usr/lib/xenova python3 - <<'PY'
import json
from xenova_management import Manager
result = Manager().call("cpu-layout", {"timeout_ms":5000}, request_id="cpu-status-01")
print(json.dumps(result, ensure_ascii=False, indent=2))
PY
```

[명령어 색인으로](#command-index)

<a id="assets"></a>
## 8. 자산 수명주기

보관·복원·영구 제거는 자산 lifecycle을 바꾼다. 사용자 실행 의도를 새로 수락하지 않는다. retire가 stopped 의도를 만들거나 restore가 running 의도/자동 시작을 만드는 계약은 없다.

<a id="cmd-retire"></a>
### `xenova-vm retire` — 정지 자산 보관

**인자**

`TARGET`, `--idempotency-key KEY` 필수(계획 포함). `--execute / --json`. `delete`는 retire의 CLI 별칭이다.

**사용 조건**

REGISTERED·실제 정지·자산 미사용이어야 한다. 자동 정지·자동 시작은 하지 않는다.

**실행 예시**

```bash
xenova-vm list --lifecycle REGISTERED --json
read -r -p "처리할 시험 VM UUID: " VM_ID
xenova-vm get "$VM_ID" --json
KEY="$(cat /proc/sys/kernel/random/uuid)"
printf '작업 키: %s\n' "$KEY"
xenova-vm retire "$VM_ID" --idempotency-key "$KEY" --json
# 대상·계획 ok=true를 확인한 뒤 같은 입력으로 실행한다.
xenova-vm retire "$VM_ID" --idempotency-key "$KEY" --execute --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

최초 완료 `data`는 VMView 자체. 예: `{"vm_id":"22222222-2222-4222-8222-222222222222","lifecycle":"RETIRED","registered":false,"generation":1}`. 완료 재전달은 VMView 필드에 `operation/original_result/current` 추가.

**오류 응답**

실행 중 `VM_NOT_STOPPED`(3). 사용 자산 `ASSET_IN_USE`(3), 키 재사용 불일치 `IDEMPOTENCY_CONFLICT`(3).

**확인 방법**

get의 lifecycle·generation과 operation journal을 비교한다. 완료마다 generation이 증가하고 원 실행 의도는 보존된다. 재전달이 더 최신 lifecycle을 과거 상태로 돌리지 않는다.

**Manager 매핑:** `retire / {vm_id}`. `delete`를 Python action으로 보내지 않는다.

[명령어 색인으로](#command-index)

<a id="cmd-restore"></a>
### `xenova-vm restore` — 보관 자산 복원

**인자**

`TARGET`, `--idempotency-key KEY` 필수(계획 포함). `--execute / --json`.

**사용 조건**

RETIRED·UUID/이름/MAC/IP 충돌 없음·정상 자산·용량 확보이어야 한다. 자동 정지·자동 시작은 하지 않는다.

**실행 예시**

```bash
xenova-vm list --lifecycle RETIRED --json
read -r -p "처리할 시험 VM UUID: " VM_ID
xenova-vm get "$VM_ID" --json
KEY="$(cat /proc/sys/kernel/random/uuid)"
printf '작업 키: %s\n' "$KEY"
xenova-vm restore "$VM_ID" --idempotency-key "$KEY" --json
# 대상·계획 ok=true를 확인한 뒤 같은 입력으로 실행한다.
xenova-vm restore "$VM_ID" --idempotency-key "$KEY" --execute --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

최초 완료 `data`는 VMView 자체. 예: `{"vm_id":"22222222-2222-4222-8222-222222222222","lifecycle":"REGISTERED","registered":true,"generation":2}`. 완료 재전달은 VMView 필드에 `operation/original_result/current` 추가.

**오류 응답**

복원 충돌 `RESTORE_CONFLICT`(3), purge된 대상 `VM_PURGED`(3). 사용 자산 `ASSET_IN_USE`(3), 키 재사용 불일치 `IDEMPOTENCY_CONFLICT`(3).

**확인 방법**

get의 lifecycle·generation과 operation journal을 비교한다. 완료마다 generation이 증가하고 원 실행 의도는 보존된다. 재전달이 더 최신 lifecycle을 과거 상태로 돌리지 않는다.

**Manager 매핑:** `restore / {vm_id}`. `delete`를 Python action으로 보내지 않는다.

[명령어 색인으로](#command-index)

<a id="cmd-purge"></a>
### `xenova-vm purge` — 보관 자산 영구 제거

**인자**

`TARGET`, `--idempotency-key KEY` 필수(계획 포함). `--confirm-vm-id UUID` 필수, 대상과 같아야 함. `--execute / --json`.

**사용 조건**

RETIRED·본인 소유의 폐기 대상·사용 연결 없음·동일 UUID 확인이어야 한다. 실행하면 대상 자산 파일을 삭제한다. UUID tombstone·작업 이력은 남는다.

**실행 예시**

```bash
xenova-vm list --lifecycle RETIRED --json
read -r -p "영구 제거할 폐기용 VM UUID: " VM_ID
xenova-vm get "$VM_ID" --json
KEY="$(cat /proc/sys/kernel/random/uuid)"
printf '작업 키: %s\n' "$KEY"
xenova-vm purge "$VM_ID" --confirm-vm-id "$VM_ID" --idempotency-key "$KEY" --json
# 대상·계획 ok=true를 확인한 뒤 같은 입력으로 실행한다.
xenova-vm purge "$VM_ID" --confirm-vm-id "$VM_ID" --idempotency-key "$KEY" --execute --json
xenova-vm get "$VM_ID" --json
```

**정상 응답**

최초 완료 `data`는 VMView 자체. 예: `{"vm_id":"22222222-2222-4222-8222-222222222222","lifecycle":"PURGED","registered":false,"generation":1}`. 완료 재전달은 VMView 필드에 `operation/original_result/current` 추가. 성공 시 `space`도 포함한다.

**오류 응답**

확인 UUID 불일치 `INVALID_SPEC`(2), 중간 삭제 실패 `CLEANUP_INCOMPLETE`(4). 사용 자산 `ASSET_IN_USE`(3), 키 재사용 불일치 `IDEMPOTENCY_CONFLICT`(3).

**확인 방법**

get의 lifecycle·generation과 operation journal을 비교한다. 완료마다 generation이 증가하고 원 실행 의도는 보존된다. `space`의 논리/실할당/여유 bytes와 `remaining_assets`를 읽는다. 중단 후 같은 키로 잔여 inventory를 재판정한다.

**Manager 매핑:** `purge / {vm_id,confirm_vm_id}`. `delete`를 Python action으로 보내지 않는다.

[명령어 색인으로](#command-index)

<a id="access"></a>
## 9. Wi-Fi·콘솔·SSH

Wi-Fi 구성·활성화·조회는 각각 독립 명령이다. 관리 Wi-Fi를 재활성화하면 SSH가 끊길 수 있으므로 configure/activate는 UART 등 복귀 경로를 확보한 상태에서 실행한다. 실제 PSK를 명령행·로그·Git에 넣지 않는다.

<a id="cmd-wifi-configure"></a>
### `xenova-wifi configure` — SSID·PSK와 국가 저장

**인자**

`--interface NAME` 기본 wlan0. `--country CC` 기본 KR, 대문자 두 자리. SSID/PSK는 명령행 인자가 아닌 **대화형 입력**.

**사용 조건**

root·대화형 TTY·기존 무선 인터페이스가 필요하다. master/bridge 소속이면 거절한다. 다른 인터페이스 conf가 있으면 병행 덮어쓰지 않는다.

**실행 예시**

```bash
xenova-wifi configure --interface wlan0 --country KR
```

**정상 응답**

프롬프트 `Wi-Fi SSID:`와 `Wi-Fi PSK:`에 입력한 문자가 표시된다. 정상 출력:

```json
{
  "configured": true,
  "interface": "wlan0",
  "credentials": "stored root-only",
  "activation_required": true
}
```

저장 완료이며 아직 AP 연결을 요청한 것은 아니다.

**오류 응답**

입력 길이·TTY·파일/인터페이스 오류: stderr `[xenova-wifi] operation failed; check interface, root-only configuration and service status`, 종료 `1`. argparse 오류 `2`. Manager envelope 없음.

**확인 방법**

`xenova-wifi status --interface wlan0`에서 configured를 확인한다. 실제 연결은 activate 후 authentication=COMPLETED와 IPv4를 확인한다. conf 전체/PSK를 출력하지 않는다. [저장 형식·입력 제약](#wifi-contract).

**Manager 매핑:** Wi-Fi 전용 CLI.

[명령어 색인으로](#command-index)

<a id="cmd-wifi-activate"></a>
### `xenova-wifi activate` — 저장한 Wi-Fi 활성화

**인자**

`--interface NAME` 기본 wlan0. `--country`도 parser가 받지만 activate에서 새 국가/자격증명을 저장하지 않는다. `--json` 없음.

**사용 조건**

root-only conf와 무선 인터페이스가 필요하다. 현재 관리 Wi-Fi의 서비스가 active면 restart, inactive/failed면 start한다.

**실행 예시**

```bash
xenova-wifi activate --interface wlan0
# 즉시 응답은 요청 결과다. 잠시 후 인증·DHCP를 확인한다.
sleep 20
xenova-wifi status --interface wlan0
ip -4 address show dev wlan0
```

**정상 응답**

Status에 `"activation":{"action":"start","result":"requested","stage":"complete"}`가 추가된다. 기존 active면 action=restart. 처음 authentication=unavailable/SCANNING이어도 활성화 요청 결과와 실제 연결 상태를 분리해 판단한다.

**오류 응답**

단계 실패 자체 JSON 예(필드 발췌): `{"interface":"wlan0","activation":{"result":"failed","stage":"supplicant"}}`, 종료 `1`. 실제 출력에는 action·error도 있다. 단계는 state-query/service-state/enable/supplicant/network-reload/network-reconfigure 등.

**확인 방법**

status의 `authentication=COMPLETED`·비어 있지 않은 ipv4·errors를 확인한다. requested만으로 SSH 접속 가능을 판정하지 않는다. 진단은 `journalctl -b -u xenova-wifi@wlan0.service --no-pager`. 비밀번호 재입력/모듈 재적재를 자동 재시도로 수행하지 않는다.

**Manager 매핑:** Wi-Fi 전용 CLI. service enable → start/restart → networkctl reload → 대상 인터페이스 reconfigure.

[명령어 색인으로](#command-index)

<a id="cmd-wifi-status"></a>
### `xenova-wifi status` — 인증·주소·내부망 중복 조회

**인자**

`--interface NAME` 기본 wlan0. `--json` 없음.

**사용 조건**

대상 무선 인터페이스를 조회한다. 설정 파일 전체나 비밀번호를 출력하지 않는다.

**실행 예시**

```bash
xenova-wifi status --interface wlan0
iw dev wlan0 link
ip -4 address show dev wlan0
cat /sys/class/net/wlan0/address
```

**정상 응답**

자체 JSON의 연결 완료 형식 예:

```json
{
  "interface": "wlan0",
  "configured": true,
  "authentication": "COMPLETED",
  "ipv4": ["192.168.30.73/24"],
  "internal_subnet_overlap": false,
  "errors": []
}
```

**오류 응답**

조회가 성립하면 errors가 있거나 SCANNING/DISCONNECTED 상태여도 종료 0일 수 있다. 인터페이스/파일 예외는 일반 stderr와 종료 1. `authentication=unavailable`은 supplicant 조회 불가이며 AP 인증 실패 확정이 아니다.

**확인 방법**

`configured`는 저장 여부, `authentication`은 인증 단계, `ipv4`는 실제 CIDR 배열이다. `internal_subnet_overlap=true`면 내부 `192.168.77.0/24`와 겹친다. CLI는 네트워크를 자동 재편하지 않는다. DHCP IP는 영구 보드 신원이 아니다. authentication은 `not-configured/unavailable/unknown` 또는 supplicant의 SCANNING/DISCONNECTED/COMPLETED 등 상태다.

**Manager 매핑:** Wi-Fi 전용 CLI.

[명령어 색인으로](#command-index)

<a id="cmd-console"></a>
### `xenova-vm console` — Xen PV 콘솔 접속

**인자**

`INTERNAL_NAME` 필수. **`--json` 없음**.

**사용 조건**

등록된 내부 이름·UUID·현재 domid를 검증할 수 있어야 한다. 콘솔 연결 동안 공통 작업 잠금을 계속 잡지 않는다.

**실행 예시**

```bash
xenova-vm status
read -r -p "콘솔에 접속할 실행 중 VM 내부 이름: " INTERNAL_NAME
xenova-vm console "$INTERNAL_NAME"
```

**정상 응답**

검증한 도메인의 `xl console -t pv DOMID` 대화형 세션. JSON 응답 없음.

**오류 응답**

부재·신원 변경·잘못된 대상은 stderr `[xenova] ERROR: …`, 종료 1. 도메인 번호를 고정해 다른 VM 콘솔에 접속하지 않는다.

**확인 방법**

게스트 로그인/부팅 출력을 확인한다. dom0 셸로 돌아오는 키는 **Ctrl+]**다. 콘솔 연결이 앱 준비를 보장하지 않는다.

**Manager 매핑:** 운영 CLI 전용.

[명령어 색인으로](#command-index)

<a id="ssh-agent"></a>
### SSH·Linux 게스트 Agent 확인

**관리 PC에서 실행**한다. 보드 IP는 현재 Wi-Fi 주소이며 게스트 IP는 보드의 `xenova-vm get UUID --json`에서 읽은 등록 주소다.

```bash
read -r -p "현재 보드 관리 IPv4: " BOARD_IP
ssh root@"$BOARD_IP"
```

dom0 셸에서 `exit`로 관리 PC에 돌아온 다음, Linux 게스트로 접속한다. 아래 `read` 문법은 관리 PC의 Bash/WSL 기준이다.

```bash
read -r -p "현재 보드 관리 IPv4: " BOARD_IP
read -r -p "get에서 확인한 Linux 게스트 IPv4: " GUEST_IP
ssh -J root@"$BOARD_IP" root@"$GUEST_IP"
```

인증은 해당 OS 계정·SSH 정책을 사용한다. 호스트키 불일치는 무시하지 말고 보드/게스트 신원을 확인한다. 성공은 대상 셸 진입이며 네트워크 timeout·인증 실패·호스트키 오류는 SSH stderr로 판별한다.

**Linux domU 안에서 실행**:

```bash
cat /etc/xenova/instance-id
cat /etc/machine-id
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
python3 -c 'import paho.mqtt.client'
systemctl is-enabled xenova-agent.service
systemctl status xenova-agent.service --no-pager
```

instance-id·machine-id·SSH host key는 VM 신원이며 reboot/복구/자원 변경으로 재생성하지 않는다. Linux 템플릿에는 Paho·공용 MQTT 클라이언트·`/opt/xenova/agent`가 설치된다. 일반 VM은 생성 때 instance-id를 기록하고 factory seed는 `xenova-guest-identity.service` 뒤에 Agent/SSH를 연결한다.

Agent는 기본 5초 heartbeat를 `xenova/v1/vms/{vm_id}/heartbeat`로 발행하며 수신 측이 생존을 판정한다. 기본 broker는 `192.168.77.1:1883`, 선택 환경은 `/etc/xenova/agent.env`다. dom0에도 Paho·공용 MQTT 클라이언트·Mosquitto/clients가 설치되지만 broker enable·접속/ACL은 별도 운영 설정이다. 설치/import/enable 성공을 broker 종단 통신 성공으로 확대하지 않는다. 메시지는 [MQTT 계약](../contracts/mqtt-contract-v1.md)을 따른다.

Zephyr Network Starter의 capability는 가상 Ethernet·IPv4·DHCP·ICMP이며 SSH/Python Agent capability는 없다.

<a id="diagnostics"></a>
## 10. 운영 진단·설치 도구

진단 명령은 기능별 성공 범위를 구분한다. 외부 IPv4 ping 성공은 DNS/NTP 성공이 아니며 설정 파싱 통과도 실제 VM 부팅 성공이 아니다.

<a id="cmd-check-xen"></a>
### `xenova-vm check-xen` — dom0 Xen 제어 기반 확인

**인자**

인자·`--json` 없음.

**사용 조건**

dom0 capability·문자 장치 `/dev/xen/privcmd`·xl 응답·Domain-0를 확인한다.

**실행 예시**

```bash
xenova-vm check-xen
printf "check-xen rc=%s\n" "$?"
```

**정상 응답**

성공 stdout 없음, 종료 0.

**오류 응답**

stderr `[xenova] ERROR: …`, 종료 1. 단순 xl 바이너리 존재만으로 통과시키지 않는다.

**확인 방법**

위 rc가 0인지 확인한다. 실제 VM 제어/복구 시험은 별도로 수행한다.

**Manager 매핑:** 운영 CLI 전용.

[명령어 색인으로](#command-index)

<a id="cmd-check-network"></a>
### `xenova-vm check-network` — 격리된 내부 브리지 검사

**인자**

인자·`--json` 없음.

**사용 조건**

`xenbr0`가 Linux bridge이고 주소가 `192.168.77.1/24`이며 실제 Xen backend VIF만 연결됐는지 검사한다. dhcp-hosts가 없을 때만 빈 파일을 만든다.

**실행 예시**

```bash
xenova-vm check-network
printf "check-network rc=%s\n" "$?"
ip -brief address show xenbr0
ls -l /run/xenova/dhcp-hosts
```

**정상 응답**

성공 stdout 없음, 종료 0. 기존 hosts 파일을 덮어쓰지 않는다.

**오류 응답**

물리 NIC 편입·주소/브리지 위반은 stderr `[xenova] ERROR: …`, 종료 1.

**확인 방법**

bridge 주소·VM의 등록 IP·MAC/lease를 각각 비교한다. 생성한 hosts는 비밀 없는 예약 파일이며 0644 게시 계약을 따른다. Wi-Fi가 bridge에 편입되면 안 된다.

**Manager 매핑:** 운영 CLI 전용.

[명령어 색인으로](#command-index)

<a id="cmd-diagnose"></a>
### `xenova-vm diagnose` — 부팅 인자·가동 시간·시각 진단

**인자**

인자·`--json` 없음.

**사용 조건**

dom0의 /proc와 timedatectl 정보를 읽는다.

**실행 예시**

```bash
xenova-vm diagnose
timedatectl
systemctl is-active xenova-vms.service xenova-host-admission.service xenova-dom0-manager.service
journalctl -b -u xenova-vms.service -u xenova-dom0-manager.service --no-pager
```

**정상 응답**

자체 JSON 예: `{"time":{"synchronized":true,"details":{"NTPSynchronized":"yes","NTP":"yes","Timezone":"Asia/Seoul"},"interval_clock":"monotonic"},"uptime":"120.00"}`. cmdline도 포함한다.

**오류 응답**

timedatectl 실패는 time의 `synchronized=null,error`로 진단될 수 있다. 외부 명령/파일의 처리되지 않은 오류는 stderr/종료 1. 이 응답에는 Manager ok가 없다.

**확인 방법**

`synchronized`와 서버/패킷 상태를 실제 NTP 진단과 비교한다. Asia/Seoul은 표시 시간대이며 UTC에 9시간을 더하는 보정이 아니다. 제어·복구 기한은 단조 시계를 사용한다.

**Manager 매핑:** 운영 CLI 전용.

[명령어 색인으로](#command-index)

<a id="cmd-domain-identity"></a>
### `xenova-domain-identity` — libxenmanage로 incarnation 조회

**인자**

`--domid N` 필수. 정수 `0<N<0x7ff0`.

**사용 조건**

실행 중인 domU와 설치된 libxenmanage.so.1이 필요하다.

**실행 예시**

```bash
xl list -v
read -r -p "조회할 domU의 현재 domid: " DOMID
xenova-domain-identity --domid "$DOMID"
```

**정상 응답**

자체 JSON 예: `{"domid":6,"xen_unique_id":"7657871419622292281","state_bits":1}`. unique ID는 10진 문자열이다.

**오류 응답**

실행 조회 오류는 stderr·종료 1, 인자 오류는 argparse 종료 2. domid 재사용을 VM 신원 보존으로 해석하지 않는다.

**확인 방법**

live bit(1)·nonzero unique ID를 확인하고 플랫폼 execution-context의 domid/unique ID와 함께 비교한다.

**Manager 매핑:** 독립 설치 도구. Manager action 없음.

[명령어 색인으로](#command-index)

<a id="cmd-verify-artifacts"></a>
### `xenova-verify-artifacts` — 생성 cfg·Xen/QEMU 설치 내용 검사

**인자**

| 인자 | 조건 |
|---|---|
| `--config CFG` | 플랫폼이 생성한 원 xl cfg |
| `--instance-json RECORD` | 해당 VM의 실제 instance.json |
| `--package-manifest MANIFEST` | 빌드 인계물의 설치 패키지 manifest |
| `--qemu PATH` | 선택, 기본 qemu-system-i386 |

**사용 조건**

cfg·record가 동일 VM의 검증된 파일이어야 한다. manifest를 빌드 인계물에서 보드로 준비한다. 운영 바이너리 기능 검사이며 개발 QEMU lab 실행 명령이 아니다.

**실행 예시**

```bash
xenova-vm status
read -r -p "검사할 VM 내부 이름: " INTERNAL_NAME
read -r -p "보드에 준비한 package manifest 절대 경로: " MANIFEST
xenova-vm config "$INTERNAL_NAME"
xenova-verify-artifacts --config "/run/xenova/$INTERNAL_NAME.cfg" \
  --instance-json "/srv/xenova/instances/$INTERNAL_NAME/instance.json" \
  --package-manifest "$MANIFEST"
```

**정상 응답**

자체 JSON: config_sha256, xl_path/xl_sha256, qemu_path/qemu_sha256, parsed_config, qemu_checks, xl_parser_passed, not_tested. 예: `{"xl_parser_passed":true}`. qemu_checks에는 command·qmp·xenpv_properties·integration_boot_tested=false가 포함된다.

**오류 응답**

stderr `[xenova-runtime-artifacts] ERROR: …`, 종료 1. 원 cfg를 바꾸거나 가짜 manifest로 통과시키지 않는다.

**확인 방법**

`xl -N create` 파싱(실제 VM 생성 아님), record·qdisk/backend, Xen tools 4.21+stable/QEMU 10.2.0 manifest와 설치 해시·QMP/xenpv 기능을 확인한다. `not_tested`의 DTB/통합 부팅/앱 준비는 별도 시험이다.

**Manager 매핑:** 독립 설치 도구.

[명령어 색인으로](#command-index)

<a id="cmd-update-guest-ssh"></a>
### `update_guest_ssh.py` — 정지 Linux rootfs의 SSH service 정책 갱신

**인자**

`python3 /usr/lib/xenova/update_guest_ssh.py --root ABS_DIR [--execute]`. root 기본값 /이나 **명시적 guest root 사용**.

**사용 조건**

dom0에서 마운트한 **정지 Linux 게스트 rootfs**의 canonical 경로여야 한다. template-prepared 표식·충돌 없는 SSH 설정이 필요하다. host root를 변경하는 용도가 아니다.

**실행 예시**

```bash
read -r -p "명시적으로 마운트한 정지 guest rootfs 절대 경로: " GUEST_ROOT
python3 /usr/lib/xenova/update_guest_ssh.py --root "$GUEST_ROOT"
# 계획·대상이 맞는지 확인한 뒤 실행한다.
python3 /usr/lib/xenova/update_guest_ssh.py --root "$GUEST_ROOT" --execute
```

**정상 응답**

계획: `{action:"plan-guest-ssh-service-policy",root,identity_reinitialization:false,starts_or_stops_services:false}`. 실행: `{action:"guest-ssh-service-policy-updated",backup,identity_files_preserved:true,starts_or_stops_services:false}`.

**오류 응답**

stderr `[xenova-guest-ssh] ERROR: …`, 종료 1. host root·충돌 SSH 설정은 계획에서도 거절한다.

**확인 방법**

출력 backup 경로(guest의 var/lib/xenova/ssh-policy-backup-*)를 보관한다. instance-id/machine-id/host key 해시는 유지돼야 한다. service·generator mask·seed identity gate만 갱신하고 실제 서비스를 시작/정지하지 않는다.

**Manager 매핑:** 설치 보조 도구. Manager action 없음.

[명령어 색인으로](#command-index)

<a id="host"></a>
## 11. host gate·보드 종료·서비스 내부 명령

이 절은 전체 VM의 시작/복구와 보드 전원에 영향을 줄 수 있다. 단일 시험 VM의 제어에는 2절을 사용한다. `host-resume`·`cpu-host-prepare`는 Manager 명령이며, 이 절의 나머지 운영 명령에는 `--json`을 붙이지 않는다.

<a id="cmd-cpu-host-prepare"></a>
### `xenova-vm cpu-host-prepare` — 설치·부팅 내부 CPU 준비

**인자**

선택 `--execute`, `--restore`, `--timeout-ms N`(기본 15000, 1..15000), `--json`. 정책 변경 명령과 구분되는 설치/부팅 서비스 내부 호출이다.

**사용 조건**

모든 관리 게스트 정지·미완료 실행/작업 부재와 현재 정책·Xen pool을 확인한다. 일반 조회/start가 이 준비를 대신하지 않는다. 운영자가 VM 시작 때마다 수동 호출하는 절차가 아니다.

**실행 예시**

승인된 유지보수 창의 dom0 root 셸:

```bash
xenova-vm cpu-host-prepare --json
xenova-vm cpu-host-prepare --execute --json
xenova-vm cpu-layout --json
```

부분 적용을 포기하고 원본 dom0 마스크로 되돌리는 경우에만 별도로 실행:

```bash
xenova-vm cpu-host-prepare --restore --json
xenova-vm cpu-host-prepare --restore --execute --json
```

**정상 응답**

계획 data는 `{plan:true,configured:true,policy_revision:1,dom0_vcpus:실제_수,restore:false}`다. 실행은 `/run/xenova/cpu-host.json`의 준비 영수증을 반환하며 host_boot_id·policy_revision·policy_sha256·dom0_unique_id·vcpus·원본 마스크·완료 vCPU·phase를 포함한다. 정책 미설정은 `{configured:false,ready:false,legacy:true}`이며 전용 배치 완료가 아니다.

**오류 응답**

실행 중 `VM_NOT_STOPPED`(3), 미완료/uncertain `RECONCILE_REQUIRED`(4), boot·dom0 incarnation·외부 마스크 변경 `TARGET_CHANGED`(3), 실제 충돌/부분 적용 `CPU_APPLY_INCOMPLETE`(4), 정책 손상 `CPU_POLICY_INVALID`(4), 관측/deadline 오류를 구분한다.

**확인 방법**

적용은 여러 `xl vcpu-pin` 호출이므로 원자적이지 않다. phase=applying/failed/restoring이면 새 시작을 차단한다. 같은 boot·revision·dom0 incarnation에서 원래 또는 예정 마스크만 남아 있으면 동일 실행을 재전달해 재개한다. 복원은 원본 영수증의 동일 문맥을 요구하고 정책을 지우지 않으며 완료 후 ready=false다. `cpu-layout`으로 실제 배치를 다시 확인한다.

**Manager 매핑:** `cpu-host-prepare / {timeout_ms?,restore?}`, `execute`는 call 키워드 인자.

[명령어 색인으로](#command-index)

<a id="cmd-host-init"></a>
### `xenova-vm host-init` — 이번 부팅의 host barrier 초기화

**인자**

인자 없음. 즉시 동작.

**사용 조건**

host admission service가 사용하는 초기화 진입점이다. 현재 host barrier가 없을 때만 open을 만든다. 이미 quiescing인 gate를 강제로 여는 명령이 아니다.

**실행 예시**

```bash
xenova-vm host-init
cat /run/xenova/admission/host.json
```

**정상 응답**

자체 barrier JSON 예: `{"state":"open","action":"host-boot"}`. [전체 barrier 필드](#admission-fields).

**오류 응답**

저장/잠금 오류는 stderr `[xenova] ERROR: …`, 종료 1.

**확인 방법**

host_boot_id와 barrier_id를 확인한다. 현재 host의 기존 barrier가 있으면 state가 무조건 open으로 바뀌지 않아야 한다.

**Manager 매핑:** 운영 CLI 전용.

[명령어 색인으로](#command-index)

<a id="cmd-host-quiesce"></a>
### `xenova-vm host-quiesce` — 전역 시작·복구 gate 닫기

**인자**

인자 없음. 즉시 전역 동작.

**사용 조건**

종료 준비를 위해 새 시작/복구를 차단한다. 이 명령만으로 guest 종료나 host 전원을 실행하지는 않는다. 정상 운영 중에는 재개 절차와 작업 경합을 먼저 확인한다.

**실행 예시**

```bash
xenova-vm host-quiesce
cat /run/xenova/admission/host.json
```

**정상 응답**

자체 barrier JSON 예: `{"state":"quiescing","action":"systemd-shutdown"}`.

**오류 응답**

잠금/저장 실패는 stderr/종료 1. 반환 후에는 start/recovery가 HOST_QUIESCING으로 거절될 수 있다.

**확인 방법**

새 barrier_id·state를 기록한다. gate를 다시 열 때는 현재 barrier ID로 host-resume을 사용하고 shutdown-requested 여부를 확인한다.

**Manager 매핑:** 운영 CLI 전용.

[명령어 색인으로](#command-index)

<a id="cmd-host-resume"></a>
### `xenova-vm host-resume` — 현재 barrier가 일치할 때 gate 재개

**인자**

`--barrier-id UUID` 필수, 현재 표준형 barrier ID. `--json`. 즉시 동작.

**사용 조건**

현재 host가 quiescing이고 지정 ID가 정확히 일치하며 shutdown-requested가 없어야 한다. 종료 절차를 무시하는 강제 해제가 아니다.

**실행 예시**

```bash
cat /run/xenova/admission/host.json
read -r -p "현재 quiescing barrier_id: " BARRIER_ID
xenova-vm host-resume --barrier-id "$BARRIER_ID" --json
```

**정상 응답**

`data` 예: `{"host_admission":{"state":"open"}}`.

**오류 응답**

오래된 barrier는 `TARGET_CHANGED`(3), 종료 marker는 `HOST_QUIESCING`(3), 잘못된 UUID는 `INVALID_SPEC`(2).

**확인 방법**

반환 host_admission과 현재 파일의 host/state/barrier를 비교한다. marker·journal을 삭제해서 재개하지 않는다.

**Manager 매핑:** `host-resume / {barrier_id}`

[명령어 색인으로](#command-index)

<a id="cmd-shutdown-all"></a>
### `xenova-vm shutdown-all` — 등록 게스트 전체 종료

**인자**

선택 `--force`. Linux를 포함한 **전체 대상**에 즉시 제거를 적용한다. `--json` 없음.

**사용 조건**

전체 등록 VM에 영향을 준다. 저장 작업·다른 담당자의 사용을 조율하고 UART/재접속 경로를 확보한다. 알 수 없는 실행 도메인·신원 변경·실제 제거 실패가 있으면 후속 host 종료를 거절한다. host 전원은 유지하고 gate는 quiescing으로 남긴다.

**실행 예시**

```bash
xenova-vm status
# 전체 대상과 종료 준비를 확인한 뒤 실행한다.
xenova-vm shutdown-all
```

**정상 응답**

자체 종료 결과에 action·attempt_id·stopped·already_stopped·forced·graceful_shutdown_requested·instances·board_shutdown·global_force·observation이 포함된다. 예: `{"action":"stop","stopped":[],"already_stopped":[],"forced":[]}`(필드 발췌). 전원 동작은 결과 뒤 진행될 수 있다.

**오류 응답**

stderr `[xenova] ERROR: …`, 종료 1. 원인별 VM method/stage/xen_state/error와 종료 기록을 읽는다. timeout을 자동 force로 승격하지 않는다.

**확인 방법**

`/run/xenova/last-stop.json`·`stop-attempt-*.json`·`board-shutdown-result.json`와 UART/다음 부팅 로그를 대조한다. host는 계속 실행되고 gate는 닫혀 있다. 이 동작은 모든 VM에 사용자 stopped 의도를 새로 쓰지 않는다.

**Manager 매핑:** 운영 CLI 전용. [종료 순서·정책](#shutdown-contract).

[명령어 색인으로](#command-index)

<a id="cmd-poweroff"></a>
### `xenova-vm poweroff` — 조율 후 보드 전원 종료

**인자**

선택 `--force`. Linux를 포함한 **전체 대상**에 즉시 제거를 적용한다. `--json` 없음.

**사용 조건**

전체 등록 VM에 영향을 준다. 저장 작업·다른 담당자의 사용을 조율하고 UART/재접속 경로를 확보한다. 알 수 없는 실행 도메인·신원 변경·실제 제거 실패가 있으면 후속 host 종료를 거절한다. guest 제거 후 서비스 정리·데이터 unmount·systemctl poweroff를 수행한다. 다시 켜려면 물리 전원 작업이 필요하다.

**실행 예시**

```bash
xenova-vm status
# 전체 대상과 종료 준비를 확인한 뒤 실행한다.
xenova-vm poweroff
```

**정상 응답**

자체 종료 결과에 action·attempt_id·stopped·already_stopped·forced·graceful_shutdown_requested·instances·board_shutdown·global_force·observation이 포함된다. 예: `{"action":"stop","stopped":[],"already_stopped":[],"forced":[]}`(필드 발췌). 전원 동작은 결과 뒤 진행될 수 있다.

**오류 응답**

stderr `[xenova] ERROR: …`, 종료 1. 원인별 VM method/stage/xen_state/error와 종료 기록을 읽는다. timeout을 자동 force로 승격하지 않는다.

**확인 방법**

`/run/xenova/last-stop.json`·`stop-attempt-*.json`·`board-shutdown-result.json`와 UART/다음 부팅 로그를 대조한다. 최종 poweroff와 reboot는 별도 수락 항목이다. 이 동작은 모든 VM에 사용자 stopped 의도를 새로 쓰지 않는다.

**Manager 매핑:** 운영 CLI 전용. [종료 순서·정책](#shutdown-contract).

[명령어 색인으로](#command-index)

<a id="cmd-reboot-board"></a>
### `xenova-vm reboot-board` — 조율 후 보드 재부팅

**인자**

선택 `--force`. Linux를 포함한 **전체 대상**에 즉시 제거를 적용한다. `--json` 없음.

**사용 조건**

전체 등록 VM에 영향을 준다. 저장 작업·다른 담당자의 사용을 조율하고 UART/재접속 경로를 확보한다. 알 수 없는 실행 도메인·신원 변경·실제 제거 실패가 있으면 후속 host 종료를 거절한다. guest 제거 후 서비스 정리·데이터 unmount·systemctl reboot를 수행한다. SSH 접속이 끊기며 다음 부팅 주소를 다시 확인한다.

**실행 예시**

```bash
xenova-vm status
# 전체 대상과 종료 준비를 확인한 뒤 실행한다.
xenova-vm reboot-board
```

**정상 응답**

자체 종료 결과에 action·attempt_id·stopped·already_stopped·forced·graceful_shutdown_requested·instances·board_shutdown·global_force·observation이 포함된다. 예: `{"action":"stop","stopped":[],"already_stopped":[],"forced":[]}`(필드 발췌). 전원 동작은 결과 뒤 진행될 수 있다.

**오류 응답**

stderr `[xenova] ERROR: …`, 종료 1. 원인별 VM method/stage/xen_state/error와 종료 기록을 읽는다. timeout을 자동 force로 승격하지 않는다.

**확인 방법**

`/run/xenova/last-stop.json`·`stop-attempt-*.json`·`board-shutdown-result.json`와 UART/다음 부팅 로그를 대조한다. 최종 poweroff와 reboot는 별도 수락 항목이다. 이 동작은 모든 VM에 사용자 stopped 의도를 새로 쓰지 않는다.

**Manager 매핑:** 운영 CLI 전용. [종료 순서·정책](#shutdown-contract).

[명령어 색인으로](#command-index)

<a id="cmd-bridge-up"></a>
### `xenova-bridge-up` — 내부 bridge 생성·주소 준비

**인자**

인자 없음. 즉시 네트워크 준비.

**사용 조건**

서비스 내부 초기화 도구다. xenbr0가 없으면 생성하고 .1/24 주소·UP 상태를 준비한다. 물리 eth0/wlan0를 편입하지 않는다.

**실행 예시**

```bash
xenova-bridge-up
xenova-vm check-network
```

**정상 응답**

`ip -br address show dev xenbr0` 출력. 예: `xenbr0 UP 192.168.77.1/24`.

**오류 응답**

ip/sysfs 준비 실패는 셸 stderr·비정상 종료 코드. 검사 실패를 무시하고 물리 NIC를 편입하지 않는다.

**확인 방법**

check-network와 주소를 확인한다. 이미 구성된 관리 연결의 재편 명령으로 사용하지 않는다.

**Manager 매핑:** 설치된 서비스 보조 셸 도구.

[명령어 색인으로](#command-index)

<a id="cmd-dhcp-ready"></a>
### `xenova-vm dhcp-ready` — dnsmasq의 UDP/67 준비 확인

**인자**

`PID` 필수, dnsmasq의 현재 정수 PID. `--json` 없음.

**사용 조건**

서비스 내부 readiness 확인이다. 단순 포트 점유가 아니라 해당 PID가 소유한 소켓이어야 한다.

**실행 예시**

```bash
PID="$(systemctl show xenova-dhcp.service -p MainPID --value)"
xenova-vm dhcp-ready "$PID"
printf "dhcp-ready rc=%s\n" "$?"
```

**정상 응답**

성공 stdout 없음, 종료 0. 최대 5초 동안 확인한다.

**오류 응답**

프로세스 종료·UDP/67 미준비는 stderr `[xenova] ERROR: …`, 종료 1.

**확인 방법**

서비스 active와 등록 MAC/IP의 실제 lease를 별도로 확인한다. 준비 완료가 특정 guest의 등록 IP 획득까지 뜻하지 않는다.

**Manager 매핑:** 운영 CLI 전용.

[명령어 색인으로](#command-index)

<a id="cmd-first-boot"></a>
### `xenova-first-boot grow/register` — 저장소 확장·factory 등록

**인자**

하위 동작 `grow` 또는 `register` 필수. service 내부 명령, 공통 operation lock 사용.

**사용 조건**

최종 이미지의 seed·데이터 준비·마운트 순서에서 사용한다. grow는 실제 장치의 마지막 ext4 데이터 파티션을 확장하고 VM 디스크 크기는 바꾸지 않는다. register 재실행은 기존 사용자 의도·신원을 초기화하지 않는다.

**실행 예시**

```bash
# 이미지의 최초 등록을 다시 만들지 않고 현재 서비스 결과를 확인하는 예다.
systemctl status xenova-data-prepare.service srv-xenova.mount xenova-first-boot.service --no-pager
journalctl -b -u xenova-data-prepare.service -u xenova-first-boot.service --no-pager
# service가 호출하는 실제 진입점:
# xenova-first-boot grow
# xenova-first-boot register
```

**정상 응답**

grow 완료 문자열: `storage verified; last ext4 partition uses actual device bytes; VM disks unchanged`. register: 선택적 registration/asset_errors JSON과 `installation verified; hand off to existing VM autostart service`.

**오류 응답**

stderr `[xenova-first-boot] ERROR: …`, 비정상 종료. 자산 오류를 registry 초기화/seed 재생성으로 덮지 않는다.

**확인 방법**

서비스 로그·설치 영수증·현재 UUID/등록 목록을 대조한다. 기본 Debian 1대·Network Starter A/B 2대의 최초 신원은 보존한다.

**Manager 매핑:** 설치/서비스 진입점. 공통 Manager action 없음.

[명령어 색인으로](#command-index)

<a id="contract-response"></a>
## 공통 계약 A. 응답·오류·멱등성

### A.1 Manager envelope

| 필드 | 타입 | 의미 |
|---|---|---|
| `schema_version` | string | 현재 `"1.0"` |
| `ok` | boolean | 이번 호출 성공 여부 |
| `request_id` | string | 호출자가 지정하거나 플랫폼이 발급한 추적 ID |
| `operation_id` | UUID / null | 연결된 영구 작업. 일반 조회·수락 전 거절은 null 가능 |
| `replayed` | boolean | 동일 제어 키·자산 키·복구 시도 재전달 여부 |
| `data` | object / null | action별 결과. 실패 시 null |
| `error` | object / null | 실패 시 code/message/retryable/details, 성공 시 null |

`error.code`는 문자열, `message`는 진단 문자열, `retryable`은 boolean, `details`는 추가 문맥 객체다. 코드만으로 재시도 정책을 만들지 않는다. 동일 작업이 아직 실행될 수 있으면 원 작업을 조회한다.

실패 envelope의 형식 예(미등록 UUID 조회, 실측 로그 아님):

```json
{
  "schema_version": "1.0",
  "ok": false,
  "request_id": "11111111-1111-4111-8111-111111111111",
  "operation_id": null,
  "replayed": false,
  "data": null,
  "error": {
    "code": "VM_NOT_FOUND",
    "message": "VM UUID not found",
    "retryable": false,
    "details": {}
  }
}
```

CLI 어댑터의 spec-file 읽기·JSON 해석·내부 이름 해석 도중 OSError/ValueError/PlatformError는 `STORAGE_UNAVAILABLE`로 정규화될 수 있다. 모든 잘못된 JSON이 INVALID_SPEC으로 반환된다고 가정하지 않는다. 운영 CLI는 argparse 오류 2, 일반 stderr `[xenova] ERROR: …`와 실행 오류 1을 사용한다. Wi-Fi/설치 보조 도구는 각 절의 자체 계약을 따른다.

<a id="acceptance-contract"></a>
### A.2 수락·키·현재 결과

| 구분 | 중복 판정 기준 | 완료 재전달 |
|---|---|---|
| create | `create:<정규화한 vm_id>` + 정확한 생성 명세 | 물리 준비 반복 없이 원 작업/현재 자산 반환 |
| 자산 lifecycle·자원·firmware 전환 | 필수 idempotency_key + 대상 포함 원 명세 | 원 결과와 현재 상태 분리 |
| 사용자 start/stop/reboot/destroy | 선택 idempotency_key + VM/action/force | Xen 동작 반복 없음. 응답 vm은 현재 관측 |
| recover_once | recovery_attempt_id + 원 요청의 모든 필드 | 원 결과와 현재 관측 분리. 이전 host는 실행 API에서 거절 |

키는 VM마다 독립인 단순 이름표가 아니다. **해당 journal 계열 안의 키가 전역으로 비교**되므로 다른 VM/동작에 같은 키를 재사용하면 충돌한다. 원 명세에는 vm_id도 포함된다. request_id와 지정한 idempotency_key는 제어문자 없는 1..200 UTF-8 bytes다.

사전 거절은 실행 의도를 바꾸지 않는다. 새 제어 수락은 이미 running인 start·이미 부재인 stop도 revision을 증가시킨다. **수락 후 물리 실행 실패가 영구 의도를 이전 값으로 되돌리지는 않는다.** `details.accepted/intent_revision/operation_id`와 원 journal을 대조한다. 필수 수락 저장의 불확실성은 `acceptance:"unconfirmed"` 등의 문맥으로 나타날 수 있으며, 영구 수락을 확인하지 못한 상태에서 Xen 실행을 성공으로 간주하지 않는다.

완료한 실행 뒤 비핵심 event 쓰기만 실패했다면 durable completed를 failed로 덮지 않는다. `DIAGNOSTIC_UNAVAILABLE` warning이 성공 `data.diagnostic_warnings` 또는 오류 `details.diagnostic_warnings`에 붙을 수 있다. 의도·launch binding·필수 journal 자체의 저장 실패는 이 규칙으로 무시하지 않고 재판정한다.

<a id="error-codes"></a>
### A.3 전체 Manager 오류 코드

`retryable`은 아래 코드의 고정 속성이 아니다. busy는 주로 true지만 원 owner/journal 확인이 먼저다. cleanup과 일부 TARGET_CHANGED 관측 오류도 응답별로 다르다.

| code | CLI 종료 | 발생 조건·대응 |
|---|---:|---|
| `INVALID_SPEC` | 2 | 필드·타입·UUID·필수 키·범위·force/selector 오류. 입력 수정 |
| `VM_NOT_FOUND` | 2 | 등록 UUID 또는 지정 journal 없음 |
| `TEMPLATE_NOT_FOUND` | 2 | 공개 template ID 없음 |
| `TEMPLATE_VERSION_NOT_FOUND` | 2 | 정확한 게시 버전 없음 |
| `RESOURCE_UNSUPPORTED` | 2 | 템플릿/게스트 종류·자원 범위·고정 프로필 위반 |
| `RECOVERY_MODE_UNSUPPORTED` | 2 | 협조형 기능 미준비 또는 force 복구 승인 없음 |
| `VM_NAME_CONFLICT` | 3 | 내부 실행 이름/UUID/MAC 소유 충돌 |
| `IP_CONFLICT` | 3 | 요청·예약 IP/MAC와 현재 소유권 충돌 |
| `IDEMPOTENCY_CONFLICT` | 3 | 같은 키/attempt에 다른 요청 |
| `OPERATION_IN_PROGRESS` | 3 | 공통/짧은 잠금 또는 미완료 소유 작업. 원 작업 확인 |
| `VM_NOT_STOPPED` | 3 | 실제 Xen 도메인이 있어 자산 작업 거절 |
| `TARGET_CHANGED` | 3 | host/lifecycle/설정/실행 신원·barrier·소유 자산 변경 |
| `ASSET_IN_USE` | 3 | Xen/loop/mount/열린 프로세스 연결·공유 파일 |
| `RESTORE_CONFLICT` | 3 | 보관 신원·이름·MAC/IP의 복원 충돌 |
| `VM_PURGED` | 3 | 영구 제거된 자산의 실행/복원 요청 |
| `HOST_QUIESCING` | 3 | 종료 gate 또는 shutdown-requested. 종료 상태 조사 |
| `BOOT_ADMISSION_REQUIRED` | 3 | 현재 host·세대·의도의 복구 admission 불일치/미승인 |
| `INTENT_MISMATCH` | 3 | 기대 running revision 또는 변경용 stopped 의도 불일치 |
| `ARTIFACT_INVALID` | 4 | 자산·record·경로/manifest 검증 또는 분류되지 않은 사전 플랫폼 오류 |
| `STORAGE_UNAVAILABLE` | 4 | 영구 저장소/registry 접근·의도 수락 저장 불확실 |
| `INSUFFICIENT_STORAGE` | 4 | 현재 준비와 기존 VM 증가 예약을 포함한 용량 부족 |
| `IP_POOL_EXHAUSTED` | 4 | 사용 가능한 내부 예약 IP 없음 |
| `OBSERVATION_UNAVAILABLE` | 4 | Xen/host/handler incarnation의 검증된 관측 실패 |
| `DEADLINE_EXCEEDED` | 4 | probe/short lock/복구/협조형 reboot 기한 만료 |
| `XEN_EXECUTION_FAILED` | 4 | 수락 후 물리 제어 실패. accepted 상태·journal 확인 |
| `RECOVERY_REQUIRED` | 4 | 자산 준비/부분 게시/journal 무결성·중단 복원 조사 |
| `RECONCILE_REQUIRED` | 4 | launch/제어/복구/완료 저장의 실행 불확실성. 물리 요청 반복 없이 조사 |
| `CPU_POLICY_INVALID` | 4 | 활성 CPU 정책·anchor·publication history 유실/손상/읽기 실패. legacy fallback 금지 |
| `CPU_NOT_READY` | 4 | 현재 boot·policy revision의 dom0 준비 미완료 또는 불일치 |
| `CPU_APPLY_INCOMPLETE` | 4 | 도메인 존재 가능, 실제 배치 불일치·충돌·부분 적용. 신원/journal 확인, 자동 destroy·repin 금지 |
| `CLEANUP_INCOMPLETE` | 4 | purge 도중 저장/삭제 예외. 같은 키로 잔여 소유 inventory 판정 |

`DIAGNOSTIC_UNAVAILABLE`는 비핵심 sink warning이며 단독 Manager 실패/종료 코드를 만들지 않는다. CLI 종료 4만 보고 새 복구 attempt를 자동 발급하지 않는다.

<a id="contract-identity"></a>
## 공통 계약 B. 신원·실행 의도·필드 사전

### B.1 신원의 수명

| 신원 | 수명·계약 |
|---|---|
| vm_id / configuration.uuid | 자산의 영구 UUID. 외부 발급 UUID 유지. 대상 UUID의 대소문자/32자리 표기는 소문자 하이픈형으로 정규화, nil 거절 |
| name | 표시 이름. CLI create는 1..63 Python 문자, 공백만인 값·C0/C1 제어문자·surrogate 거절. 유효한 앞뒤 공백은 보존. 경로/Xen 이름 아님 |
| configuration.id/name/hostname | 내부 실행 이름·경로·hostname. `[a-z][a-z0-9-]{0,39}`. 일반 생성은 `vm-<UUID 하이픈 제거>`, factory는 debian-dev/zephyr-drive-a/b |
| MAC / IPv4 | 플랫폼이 예약하는 독립 네트워크 신원. 재시작에 재할당하지 않음 |
| generation | 자산 lifecycle 세대. retire/restore/purge 완료마다 증가. 자원/활성 firmware 변경은 유지 |
| desired_state / intent_revision | 수락한 running/stopped 의도·새 제어 수락마다 증가하는 revision |
| host_boot_id | 이번 dom0 부팅의 `/proc/sys/kernel/random/boot_id` |
| execution_id | 플랫폼의 새 launch와 결합된 실행 UUID. 새 실행마다 교체 |
| domid | 현재 Xen 번호. 재사용 가능하므로 영구 신원 아님 |
| xen_unique_id | Xen incarnation의 uint64를 **10진 문자열**로 전달. 숫자 변환 시 정밀도 손실 주의 |
| VMView.configuration_sha256 | 현재 configuration 객체의 canonical JSON SHA256. 자원/firmware 변경 조건 |
| execution_context.configuration_sha256 | 렌더링한 xl cfg **문자열** fingerprint. 위 설정 객체 해시와 다름 |

canonical JSON은 UTF-8·키 정렬·공백 없는 구분자·ASCII escape 없음·NaN 거절이다. operation/attempt/barrier **선택자**는 이미 소문자 하이픈 표준 UUID여야 한다. 일반 대상 UUID의 정규화 허용과 구분한다.

**자산 lifecycle·실행 의도·Xen 관측·앱 준비/차량 권한은 별개다.** registered=true, running/blocked 또는 복구 완료가 STANDBY_READY·ACTIVE·운행 허가를 생성하지 않는다. 플랫폼 app_readiness는 unknown이다.

<a id="vmview"></a>
### B.2 VMView 전체 필드

| 필드 | 타입·값 | 해석 |
|---|---|---|
| `vm_id` | UUID string | 영구 대상 |
| `name` | string | 표시 이름 |
| `template_id` | string / null | 공개 생성 템플릿 ID. 구형 record는 미설정 가능 |
| `template_version` | string / null | 생성에 사용한 정확한 게시 버전 |
| `guest_kind` | linux / zephyr | 게스트 종류 |
| `vcpus` | integer | 현재 등록 CPU |
| `memory_mib` | integer | 현재 등록 메모리 MiB |
| `ipv4` | string / null | 예약 주소. legacy 비네트워크는 null |
| `lifecycle` | REGISTERED/RETIRED/PURGING/PURGED | 자산 수명주기 |
| `generation` | integer ≥0 | 자산 세대 |
| `registered` | boolean | 현재 등록 자산 여부 |
| `initial_spec` | object / null | 원 생성 명세. factory/기존 등록은 null 가능 |
| `configuration` | object | 실제 현재 instance record |
| `configuration_sha256` | SHA256 string | 설정 객체 fingerprint |
| `xen_state` | string / null | 현재 Xen 상태. 최초 자산 결과는 관측 생략 가능 |
| `domid` | integer / null | 현재 실행 도메인 |
| `app_readiness` | unknown | 앱 준비 판정 없음 |
| `desired_state` | running/stopped/unknown | 영구 의도, 미설정은 unknown |
| `intent_revision` | integer / null | 의도 revision, 미설정은 null 가능 |
| `intent_applicable` | boolean | 현재 lifecycle 세대에 적용되는 의도인지 |
| `intent_lifecycle_generation` | integer / null | 의도가 수락된 자산 세대 |
| `host_boot_id` | UUID string | 현재 host |
| `boot_admission` | object / null | 현재 host의 VM admission |
| `host_admission` | object | host barrier |
| `shutdown_requested` | boolean | 종료 marker 여부 |
| `execution_id` | UUID string / null | 현재 managed 실행 ID |
| `execution_context` | object / null | 현재 incarnation과 일치하는 launch 기록 |
| `observation_revision` | integer / null | 관측 revision. 관측 생략 시 null |

initial_spec과 configuration은 자원 변경 후 다르다. 자산 작업 최초 완료는 live 관측을 생략할 수 있어 xen_state/domid/execution_id가 null일 수 있다. 현재 실행은 get/probe로 읽는다.

<a id="template-fields"></a>
### B.3 Template·생성·주소 계약

Template 객체는 다음 필드를 제공한다.

| 필드 | 내용 |
|---|---|
| schema_version | descriptor 버전 |
| template_id / template_version | 공개 ID·정확한 버전 |
| guest_kind | linux/zephyr |
| asset_directory / directory | 자산 디렉터리 정보·보드 실제 경로 |
| manifest_sha256 | 게시 manifest 해시 |
| source_template_id | 원본 템플릿 식별 |
| default_resources | 기본 vcpus/memory_mib |
| resource_limits | 허용 자원 범위 |
| capabilities | 네트워크·협조 종료 등 기능 |
| source | 출처·빌드 입력 |

| 현재 기본 카탈로그 | 버전 | 기본 vCPU/MiB | 허용 vCPU/MiB |
|---|---|---|---|
| ubuntu-24.04-arm64 | 1.0.2 | 1 / 512 | 1..4 / 128..8192 |
| debian-13-arm64 | 1.0.2 | 1 / 512 | 1..4 / 128..8192 |
| zephyr-xenvm-starter-arm64 | 1.1.0 Network Starter | 1 / 16 | 1 / 16 고정 |

실제 설치 버전과 기능은 templates 결과를 우선한다.

<a id="creation-contract"></a>
create의 정확한 spec은 `vm_id,name,template_id,template_version,vcpus,memory_mib`와 선택 `ipv4`다. 자원은 bool이 아닌 양의 정수다. `--auto-ip`는 ipv4 생략을 표현하며 Manager 필드가 아니다.

- Linux·Network Starter의 자동/명시 예약 IP는 `192.168.77.2..99`다. 자동은 사용 가능한 낮은 주소부터 선택한다.
- 등록·보관 자산·미완료 작업·factory/운영 예약·DHCP hosts/lease를 함께 대조한다. 현재 운영 예약은 .10/.20이며 factory Debian .20은 그 소유권으로 유지한다.
- Network Starter는 MAC/IP/hostname을 갖고, legacy 비네트워크 Starter는 ipv4 입력을 거절한다.
- MAC은 플랫폼이 생성하는 `02:xx:xx:xx:xx:xx`다. UUID·MAC·IP·내부 이름 예약은 공통 operation lock 안에서 대조한다.
- create 완료는 REGISTERED/generation=0/autostart=false/의도 stopped/revision=1. 기본 board policy는 협조형 graceful, legacy refuse다.
- 용량 검사는 현재 할당뿐 아니라 기존·미완료 VM의 향후 디스크 증가 예약을 포함한다. 부분 준비본은 journal/workspace를 보존하며 자동 디스크 재초기화를 하지 않는다.

<a id="observation-fields"></a>
### B.4 Observation·Snapshot

성공 probe의 `data.observation`에는 다음 필드가 있다. observation 내부에 vm_id/schema_version을 임의로 추가해 소비하지 않는다.

| 필드 | 타입·값 | 의미 |
|---|---|---|
| configuration_revision | SHA256 string | 현재 설정 객체 해시 |
| desired_state / intent_revision / intent_applicable | string / integer 또는 null / boolean | 관측 시 의도 |
| boot_admission | object / null | 해당 의도에 대한 admission |
| presence | present/absent | 성공 관측으로 확인한 존재 여부 |
| xen_state | running/blocked/paused/shutdown/crashed/dying/unknown/stopped | absent이면 stopped |
| xen_state_flags | string / null | 실제 Xen flags |
| domid / xen_unique_id | integer 또는 null / decimal string 또는 null | 현재 incarnation |
| execution_id | UUID / null | 현재 managed 실행 |
| execution_status | managed/unmanaged/absent | 플랫폼 실행과 결합 여부 |
| lifecycle_generation | integer | 관측한 자산 세대 |
| app_readiness | unknown | 앱 준비 판정 아님 |
| observation_revision | integer | 성공 관측 값 변경 시 증가 |
| host_boot_id | UUID | 현재 host |
| observed_monotonic_ms | integer | 같은 host의 성공 관측 단조 ms |

running 또는 정상 I/O 대기 blocked는 Xen 실행 중이다. 기존 paused/shutdown/crashed/dying/unknown을 start가 임의 제거하지 않는다. 외부 xl 실행은 플랫폼 launch와 결합되지 않으면 unmanaged이며 실행 ID를 만들어 붙이지 않는다.

Xen 목록의 전후 비교·incarnation 확인 도중 변경되면 TARGET_CHANGED다. 관측 실패는 absent가 아니다. 같은 성공 값의 반복 관측은 revision을 유지하고 시각만 갱신한다. probe budget은 Xen·게시 short lock에 적용하며 명시적 무기한 flock 대기를 두지 않는다. 전체 HTTP/파일시스템 호출의 hard real-time 보장은 아니다.

| Snapshot 필드 | 타입·해석 |
|---|---|
| vm_id / host_boot_id / observation_revision | 대상·현재 host·캐시 성공 revision(캐시 없으면 null) |
| host_admission / shutdown_requested | 장기 VM 작업과 독립 조회하는 host gate |
| stale / unknown | boolean. 현재 판단에 사용할 수 없는지 |
| age_ms | integer / null. 성공 관측의 단조 age |
| last_success | null 또는 `{monotonic_ms,value}`. 과거 성공 증거 |
| last_error | null 또는 최신 오류 정보 |
| last_attempt_monotonic_ms | integer / null. 마지막 시도 |
| current | 유효한 last_success.value 또는 null |

성공 없음·이전 host·음수 age·max_age 초과면 stale이다. stale 또는 현재 오류가 있으면 unknown/current=null이다. snapshot.current는 저장된 value이므로 probe 바깥에서 붙이는 host/revision/time 필드가 없다. host/revision은 Snapshot 상위, 시각은 last_success.monotonic_ms에서 읽는다. ok=true/unknown=true는 현재 실행을 성공 관측했다는 뜻이 아니다.

### B.5 Runtime execution record

| 필드 | 타입·의미 |
|---|---|
| schema_version | "1.0" |
| vm_id / host_boot_id / execution_id | 자산·부팅·실행 UUID |
| lifecycle_generation / intent_revision | launch에 결합한 자산 세대·의도 revision |
| phase | launching/observed/stopped/uncertain |
| owner | 자동 시작 null, 사용자 `{kind:"user",operation_id}`, 복구 `{kind:"recovery",recovery_attempt_id}` |
| prepared_monotonic_ms | launch 준비 시각 |
| configuration_sha256 | 생성 xl cfg 문자열 해시 |
| domid / xen_unique_id | 결합 전 null, 결합 후 incarnation |
| observed_monotonic_ms | 결합 후 추가되는 관측 시각 |
| stopped_monotonic_ms | 종료 기록에 추가되는 시각 |
| cpu_policy | configured·policy_revision·정책의 canonical SHA256·allocation·desired_hard_affinity. instance.json 해시와 별도 문맥 |
| cpu_verification / cpu_error | 생성 후 CPU 검증 실패 시 incomplete와 원 오류. observed 실행 신원은 유지 |

cpu_policy의 policy_sha256은 key 정렬·공백 없는 UTF-8 JSON(ensure_ascii=false)의 SHA256이며 들여쓴 파일 자체의 sha256sum과 다르다. control/recovery/resource journal에도 수락한 정책 문맥을 연결한다. recover_once 요청 형태는 유지하며 하위 계층에서 기록·대조한다.

모든 시각은 같은 host의 단조 ms다. VMView.execution_context는 현재 managed incarnation과 일치할 때만 노출한다.

<a id="contract-journal"></a>
## 공통 계약 C. 복구·journal·중단 복원

### C.1 journal 종류와 결과의 시간

| 계열 | ID·주요 필드 | 상태 |
|---|---|---|
| 자산 | schema_version, operation_id, idempotency_key, action, vm_id, request, initial_spec, spec_hash, result, error. 동작별 snapshot·예약·잔여 inventory | status=RUNNING/SUCCEEDED/RECOVERY_REQUIRED |
| 사용자 제어 | schema_version, operation_id, vm_id, request, request_hash, idempotency_key, host_boot_id, lifecycle_generation, base_revision, intent_revision, desired_state, source, result, error, events | prepared/accepted/executing/completed/failed/uncertain |
| 복구 | schema_version, recovery_attempt_id, operation_id, request, request_hash, host_boot_id, accepted_monotonic_ms, result, error, events, phase | accepted/stopping/removed/launching/observed/completed/failed/uncertain |

자산 journal phase는 preparing/published/dhcp-pending/dhcp-applied/moving/moved/purge-inventory/deleting/complete 등 물리 진행 경계다. operation 조회의 effective_status는 SUCCEEDED이면 SUCCEEDED, 나머지는 RECOVERY_REQUIRED다. 이는 별도의 자동 복구 실행 성공을 뜻하지 않는다.

최초 create/retire/restore 완료의 data는 VMView 자체다. 완료 재전달은 이 필드에 operation/original_result/current을 추가한다. 자원/firmware 전환 완료는 처음부터 operation/original_result/current으로 감싼다. 제어 응답의 vm은 현재 관측이며 원 제어 결과는 control-operation.result다. 이 차이를 응답 parser에서 유지한다.

비핵심 event는 schema_version, event, host_boot_id, monotonic_ms, vm_id, operation_id, details를 기록한다. 완료/재전달/거절 event 저장 실패는 원 실행 결과를 가리지 않는 warning이다.

### C.2 게스트별 stop/reboot와 실행 결합

| 대상 | 정상 동작 |
|---|---|
| Linux | 검증한 domid에 graceful shutdown 후 실제 제거 확인. reboot는 같은 UUID·디스크의 새 incarnation과 새 execution_id 결합 |
| Network Starter | 활성 bundle + 현재 incarnation의 XenStore control/feature-poweroff=1 및 feature-reboot=1을 함께 확인 |
| legacy Starter | 협조형 종료 없음. 단일 VM stop/reboot는 명시적 --force 필요 |

Network Starter reboot의 durable 단계는 `request-accepted → shutdown-requested → old-removed → new-launching → new-observed → completed`다. 원래의 기본 90초 deadline을 유지하며 물리 요청 제출 이후 재전달은 요청을 중복 제출하지 않고 관측·완료 판정한다. 제거 확인은 이전 도메인의 이름 변경/잔여 상태와 incarnation을 대조한다.

사용자 --force는 명시적 물리 제어이며 복구 force-restart 허용 목록과 별개다. 자산 보관/삭제 보호를 우회하지 않는다. timeout 때 자동 force로 바꾸지 않는다.

<a id="recovery-reconciliation"></a>
### C.3 복구 성공·만료·중단 판정

복구 original_result는 다음 필드를 제공한다.

| 필드 | 의미 |
|---|---|
| vm_id / host_boot_id | 대상·수락 host |
| lifecycle_generation / intent_revision | 수락한 문맥 |
| execution_id | 새 실행 ID |
| observation | 새 실행 성공 관측 |
| platform_elapsed_ms | 시도 수락부터의 단조 ms. 앱 준비 시간 아님 |
| app_readiness | unknown |

복구 성공 data는 attempt/original_result/current_observation을 분리한다. 같은 attempt의 어느 필드든 바꾸면 IDEMPOTENCY_CONFLICT다. 동일 요청은 원 결과를 보존하며 물리 실행을 반복하지 않는다. 이전 host 요청은 완료 이력이라도 실행 API에서 TARGET_CHANGED이며 recovery-operation으로 이력만 조회한다.

| 중단 위치·상태 | 판정 |
|---|---|
| accepted/removed 후 만료 | 공통 소유 잠금에서 자산·의도·실제 Xen·launch 기록 대조. 아직 launch 전이고 이후 실행 가능성이 없음이 확정되면 failed/DEADLINE_EXCEEDED로 닫아 새 attempt 허용 |
| stopping/launching/observed/uncertain | 시간·PID·일시적 부재만으로 owner 해제 금지. 해당 owner의 새 managed 실행이면 완료 기록만 확정, 아니면 RECONCILE_REQUIRED |
| 사용자 stop 후 다시 start | running이어도 revision이 바뀌므로 과거 요청은 INTENT_MISMATCH |
| 필수 완료 journal 저장 실패 | 실행 성공처럼 단정하지 않고 실행 신원·owner로 재판정 |
| 완료 뒤 진단 event만 실패 | durable completed 유지, diagnostic warning 분리 |

unresolved details에는 blocking_attempt_id/blocking_operation_id/phase/next_action/allowed_actions/automatic_retry가 포함될 수 있다. automatic_retry=false이면 이유와 허용 조치를 읽고 원 요청·journal을 조사한다. 미완료 자산/제어/복구 owner와 launching/uncertain 실행이 새 실행을 막는다.

복구는 같은 UUID·기존 디스크/firmware·MAC/IP를 사용한다. heartbeat 감시·주행/Jetson 입력 장애 판단·재시작 횟수/대기/소진·AUTO/MANUAL·ACTIVE/STANDBY·안전 정지·재무장·제어권은 담당 서비스의 책임이다.

<a id="firmware-contract"></a>
### C.4 firmware bundle·자원 변경 보호

bundle은 canonical 절대 디렉터리이며 symlink·공유 파일을 허용하지 않는다. payload는 읽기 전용으로 준비하고 전체 논리 크기는 64 MiB 이하다. manifest는 firmware.json을 우선하며 legacy bundle은 template.json일 수 있다.

검증 대상은 zephyr.bin, zephyr.elf, zephyr.config, zephyr.dts, xen.cfg.in, LICENSE와 manifest의 해시·bytes다. firmware.json bundle은 zephyr.version.h를, Network profile은 zephyr.map·XEN-ABI-LICENSE도 포함한다. ARM64 Image/ELF·DTS·config·고정 1vCPU/16MiB/GICv2·고정 Zephyr 4.4.0/SDK 입력을 대조한다.

firmware-transition은 revision을 독립 복사하고 active metadata·프로필·capabilities·종료 정책을 게시한다. UUID·generation·initial_spec·creation_origin·원본 firmware·다른 VM을 유지한다. legacy→Network의 MAC/IP/hostname은 journal에 보관해 재전달에 재할당하지 않는다. 역전환은 기존 주소 예약을 보존한다. 계획의 address_reserved=false는 아직 주소 소유권을 게시한 실행이 아니라는 뜻이다.

update-resources는 instance.json의 vcpus/memory_mib만 바꾸며 initial_spec·UUID·디스크·MAC/IP·의도 revision·generation을 보존한다. 자원/firmware 작업 모두 현재 세대의 stopped 의도·실제 부재·정확한 설정 객체 해시·자산 미사용이 필요하다. loop/mount/열린 프로세스·중단된 owner·불확실 launch 보호는 유지한다.

### C.5 보관·복원·purge 보호

retire는 정지 자산을 숨김 archive로 이동하고 RETIRED/generation+1을 기록한다. restore는 RETIRED의 신원·주소·이름·자산·파일시스템 검사·용량을 확인해 REGISTERED/generation+1로 복원한다. 둘 다 원 실행 의도를 바꾸지 않으므로 lifecycle 세대가 바뀌면 기존 의도는 부적용일 수 있다. 필요한 새 사용자 제어로 의도를 수락한다.

purge는 RETIRED → PURGING → PURGED이며 완료 시 generation+1이다. 소유 inventory의 dev/inode/종류·단일 hardlink·symlink·Xen 연결·loop·mount·열린 프로세스를 확인하고 원본/타 VM/공유 자산을 보호한다. 중간 실패 후 같은 키로 잔여 자산을 판정하며 UUID tombstone·이력은 남긴다.

| purge space 필드 | 단위·의미 |
|---|---|
| logical_bytes | 삭제 대상 논리 bytes |
| allocated_bytes | 실제 할당 bytes |
| free_bytes | 정리 전 파일시스템 여유 bytes |
| free_bytes_after | 정리 후 여유 bytes |
| free_bytes_delta | 전후 여유 차이 |

journal의 inventory/remaining_assets/space_before/space_result를 함께 읽는다. free bytes 차이를 파일 논리 크기와 같은 것으로 계산하지 않는다.

<a id="contract-boot"></a>
## 공통 계약 D. 부팅·종료·배포·저장 경로

<a id="boot-policy"></a>
### D.1 자동 부팅 정책과 같은 host 의도 보존

정책 경로는 `/etc/xenova/execution-policy.json`이다. 현재 게시 값:

```json
{
  "schema_version": "1.0",
  "autostart_stopped": "keep-stopped",
  "manual_running": "keep-stopped",
  "force_restart_vm_ids": []
}
```

파일이 없으면 두 충돌 정책은 null, force 목록은 공란으로 취급한다. 정책 파일은 group/world writable을 허용하지 않는다.

| autostart | 적용 가능한 intent | 판정 |
|---|---|---|
| true | running | 시작 허용, POLICY_MATCH |
| false | stopped | 생략, POLICY_MATCH |
| true | stopped | autostart_stopped 선택 적용 |
| false | running | manual_running 선택 적용 |

충돌 정책의 값은 start/keep-stopped/null이다. start는 승인된 시작, keep-stopped는 생략이다. stopped를 start로 승인하면 새 running revision을 수락한다. null은 BOOT_POLICY_PENDING/allowed=false를 기록하면서 **물리 autostart는 기존 autostart 플래그**를 따른다. 미설정/현재 세대 부적용 의도도 기존 autostart 경로를 사용하되 복구 admission=false다. 미설정 정책을 임의 승인된 선택으로 해석하지 않는다.

같은 host에서 이미 수락한 사용자 의도가 있으면 위 fallback보다 먼저 현재 원본을 대조한다. 과거 user-start admission을 최신 stopped보다 우선하지 않는다. VM·host·generation·intent revision이 일치할 때 재사용하며 intent/journal로 복원하거나 INTENT_RECONCILIATION_REQUIRED로 시작을 막는다. 같은 host의 명시 running은 autostart=true일 때 자동 시작하고 false일 때 생략한다. 복구 허가는 별도 allowed admission으로 판정한다.

factory/부팅 초기화는 의도가 없을 때만 만든다. 일반 create는 stopped, factory는 자동 시작 플래그에 따라 초기 running/stopped다. 기존 의도·신원은 재등록/서비스 재시작으로 초기화하지 않는다.

<a id="admission-fields"></a>
### D.2 admission 필드

| 객체 | 필드 |
|---|---|
| boot_admission | schema_version, vm_id, host_boot_id, lifecycle_generation, intent_revision, allowed, source, reason, lifecycle |
| host_admission | schema_version, host_boot_id, barrier_id, state, action, monotonic_ms |

boot allowed는 boolean이며 해당 host·세대·revision에만 유효하다. host state는 open/quiescing이다. monotonic_ms는 같은 host 단조 ms다. autostart VM별 결과는 /run/xenova/autostart-results.json에 게시한다.

<a id="shutdown-contract"></a>
### D.3 종료 barrier와 board policy

정상 종료는 gate를 먼저 quiescing으로 닫고 공통 operation lock의 기존 작업을 drain한다. 전체 종료 경로의 기본 budget은 90초다. 정상 systemd 종료에서는 admission unit의 ExecStop이 gate를 닫고 VM unit의 ExecStop이 shutdown-all을 수행한다. **dom0-manager 프로세스 재시작은 host 종료가 아니다.**

| 대상 | 저장 가능한 board policy | 실제 종료 경로 |
|---|---|---|
| Linux | graceful | --force가 없으면 협조형 |
| legacy Starter | refuse / force-allowed | 비협조형. force-allowed는 보드 종료 시 명시 제거 승인 |
| Network Starter | graceful / refuse / force-allowed | --force가 없으면 협조형 |

현재 Network Starter 종료 구현은 refuse 값만으로 별도 veto 분기를 만들지 않는다. force-allowed는 timeout 후 자동 force로 승격하는 정책이 아니다. 명시 --force는 Linux를 포함한 전체 대상에 적용된다.

shutdown-all은 guest를 정리하고 gate를 닫아 둔다. poweroff/reboot-board는 추가로 미등록 실행 도메인·잔여 실행·신원 변경을 검사한 뒤 VM/종료/DHCP 서비스 정리 → /srv 데이터 unmount → host 전원 동작을 진행한다. 실제 제거가 확인되지 않으면 후속 host 종료를 진행하지 않는다. 사용자 stopped 의도를 모든 VM에 일괄 생성하지 않는다.

종료 결과는 action, attempt_id, stopped, already_stopped, forced, graceful_shutdown_requested, instances, board_shutdown, global_force, observation을 제공한다. observation에는 samples/changed_pairs/recent_changes, VM별 결과에는 method/stage/xen_state/error 등이 있다. shutdown-requested가 있으면 단순 host-resume을 허용하지 않는다.

### D.4 초기 등록·내부망·Wi-Fi 저장

서비스 순서는 `xenova-data-prepare.service → srv-xenova.mount → xenova-first-boot.service`와 Xen ready 완료 → `xenova-cpu-prepare.service` → `xenova-vms.service` autostart → 기존 host admission이다. CPU prepare는 Wants+After로 순서만 연결하며 Requires/PartOf/BindsTo·ExecStop으로 게스트 종료나 affinity 초기화를 전파하지 않는다. 수동 start·reboot·복구도 이번 boot 준비를 별도로 검사한다.

신규 설치는 `/etc/xenova/cpu-policy-preset.json`을 첫 identity plan의 A/B UUID에 연결해 cpu_policy_activation ticket을 만든 뒤 같은 정책 저장 코드로 게시·적용한다. 기존 identity plan에는 소급 추가하지 않는다. 정책·anchor·history가 모두 없는 기존 설치는 과거 호환 모드다. 활성화는 승인된 정지 창에서 cpu-policy-set → cpu-host-prepare로 수행한다. 활성화 후 정책 유실·손상은 미설정과 구분해 새 시작을 차단한다. seed는 최초 등록 전 상태로 준비하고 데이터 확장·독립 UUID/MAC·Linux machine-id/SSH 신원을 보드에서 확정한다. 기본은 Debian Interaction 1대와 Network Starter A/B 2대다.

xenbr0=192.168.77.1/24는 격리된 내부망이다. 예약 .2..99, dnsmasq bringup 동적 풀 .100..150을 구분한다. DHCP에서 기본 라우터를 배포하지 않고 DNS는 port=0이다. eth0/wlan0를 편입하거나 외부 NAT를 자동 설정하지 않는다. Manager는 비밀 없는 dhcp-hosts를 **0644로 원자 게시**하고 dnsmasq HUP으로 예약을 반영한다.

<a id="wifi-contract"></a>
Wi-Fi 입력/저장 계약:

| 항목 | 제약·저장 |
|---|---|
| interface | 영숫자로 시작하는 1..15자리 영숫자/_.- |
| country | 두 자리 대문자, 기본 KR |
| SSID | C0 제어문자 없는 1..32 UTF-8 bytes |
| PSK | 8..63 printable ASCII 또는 64자리 hex |
| 프롬프트 | SSID/PSK 입력 문자 표시. 실제 값을 argv·로그·Git에 남기지 않음 |
| /etc/xenova/wifi/NAME.conf | 0600, 부모 0700. SSID UTF-8 hex, 패스프레이즈는 PBKDF2-HMAC-SHA1 4096회/32 bytes, hex 입력은 소문자 |
| supplicant | WPA-PSK/RSN, disable_scan_offload=1 |
| /etc/systemd/network/25-xenova-wifi.network | 0644, IPv4 DHCP, RouteMetric=600, IPv6AcceptRA=no, RequiredForOnline=routable |

저장 conf·enablement가 다음 부팅에 사용되며 무선 SDIO PIO·CYW 모듈·feature_disable=0x2000도 이미지 부팅 경로에 포함된 설정을 따른다. 현재 연결 성공만으로 다음 이미지의 자동 연결까지 검증됐다고 선언하지 않는다. country/interface/PSK를 외부망 연결을 위한 영구 VM 신원으로 쓰지 않는다.

### D.5 저장 경로와 잠금

| 경로 | 내용 |
|---|---|
| /srv/xenova/catalog/ID/VERSION.json | 게시 descriptor |
| /srv/xenova/templates / rtos-templates | 불변 Linux/Zephyr 원본 |
| /srv/xenova/instances/INTERNAL_NAME | 독립 root.raw·부트 자산/firmware revision |
| /srv/xenova/tombstones/UUID.json | lifecycle·세대·purge 뒤 식별 이력 |
| /srv/xenova/operations/OP_ID.json | 생성·자산·자원·firmware journal |
| /srv/xenova/control/UUID.json | 실행 의도 원본, 보관 스냅샷과 분리 |
| /srv/xenova/control-requests/OP_ID.json | 사용자 제어 journal |
| /srv/xenova/recovery-attempts/ATTEMPT_ID.json | 복구 journal |
| /srv/xenova/cpu-policy.json / cpu-policy-enabled.json | CPU 정책 원본 / 최초 활성화 anchor |
| /srv/xenova/cpu-policy-operations/OP_ID.json | CPU 정책 publication journal |
| /run/xenova/cpu-host.json | 이번 host·policy revision의 dom0 준비·부분 적용·복원 영수증 |
| /run/xenova/executions/UUID.json | 이번 host launch/binding |
| /run/xenova/observations/UUID.json | 마지막 성공 관측·현재 오류 |
| /run/xenova/boot-admission/UUID.json | VM admission |
| /run/xenova/admission/host.json | host gate·barrier |
| /run/xenova/execution-events | 비핵심 진단 event |
| /run/xenova/dhcp-hosts / dnsmasq.leases | 예약·실제 lease |
| /run/xenova/INTERNAL_NAME.cfg | 실행용 생성 xl cfg |
| /run/xenova/autostart-results.json | 자동 시작 결과 |
| /run/xenova/last-stop.json / stop-attempt-*.json / board-shutdown-result.json | 종료 증거 |

CPU 정책 게시 순서는 prepared journal → activation anchor → 정책 원자 게시 → completed다. 중단 시 원 spec/key로 재개한다. 과거 유효 정책 파일을 복사해도 최신 완료 revision·소유자를 대조하므로 rollback으로 거절한다. 정책·anchor·history를 모두 지우는 외부 root 조작까지 탐지하는 저장소는 아니다.

영구 저장은 파일 fsync → rename → 부모 fsync로 게시한다. 공통 operation lock은 기본 경합 시 즉시 거절하며 종료 drain은 deadline을 사용한다. 관측/admission short lock도 무기한 flock 대기를 두지 않는다. 잠금 순서는 operation → short lock이다. 파일·journal 삭제나 PID/시간만으로 owner를 해제하지 않는다.

<a id="integration"></a>
## 연동 명세. Python Manager·HTTP·WebSocket

### Python Manager 호출

보드 dom0에서 실행하는 독립 조회 예:

```bash
xenova-vm list --json
read -r -p "조회할 VM UUID: " VM_ID
export VM_ID
PYTHONPATH=/usr/lib/xenova python3 - <<'PY'
import json, os
from xenova_management import Manager

result = Manager().call(
    "get",
    {"vm_id": os.environ["VM_ID"]},
    execute=False,
    idempotency_key=None,
    request_id="manual-get",
)
print(json.dumps(result, ensure_ascii=False, indent=2))
PY
```

정식 Python 진입점은 `xenova_management.Manager.call`이다. 서명은 `Manager.call(action, spec=None, *, execute=False, idempotency_key=None, request_id=None)`다. spec은 JSON 표현 가능한 dict이며 허용 필드 외 입력은 거절한다. execute는 bool이다. 정확한 action/spec은 각 명령의 Manager 매핑을 따른다. execute=False도 start/stop/설정 변경에는 dry-run이 아니다.

정상/오류는 [envelope](#contract-response)를 반환한다. Python에서는 dict의 ok/data/error를 확인하며 CLI 종료 코드는 별도로 적용하지 않는다. CLI delete/status/recover-once 별칭을 그대로 Python action으로 옮기지 않고 각각 retire/list/recover_once를 호출한다.

### FastAPI dom0 Manager


설치 서비스는 `xenova-dom0-manager.service`, 설정은 `/etc/xenova/dom0-manager.env`다. 게시 기본은 `XENOVA_DRIVER=platform`, `XENOVA_HOST=127.0.0.1`, `XENOVA_PORT=8081`, `XENOVA_PLATFORM_LIB=/usr/lib/xenova`다. Python 기본 Settings의 mock 모드와 보드 배포 설정을 구분한다.

| 경로 | 입력 | 성공 응답·공통 Manager와의 관계 |
|---|---|---|
| `GET /healthz` | 없음 | `200 {"status":"ok"}`, Xen 불가면 `503 {"status":"xen_unavailable"}`. xl 응답·registry 디렉터리 확인 |
| `GET /api/v1/stats` | 없음 | `200 {sampled_at,vms:[VmStats…]}`. xentop 실제 표본 |
| `GET /api/v1/vms` | 없음 | `200 [Vm…]`. 등록 목록 중 실제 Xen 도메인 있는 대상만 |
| `GET /api/v1/vms/{vm_id}` | UUID | `200 Vm`. 등록돼 있어도 정지/부재면 `404` |
| `POST /api/v1/vms` | `vm_id,name,template_id,template_version,vcpus,memory_mib` | `201 Vm`. 신규 등록 또는 정지된 기존 등록의 시작. 이미 실행 중이면 `409 VMExists` |
| `POST /api/v1/vms/{vm_id}/stop` | UUID | `204`, body 없음. graceful stop·디스크 보존. 등록 없음도 정지 목표가 달성된 것으로 처리 |
| `DELETE /api/v1/vms/{vm_id}` | UUID | `204`, body 없음. 정지된 등록 retire. 실행 중 `409`, 미등록 `404` |
| `WS /api/v1/vms/{vm_id}/console` | UUID·서비스 Bearer·로컬 Xen driver | PTY 콘솔 relay. 아래 조건 |

HTTP `Vm`은 **`vm_id,state,vcpus,memory_mib` 네 필드**이며 Manager VMView나 envelope와 다르다. HTTP 상태는 `running/paused/stopping/crashed`다. blocked/unknown은 running, shutdown/dying은 stopping으로 매핑하고 stopped는 응답 대상 도메인 부재다. HTTP POST 자원 모델은 vCPU `1..4`, 메모리 `16..1024 MiB`이며 이후 실제 템플릿 제한·호스트 가용 자원을 추가 검사한다. name은 앞뒤 ASCII 공백도 거절하므로 CLI create와 검증 범위가 다르다.

기존 UUID의 POST는 기록된 생성 명세와 template/version/자원을 대조한다. 기존 등록의 자원을 갱신하는 요청이 아니며 자원 변경에는 [자원 변경](#cmd-update-resources)을 사용한다.

HTTP에는 공통 Manager의 모든 action을 자동 노출하지 않는다. 복구·snapshot·자원 변경 등의 계약은 각 명령의 공통 API/CLI로 호출한다. Python 서비스 내 Manager 어댑터는 프로세스 안 동시 호출을 기본 5초까지 직렬화하고 초과 시 `503 PlatformUnavailable`로 변환한다. 이는 작업 전체의 5초 완료 보장이 아니다.

보드 서비스의 ExecStartPre가 `dom0_manager.service_token`으로 `/etc/xenova/dom0-manager-token.env`를 생성·검증한다. root 소유 0600 일반 파일이며 공통 이미지에는 값이 없다. 기존 `/etc/xenova/dom0-manager.env`의 XENOVA_SERVICE_TOKEN은 우선하고 자동 회전하지 않는다. 서비스 unit은 토큰 파일 → 기존 env 순서로 읽는다. HTTP 서비스 토큰은 API 호출자 인증용이며 MQTT broker 계정이나 Wi-Fi PSK와 별개다. 토큰 파일·환경 전체를 로그로 덤프하지 않는다.

토큰 설정 시 `/healthz`를 포함한 HTTP API는 `Authorization: Bearer …`를 검사한다. `/docs,/redoc,/openapi.json`은 공개 문서 경로다. WebSocket은 별도 인증으로 비어 있지 않은 서비스 토큰·`XENOVA_CONSOLE_ENABLED=true`·로컬 non-mock driver가 필요하다. VM당 한 콘솔, 입력 프레임 최대 65536bytes, 출력은 binary, text 입력도 허용한다. `{"type":"disconnect"}`는 정상 해제 요청이다. domid가 바뀌면 기존 연결을 닫는다.

| WebSocket 종료 | 의미 |
|---|---|
| `1000` | 정상 해제/정상 콘솔 종료 |
| `1003` / `1009` | 입력 형식/크기 위반 |
| `1008` | 비활성화 또는 로컬 driver 조건 위반 |
| `4400` / `4401` / `4404` | UUID·대상 입력 / 인증 / VM 없음 |
| `4409` | 콘솔 점유·사용 가능한 도메인 없음·실행 교체 |
| `1011` | 콘솔/도구/전송 timeout 등 내부 오류 |

HTTP 오류 body는 `{code,message,detail?,resource?,platform_code?}`다. platform_code는 하위 Manager 원본 코드이며 detail 문자열 대신 이 필드로 분기한다. `400 InvalidArgument, 401 Unauthorized, 404 VMNotFound, 409 VMExists/InvalidState, 507 InsufficientResources, 501 NotSupported, 503 PlatformUnavailable, 504 DriverTimeout, 500 DriverError`를 사용한다. Manager 코드·CLI 종료 코드를 HTTP 코드로 그대로 옮기지 않는다. 예를 들어 operation busy는 CLI `3`이지만 HTTP `503`이다. 정확한 schema와 게이트웨이 책임은 [VM API 계약](../contracts/vm-api-contract-v1.md), [콘솔 WebSocket 계약](../contracts/console-websocket-v1.md)에 정의한다.



### 통계·heartbeat worker의 운영 계약

`GET /api/v1/stats`는 실행 중 VM의 `vm_id,domid,source=xen,sampled_at,state,vcpus,cpu_time_s,memory_mib,max_memory_mib,net_rx_bytes,net_tx_bytes,disk_rd_bytes,disk_wr_bytes,unavailable`을 반환한다. CPU 시간·network/disk 값은 실행 이후 누적값이며 사용률은 연속 표본으로 계산한다. 수집 불가 필드는 0 대신 null과 NO_DEVICE/DEVICE_ERROR/NOT_REPORTED/PARSE_ERROR 사유를 반환한다. domid 교체 전후 누적값을 이어 빼지 않는다. 이 통계는 CPU hard affinity 조회와 별개다.

`xenova-recovery-worker.service`는 `xenova-dom0-manager` Yocto 패키지가 설치·enable하는 별도 프로세스다. 진입점은 `python3 -m dom0_manager.recovery`, 소스는 같은 서비스의 recovery 모듈이다. HTTP 프로세스 재시작과 host quiesce를 PartOf로 묶지 않는다. VM autostart 뒤·host admission 앞에 시작하며 외부 network-online을 기다리지 않는다.

worker는 실제 Xen·실행 의도와 agent/app heartbeat를 관측해 판정을 queue에 넣고 MQTT 건강 상태를 게시한다. 현재 entrypoint의 queue 소비자가 자동 recover_once를 실행하는 구조로 해석하지 않는다. 플랫폼 일회성 복구는 [recover-once](#cmd-recover-once)의 독립 계약이다. broker/ACL 구독이 성립하지 않으면 해당 heartbeat 손실 판정을 보류하며, Xen crashed/absent 관측은 구분해 계속한다. 앱 heartbeat를 선언하지 않은 Starter를 주행 앱 heartbeat 장애로 꾸미지 않는다.

| 설정 | 기본값·의미 |
|---|---|
| XENOVA_RECOVERY_BROKER_HOST / PORT | 127.0.0.1 / 1883 |
| XENOVA_RECOVERY_BROKER_USERNAME / PASSWORD | 미설정. 보드의 root 전용 recovery-worker.env로 제공 |
| XENOVA_RECOVERY_SOURCE_ID | dom0-server |
| XENOVA_RECOVERY_INTERVAL / INTENT_REFRESH | 5 / 60초 |
| XENOVA_RECOVERY_HEARTBEAT_LOST / APP_HEARTBEAT_LOST | 30 / 15초 |
| XENOVA_RECOVERY_BOOT_GRACE / ZEPHYR_BOOT_GRACE / ABSENT_GRACE | 90 / 30 / 60초 |
| XENOVA_RECOVERY_HEALTH_REFRESH / STATE_DIR / LOG_LEVEL | 15초 / /run/xenova/recovery / INFO |

실행 위치는 dom0 root 셸. 아래 조회는 service를 재시작하거나 VM·네트워크를 변경하지 않는다.

```bash
systemctl is-enabled xenova-dom0-manager.service xenova-recovery-worker.service
systemctl is-active xenova-dom0-manager.service xenova-recovery-worker.service
journalctl -b -u xenova-recovery-worker.service -n 40 --no-pager
stat -c '%a %U %n' /etc/xenova/dom0-manager-token.env
```

enable/active는 broker 종단 성공과 다르다. 실제 인증·SUBACK·신호 수신·건강 상태 게시를 해당 broker 설정으로 따로 검증한다.

### CPU 조회를 dom0-manager에 연결하는 계약

대표 호출은 `Manager.call("cpu-layout", {"vm_id": UUID, "timeout_ms":5000}, request_id=...)`이며 vm_id 생략은 전체 조회다. 기존 네 필드 Vm DTO 대신 별도 자원 조회 응답으로 data를 연결한다. 기존 start/stop/reboot·recover_once 호출자는 별도 pin 동작을 추가하지 않는다. 공개 CPU 정책 변경은 로컬 cpu-policy-set 하나이며 새 HTTP 변경 API를 이 하위 계약에서 만들지 않는다.

| 하위 오류 | 권장 HTTP / 처리 |
|---|---|
| INVALID_SPEC / RESOURCE_UNSUPPORTED | 400 / 입력·지원 환경 수정 |
| VM_NOT_FOUND | 404 / 최신 UUID 확인 |
| VM_NOT_STOPPED / IDEMPOTENCY_CONFLICT / TARGET_CHANGED | 409 / 현재 문맥 확인. 새 key로 무조건 반복 금지 |
| OPERATION_IN_PROGRESS | 503 / retryable 응답 확인 후 제한된 backoff |
| RECONCILE_REQUIRED / STORAGE_UNAVAILABLE / RECOVERY_REQUIRED | 503 / journal·저장소 조사 |
| CPU_POLICY_INVALID / CPU_NOT_READY | 503 / 관리자 복구·host 준비 |
| CPU_APPLY_INCOMPLETE | 409 / 실행 신원·실제 배치 확인, 자동 destroy·repin 금지 |
| OBSERVATION_UNAVAILABLE / DEADLINE_EXCEEDED | 503 / 504 / 새 조회 가능, 변경은 journal 우선 |

위 CPU 세 코드는 신규 매핑 인계 항목이다. 현행 mapper는 미등록 CPU 오류를 DriverError(500)로 처리한다. 원 code는 platform_code로, message는 detail로 전달하지만 retryable/details를 구조화해 보존하지 않으므로 별도 CPU 어댑터에서 보존 방식을 정해야 한다. CPU 오류의 retryable 기본값은 false다.

하위 timeout_ms는 1..15000ms이고 상위 SerializedManager의 기본 최대 5초 mutex 대기는 포함하지 않는다. 현재 HTTP 조회 5초·Spring 6초 예산 안에서 상위 대기를 줄이거나 소모 시간을 빼고 남은 기한을 전달한다. ok=true인 불일치 응답을 HTTP 성공 여부만으로 배치 준수라고 판정하지 않는다.

<a id="sources"></a>
## 구현 근거와 유지 기준


| 구현 | 이 문서의 대응 |
|---|---|
| [xenova_vm.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_vm.py) | 명령 라우팅, Xen·자산 확인, 시작/종료/보드 조율 |
| [xenova_management_cli.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management_cli.py) · [xenova_management.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_management.py) | 정확한 spec, envelope, 키, lifecycle, 반환/오류 |
| [xenova_execution.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_execution.py) · [xenova_recovery.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_recovery.py) | 의도·관측·신원·복구·중단 복원 |
| [xenova_resources.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_resources.py) · [xenova_zephyr_transition.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_zephyr_transition.py) | 자원/활성 프로필 변경 |
| [xenova_zephyr_control.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_zephyr_control.py) · [xenova_zephyr.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_zephyr.py) | 협조형 reboot, bundle·capability 검증 |
| [xenova_wifi.py](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_wifi.py) | 대화 입력·저장·활성화·조회 |
| [CPU 정책·관측 구현](https://lab.ssafy.com/s15-final/S15P31E110/-/blob/347d935cb7ef09e07f3082a13af1733bb6acd2cb/platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_cpu.py) | CPU 정책 저장·host 준비·실제 전체 domain 관측 |
| [dom0-manager](../../platform/services/dom0-manager/dom0_manager) | HTTP 모델·driver 변환·콘솔 |
| [platform/tests](../../platform/tests) | Manager·복구·자원·네트워크 Zephyr·Wi-Fi 경계 회귀 |

명령·필드·응답·오류·설치 경로가 바뀌면 이 명세와 대응 시험·HTTP 계약을 함께 대조한다. 새 빌드의 설치 결과와 실보드 수락은 [빌드 가이드](build-guide.md)의 이미지 해시 기준으로 확인한다.
