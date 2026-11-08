# 2026-11-05 변경 안내
기본 표시 방식은 HDMI이며 터미널 명령으로 휴대폰/HDMI를 왕복 전환한다.
[전환 명령과 준비 조건](display-mode.README.md)이 아래의 수동 표시 선택 절차보다 우선한다.
아래 빌드·USB·공유 feed·인증 준비는 계속 필요하다.

---

# 351 휴대폰 표시 통합 배포 순서

기존 native 이미지의 공유 display-feed 1개 → USB 접속 서비스 1개 → 휴대폰의
`/display/mobile`이다. USB 주소·서비스 토큰·VM UUID·주행 상태는 기존 담당과
실제 설치값을 사용한다. 아래 안내는 이미지/실행 준비이며 물리 성공 증거가 아니다.

## 빌드 담당 확인

1. 동일 platform 커밋의 frontend lockfile과 vm-screens를 기존 빌드 가이드대로 빌드한다.
   `XENOVA_DISPLAY_WEB_ASSETS`는 **display-web 폴더의 부모** 경로이다.
   `${XENOVA_DISPLAY_WEB_ASSETS}/display-web/index.html`과 모든 연결된 자산이 필요하다.
   웹 서버의 frontend 배포만 갱신했다고 보드의 내장 자산이 갱신되는 것은 아니다.
2. `bitbake-layers show-appends`, `bitbake -e xenova-display`에서 349의 wildcard append와
   351의 0.1.0 append가 모두 적용되는지 확인한다. 기본 recipe와 같은 파일명을
   가진 다른 layer override가 있으면 실제 FILESPATH 선택을 대조한다.
3. 제품 Python 패키지, feed/mobile/native HDMI unit, `50-mobile-first.conf`,
   `/usr/share/xenova/display/web`과 운영 문서가 같은 이미지에 설치되어야 한다.
   모바일 서비스·USB 사설 설정·휴대폰 우선 marker는 자동 활성화/생성하지 않는다.
4. **349 MR !391의 연결 폼 출처 정책 수정도 이미지 기준 커밋에 포함해야 한다.**
   최초 점검 기준 `cd571b9`에는 없었으나 최종 확인한 `5dc0c3b`에는 머지되어 있다.
   351에서 그 변경을 복제하지 않는다.
5. 빌드된 fresh rootfs에 저장소의 아래 명령을 실행한다.
   `python3 platform/scripts/check-mobile-display-rootfs.py --root <rootfs>`
   이는 파일·의존 자산의 오프라인 검사이며 BitBake,
   패키지 QA, USB 드라이버·실제 시작·휴대폰 표시를 대신하지 않는다. 서비스나 네트워크를
   바꾸지 않고 비밀 환경 파일을 읽거나 출력하지 않는다. 운영 중인 `/`를 대상으로 하지 않는다.
6. 커널 담당은 S24에서 실제 사용하는 USB 네트워크 방식의 host 드라이버·설정이
   이미지에 포함됐는지 별도로 확인한다. USB 충전만으로 네트워크 지원을 판정하지 않는다.

기존 이미지의 그래픽 패키지/자동 HDMI 실행을 제거하지 않는다. 휴대폰을 위해
그래픽·인코더를 켜거나 새 MQTT/VM 상태 수집기를 만들 필요는 없다.

## 실제 장비에서 준비할 것

먼저 소스/이미지 커밋, 현재 unit active/enabled, marker 유무와 기존 private 설정의
보존 방법을 기록한다. 설정 원문·토큰·번호를 화면 캡처나 인계 파일에 넣지 않는다.

- 안정적인 USB 연결, 실제 USB 인터페이스·폰/보드 IP는 348에서 확인한다.
- 기존 `/etc/xenova/display.env`와 `/etc/xenova/display-bindings.json`을 재사용한다.
  토큰/MQTT/Gateway/관측 경로/실제 UUID를 예제 값으로 덮지 않는다.
- 실제로 검증한 USB 값으로 root 소유 0600 `/etc/xenova/display-mobile.json`을 준비한다.
  [349 안내](../mobile-access/mobile-access.README.md)의 형식과 권한 검사를 따른다.
- feed와 mobile이 동일한 기존 `/etc/xenova/display.env`를 읽는지 확인한다.
  feed는 단일 worker의 `127.0.0.1:8082`로 유지한다. 외부 공개로 우회하지 않는다.
- 기존 관리 서비스의 공유 관측과 Gateway 읽기 경로가 실제로 제공되는지 확인한다.
  미수신 데이터를 정상 주행 값으로 만들거나 표시 때문에 제어 서비스를 임의 기동하지 않는다.

## 실행 순서 — 휴대폰 접속 확인 후 HDMI 중지

**349 접속 확인을 위해 먼저 HDMI를 중지할 필요는 없다.** 기존 HDMI가 실행 중이어도
공유 feed와 mobile은 독립적으로 준비할 수 있다. 단계 간 준비 조건을 순환시키지 않는다.

1. 기존 feed가 이미 실행 중이면 유지한다. 꺼져 있으면 설정을 검증한 운영자가
   feed만 시작하고 `/display/mobile`과 presentation이 제공되는지 보호된 인증 경로로 확인한다.
2. native mobile 서비스와 설정을 확인한 뒤 운영자가 mobile만 시작한다. 재시도 제한에
   걸렸다면 원인을 고친 후 해당 mobile unit만 reset-failed한다. VM·API·MQTT·Gateway는
   재시작하지 않는다.
3. `/run/xenova-display-mobile/endpoint.json`의 주소로 휴대폰에서 접속한다. root 터미널의
   임시 pairing-code를 휴대폰에 입력한다. 데이터 수신·미확인 안내를 확인한다. 코드·세션은
   사용자가 처음 연결한 폰의 읽기 전용 표시용이고 관리자 자격 증명이 아니다.
4. 그다음 [351 native 절차](mobile-first.README.md)에 따라 이 배포가 소유한 marker를
   만들고 **HDMI 서비스만** 중지한다. 전체 display target을 stop하지 않는다.
   feed/mobile과 기존 제어 경로는 유지한다. graphics/kiosk가 별도 legacy user 서비스라면
   native 절차로 강제 정리하지 말고 설치 담당과 먼저 대조한다.
5. 기존 `mobile_preflight`는 공유 feed/HDMI 미실행 상태를 한 번만 확인한다. 이 점검기의
   성공은 USB/phone 접속 성공을 뜻하지 않으므로 위 휴대폰 확인 결과도 별도로 기록한다.
6. 재부팅 자동 실행은 검증된 주소·설정과 기존 enabled 상태를 확인한 뒤 운영자가
   필요한 feed/mobile 서비스만 명시적으로 enable한다. marker가 있어도 USB 연결이나
   인증이 자동으로 준비되지는 않는다. USB가 늦게 생겨 mobile 시작 제한에 걸릴 수 있으며,
   현재 구현이 자동 USB 주소 재탐색을 제공한다고 간주하지 않는다.

## 만료·복귀·HDMI 대체와 원복

- 세션 만료 시 이전 정상 값을 숨기고 인증 필요를 표시한다. 운영자가 새 번호를 발급하고
  사용자가 새로고침해 연결 화면에서 입력한다. 자동 로그인이나 코드의 URL 전달은 없다.
- USB 재연결로 주소가 달라지면 실제 값을 다시 확인하고 해당 mobile 설정·서비스만
  승인된 운영 절차로 갱신한다. Wi-Fi/Tailscale 주소로 자동 대체하지 않는다.
- 휴대폰 시연이 안 되면 원인을 기록한 뒤 이 배포가 만든 marker만 해제하고 기존 native
  HDMI 절차를 실행한다. 직접 HDMI 표시를 위해 원격 인코더를 새로 띄우지 않는다.
- 원복 시 이 시험에서 바꾼 mobile/feed enable 상태와 소유 marker를 기록과 대조한다.
  원래 실행 중인 공유 feed나 다른 담당의 서비스·private 파일을 지우지 않는다.
- S24 가독성·USB 분리/복귀·장시간 성능과 실제 두 HDMI 대체 출력은 352에서 실증한다.

빠른 구분: rootfs 검사=파일 구성, mobile_preflight=로컬 실행 구성,
349·350 폰 확인=실제 접속/화면, 352=물리 복귀/장시간/대체 시연이다.
