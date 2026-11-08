# HDMI A안 — 웹 UI는 표시 domU, 그리기는 dom0 (S15P31E110-414)

작성: 2026-11-09 · 이전 설계(B안, 보류): [display-plan-b-domu-render-relay.md](display-plan-b-domu-render-relay.md)

## 1. 결정

- 표시 선택지는 **HDMI 1대** 또는 **휴대폰 직접 렌더링** 중 하나다. HDMI에는 휴대폰과 같은 화면을 가로로 띄운다.
- **HDMI는 A안**:
  - 웹 UI(vm-screens 빌드와 화면 앱 서버 `guest_app`)는 **VM3(debian-dev)** 가 제공한다.
  - **dom0의 기존 Chromium kiosk**가 그 화면을 열어 HDMI에 그린다.
- 상태 수집·정리·화면 결정(policy)은 dom0에 둔다. VM3는 정리된 데이터만 읽기 전용으로 받는다.
- B안(VM3가 그리고 dom0는 프레임만 중계)은 보류한다. 410·411·413의 세션·송신·출력기 코드는 남겨 두되 쓰지 않는다.

### 바꾼 이유 (박신영 의견, 2026-11-09)

| | A안 | B안 |
| --- | --- | --- |
| VM3에 필요한 것 | 표준 Python 서버, 화면 파일. **새 패키지 없음** | Xvfb, Chromium, ffmpeg, 폰트 (VM3는 인터넷이 없어 설치가 어려움) |
| CPU | dom0가 그린다 | VM3가 dom0와 같은 pCPU {0,3}를 써서 실제 이득이 없다. 압축·전송·풀기 비용이 더해진다 |
| 메모리·지연 | VM3 512 MiB로 충분, 추가 지연 없음 | Chromium에는 512 MiB가 빠듯함, 약 0.2~0.8초 추가 지연 |
| GUI·영상 송출 | 필요 없음 | 필요함 |

## 2. 구성

```text
dom0                                                VM3 debian-dev (192.168.77.20)
──────────────────────────────────────────          ───────────────────────────────────────
feed 127.0.0.1:8082 (기존, service token)
  ▲ LocalFeed
guest data 192.168.77.1:8085  ◀── guest-data token ── guest_app 192.168.77.20:8086
  (GET /api/v1/display/presentation 하나)                 · vm-screens 파일 (/opt/xenova/display-web)
                                                         · presentation만 dom0로 전달
Chromium kiosk (HDMI, X11 :5) ──── GET /display/mobile ───▶ (dom0 192.168.77.1만 허용)
```

## 3. 코드 변경 (414)

| 파일 | 변경 |
| --- | --- |
| `outputs.RuntimeConfig.ui_origin` | `http://<사설 IPv4>:<port>`만 허용, Chromium 렌더러 전용. 페이지 주소 = `ui_origin` + `page` |
| `kiosk.py` | `ui_origin`이 있으면 그 주소를 연다. **dom0 feed 세션 쿠키를 만들지 않고**, 렌더러 ack용 presentation 조회도 하지 않는다(`feed_status: not_used`) |
| `guest_app.py` | `bind`(VM3 브리지 주소)와 `allowed_clients`(dom0만) 추가. 허용 밖 요청은 403 |
| `guest_frames.GuestRelayConfig` | `guest: null`이면 guest data 경로만 실행한다(프레임 중계 없음, 토큰 1개) |

## 4. 보안 경계

- dom0 Chromium이 VM3가 준 웹 콘텐츠를 실행한다. 그래서 그 브라우저 컨텍스트에는 dom0 feed 세션을 주지 않는다. Chromium 샌드박스는 그대로 둔다.
- VM3 화면 앱 서버는 dom0만 접근할 수 있다. dom0 guest data는 토큰이 필요하고 한 경로만 연다. service token은 dom0 밖으로 나가지 않는다.
- 브리지(xenbr0)에는 모든 domU가 붙어 있으므로, 출발지 제한 방화벽은 배포 담당과 협의한다.

## 5. 배포·빌드

- 지금은 VM3에 손으로 임시 설치한다(박신영 확인).
- 이미지에 넣을 항목: [BUILD-CHECKLIST.md](../../platform/services/display/router/deploy/guest-display/BUILD-CHECKLIST.md)
- 요청서: [2026-11-09-display-domu-renderer-platform.md](../requests/2026-11-09-display-domu-renderer-platform.md)
