# 253: 공통 주행 앱의 VM별 보고 수집

2026-10-27. 수신·정규화 범위이며 송신 펌웨어, 브로커, 제어 권한 계약을 변경하지 않는다. 이전 단일 앱 매핑은 `display-source-bindings-v1.md`를 따른다.

## 매핑과 토픽

VM1=주 구동, VM2=백업이며 둘 다 같은 공통 앱 번들에서 `autonomous`와 `manual`을 보고한다. 앱 ID가 VM 역할이나 현재 AUTO/MANUAL 모드를 결정하지 않는다.

```json
{
  "vm_id": "11111111-1111-4111-8111-111111111111",
  "role": "primary_drive",
  "expected_os": "zephyr",
  "app_ids": ["autonomous", "manual"],
  "app_topic_scope": "vm_scoped",
  "agent_supported": false
}
```

위 UUID는 합성 예시다. VM2도 같은 두 앱 ID를 쓰되 자기 UUID와 `backup_drive` 역할을 지정한다. 새 모드는 정확히 두 앱을 요구하고 단일 `app_id`와 혼합할 수 없다. 기본 `legacy` 모드는 기존 `app_id` 하나와 전역 토픽을 유지한다. 암묵적인 혼합·이전 토픽 우회는 없다.

구독 토픽: `xenova/v1/vms/{vm_id}/apps/{autonomous|manual}/events`. 설정된 VM UUID·앱 ID·토픽·봉투가 모두 일치해야 한다. `drive.command`와 기타 타입은 수집하지 않는다. VM3 Agent 수신은 기존 v1 heartbeat/inventory 경로를 유지하며 v2 telemetry는 이 변경 범위에 없다.

## 수신과 만료 규칙

| 보고 | 허용 내용 | 신선도 |
| --- | --- | --- |
| `app.heartbeat` | `state`: active/standby/idle/error, 유한한 0 이상 `uptime_s` | 수신 후 3초 미만 |
| `app.readiness` | 아래 네 필드만 허용, 전체 MQTT payload 1024바이트 이하 | 수신 후 300ms 미만 |

새 모드는 QoS 0·retain=false만 허용한다. MQTT 재연결 때는 토픽 SUBACK 성공과 새 보고를 요구한다. Xen 조회가 신선하고 해당 VM이 running이어야 수신하며, 실행이 바뀌면 이전 관측을 폐기한다. boot_id는 heartbeat로 시작한다. readiness가 생존 보고나 새 실행을 대신할 수 없다.

readiness 필드:

- `applied_generation`: uint32 정수. bool은 거부한다.
- `progress_seq`: uint64 범위의 ASCII 10진수 문자열. 불필요한 선행 0을 거부하고 문자열로 보존한다.
- `readiness`: PREPARING / READY / NOT_READY.
- `reason`: READY이면 null. 나머지는 CONTROL_UNAPPLIED / INPUT_UNAVAILABLE / INPUT_STALE / INPUT_INVALID / STATE_PREPARING / CALCULATION_FAILED / OUTPUT_INVALID 중 하나.

READY는 generation과 progress가 모두 양수여야 한다. 같은 앱·송신 세션에서 generation/progress 역행을 거부하고, 같은 progress의 READY 반복으로 만료 시간을 연장하지 않는다. NOT_READY는 같은 progress로 취소 보고가 가능하다. heartbeat와 readiness 봉투 `seq`는 같은 앱 토픽의 순번을 공유한다. 수신 가능한 형식의 준비 보고가 작업 진전 부족으로 거부되더라도 관측한 순번 이하의 과거 heartbeat로 생존 시간을 연장할 수 없다. 다른 타입의 무시된 메시지는 앱 신선도를 갱신하지 않는다.

각 앱의 송신 세션을 별도로 추적한다. 한 앱의 재시작은 그 앱의 readiness만 지우며 다른 앱은 유지한다. 퇴역한 송신 세션의 뒤늦은 메시지를 거부한다. 이 세션 추적은 송신자가 보고한 ID에 기반한 방어이며, root 실행 바인딩 검증을 대체하지 않는다.

## 스냅샷과 표시 의미

기존 인증된 REST/WS 경로를 그대로 쓰고 `vms[].apps.autonomous`, `vms[].apps.manual`을 추가한다. 각 앱에는 `app_state`, `reported_app_state`, heartbeat `source`, `readiness`가 있다.

`readiness.reported`는 마지막 수락한 보고이고, `current`는 Xen 실행·heartbeat·readiness가 신선하고 heartbeat가 error가 아닐 때만 존재한다. 이 값은 보고 관측이며 준비 상태를 Gateway 대신 확정하지 않는다. `execution_verified: false`, `authority_verified: false`를 명시한다. 적용 generation이 현재 권위 generation과 일치하는지도 아직 검증하지 않는다.

기존 `app_state: ready`는 생존 보고가 있고 오류가 없다는 뜻이다. VM 요약은 두 앱 모두 이 조건을 충족해야 ready, 하나라도 신선한 error면 failed, 그 외 unknown이다. 새 VM 매핑의 단일 `app_id`/`reported_app_state`는 null이며 앱별 값은 `apps`에서 확인한다. 기존 렌더러는 VM 요약을 계속 쓸 수 있고, 두 앱 상세 표시는 후속 UI 작업이다.

수집은 `drive.active_vm_id`, `drive.mode`, 선택 VM을 생성하거나 변경하지 않는다. 주행 모드·활성 VM의 권위는 별도 `display.drive_state` 계약에 있다.

## 실제 적용 전 필요한 것

1. 확정 소스에서 공통 펌웨어를 다시 빌드해 해시를 기록한다. 과거 후보 해시를 최신 빌드 해시로 사용하지 않는다.
2. VM 등록 UUID, 인증된 브로커 계정/ACL, Bootstrap, 현재 Xen 실행과 root 바인딩의 일치를 준비한다. 표시 수집기의 구독 계정도 네 앱 토픽을 읽을 권한이 필요하다.
3. VM3의 실제 앱 ID와 ACL은 담당자와 별도 확인한다. 예시 `interaction`으로 기존 계정을 자동 마이그레이션하지 않는다.
4. 승인된 실보드 적용 후 실제 보고·중단·복귀를 확인한다. 송신기 자동 복구와 주행 권한 판정은 해당 담당 범위다.

이 변경은 위 배포 절차나 root 실행 바인딩 어댑터, 펌웨어 적용을 수행하지 않는다.
