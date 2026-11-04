# 표시용 주행 상태 — 기존 Gateway 읽기 경로

2026-11-02 · S15P31E110-255 · 기준 platform e82fa66.

최신 platform **01ca838**에서도 Gateway provider/client/IPC·표시 수집기 관련 소스가 기준 대비 바뀌지 않았음을 대조했다. Gateway context·client 전용 패키지·서비스·이미지 포함 경로는 저장소에 이미 구현돼 있다. 오래된 보드의 부재를 최신 Git의 제공 기능 부재로 해석하지 않는다.

사용자가 ‘기존 정보를 직접 읽어 연결’과 ‘자율 모드 선택 · 구동 준비 중’ 표시를 선택했다. **명시적 gateway 모드에서는 별도 MQTT 표시 토픽 대신 기존 Gateway context를 읽는다.** 기존 기본값 mqtt는 호환성을 위해 유지하며 두 경로를 혼합·자동 전환하지 않는다. Gateway·VM·주행 제어 서비스를 자동 기동하지 않는다.

## 경로·자원

```text
기존 Gateway actor·Authority
 → 기존 root 전용 control.sock / GatewayClient.context()
 → 표시 수집기의 단일 1초 주기 조회
 → 상태/표시 API·WebSocket
 → 계기판·인포테인먼트
```

[기존 client](../../platform/services/gateway/xenova_gateway/client.py)의 UDS framing·소유권·0600/0700·SO_PEERCRED UID=0·통신 budget을 재사용한다. context만 호출하며 submit/get/commit/cancel/finish·resume·제어 명령은 호출하지 않는다. Authority 파일을 직접 읽거나 socket 권한을 넓히지 않는다. 외부 웹에는 socket을 노출하지 않는다.

수집기당 조회 하나이며 화면·구독자마다 추가 조회하지 않는다. 한 번에 한 호출만 수행하고 기본 최대 0.5초·설정 상한 1초다. bounded client를 작업 스레드에서 호출해 async HTTP/WS 처리를 막지 않는다. catch-up 조회·프레임 이력을 쌓지 않는다. 새 Xen 조회·MQTT 송신·상태 판단 엔진은 만들지 않는다.

## 수락·신선도

- host_boot_id는 로컬 host boot와 같아야 한다. owner와 targets 두 VM은 등록된 주 구동·백업 UUID와 일치해야 한다.
- generation은 양의 uint32다. 같은 실행 중 감소하거나 같은 generation에서 owner·mode가 바뀌면 거부한다. 동일 값의 정상 context 조회는 새 현재 관측이다.
- mode·phase·불리언 출력 허가/저장 불확실성·binding UUID/revision을 검증한다. 추가 진단 원문을 외부에 통째 복사하지 않는다.
- 신선도는 **조회 시작 단조시각**부터 계산한다. 호출 budget 초과는 거부한다. 3초 이상 새 관측이 없으면 stale, 조회 실패는 disconnected, 최초 미수신은 unknown이다.
- fresh가 아니면 mode=unknown·active_vm_id=null·authority=null이다. 실패 후에도 generation 기준은 유지하고 정상 새 조회로 복귀한다.
- gateway 모드에서는 display.drive_state를 구독하지 않고 들어와도 거부한다. Agent·앱 MQTT는 유지한다. MQTT 단절은 Gateway 상태의 신선도를 연장하거나 무효화하지 않는다.

## 상태와 UI 의미

기존 drive 필드를 유지하고 gateway 모드에서 optional authority를 추가한다.

| 필드 | 의미 |
| --- | --- |
| authority.selected_vm_id / selected_mode | Gateway가 선택한 값. 재시작 후 선호 값일 수 있음 |
| authority.generation / phase | 제어 세대와 DISARMED/PENDING/APPLIED/FAILED. 화면 revision·복구 작업 phase와 별개 |
| authority.output_permitted | Gateway 출력 허가. 실제 이동·장치 적용의 증명이 아님 |
| authority.storage_uncertain | true이면 주행 모드·현재 구동 VM은 미확인 |
| drive.active_vm_id | fresh·저장 확정·APPLIED·출력 허가·현재 owner binding 존재·AUTO/MANUAL일 때만 owner. 그 외 null |
| drive.mode | fresh·저장 확정일 때 선택된 모드. 출력 허가와 구분해 렌더링 |

AUTO/MANUAL 선택은 있지만 active_vm_id가 없으면 ‘자율/수동 모드 선택 · 구동 준비 중’, FAILED면 ‘구동 확인 필요’다. 선택 VM과 현재 구동 VM을 구분한다. SAFE_STOP은 ‘안전 정지 모드’, AUTO_READY는 ‘자율 재개 대기’이며 실제 정지·출발을 만들어내지 않는다. 조회 단절·만료·저장 불확실성은 ‘주행 모드 미확인’이다. pinned/follow_active는 확인된 active_vm_id만 따른다.

새 프론트는 authority의 형식·신선도·출력 허가 일치를 검증한다. **수집기와 새 프론트를 함께 배치해야 한다.** 구형 UI는 선택된 AUTO와 준비 상태를 구분하지 못할 수 있으므로 수집기만 켜지 않는다. 기존 MQTT 응답은 authority 없이 계속 지원한다.

## 배포 전제와 복원

1. 기존 Gateway의 **warm 프로필**에서 provider UDS가 실행 중이고 UUID pair가 맞는지 확인한다. 현재 __main__은 warm에서만 GatewayServer를 연다. 예제 환경은 warm과 기본 control.sock을 지정한다. legacy/verified 프로필의 기동만으로 UDS가 생긴다고 가정하지 않으며 표시 작업에서 프로필 변경·Gateway 설치·enable/start를 자동 수행하지 않는다.
2. 정식 xenova-gateway-client RPM 또는 같은 소스의 client 전용 패키지(__init__.py/client.py/ipc_contract.py)가 수집기 Python 경로에 필요하다. 서버 전체 import로 대체하지 않는다. root 실행·namespace에서 기존 socket이 보여야 한다.
3. 새 수집기·web build를 함께 준비하고 [환경 예시](../../platform/services/display/router/deploy/board-kms/gateway-read.env.example)의 설정만 기존 private 환경에 추가한다. 토큰·MQTT·binding을 덮지 않는다.
4. 복원은 이전 수집기·web release와 환경 설정을 함께 되돌린다. MQTT로 되돌린다고 존재하지 않는 발행자가 생기지는 않는다.

소켓 부재·조회 오류에서는 미확인으로 남는다. client 패키지 자체가 없으면 opt-in 시작이 실패하며 MQTT나 Authority 파일로 조용히 우회하지 않는다. 추가 표시 토픽·ACL은 새 경로에 필요 없다. 보드 수신과 전체 255 완료는 [검증 기록](../verification/display-255-gateway-read-20261102.md)과 구분한다.
