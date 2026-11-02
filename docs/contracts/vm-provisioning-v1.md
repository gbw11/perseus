# VM 자격 공급·신호 프로필·등록 통지

2026-11-02 제공자 계약이다. 일반 생성과 factory 모두 영구 VM UUID를 사용한다.
아래 Python/CLI는 하위 제공물이다. 새 HTTP 관리 route를 제공한다는 뜻은 아니다.
Spring의 등록-only route는 **계약안·HTTP fixture 수락 상태**이며 담당자 합의와 제품 구현이 남아 있다.

## 자격의 원본과 전달

계정·암호·ACL의 원본은 기존 `xenova_broker`의 `/srv/xenova/mqtt/current` 세대다.
`xenova_provisioning.ensure(data, record)`가 검증된 `provisioning_profile.channels`에 필요한
기존 UUID 계정 또는 `telemetry-UUID` 계정만 확보한다. 새 암호 생성기는 만들지 않았다.
Linux Agent는 기존 UUID 계정으로 v1과 자기 VM의 v2를 발행한다. Zephyr v2 Publisher와
drive 연결은 기존 별도 계정을 쓴다. 같은 인증 자격을 쓰는 동시 MQTT 연결은 서로 다른 Client ID를 사용한다.

| 사건 | 실제 호출·저장·소비 |
|---|---|
| Debian/Ubuntu 일반 create | 검증 복사 1회 안의 `prepare_instance.make_instance` → `apply_linux` → private root `/etc/xenova/agent.env`. 실제 `AgentSettings.from_env`·Agent unit이 소비 |
| factory Linux | seed에는 비밀 없음. `FirstBoot.register`의 영구 UUID 계획 뒤 private root에 위 env와 `platform-identity.json` 공급. guest identity unit이 host UUID로 instance-id를 확정한 뒤 Agent 시작 |
| Zephyr 일반 create | 기존 대상 AssetLease/lifecycle slot 아래 private firmware 준비 → `ensure` → 짧은 등록 게시. 해시 동안 공통 관리 잠금을 반환 |
| factory Zephyr | `FirstBoot.register`의 같은 UUID 계획 뒤 `ensure`. A/B는 같은 firmware·서로 다른 UUID와 암호 |
| Zephyr start/autostart/reboot/recover | `Platform.start`의 실제 실행 binding 뒤 `bootstrap_execution` → 기존 Bootstrap codec·XenStore/receipt. 영구 암호 유지, 새 execution의 boot/publisher/app 신원만 원 API로 발급 |
| 명시 재공급 | `xenova-vm provision UUID --execute`. Linux는 정지 의도·현재 미사용·대상 lease guard 후 offline mount. Zephyr는 원 자격 재사용, running intent이면 현재 실행에 Store 적용. stopped intent이면 local 준비만 하며 VM을 시작하지 않음 |
| 읽기 | `xenova-vm provisioning UUID --json`. 자격 발급/reload/POST/guest 변경 없음 |

`provision UUID`는 계획이고 `--execute`가 실제 공급이다. `--json`은 출력만 바꾼다.
명시 timeout/budget은 적용하며 잘못된 옵션은 거절한다. 다음 호출은 기존 full create와 함께 쓸 수 있다.

```sh
xenova-vm provisioning 850e8400-e29b-41d4-a716-446655440001 --json
xenova-vm provision 850e8400-e29b-41d4-a716-446655440001
xenova-vm provision 850e8400-e29b-41d4-a716-446655440001 --execute --timeout-ms 10000 --json
```

Worker/Bootstrap의 root 로컬 호출자는 설치된 `/usr/lib/xenova`에서 다음을 사용한다.

```python
from xenova_provisioning import credential_file
drive_credentials = credential_file(data_root, vm_id, 'drive')  # Path, 발급·reload 없음
telemetry_credentials = credential_file(data_root, vm_id, 'telemetry')
# ProductionBootstrap의 config['credentials'][vm_id][channel]에 경로를 연결한다.
# 파일 본문을 로그/HTTP/원 journal에 복제하지 않는다.
```

private adapter 파일은 `/srv/xenova/provisioning/UUID/`의 root 0700 아래 0600이다.
broker가 원본이며 adapter 사본은 실행 신원/건강의 원본이 아니다. 완성 create replay는 공급을 다시 실행하지 않는다.
이미 계정이 있었다면 암호·해시·세대를 재사용한다. 정상 반복 확인은 reload하지 않는다.
직전 reload 미확정은 명시 provision에서만 다시 요청할 수 있다. `reload_requested`는 인증 성공이 아니다.
계정의 명시 제거는 revoked fence를 남겨 자동 ensure가 부활시키지 못하게 한다. 복원은 기존 broker의 명시 add 절차다.
공급 API의 `CREDENTIAL_REVOKED`와 `CREDENTIAL_SOURCE_UNAVAILABLE`은 수습 방법이 다르다.
후자는 broker의 비밀 없는 원인 사유를 보존하고 손상/원문 소실을 재발급으로 처리하지 않는다.
평문 staging/해시 완료 뒤 중단은 원 암호로 재개한다. 손상·원문 소실·혼합 형식은 정상 부재나 새 암호로 보정하지 않는다.
Zephyr create의 완성 private firmware는 명시한 같은 create로 대상 lease·원 명세·작은 firmware를
다시 검증한 뒤 공급/게시만 재개할 수 있다. 새 firmware 복사를 만들지 않는다. 불완전/손상 자산은 삭제·재복사하지 않는다.
Linux 실패 private 디스크의 기존 명시 수습/폐기·예약 반환 계약은 이 동작과 별개로 유지한다.

공개 supply는 `credential_state`, `broker_application`, `guest_application`, `authentication`을 나눈다.
`stored`/`next_start`/`reload_requested`/`private-root-configured`/`store-published`는 각 단계의 사실이다.
Store receipt의 `applied`도 Store 게시이며 guest 소비·MQTT 인증·앱 READY 증명이 아니다.
기존 Worker `receipt_dir=/srv/xenova/drive-rejoin`의 `UUID/execution/{telemetry,drive}.json`과
같은 receipt/lock을 사용한다. `receipt_directory(data, vm_id, execution_id)`로 경로를 확인한다.
별도 디렉터리에서 원 boot/app 신원을 다시 발급하지 않는다. 예전 설치의 임의 경로는 교체 전에 대조해야 한다.
인증 관측이 없으면 `authentication=unobserved`다. broker offline과 필수 local 저장 실패를 합치지 않는다.
후자는 등록 전 원 준비를 실패로 남기며, 이미 등록된 VM의 공급 실패는 등록·원 성공을 지우지 않는다.
stop/retire/purge가 자동 계정 폐기·암호 회전을 의미하지 않는다.

## 별도 world/Jetson 인증 영역

Pi 계정으로 Jetson 인증을 가정하지 않는다. Jetson broker 담당자가 해당 UUID의 read-only world
접근을 승인하고 다음 private 파일을 전달한다. 비밀번호 예시는 fixture 설명이며 운영값을 Git에 넣지 않는다.

```json
{"vm_id":"850e8400-e29b-41d4-a716-446655440001","world":{"broker_ipv4":"192.0.2.2","port":1883,"username":"850e8400-e29b-41d4-a716-446655440001","password":"fixture-only","profile":"lattice-lab-1","trusted_internal_plaintext":true}}
```

root 0600 파일만 `xenova-world-install --vm-id UUID --file /private/approved-world.json`로 설치한다.
등록된 정지 Zephyr와 기존 작업/의도/미사용 guard가 필요하다. 같은 codec의 메모리 검증 후 기존 drive
설정의 world 부분만 원자 교체한다. Pi 암호·broker 계정·VM/디스크를 만들지 않는다.
새 실행 전에 설치하며 기존 receipt는 고치지 않는다. 현재 실행 변경이 필요하면 정식 stop/start 문맥을 사용한다.
world 미제공이면 AUTO 입력은 unavailable이고 MANUAL/Pi 관리는 별개다.
이 평문 프로필은 신뢰된 내부망에 한정하며 외부 TLS 요구를 무시하는 fallback이 아니다.

## 선언과 현재 실행

manifest의 검증된 `provisioning_profile`이 공통 원천이다. 구형 자산은 실제 Agent 설치 receipt,
알려진 앱 source와 검증한 `.config`에 한해 해석한다. 미선언은 null이며 오류를 false로 바꾸지 않는다.
`channels`는 이번 공급 선택, `signals`는 제공 기대, `apps`는 stateful/stateless·Standby progress 계약이다.
broker ACL은 별도 원본이며 network 지원이나 앱 자기 보고만으로 출력 권한을 주지 않는다.

`provisioning` 응답의 `initial_template`은 최초 출처, `profile`은 다음 기동 선택,
`current_profile`은 현재 host 실행의 검증된 loader capture다. 후자는 `captured-launch`,
`observation=not-refreshed`이며 이번 조회가 Xen/Store/Agent를 관측했다는 뜻이 아니다.
구형 실행에 capture가 없으면 null이다. 순수 `intents`의 읽기 전용·Xen 없음 계약은 그대로다.
new incarnation은 새 capture/binding을 확인한다. firmware 교체가 과거 실행의 선언을 바꾸지 않는다.

Starter의 `app_heartbeat=false`, 미선언 null, 실제 drive의 true는 서로 다르다.
true로부터 신호 수신·정상 계산·READY·출력 허가를 추론하지 않는다. warm Standby의 progress idle은 정상이다.
현재 lattice AUTO는 stateful, MANUAL은 stateless이며 미선언을 stateless로 승인하지 않는다.

## Spring 등록-only 계약안과 실제 sender

제공자 송신부: `xenova_registration`, 수동 CLI: `xenova-vm registration-notify UUID [--execute] [--json]`.
계획은 무송신이다. 실제 송신 결과 미확인은 JSON의 `confirmed=false`와 CLI exit 4로 반환한다.
이 CLI는 현재 REGISTERED 대상만 읽으며 VM/계정/디스크 생성, create/start 역호출을 하지 않는다.

**합의 전 수신 계약안:** `POST /api/v1/vms/registrations`, HTTPS, 호스트에 한정된 Bearer token.
현재 `VmController.create`의 `POST /api/v1/vms`는 새 UUID·Pi create/start를 수행하므로 사용하지 않는다.
송신 JSON은 `schema_version=1.0`, `vm_id`, `name`, `guest_kind`, `template_id`,
`template_version`(과거 등록에 없으면 null), `vcpus`, `memory_mib`, 네트워크 VM의 `assigned_ipv4`다.
값은 등록 구성이며 실제 running/Agent IP/현재 건강을 넣지 않는다. 인증이 source host와 등록 권한을 결합해야 한다.
UUID만으로 권한이 생기지 않는다.

수신자는 전달 UUID를 그대로 저장/확인한다. 같은 UUID의 재전송은 중복 행·create/start를 만들지 않는다.
기존 행의 현재 자원/이름/의도/보관·삭제 상태를 늦은 등록 body로 덮거나 부활시키지 않는다.
기존 UUID의 다른 host/guest identity는 409, 무권한은 401/403, 무효 DTO는 400으로 거절하는 안이다.
DB transaction을 확정하거나 기존 허용 등록을 확인한 경우만 다음 body와 200/201을 반환한다.

```json
{"vm_id":"850e8400-e29b-41d4-a716-446655440001","registration":"confirmed"}
```

다른 2xx/빈 응답/접수 body는 `response-unconfirmed`다. 3xx는 따라가지 않는다.
연결 불가, TLS 검증 실패, 서버 거절, 응답 형식 오류, 전송 후 응답 유실/timeout을 구별한다.
응답 유실은 DB 미등록 증거가 아니며 자동 retry/GET은 없다. 운영 endpoint/token 공급과 실제 Spring 수신 구현은 미완료다.

설정은 root 0600 `/etc/xenova/registration-notification.json`과 그 `token_file`의
root 0600 `{"token":"…"}`다. 예시는 `/usr/share/xenova/registration-notification.json.example`에만 설치한다.
공급 전에는 not-configured이며 무송신이다. loopback HTTP는 명시 시험 설정에서만 허용한다.

자동 기회는 create 완료 journal의 `registration_notice=consumed`, factory complete progress의
`registration_notices`에 먼저 기록한다. 기록 후 중단하면 실제 송신 0회일 수 있다.
완료 replay/재부팅/복구/restore/profile 변경/기존 VM 순회로 보충하지 않는다.
등록 확정 후 독립 프로세스에 공개 body만 넘기며 관리/대상 FD를 상속하지 않는다.
runtime flock slot 4개가 전체 동시 수를 제한한다. 설정의 기본 2초·상한 5초는 전송 자원 예산이며
VM 실행/운영 성능 보장이 아니다. supervisor가 DNS/TLS/read를 포함한 HTTP 자식을 기한에 종료·회수한다.
포화는 capacity-skipped로 소비된 자동 기회를 종료한다. 자동 큐/daemon/restart/timer는 없다.
부가 결과는 root 0700 `/srv/xenova/registration-notices/UUID.json`에 기록한다.
결과 기록/기동 실패도 이미 확정된 로컬 등록을 뒤집지 않는다. 수동 호출은 같은 sender를 한 번 실행한다.

## 재현과 제공 수준

실제 제품 시험은 `platform/tests/test_xenova_provisioning.py`, `test_xenova_registration.py`,
`test_xenova_signal_profiles.py`다. native hash·격리 broker·HTTP socket/process·실제 Agent parser를 사용한다.
mount/guest OS/Store 및 Spring DB는 명시 fixture다. 실제 ARM 번들을 지정하는 factory 시험은
`XENOVA_ACTUAL_DRIVE_TEMPLATE=/absolute/drive-template`로 실행한다. 대체 firmware를 만들지 않는다.
명령/후보·package·원자료는 [검증 보고](../verification/recovery-scenario-closure-20261030.md#vm-provisioning-integration)에 연결한다.
실제 제품 상위 연결·새 Linux rootfs 생성·운영 설치·실보드 인증/앱/차량·7번은 별도다.
