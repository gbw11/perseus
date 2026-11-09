# 349 휴대폰 유선 접속·인증·상태 전달

## 사용자가 확정한 접속 흐름

1. 갤럭시 S24를 데이터 케이블로 Raspberry Pi의 검증된 USB-A 포트에 연결하고 USB 테더링을 켠다.
2. 운영자는 보드 터미널에서 접속 주소와 **2분 동안 한 번 쓸 수 있는 6자리 연결번호**를 확인한다.
3. 휴대폰 브라우저에서 주소를 열고 번호를 한 번 입력한다.
4. 같은 화면에서 계기판·인포테인먼트를 합친 차량 화면(`/display/combined`, vm-screens `CombinedPage`)을 본다.
   - HDMI와 같은 통합 화면이다. 2026-11-08 결정: 선택한 단말 하나에 통합 화면 하나.
   - `endpoint.json`에 나오는 주소는 이전처럼 `/display/mobile`이고, 열면 `/display/combined`로 넘어간다.
   - 화면은 1920×1080 기준이고 크기를 `vh`로 맞춘다. 휴대폰은 **가로로 두고** 본다.

관리자 비밀번호·내부 서비스 토큰은 휴대폰에 입력하거나 URL에 넣지 않는다.
시연 목표는 휴대폰이고 어려울 때 HDMI로 대체하는 운영 절차는 351번을 따른다.

## 구성과 경량화

```text
기존 Agent/MQTT·공유 VM 관측·Gateway 읽기
             ↓ (기존 수집기 1개)
기존 display-feed: 127.0.0.1:8082
             ↓ (허용 GET만, 기존 서비스 토큰은 보드 내부)
선택형 USB 접속 서비스: 검증된 USB IPv4:8084
             ↓ (허용한 폰 IP + 6자리 번호 + HttpOnly 세션)
갤럭시 브라우저: 통합 UI를 직접 렌더링
```

- 새 VM·Gateway·MQTT 수집기를 만들지 않는다. 별도 WebSocket·영상 캡처·인코더도 필요 없다.
- 추가되는 것은 단일 worker의 가벼운 접속 중계다. 상태를 따로 저장/캐시하거나 신선도를 재판정하지 않는다.
  기존 presentation과 브라우저의 순서/freshness 처리를 재사용한다.
- 파일 전달은 빌드된 모바일 HTML·허용된 `/assets/` 파일과 presentation GET만 허용한다.
  VM 관리·주행 입력·selection·내부 state/WS·session API·HDMI ACK는 제공하지 않는다.
- 중계는 upstream에 브라우저 cookie나 임의 전달 헤더를 보내지 않는다. 기존 feed의 session을
  대신 발급하지 않고 중계 전용 읽기 세션 하나만 메모리에 유지한다.
- 기존 loopback bind·MQTT·Gateway·Spring·Tailscale·SSH 정책을 바꾸지 않는다.

## 이미지와 배포 준비

현재 파일은 보드 없이 준비한 코드다. **348의 실제 USB 연결·드라이버·IP 확인을 대체하지 않는다.**

351의 기존 recipe/append는 보존한다. 새 `xenova-display_%.bbappend`가 다음 파일만 추가 설치한다.

```text
/usr/lib/systemd/system/xenova-display-mobile.service
/usr/share/xenova/display/mobile-access/mobile-access.config.example.json
/usr/share/xenova/display/mobile-access/mobile-access.README.md
# 기존 package 설치에 포함되는 mobile_access/mobile_network/mobile_upstream.py
```

접속 서비스는 이미지에서 자동 enable/start하지 않는다. 운영 설정·비밀값·USB 주소·DHCP 설정도
이미지에 새로 넣지 않는다. 실제 `systemd_system_unitdir`는 배포 환경에 따라 다를 수 있다.

다음 사항이 확인된 뒤 배포한다:

- USB 연결이 안정적이고 실제 네트워크 드라이버와 인터페이스가 존재한다.
- 해당 USB 인터페이스가 UP이고 RFC1918 IPv4를 가지고 있으며, 폰 IPv4와 같은 서브넷이다.
- 기존 feed와 350의 모바일 web assets가 배포됐고 `/etc/xenova/display.env`의 기존 서비스 토큰을 사용한다.
- 351의 휴대폰 우선 실행 구성이 적용됐다. Xorg/Chromium/HDMI가 접속 중계의 의존성이 아니다.

## 실제 보드에서 설정할 값

아래는 나중에 보드 root shell에서 실행할 운영 명령이다. 이번 로컬 작업 중 실행한 기록이 아니다.

```sh
ip -j -4 address
```

348에서 **확인한 USB 인터페이스와 폰 IPv4**를 사용해 `/etc/xenova/display-mobile.json`을 만든다.
예시의 `VERIFY_...`는 그대로 실행하면 거부되는 자리표시자다. Wi-Fi나 Tailscale 인터페이스로 대체하지 않는다.

```json
{
  "schema_version": 1,
  "usb_interface": "VERIFY_USB_INTERFACE",
  "phone_ipv4": "VERIFY_PHONE_IPV4",
  "port": 8084
}
```

설정은 root 소유 일반 파일, 0600 권한을 사용한다. 기존 설정 파일이 있으면 자동으로 덮어쓰지 않는다.
8081·8082·8083 기존 포트는 사용하지 않는다. 포트 변경이 필요하면 별도의 비특권 포트를 명시한다.
보드의 USB IPv4는 해당 인터페이스에서 읽는다. IP·라우트·DNS·DHCP를 할당하거나 바꾸지 않는다.

## 시작·주소·연결번호 확인

```sh
systemctl daemon-reload
systemctl start xenova-display-mobile.service
systemctl status xenova-display-mobile.service --no-pager
cat /run/xenova-display-mobile/endpoint.json
cat /run/xenova-display-mobile/pairing-code
```

`endpoint.json`의 URL을 휴대폰 브라우저로 열고 방금 확인한 번호를 입력한다.
번호는 journal·URL·화면 HTML에 출력하지 않는다. `pairing-code`는 root 전용 임시 파일이고
성공한 뒤 제거된다. 2분이 지나거나 잘못된 번호를 5번 입력하면 새 번호가 필요하다.

새 번호 발급(기존 폰 세션도 무효화):

```sh
systemctl kill --kill-whom=main --signal=USR1 xenova-display-mobile.service
cat /run/xenova-display-mobile/pairing-code
```

코드를 다시 발급해도 feed·VM·Gateway를 재시작하지 않는다.
폰 세션은 최대 8시간이며 서비스 재시작 시 무효화된다. 만료되면 통합 화면에서 인증 필요를
표시하고, 브라우저 새로고침으로 번호 입력 화면을 다시 연다.

현재 전송 범위는 **직결 USB의 내부 HTTP**다. cookie는 HttpOnly·SameSite=Strict이고 응답은
no-store다. HTTPS나 일반 Wi-Fi/인터넷 공개 서버로 사용할 구성이 아니다.
서비스는 검증된 USB 장치에 `SO_BINDTODEVICE`로 묶고 해당 폰의 실제 IP만 허용한다.
Host/POST Origin도 확인하며 X-Forwarded-For를 접속 신원으로 신뢰하지 않는다.

## 끊김·주소 변경·재부팅

- USB가 잠깐 끊겼다가 **같은 주소**로 돌아오고 폰 세션이 유효하면 다음 조회부터 새 상태를 받는다.
  중계에 저장된 정상 화면/상태는 없고, 미수신은 기존 모바일 UI가 미확인으로 표시한다.
- USB 주소나 폰 IPv4가 바뀌면 기존 URL을 자동으로 새 주소라고 간주하지 않는다.
  실제 주소를 다시 확인하고 필요한 설정만 갱신한 뒤 **접속 서비스만** 재시작하고 새 URL/번호로 접속한다.
- USB 인터페이스 부재·다른 버스·주소 모호함·잘못된 설정이면 시작을 거부한다. Wi-Fi로 자동 우회하지 않는다.
- feed가 미준비·중단·내부 인증 불일치이면 503/고정 오류 코드로 처리하며 이전 성공 값을 돌려주지 않는다.
  기존 통신 설정을 임의로 덮거나 자동 재시작하지 않는다.
- 부팅 후 USB가 준비되면 접속 서비스를 start한다. 부팅 시 항상 USB 준비가 된다는 것이 실제로
  확인된 경우에만 운영자가 enable 여부를 결정한다. 351의 기존 feed 자동 시작과 별개다.
- 휴대폰 조작·USB 분리·번호 발급은 주행 모드 변경·VM 승계·출력 허가에 영향을 주지 않는다.

## 종료·인계와 남은 검증

```sh
systemctl stop xenova-display-mobile.service
```

이 서비스만 종료한다. systemd가 소유한 임시 디렉터리와 메모리 세션이 정리되고 기존 feed와
VM/통신은 유지된다. 시험에서 새 설정/enable 상태를 바꿨다면 최초 기록에 따라 그 범위만 복원한다.
기존 설정·비밀값을 일괄 삭제하지 않는다.
HDMI 대체 시연을 채택했다면 불필요한 이 접속 서비스는 종료하고, 351의 기존 HDMI 실행 절차를 따른다.

현재 로컬 검증은 실제 S24·USB·새 이미지 부팅 성공을 뜻하지 않는다. 실제 IP/드라이버·번호 입력·
세션 만료/재접속은 보드가 준비된 뒤 348/349에서 확인하고, 장시간 사용·발열·성능·물리 복귀는 352에서 확인한다.
