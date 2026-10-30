# Display pipeline v1 — 제안 계약

2026-10-22 · S15P31E110-250 · 1~4단계 구현 및 5단계 가상 통합 검증. 실보드 HDMI 검증은 남아 있다.

## 2026-10-28 차량 HDMI 화면 변경 (최신)

사용자 확정 구성은 HDMI 1=계기판, HDMI 2=인포테인먼트·게스트 데이터다. 아래 전체/상세 설명은 변경 전 이력이다. 외부 웹 대시보드·VM 상세·관리/제어 라우팅은 이 변경 대상이 아니다.

- `/display/monitor-1`: 속도·배터리·확정 주행 모드·주요 경고·대기 연출.
- `/display/monitor-2`: 네비·음악·확정 주행 모드·현재 켜진 VM·선택된 게스트 콘텐츠·오류/작업 알림. 별도 VM 상태 요약 패널은 두지 않는다.
- presentation.outputs에 `screen=instrument|infotainment`를 추가해 각각 monitor-1/2에 고정한다. 기존 `view=overview|detail`은 선택 API 호환용 필드이며 새 UI 모양을 뜻하지 않는다. 새 렌더러는 screen 매핑이 없거나 바뀐 이전 응답을 거절한다. 수집기와 웹 빌드를 함께 배치해야 한다.
- 모니터 2의 기존 UUID 선택(pinned/follow_active)은 게스트 콘텐츠 선택에만 적용한다. 주행 모드·활성 VM은 신선한 제어 보고로만 표시하며 VM 장애만으로 모드나 제어권을 추측하지 않는다.
- 속도·배터리·네비·음악의 수신 계약/송신기와 게스트 영상 어댑터는 아직 연결되지 않았다. 이 값은 0·가상 정상값 대신 수신 대기로 표시한다. opaque graphics handle을 재생 URL로 사용하지 않는다.
- 오류·작업 알림은 수집기의 확인된 VM 표시 결정을 따른다. 복구 완료 사건 수신 경로가 없는 상태에서 완료 알림을 만들어 내지 않는다.

## 계약 확정 상태

현재 구현·검증 사실과 팀/사용자가 확정할 항목을 [250 결정 기록](display-250-decisions.md)에 구분한다. 가상 시험은 [5단계 결과](../verification/display-step5.md)를 따른다. 아래 단계별 설명은 해당 단계의 이력이며 최신 출력 실행 구현은 4단계 설명을 우선한다.

사용자 선택으로 기존 `vehicle/control`의 변경 시 발행/retain 규칙과 표시 상태 보고를 분리했다. 표시기는 `xenova/v1/display/drive-state`, `type=display.drive_state`의 1초 주기 비-retained 보고만 받으며 기존 차량 제어 메시지를 구독하지 않는다. [연결 규격](display-drive-state-v1.md)에 수신 구현과 팀원 합의 대기를 구분한다. 기존 제어 발행자·주행 명령·공용 MQTT 계약·실제 브로커 ACL은 변경하지 않았다.

## 4단계 구현 현황

X11 connector/EDID/좌표 매핑과 두 Chromium 창의 실행·세션 갱신을 추가했다. 출력 상태는 수집기 API와 별도인 runtime JSON 파일로 전달한다. 실제 x11 backend에서 native 창 위치·매핑과 DOM ACK가 확인되면 runtime 파일의 applied_revision을 채운다. 가상 시험은 physical_verified=false, applied_revision=null을 유지한다. 이것은 실모니터 육안 검증 결과가 아니다.

별도 송신기는 동일 X11 root 픽셀을 FFmpeg로 JPEG 인코딩해 `/streams/{output_id}.mjpeg`로 전송한다. 시연용 인증은 feed 키와 구별된 접속 키/쿠키이며, 원격 뷰어는 두 스트림을 실제로 디코딩한다. 상태/프레임 API·시연 PC 연결은 [4단계 실행 규격](../../platform/services/display/router/README.md#4단계-x11-실행과-원격-시연), 결과는 [검증 기록](../verification/display-step4.md)을 참고한다.

현재 capture_epoch/frame_sequence는 실제 인코더의 세대/프레임 식별자다. 특정 encoded frame과 DOM revision의 정확한 결합은 미검증이므로 render_revision=null, render_correlation=unverified로 전달한다. 스트림 전송과 물리 HDMI 준비 상태를 동일시하지 않는다.

## 3단계 구현 현황

별도 dom0 표시 수집기에 presentation/selection 및 화면 세션·DOM ACK API를 추가했다. 화면은 `/display/monitor-1`, `/display/monitor-2`이며 외부 대시보드 라우팅과 무관하다. 로컬 화면은 수집기에 직접 연결한다. [실제 API·인증 규격](../../platform/services/display/router/README.md#3단계-로컬-화면-렌더링)과 [검증 기록](../verification/display-step3.md)을 참고한다.

현재 선택 API는 고정된 전체/상세 구성에서 모니터 2의 pinned/follow_active를 변경한다. 아래 `display.desired` JSON 메시지 전체를 입력받는 범용 배치 API는 아직 아니다. `renderer_ack.stage=dom_rendered`는 현재 내용의 DOM 렌더 확인이며 3초간 갱신이 없으면 만료된다. 3단계 당시 없던 출력 실행 관측은 4단계에서 별도 runtime JSON으로 추가했다.

## 2단계 구현 현황

`platform/services/display/router`의 별도 FastAPI 수집기가 기존 dom0-manager의 읽기 전용 Xen 드라이버와 MQTT 계약을 재사용한다. 기존 dom0-manager v1 API와 MO-04는 변경하지 않는다.

| 구간 | 실제 구현 엔드포인트 | 인증 |
| --- | --- | --- |
| dom0 수집기 조회 | `GET /api/v1/display/state` (기본 8082) | 서비스 Bearer 토큰 |
| dom0 수집기 구독 | `WS /ws/v1/display/state` | handshake 서비스 Bearer 토큰 |
| Spring 외부 조회 | `GET /api/v1/display/state` | 기존 사용자 JWT |
| Spring 외부 구독 | `WS /ws/v1/display/state` | 첫 메시지 auth/JWT |

외부 WS는 `authenticated` 후 `display.state`의 `state`에 전체 스냅샷을 담는다. 연결 실패는 `display.unavailable`, REST는 503으로 표현한다. 화면 라우팅/변경 API나 프론트 페이지는 추가하지 않았다. 실행 설정과 실제 필드는 [수집기 README](../../platform/services/display/router/README.md), 시험 결과는 [2단계 검증](../verification/display-step2.md)을 참고한다.

실제 v1에서 `run_id`는 수집기 epoch + 관측 domid 식별자이고 완전한 Xen 부팅 세대 증명은 아니다. 따라서 OS의 `current_run_verified=false`를 명시한다. `drive.ai_status=unknown`, `operation=null`, 그래픽 소스 handle=null은 아직 연결하지 않은 항목이다. 기존 control의 SAFE_STOP/AUTO_READY 값은 손실 없이 전달하지만 AUTO/MANUAL과 VM 역할을 서로 유추하지 않는다.

## 기존 연결과 신규 연결

기존 MO-04 외부 콘솔은 Spring `/ws/v1/vms/{vm_id}/console` → FastAPI `/api/v1/vms/{vm_id}/console`을 재사용한다. 단말 바이트와 표시 상태 JSON/원격 영상은 서로 다른 채널이다. 기존 콘솔의 인증·바이너리 전송·명시적 disconnect 계약은 변경하지 않는다.

로컬 상태 전달은 loopback HTTP/WS(기본 8082, 서비스 토큰)로 구현했다. 후속 정책 → 렌더러/출력 실행기 인터페이스는 별도로 구현한다. 외부 Spring 중계가 끊겨도 dom0 수집은 계속 동작한다. 아래 출력 전환 메시지는 상태 조회 API와 구별하며 아직 구현하지 않았다.

## 정규화 상태 필드

| 필드 | 의미와 검증 규칙 |
| --- | --- |
| `schema_version` | `1`. 지원하지 않는 버전은 적용하지 않고 오류 기록 |
| `vm_id`, `role` | 등록된 UUID, `primary_drive` / `backup_drive` / `interaction`. domain ID는 영구 식별자로 사용하지 않음 |
| `run_id` | 현행 구현은 수집기 epoch + 관측 domid. 미실행은 `null`. 빠른 재부팅/동일 domid 재사용까지 구분하는 완전한 부팅 세대 증명은 아님 |
| `source_id`, `source_epoch`, `sequence` | 발행자·재시작 세대·세대 내 증가 번호. 소스별 중복/역순 폐기. 다른 소스 sequence를 비교하지 않음 |
| `xen_state` | 기존 값 `running`, `stopping`, `paused`, `crashed`, `stopped`, 미확인 시 `null` |
| `operation` | 시작/복구 등의 별도 작업 ID·상태, 없으면 `null`. Xen 상태 enum과 혼합하지 않음 |
| `os` | 기대 OS, 관측 OS, 근거 종류/시각, 일치 여부. 설정상의 기대 OS를 검증 완료로 표시하지 않음 |
| `app_state` | 현재 구현은 `unknown`, `ready`, `failed`. `starting`은 확장안이며 아직 반환하지 않음. 관측 근거 필요 |
| `sources` | 소스별 `observed_at`, dom0 `received_at`, 경과 시간, `fresh/stale/disconnected/unknown` |
| `screen_source` | `none/status/graphics`, 사용 가능한 렌더/캡처 핸들. 임의 URL/셸 명령을 실행하지 않음 |
| `drive` | 별도 권위 소스의 `active_vm_id`, `mode(AUTO/MANUAL/SAFE_STOP/AUTO_READY/unknown)`, `ai_status`. `source`의 freshness로 확인된 값만 노출. AI 보고 연결 전 `ai_status=unknown` |

Xen 상태, Agent 상태, 앱 상태, 주행 상태의 freshness는 독립적이다. Agent 미구현인 Zephyr를 heartbeat 부재만으로 장애로 단정하지 않는다. 기존 OS evidence 보고 API의 출처·관측/수신 시각을 재사용할 수 있으나 구형 3종 Linux 구성을 강요하지 않는다.

초기 수신 유효기간 제안은 지원되는 1초 heartbeat의 경우 3초다. 실제 주기가 다르거나 heartbeat가 없으면 소스별 설정/연결 상태를 사용한다. dom0의 단조 시계로 수신 후 경과 시간을 판단하고 UTC 시각은 표시에 사용한다. 게스트 시계 오차로 freshness를 정상화하지 않는다. 새 실행 세대에 이전 부팅의 앱 상태·프레임을 붙이지 않으며 확인 불가능한 세대는 `unknown`으로 둔다.

## 두 출력의 선택과 적용 확인

현행 X11 설치 매핑은 논리 `output_id` (`monitor-1`, `monitor-2`)와 XRandR connector 이름·EDID SHA256·해상도·좌표를 연결한다. DRM sysfs 이름은 보조 근거이며 XRandR 이름과 같다고 가정하지 않는다. 실제 번호는 보드 확인 후 기록한다. 장치 재연결 시 검증된 매핑을 재사용하고 모호하면 수동 재매핑한다.

아래는 범용 배치의 확장안이며 현행 API 요청 본문이 아니다. 현행 `POST /api/v1/display/selection`은 `policy_epoch`, `revision`, `selection`, 선택적인 `vm_id`만 받는다. 실제 호출 규격은 README를 따른다.

확장안 정책 요청 예:

```json
{
  "schema_version": 1,
  "kind": "display.desired",
  "policy_epoch": "display-service-boot-1",
  "revision": 12,
  "outputs": [
    {"output_id": "monitor-1", "view": "overview", "selection": "pinned", "vm_id": null},
    {"output_id": "monitor-2", "view": "detail", "selection": "pinned", "vm_id": "dd33284f-baf2-417f-93b2-a1af3c3f1ea2"}
  ]
}
```

UUID는 현재 QEMU의 VM1 예시다. 배포 환경에서는 등록 VM 목록에서 선택한다. `overview`는 `vm_id=null`, `detail`은 등록 UUID가 필수다. `follow_active`에서 권위 상태가 미확인이면 마지막 대상을 지연 배지와 함께 유지하며, 초기 대상도 없으면 대기 화면을 사용한다.

실행기는 동일 epoch 내 오래된 revision을 거부하고 같은 요청은 중복 실행하지 않는다. 새 epoch는 인증된 로컬 서비스 재접속/전체 스냅샷으로만 수락한다. 출력별 `applied_revision`, `render_revision`, `status(ready/disconnected/error)`, `error_code`를 회신한다. 렌더러가 해당 내용을 실제로 표시한 뒤 applied를 올린다. 요청 수신만으로 화면 전환 완료라고 기록하지 않는다. 한 출력 실패가 다른 출력의 성공을 취소하지 않는다.

재연결 시 상태/배치의 최신 전체 스냅샷을 전송한다. 주행 명령이나 콘솔 키 입력을 재전송하지 않는다. 적용 오류 시 가능한 마지막 정상 화면을 유지하되 오류 배지·실제 적용 revision을 노출한다.

## 원격 시연 스트림 식별

스트림별 메타데이터는 `stream_id`, `output_id`, `capture_source_id`, `capture_epoch`, `render_revision`, 해상도, codec, FPS, `frame_sequence`, 캡처 시각, 연결 상태를 갖는다. 메타데이터의 revision과 실제 프레임의 대응을 캡처 어댑터가 확인할 수 없으면 미확인으로 표시한다. 영상 자체를 상태 WebSocket JSON의 base64 필드로 싣는 방식은 기본안으로 채택하지 않는다. 실제 미디어 전송 규약은 실보드 검증 후 선정한다.

수신기는 두 스트림을 출력 ID로 구분한다. 초기 프레임 전에는 대기, 중단 시 마지막 프레임에 끊김 표시를 한다. 내부 가변 길이 큐를 무제한 쌓지 않고 느린 수신자에는 과거 영상 프레임을 버려 지연을 제한한다. 송신기/수신기 장애 시 로컬 출력 프로세스는 독립적으로 계속 실행한다.

## 접근 제어

로컬 IPC는 서비스 계정/파일 권한으로 제한하고, 외부 웹 구간은 기존 인증을 유지한다. 원격 수신 연결도 인증 및 신뢰된 네트워크/암호화 경계를 정한 후 노출한다. 토큰을 URL, 화면, 일반 로그에 기록하지 않는다. 스트림 소스와 화면 대상은 서버에 등록된 핸들/UUID만 허용하며 요청에서 임의 셸 명령·파일 경로·웹 URL을 받지 않는다. 최종 배포 인증 방법은 전송 프로토콜 결정에 포함한다.
