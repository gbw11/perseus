# 표시용 주행 상태 v1 — 제어 담당 연결 규격 (폐기)

> **폐기 (2026-11-06, Gateway 읽기로 대체):** 사용자 결정으로 표시 수집기의 주행 상태 입력을 [Gateway 읽기 계약](display-gateway-read-v1.md) 하나로 정리했다. 이 문서의 `xenova/v1/display/drive-state` / `display.drive_state` 토픽은 발행자·ACL이 끝내 연결되지 않았고, 수집기는 더 이상 구독·수락하지 않는다(`XENOVA_DISPLAY_DRIVE_TRANSPORT`·`XENOVA_DISPLAY_CONTROL_SOURCE` 설정도 제거, 남아 있으면 경고 후 무시). 아래 발행·ACL 요청은 **진행하지 않는다**. 이하 내용은 당시 결정 이력으로만 남긴다.

> **2026-11-02 사용자 선택:** 표시 수집기가 기존 Gateway context를 직접 읽는 [gateway 읽기 계약](display-gateway-read-v1.md)을 추가했다. 명시적 gateway 모드에서는 아래 MQTT 표시 토픽을 사용하지 않는다. 아래 1초 발행·ACL 항목은 호환 mqtt 모드의 이력이며 새 읽기 경로의 필수 의존성이 아니다. 기본값은 mqtt이므로 설정·수집기·프론트 동시 배치가 필요하다. 이 선택을 실제 보드 Gateway 배포 완료로 취급하지 않는다.

2026-11-02 · S15P31E110-250 · platform 24b26ff에서 재확인. 사용자가 별도 표시 상태의 1초 보고 방향을 선택했다. **Gateway의 제어 권위 원본·담당 근거는 Git에 있으며, 표시용 발행 경로·신선도 의미·ACL 연결 확인은 아직 남아 있다.** 이 정리에서 팀원 메시지 전송·운영 설정 변경을 하지 않았다.

## Git에서 확인한 연결 근거

[AuthorityStore.payload](../../platform/services/gateway/xenova_gateway/authority.py)는 mode·owner로 vehicle.control payload를 만들고 [VerifiedRunner](../../platform/services/gateway/xenova_gateway/verified_runner.py)는 이를 발행한다. [MQTT 계약](mqtt-contract-v1.md#54-payload)은 source_id=dom0-gateway와 mode·active_vm_id를 정의한다. [Authority 요청서](../requests/2026-10-27-authority-recovery-ctrl.md)는 현재 Gateway 담당을 박신영으로 명시한다. 담당 미정이 아니라 **표시용 전달 연동이 미확인**인 상태다.

추가로 [provider context](gateway-provider-v1.md)는 현재 owner·mode·generation 외에 phase·output_permitted·storage_uncertain을 제공한다. 저장된 선호 owner나 mode만으로 재시작/disarmed/PENDING 상태를 실제 주행 정상으로 표시해서는 안 된다. 원본 상태를 재사용하되 어떤 조건에서 표시용 active_vm_id·mode를 확정할지 맞춰야 한다.

기준 platform의 Gateway 코드에는 아래 display.drive_state 주기 발행이 없고, [gateway ACL](../../platform/deploy/mosquitto/etc/acl)에도 해당 토픽 write가 없다. 기존 vehicle.control은 변경 시 발행·retained이므로 ‘새 값이 3초 안 왔다’는 이유만으로 제어 상태 이상을 판단하는 구독으로 대체하지 않는다. context가 존재한다는 사실만으로 표시 어댑터가 연결됐다고 처리하지도 않는다. 기존 제어 엔진에 중복 결정 로직을 만들지 않고 표시용 연결 방식만 확인한다.

## 담당 경계

제어 엔진은 실제 활성 VM과 주행 모드를 결정한다. 표시 서비스는 확정된 결과를 받으며 VM 장애·AI 장애·active_app 이름으로 승계나 모드를 추론하지 않는다. 기존 주행 명령/ESP32/복구 API를 호출하지 않는다.

| 담당 | 작업 |
| --- | --- |
| 제어 담당 | 권위 있는 현재 상태를 1초마다 보고. 발행 주체/source_id·재시작 세대·seq 관리 |
| 표시 담당 | 아래 토픽만 구독, 값/권위/중복/유효기간 검증 후 로컬 화면·외부 API/WS에 전달 |
| 브로커 담당 | 합의된 권위 발행자만 새 토픽 write, 표시 계정 read 가능하도록 ACL 반영 |

기존 `xenova/v1/vehicle/control`의 변경 시 발행·retained 규칙은 유지한다. 표시 상태 토픽은 명령 채널이 아니며 ESP32가 이 메시지로 모터/승계를 실행하게 연결하지 않는다.

## 메시지

- 토픽: `xenova/v1/display/drive-state`
- type: `display.drive_state`
- 주기: 1초. 값이 같아도 매 보고 `message_id`를 새로 만들고 `seq`를 증가시킨다.
- QoS: 1, retain: false. 재구독 시 retained로 재생된 보고는 현재 상태로 인정하지 않는다.
- 기본 source_id: `dom0-gateway`. 실제 담당과 확정해야 하며 수신 설정 `XENOVA_DISPLAY_CONTROL_SOURCE`로 맞춘다.
- 공용 봉투 schema_version: `"1.0"`. VM 대신 source_id를 쓰고 vm_id는 넣지 않는다.

```json
{
  "schema_version": "1.0",
  "message_id": "매 보고 새 UUID",
  "source_id": "dom0-gateway",
  "boot_id": "발행 호스트의 부팅 ID",
  "agent_instance_id": "발행 프로세스 시작 시 새 UUID",
  "type": "display.drive_state",
  "seq": 1,
  "sent_at": "2026-10-22T02:00:00.000Z",
  "payload": {
    "active_vm_id": "dd33284f-baf2-417f-93b2-a1af3c3f1ea2",
    "mode": "MANUAL"
  }
}
```

VM UUID는 QEMU VM1 예시이며 실보드 등록 UUID로 교체해야 한다. `active_vm_id`는 등록된 주 구동/백업 UUID 또는 null만 가능하고 Debian 인터랙션 VM은 거부한다. `mode`는 AUTO/MANUAL/SAFE_STOP/AUTO_READY 중 확인된 값이다. payload에는 두 필드를 모두 보낸다.

제어 담당이 현재 상태를 확인할 수 없으면 이전 값을 계속 정상 보고하지 않고 발행을 중단해 수신기가 만료시키도록 한다. 별도 어댑터가 과거 제어 이벤트를 무조건 1초마다 복사하면 현재 권위가 살아 있다는 증거가 되지 않으므로 사용하지 않는다. SAFE_STOP/null 같은 의미 조합은 실제 제어 엔진이 확정하며 표시 서비스가 조합을 만들어내지 않는다. AI/인식·복구 작업 상태는 이 계약에 연결하지 않았으므로 수신 화면의 해당 값은 unknown/null이다.

## 수신 규칙

수신기 단조 시계 기준 3초 미수신이면 drive.source=stale, mode=unknown, active_vm_id=null이다. MQTT 단절은 disconnected로 표시한다. mode 변화는 활성 VM 변경을 의미하지 않는다. 기본 상세 화면은 VM1 pinned이며 명시적으로 follow_active를 고른 경우에만 신선한 active_vm_id를 따라간다. 만료 시 마지막 상세 대상을 유지하되 활성/주행 정상 주장은 제거한다.

같은 발행 세대의 중복/역순 seq는 freshness를 연장하지 않는다. 새 agent_instance_id/boot_id가 수락되면 퇴역한 세대의 지연 메시지로 돌아가지 않는다. 게스트 sent_at의 미래 시각으로 유효기간을 연장하지 않는다. source_id만으로 인증하지 않고 브로커 계정/ACL을 함께 사용한다.

## 팀원 확인 사항

1. Git상 원본은 dom0 Gateway, source_id=dom0-gateway, 담당 근거는 박신영이다. 이를 표시용 발행에도 사용할지와 발행 구현 담당을 확인한다.
2. 위 토픽/type/1초 주기/retain=false와 3초 freshness에 동의하는지.
3. 등록 VM UUID, 허용 모드/null의 의미를 확인한다. 특히 provider phase·output_permitted·storage_uncertain 및 저장된 선호 owner와 실제 적용 상태를 구분한다.
4. 권위 상태를 모를 때 발행 중단, 프로세스 재시작 시 새 instance_id·seq 초기화가 가능한지.
5. 발행 계정과 새 토픽 ACL을 누가 반영할지, 실제 발행 연결 검증 일정을 언제 잡을지.

현재 ACL의 `gateway`에는 새 토픽 쓰기 권한이 없다. 아래는 **검토용**이며 운영 ACL에 적용하지 않았다. 실제 권위 계정 확정 뒤 해당 user 블록에 반영한다.

```text
user gateway
topic write xenova/v1/display/drive-state
```

브로커가 전달한 retain 플래그는 재생 retained 메시지 판별에 사용한다. 기존 구독자에게 live로 전달되는 메시지의 원발행 retain 설정을 항상 증명하는 값은 아니므로 발행자에서도 retain=false를 설정해야 한다.

현재 표시 담당 준비 상태는 [250 결정 기록](display-250-decisions.md), 실제 보드 확인은 [수락 절차](../verification/display-hardware-acceptance.md)를 따른다.
