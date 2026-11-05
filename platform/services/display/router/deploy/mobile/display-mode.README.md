# 351 — 터미널에서 HDMI ↔ 휴대폰 표시 전환

2026-11-05 변경: 기본은 HDMI 두 화면. 제품용 버튼/웹 관리 API 대신 보드의
root 또는 sudo 터미널에서 명령으로 왕복 전환한다. 이 문서가 이전
휴대폰 우선/수동 HDMI 대체 문서의 **표시 선택 절차**를 대체한다.

```sh
sudo python3 -m xenova_display.display_mode status
sudo python3 -m xenova_display.display_mode mobile
sudo python3 -m xenova_display.display_mode hdmi
```

## 설치·실행 전제

- 같은 소스의 display Python 패키지와 native HDMI/mobile unit,
  `xenova-display-hdmi.service.d/60-display-mode.conf`를 설치하고 unit reload를 끝낸다.
  이 CLI는 설치나 daemon-reload, enable/disable을 하지 않는다.
- 기존 feed가 정상 실행 중이어야 한다. 기존 실제 UUID·EDID·토큰·공유 관측/Gateway
  읽기 경로를 보존한다. CLI는 토큰이나 MQTT 설정을 읽지 않는다.
- HDMI용 실제 장치 설정/그래픽 도구와 휴대폰용 USB host 드라이버·연결·IP,
  root 전용 `/etc/xenova/display-mobile.json`·`display.env`를 먼저 준비한다.
  CLI는 USB 설정을 만들거나 주소를 변경하지 않는다.
- 과거의 `/etc/xenova/display-mobile-first`가 있으면 CLI는 변경 없이 거부한다.
  이 파일은 부팅 정책이므로 운영자가 기존 배포의 소유·백업을 확인하고 수동으로
  해제해야 한다. 남의 marker를 자동 삭제하지 않는다.
- native 이미지용이다. legacy 별도 Xorg/키오스크/원격 stream을 동시에 운영하지 않는다.
  특히 별도의 user stream 서비스는 CLI 소유가 아니므로 운영자가 먼저 정리해야 한다.
  CLI는 user manager 탐색이나 다른 사용자의 프로세스 종료를 하지 않는다.

## 동작

`mobile`: mobile 서비스만 시작하고 active 및 root 전용 endpoint 파일을 확인한 뒤,
`/run/xenova-display-mode/mobile`을 만들고 native HDMI만 중지한다.
이미 active인 mobile은 재시작하지 않아 기존 세션을 유지한다.
이어서 기존 349 절차로 endpoint 주소에 접속하고 연결번호를 입력한다.

```sh
sudo cat /run/xenova-display-mobile/endpoint.json
sudo cat /run/xenova-display-mobile/pairing-code
```

번호는 채팅·로그·Git에 공유하지 않는다. 필요하면 기존 349 운영 절차로 재발급한다.
CLI는 접속 번호를 자동으로 출력하거나 인증을 우회하지 않는다.
mobile 서비스 준비는 실제 S24 연결·화면 성공과 다르다.

`hdmi`: runtime marker를 해제하고 native HDMI를 시작한다. 서비스 active만으로
성공 처리하지 않고 기존 kiosk 상태에서 두 출력의 mapped/renderer_alive/physical_verified와
3초 이내 검증 시각을 기다린다. 이후 mobile 서비스만 중지한다.
이 상태 검증도 사용자의 실제 모니터 육안 확인을 대신하지 않는다.
휴대폰 listener를 중지하므로 휴대폰 페이지 연결은 끊기며, 다음 mobile 전환에서는
새 연결번호 인증이 필요하다. USB 테더링 자체는 변경하지 않는다.

공유 feed·VM·주행 제어·Gateway·MQTT·API·Tailscale·네트워크·unit enable 상태는
전환 대상이 아니다. 새 수집기/인코더/상주 모드 관리 서비스를 추가하지 않는다.
현재 CLI가 외부 stream을 자동 재개하거나 제어한다고 가정하지 않는다.

## 실패·부팅·상태

- flock으로 명령을 직렬화하고 겹친 요청은 `DISPLAY_MODE_BUSY`로 거부한다.
- 새 경로가 준비되지 않으면 이전 marker/표시 서비스 상태로 복원을 시도한다.
  복원이 실패하면 `ROLLBACK_INCOMPLETE_CHECK_DISPLAY_SERVICES`를 반환한다.
  서비스 재시작으로 세션이 달라질 수 있으므로 복원 뒤 실제 화면을 다시 확인한다.
- timeout·원문 예외·서비스 환경·인증정보를 출력하지 않고 고정 오류 코드만 출력한다.
- `status`는 marker와 표시 서비스/렌더러 상태만 조회하며 서비스를 바꾸지 않는다.
  selected는 요청된 실행 구성이고 실제 화면 성공을 뜻하지 않는다.
- 선택은 `/run`에만 보관한다. 재부팅 시 marker가 사라져 **HDMI 기본**으로 돌아간다.
  정상 HDMI 자동 실행은 기존 target/설치 설정이 준비돼 있어야 한다.
  CLI가 누락된 그래픽 패키지·장치 설정을 자동 복구하는 것은 아니다.
- `physical_display_verified`, `phone_connection_verified`는 실제 검증을 대신하지 않도록
  CLI 결과에서 항상 false다. 실제 왕복·재부팅·S24/HDMI 실증은 352에서 수행한다.
