# 카메라·AI 감지 계약 v1

**최종 갱신** 2026-10-21 v1 초안
**대상 시스템** Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼, RC카 시나리오
**구성 계층** 젯슨 카메라 → 세 번째 VM(메시지 보드, Debian) AI 앱 → dom0 웹 프록시 → 관리 콘솔·키오스크

차에 달린 카메라 영상을 세 번째 VM 이 받아 AI 로 장애물을 찾고, 감지 박스를 그린 영상을 관리 콘솔과 모니터(키오스크)에 보여주는 경로의 기준 문서입니다. 감지 결과는 MQTT 로도 냅니다 (화면·기록용).

**이 경로는 주행 판단에 쓰지 않습니다 (격리).** 주행 판단은 지금처럼 젯슨의 `perception.result`([MQTT 계약](mqtt-contract-v1.md) 5.4)로 합니다. 세 번째 VM 이 죽어도 영상·감지 화면만 멈추고 차는 그대로 달립니다 — 이것이 격리 시연입니다.

주소·형식·토픽을 바꾸면 영상을 내는 쪽과 받는 쪽 담당이 함께 리뷰합니다 (CONTRIBUTING §6).

---

## 1. 구성

```text
 젯슨 (카메라)
   │  ① 원본 MJPEG   http://<jetson>:8090/raw.mjpg      640x480, 15 fps 목표
   ▼
 세 번째 VM — 메시지 보드 (Debian, app_id message-board, 화면 경로 id interaction)
   │  AI 감지 앱: 원본을 받아 장애물을 찾고 박스를 그린다
   ├─ ② 감지 영상 MJPEG  http://<vm>:8081/stream.mjpg
   └─ ④ 감지 결과 MQTT   xenova/v1/apps/message-board/vision (MQTT-14, vision.result) ─▶ 백엔드 (화면·기록)
   ▼
 dom0 웹 프록시
   │  ③ 같은 origin 의 /camera/stream.mjpg  →  세 번째 VM :8081/stream.mjpg 로 넘김
   ▼
 관리 콘솔 (브라우저)  ·  모니터 키오스크 (vm-screens)

 ✕ 게이트웨이·자율주행 VM 은 ②·④ 를 받지 않는다. 주행 판단 = 젯슨 perception.result (따로)
```

## 2. 영상 스트림

| # | 구간 | 주소 | 형식 | 해상도 | fps | 누가 받나 |
| --- | --- | --- | --- | --- | --- | --- |
| ① | 젯슨 → 세 번째 VM | `http://<jetson>:8090/raw.mjpg` | MJPEG | 640x480 | 15 목표 | 세 번째 VM AI 앱만 |
| ② | 세 번째 VM → dom0 프록시 | `http://<vm>:8081/stream.mjpg` | MJPEG (감지 박스를 그린 영상) | 640x480 | AI 처리 속도를 따름 (① 보다 높지 않음) | dom0 프록시 |
| ③ | dom0 프록시 → 브라우저 | `/camera/stream.mjpg` (같은 origin) | ② 그대로 | 640x480 | ② 와 같음 | 관리 콘솔, 키오스크 |

공통 형식

| 항목 | 규칙 |
| --- | --- |
| MJPEG | HTTP `GET`, 응답 `Content-Type: multipart/x-mixed-replace; boundary=<임의 문자열>`, 각 파트는 `Content-Type: image/jpeg` JPEG 한 장. 브라우저 `<img src>` 로 바로 보인다 |
| 인증 | v1 은 없다. ①·② 는 플랫폼 내부망(젯슨 이더넷, xenbr0)에서만 연다. 외부에서 보이는 것은 ③ 뿐 |
| 캐시 | `Cache-Control: no-store` |
| 동시 접속 | ① 은 세 번째 VM 하나만 붙는다. ② 는 프록시가 붙는다. 브라우저 여러 개는 ③ 에 붙는다 |

- 프론트(관리 콘솔·vm-screens) 기본값: `VITE_CAMERA_URL=/camera/stream.mjpg`. 개발 중에는 이 값을 바꿔 다른 주소를 볼 수 있다.
- 프록시는 영상을 바꾸지 않고 스트림을 그대로 넘긴다 (버퍼링 끔). 세 번째 VM 주소(`<vm>:8081`)는 프록시 설정값으로 둔다.
- ① 의 원본 영상은 젯슨의 주행 인식(`perception.result`)과 같은 카메라에서 나오지만, 원본 스트림을 내는 일이 인식 주기(15 Hz)를 늦추면 안 된다. 늦춘다면 ① 의 fps·해상도를 낮춘다.

## 3. 감지 결과 MQTT-14 `vision.result`

세 번째 VM 이 감지할 때마다 낸다. 공통 봉투·규칙은 [MQTT 계약](mqtt-contract-v1.md) 2장을 따른다 (`vm_id` = 세 번째 VM, `app_id: message-board`).

| ID | 토픽 | `type` | 발행 → 구독 | 주기 | QoS | retain |
| --- | --- | --- | --- | --- | --- | --- |
| MQTT-14 | `xenova/v1/apps/message-board/vision` | `vision.result` | 메시지 보드 VM → 백엔드 (화면·기록) | ≤ 10 Hz | 0 | X |

```json
{
  "frame_id": 1287,
  "fps": 9.6,
  "objects": [
    { "label": "obstacle", "score": 0.91, "bbox": [0.42, 0.55, 0.18, 0.30] },
    { "label": "person", "score": 0.77, "bbox": [0.05, 0.40, 0.10, 0.45] }
  ]
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `frame_id` | uint32 | 감지한 프레임 번호. 앱 시작 때 0 부터 1씩 증가 |
| `fps` | float | 지금 감지 처리 속도 (최근 1초 평균) |
| `objects` | array | 찾은 물체. 없으면 빈 배열 |
| `objects[].label` | string | 물체 종류 (모델 클래스 이름, 예 `obstacle`, `person`, `car`) |
| `objects[].score` | float | 신뢰도 0~1 |
| `objects[].bbox` | float[4] | `[x, y, w, h]` — 화면 크기로 나눈 0~1 값. `x, y` 는 박스 왼쪽 위, 원점은 영상 왼쪽 위 |

- 10 Hz 를 넘겨 내지 않는다. 감지가 더 빠르면 건너뛴다.
- 새 감지 결과가 없으면(원본 끊김 등) 내지 않는다. 같은 결과를 반복하지 않는다 — 받는 쪽은 1초 넘게 못 받으면 "감지 멈춤" 으로 본다.
- QoS 0, latest-only. 늦게 온 것·빠진 것을 다시 보내지 않는다.
- 이 좌표는 **영상 화면 좌표**다. `perception.result` 의 지면 좌표(m)와 다르고, 서로 바꿔 쓰지 않는다.

## 4. 주행과 격리

| 규칙 | 내용 |
| --- | --- |
| 주행 판단에 쓰지 않는다 | 게이트웨이·자율주행 VM·수동주행 VM 은 ②·MQTT-14 를 구독하지 않는다. 주행 판단 근거는 젯슨 `perception.result` 하나 |
| 장애가 번지지 않는다 | 세 번째 VM·AI 앱·프록시가 죽거나 느려져도 주행 토픽(MQTT-06 `drive.command`, `vehicle/*`)과 게이트웨이 판정에 영향이 없어야 한다 |
| 시연 | 주행 중 세 번째 VM 을 끈다 → 콘솔·모니터의 카메라 화면만 "영상 없음" 이 되고 차는 계속 달린다 |

- 브로커 ACL 은 message-board 계정에 이 토픽 발행만 더한다 (MQTT 계약 9장). 구독은 개발 단계라 모든 계정에 열려 있으므로, "주행 쪽은 구독하지 않는다" 는 각 앱 구현이 지킨다.

## 5. 실패했을 때 화면

| 상황 | ③ (브라우저가 받는 것) | 화면 |
| --- | --- | --- |
| 정상 | 감지 영상 | 영상 + (MQTT-14 를 쓰는 화면이면) 감지 개수·fps |
| 세 번째 VM·AI 앱이 꺼짐 | 프록시가 `502`/`504` 를 주거나 스트림이 끊김 | **"영상 없음"** 안내를 영상 자리에 띄운다. 3초마다 다시 연결해 보고, 다시 오면 영상으로 돌아간다 |
| 젯슨 원본(①)이 끊김 | 세 번째 VM 이 ② 스트림을 닫는다 → ③ 도 끊김 | 위와 같음 ("영상 없음") |
| 영상은 오는데 MQTT-14 가 1초 넘게 없음 | — | 영상은 그대로, 감지 정보 칸만 "감지 멈춤" |

- 마지막 프레임을 멈춘 채로 계속 보여주지 않는다. 멈춘 영상은 살아 있는 것처럼 보인다.
- 카메라 화면이 실패해도 그 페이지의 다른 칸(주행 상태 등)은 그대로 동작한다.

## 6. 담당

| 부분 | 위치 | 담당 |
| --- | --- | --- |
| 젯슨 원본 MJPEG 스트림 (①) | `vehicle/` | 미정 |
| 세 번째 VM AI 감지 앱 (②, MQTT-14 발행) | `platform/apps/` | 미정 |
| dom0 웹 프록시 `/camera/stream.mjpg` (③) | `platform/` (dom0 웹 서버 설정) | 미정 |
| 카메라 화면 (관리 콘솔·vm-screens) | `frontend/` | 미정 (화면 정의서에서 정함) |
| MQTT-14 토픽·ACL | `docs/contracts/mqtt-contract-v1.md`, `platform/deploy/mosquitto/` | 공병우 |

## 7. 미확정

- AI 모델과 `label` 값 목록
- 젯슨 → 세 번째 VM 네트워크 경로 (젯슨 이더넷 ↔ dom0 ↔ xenbr0 라우팅)
- dom0 웹 프록시를 어느 서버가 맡는지 (콘솔 정적 파일을 내는 서버와 같은 곳)
- 백엔드가 MQTT-14 를 콘솔 WS 로 넘길지, 이벤트 로그에 남길지
- ③ 외부 노출 시 인증

## 관련 문서

- [MQTT 메시지 계약 v1](mqtt-contract-v1.md) — 6장 MQTT-14, 9장 ACL, 5.4 `perception.result`
- [화면 배치 계약 v1](display-route-v1.md) — 메시지 보드 상세 화면 `/detail/interaction`
- [아키텍처 설계서](../architecture.md)
- [RC카 시나리오](../scenario-rc-car.md)

## 변경 이력

| 버전 | 날짜 | 내용 |
| --- | --- | --- |
| v1 (초안) | 2026-10-21 | 최초. 젯슨 원본 MJPEG → 세 번째 VM AI → dom0 프록시 `/camera/stream.mjpg`, 감지 결과 MQTT-14 `vision.result`, 주행 판단 미사용(격리), 실패 시 "영상 없음" (2026-10-21 결정, S15P31E110-128) |
