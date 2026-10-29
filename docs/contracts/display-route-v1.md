# HDMI 표시 경로와 외부 화면 배치 계약

2026-10-26 · S15P31E110-252. 현재 코드에 있는 인터페이스와 아직 연결되지 않은 부분을 구분한다.

## 실제 HDMI의 기준

dom0 `xenova_display`의 수집기·254번 정책·키오스크가 로컬 화면을 결정한다. 외부 Spring이나 대시보드 없이 동작한다.

| 논리 출력 | 기본 화면 | 경로 |
| --- | --- | --- |
| monitor-1 | 등록 VM 3개 전체 상태 | `/display/monitor-1` |
| monitor-2 | 선택 UUID의 상세 상태 | `/display/monitor-2` |

표시기는 same-origin `GET /api/v1/display/presentation`을 읽는다. 서비스 토큰으로 `POST /api/v1/display/session`을 호출한 신뢰된 실행기가 화면용 HttpOnly 쿠키를 발급받는다. 브라우저 번들·URL에는 서비스 토큰을 넣지 않는다. 키오스크의 base는 `/`로 빌드한다.

`POST /api/v1/display/selection`은 서비스 Bearer 인증이 필요하다. 요청은 `policy_epoch`, `revision`, `selection=pinned|follow_active`, pinned일 때 등록된 `vm_id`다. 이전 정책은 409, 잘못된 대상은 400이다. 기본은 VM1 pinned. follow_active는 신선한 권위 보고의 주 구동/백업 UUID만 따라가며 모드나 앱 이름으로 승계를 추정하지 않는다.

`outputs[].reason`은 선택 이유, `revision`은 선택 설정 버전, `render_revision`은 전체 표시 내용 해시다. 출력별 `POST /api/v1/display/outputs/{output_id}/ack`의 `dom_rendered`는 DOM 반영 확인이며 물리 HDMI 검증이 아니다. X11 매핑·창·출력 상태와 물리 장비 검증은 별도다.

HDMI 빌드(`VITE_TELEMETRY` 비움 또는 `display`)에서 `/split`은 monitor-1, `/detail/:app`은 monitor-2로 이동한다. URL의 app 이름이 로컬 선택 정책을 변경하지 않는다. `/sim`은 모의 데이터로 바꾸지 않고 전체 상태로 이동한다. 직접 키오스크 경로는 항상 `/display/*`를 사용한다.

## 외부 앱 미리보기

`VITE_TELEMETRY=console|scenario|live|legacy-live`를 명시하면 기존 `/split`, `/detail/:app`, `/sim` 앱 미리보기를 유지한다. `console`은 동일 origin BroadcastChannel, `scenario`는 합성, `live`/`legacy-live`는 Spring 주행 상태를 사용한다. 이는 로컬 HDMI 적용 증거가 아니며 화면에 안내문을 표시한다. 운영 Docker의 외부 screens 빌드는 앱 미리보기를 명시적으로 선택한다. `/display/*`는 이 설정에서도 로컬 정책용으로 남는다.

## 기존 Spring 화면 배치 인터페이스의 경계

기존 129번은 `GET /api/v1/displays`, `PUT /api/v1/displays/{display_id}/route`(사용자 JWT), `WS /ws/v1/displays`(사용자), `WS /ws/v1/display-service`(서비스 토큰)를 제공한다. `display-0`/`display-1`, `view=split|os-face`, `vm_id`, `expected_revision`을 사용하며 서비스와 `service.hello`, `route.set`, `route.applied`, `route.failed`, `display.status`를 교환한다.

이 경로의 `screen_path=/split|/detail/{app}`와 legacy `role`은 기존 앱 미리보기 계약이다. **현재 dom0 키오스크가 이 WebSocket에 연결해 로컬 정책으로 변환하는 어댑터는 없다.** 프론트 `DisplayFollower`는 console mock에서만 그 서비스를 흉내 낸다. 실제 적용 ACK로 사용하지 않는다. 이 MR은 129번의 허용 view나 legacy role 동작을 임의 변경하지 않는다.

외부에서 실제 HDMI 선택을 제어하려면 display-0→monitor-1, display-1→monitor-2 매핑, 로컬 전체/상세 고정 범위와 충돌하는 요청의 거절 규칙, epoch/revision 변환, 물리 출력 확인 수준을 합의해 별도 어댑터를 연결해야 한다. 외부 제어가 끊기더라도 마지막 로컬 선택과 수집·표시는 유지되어야 한다. 255번 통합 검증에서 실제 연결 증거를 남긴다.

## VM 역할

VM1=Zephyr 주 구동(AUTO/MANUAL 모두 가능), VM2=동일 Zephyr의 백업, VM3=Debian 인터랙션. AI/인식은 Jetson이다. `autonomous`·`manual`·`interaction`은 기존 송신 앱 ID이며 역할·현재 주행 모드의 증거가 아니다. dom0는 표시 binding의 UUID·역할을 사용한다. 관리 웹은 배포자가 `VITE_VM_ROLE_BINDINGS` JSON으로 확인한 UUID를 설정한 경우에만 역할 이름을 붙인다. 설정이 없으면 VM 이름/역할 미확인으로 남기고 이름으로 추측하지 않는다.
