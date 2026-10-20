# 게이트웨이가 쓸 앱 신호 정리 — 제다빈(ctrl) 요청

> 작성: 공병우, 2026-10-20 · 관련: S15P31E110-155·237, MQTT 계약 v1 §5·§6.1·§7 (브랜치 `feat/app/S15P31E110-155-manual-drive-app`)

## 0. 요약

| 대상 | 해야 할 것 | 막혀 있나 |
| --- | --- | --- |
| 제다빈 (ctrl, dom0 게이트웨이·복구) | 계약 §6.1 `app.heartbeat` 추가 리뷰, 게이트웨이 구현 시 아래 신호 구분대로 판정 | 🟢 여유 있음 — 게이트웨이 구현 전. 계약 리뷰만 이번 MR에서 |

> 게이트웨이 담당이 아직 확정되지 않았습니다. 제어 계층이라 제다빈으로 두었고, 다르면 알려 주세요.

## 1. 확정된 사실

| 신호 | 의미 | 주기 | 판정 주체 | 상태 |
| --- | --- | --- | --- | --- |
| `drive.command` (`apps/{autonomous,manual}/events`) | 주행 명령이 실제로 나오고 있다. **대기 앱도 계속 발행** (수동 앱은 키 없으면 `v_mps 0`) | 50 Hz | **게이트웨이** — 100 ms 끊기면 전환 (§5.1·5.2) | ✅ 수동 앱 구현·검증 |
| `app.heartbeat` (`apps/{app_id}/events`) | 앱 **프로세스** 생존과 상태 `active/standby/idle/error`, `uptime_s` | 1초 | 백엔드·콘솔·메시지 보드 표시용, 3초 끊김 | ✅ 수동 앱 구현 (이번에 추가, §6.1) |
| `agent.heartbeat` (`vms/{vm_id}/heartbeat`) | VM 생존 | 5초 | 복구 서비스 — VM 재시작 (§3) | ✅ 기존 |

## 2. 게이트웨이에 부탁하는 것

1. **제어권 전환에 `app.heartbeat`를 쓰지 않기.** 1초 주기라 3번 놓치면 3초, 1 m/s면 약 3 m를 제어 없이 갑니다. 전환은 지금 계약대로 `drive.command` 100 ms.
2. **수동 앱 `state`는 게이트웨이가 낸 `vehicle/control`의 `active_app`을 그대로 반영합니다.** 앱이 스스로 활성을 선언하지 않습니다 (예전 명세의 "자기 보고값을 승격에 쓰지 않는다" 원칙과 같음).
3. **수동 앱은 `vehicle/control`을 못 받으면 `generation 0`으로 발행합니다.** 게이트웨이는 시작 시 `vehicle/control`을 retained로 먼저 내 주세요 (§5.3). ESP32는 세대가 다르면 버리므로 안전합니다.
4. 예전 `docs/design/vehicle-control-spec.md`의 승격 조건(최근 500 ms 유효 명령 25개 이상, 간격 100 ms 이하)과 `controller_session_id`는 지금 계약에 없습니다. 게이트웨이 구현에 필요하면 계약 §5에 옮기는 안을 내 주세요. 재시작 구분은 공통 봉투의 `agent_instance_id`(프로세스마다 새로 생성)로 대신할 수 있습니다.
5. **자율·수동주행 VM이 Zephyr로 정해져 에이전트가 없습니다** (시나리오 §2, D8). 복구 서비스의 VM 생존 판정과 §5.2 `AUTO_READY`의 "에이전트 `ONLINE`" 조건을 무엇으로 바꿀지 정해 주세요. 앱 쪽은 `app.heartbeat`(1초)를 Zephyr 판에서도 보냅니다 — 도메인 상태 + `drive.command` 신선도에 보조로 쓸 수 있습니다.

## 3. 내가 이미 해 둔 것

| 내용 | 위치 | 병합됨? |
| --- | --- | --- |
| 계약 §6.1 `app.heartbeat`, §7 `drive.input` 키·반복·끊김 | `docs/contracts/mqtt-contract-v1.md` | ❌ 위 브랜치 MR 미병합 |
| 노션 MQTT-06·11, 변경 이력 | 노션 인터페이스 계약 | ✅ 2026-10-20 |
| 게이트웨이 목 (vehicle.control retained 발행) | `platform/apps/manual/tools/mock_gateway.py`, 브라우저 테스트 화면 | ❌ 미병합 |
| 게이트웨이 구현 | 하지 않음 (ctrl 영역) | — |

## 4. 처리되면

게이트웨이가 생기면 수동 앱과 붙여 "자율 명령 끊김 → 100 ms 안에 MANUAL 전환 → 수동 앱 `state: active`"를 같이 확인하겠습니다 (S15P31E110-94 E2E 선행).
