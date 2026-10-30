# Recovery Worker 가 플랫폼에 필요한 것 — platform 파트 요청

> 2026-10-27 인수 보완: 아래는 원 S15P31E110-279 요청과 당시 상태다. 현재 하위는
> 펌웨어별 heartbeat capability·Worker unit 패키징을 제공하지만 Xen 관측 없는 별도 intents
> action은 추가하지 않았다. 이 문서를 전체 처리 완료로 닫지 않는다. Worker에 요청하는
> D01 후속 분류·건강 연결은 [Worker 전용 요청서](2026-10-27-recovery-health-ctrl.md)에서
> 이어 추적하며, [하위 Manager 현행 계약](../platform/cli-manager-spec.md)을 사용한다.

> 작성: ctrl 파트, 2026-10-22 · 관련: S15P31E110-124(heartbeat 감시기)·**S15P31E110-279**(이 요청의 플랫폼 티켓), [플랫폼 복구 기반](../platform/management-boundaries.md), [시나리오](../scenario-rc-car.md) D8, [MQTT 계약](../contracts/mqtt-contract-v1.md) 11장

## 0. 요약

| 대상 | 해야 할 것 | 막혀 있나 |
| --- | --- | --- |
| platform 파트 (템플릿 카탈로그 `xenova_zephyr.py`·`xenova_vm.py`, Manager 액션) | ① Zephyr 템플릿 capability `app_heartbeat` 를 펌웨어별로 구분해 노출, ② Xen 을 관측하지 않는 의도 조회, ③ Recovery Worker 유닛 패키징 | 🟡 ①은 D8(앱이 시작되지 않은 Zephyr VM 감지)의 기능 완료 조건. ②·③은 실보드 통합 시 필요. 코드 머지(backend)는 막히지 않음 |

## 1. 확정된 사실

| 항목 | 값 | 상태 |
| --- | --- | --- |
| 자동 복구 주체 | dom0 Recovery Worker (`platform/services/dom0-manager/dom0_manager/recovery/`, 별도 systemd 유닛) | ✅ 구현, 리뷰 중 |
| Zephyr VM 생존 판정 | Xen 도메인 상태 + `app.heartbeat`(시나리오 D8). Linux VM 은 `agent.heartbeat` | ✅ 구현 |
| Worker 가 읽는 플랫폼 정보 | `Manager.list`(의도·lifecycle·template)와 `templates`(capabilities)를 60초마다 캐시, 판정 직전 `get` 으로 재확인. Xen 은 매 5초 `xl list` | ✅ 구현 |
| 현재 Zephyr capability | `xenova_zephyr.py` 의 `CAPABILITIES` 는 모든 Zephyr 펌웨어에 공통인 전역 상수. `app_heartbeat` 키 없음 | ✅ 확인 |

## 2. 부탁하는 것

1. **템플릿 capability `app_heartbeat`** — `templates` 응답의 `capabilities` 에 앱 펌웨어(자율·수동주행) 템플릿은 `true`, starter(`zephyr-xenvm-starter`) 는 `false`. Worker 는 `true` 면 신호가 오기 전에도 감시 대상(STARTING → 30초 유예 뒤 `APP_HEARTBEAT_LOST`), `false` 면 Xen 상태만 봅니다. 키가 없으면 신호를 한 번 받은 뒤에야 대상으로 다루므로, **키가 없는 동안은 앱이 첫 기동부터 실패한 VM 을 감지하지 못합니다.** 지금처럼 전역 상수면 템플릿 종류별로 값을 갈라 주세요. 값은 JSON boolean(`true`/`false`)만 받습니다. 문자열 `"false"` 같은 값이면 그 템플릿의 VM 은 대상 여부를 알 수 없어 `OBSERVATION_UNAVAILABLE` 로 보고합니다.
2. **Xen 을 관측하지 않는 의도 조회** — `list(observe=False)` 같은 옵션 또는 `intents` 액션(`vm_id`, `desired_state`, `intent_revision`, `intent_applicable`, `lifecycle`, `template`, `guest_kind` 만). 지금 `list`·`get` 은 플랫폼 잠금을 쥐고 VM 마다 Xen 을 관측(최대 15초)해서 Worker 가 60초 캐시로 호출을 줄이고 있습니다. 가벼운 조회가 생기면 캐시 없이 매 주기 읽습니다.
3. **패키징** — `platform/services/dom0-manager/deploy/xenova-recovery-worker.service` 설치·enable, `PYTHONPATH` 에 `/opt/xenova/dom0-manager`·`/opt/xenova/mqtt-client`·paho, 상태 디렉터리 `/run/xenova/recovery`. **enable 은 MQTT 계약 11.1·ACL(계정 `dom0-server`)·①이 반영된 뒤에** 합니다.
4. (선택) stop 대기 만료를 `DEADLINE_EXCEEDED` 코드로 내주면 dom0-manager 가 메시지 문자열(`deadline`/`timed out`) 판별 없이 504 로 매핑합니다(VM API 계약 8.1).

## 3. 이미 해 둔 것

| 내용 | 위치 | 병합됨? |
| --- | --- | --- |
| Worker 가 `capabilities.app_heartbeat` 를 `true`/`false`/없음 삼상으로 읽음 | `dom0_manager/recovery/observe.py` | ❌ 124 MR (→ backend) |
| 키가 없는 동안의 대체(신호 한 번 뒤 armed, `/run/xenova/recovery/armed.json` 영속) | `dom0_manager/recovery/arming.py` | ❌ 124 MR |
| MQTT 계약 11.1 `vm.health`, 9장 `dom0-server` ACL, VM API 계약 2.5 `APP_HEARTBEAT_LOST` | `docs/contracts/` | ❌ 이 문서와 같은 MR (→ develop) |

## 4. 처리되면

S15P31E110-279 를 닫고 이 문서 맨 위에 처리 표시를 적어 주세요. Worker 는 바꿀 것이 없고, ②가 생기면 ctrl 파트가 60초 캐시를 걷어냅니다.
