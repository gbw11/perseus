# Pi4 KMS 상시 HDMI 실행 프로파일

실보드에서 검증한 전용 graphics rootfs를 사용하는 프로파일이다. 호스트 패키지 설치나 VM 제어를 하지 않는다. 다른 보드/이미지의 임의 경로에 그대로 복사하지 말고 rootfs·Python ABI·Chromium sandbox·실제 EDID·UUID를 먼저 확인한다.

## 구성

- `xenova-display.target`: 세 서비스를 함께 기동/종료. `multi-user.target`에 enable한다.
- `xenova-display-feed.service`: 기존 상태 경로와 MQTT 수신기를 사용하며 127.0.0.1:8082에만 listen한다. 상태 공유는 두 서비스의 명시적 opt-in으로만 활성화한다.
- `xenova-display-graphics.service`: private mount/PID namespace에서 KMS Xorg :5/VT2 및 Openbox를 실행한다. 준비 완료 후 systemd READY=1을 보낸다.
- `xenova-display-kiosk.service`: 그래픽 준비 후 제품 Kiosk를 실행한다. rootfs의 setpriv로 UID/GID1000으로 낮추며 Chromium sandbox를 유지한다. 물리 출력은 EDID에 고정한다.

`@GRAPHICS_ROOT@`는 설치 시 검증한 rootfs 절대 경로로 치환한다. 현재 보드 경로는 `/opt/xenova-graphics-probe/20261027-1ddf34f25d30/rootfs`다. 이름에 probe가 있지만 이 배포는 해당 영속 디렉터리를 실행 의존성으로 고정했다. 삭제하면 서비스를 시작할 수 없다. 새 이미지 재설치 시 이 실행 묶음·Playwright/Python ABI와 아래 배포 파일을 다시 준비해야 한다. Yocto 이미지 내장 배포는 아니다.

## 보드 배치

현재 고정 release: `/opt/xenova-display/releases/20261028-ffed0f8`, 진입점 symlink: `/opt/xenova-display/current`.

release의 `bin/`에 세 Python 파일, `lib/`에 호스트 Python 3.14용 기존 feed 의존성과 제품 패키지, `web/`에 252 vm-screens production build를 둔다. rootfs에는 별도 Python 3.13용 Playwright 및 pydantic 실행 의존성이 `/opt/xenova-kiosk-runtime/20261028/lib`에 있다. 서로 다른 Python의 native wheel을 섞지 않는다. 정확한 의존성 해시와 배포 manifest는 256 검증 자료를 참고한다.

- `/etc/xenova/display-256/feed.env`: PYTHONPATH, bindings 경로, web root, XENOVA_DISPLAY_TOKEN.
- `/etc/xenova/display-253/mqtt.env`: 기존 observer 읽기 전용 계정의 MQTT 인증 정보. 보드 진입점은 사용자명·암호가 없으면 시작을 거부한다. 익명 broker 접속 허용이나 ACL 변경은 하지 않는다.
- `feed-observations.conf.example`: 공유 모드의 수집기 opt-in 예시. 관리 서비스와 동일 경로를 지정하며 최종 실보드 검증 후 적용한다.
- `manager-observations.conf.example`: 관리 서비스에 공유 상태 파일 경로를 설정하는 opt-in 예시. 자동 적용하지 않는다. [적용·되돌리기 절차](../../../../../../docs/design/display-shared-observations.md)를 먼저 확인한다.
- `/etc/xenova/display-256/kiosk.env`: 같은 로컬 표시 토큰.
- `/etc/xenova/display-256/bindings.json`: 실제 UUID·역할을 확인한 deployment profile.
- 위 디렉터리 0700, 파일 0600. 토큰·비밀번호를 Git/URL/VITE 번들에 넣지 않는다.
- rootfs `/run/xenova-display-runtime.json`: 두 EDID·HDMI-1/2·1280×720 좌표와 loopback feed origin. 0644, 비밀 값 없음.
- rootfs `/run/xenova-display-Xorg.conf`: KMS modesetting, `/dev/dri/card0`, 전용 소프트웨어 렌더 설정.

rootfs의 `/run`은 호스트 `/run`과 다른 영속 디렉터리다. unit의 RootDirectory/MountAPIVFS 환경에 해당 `/run`과 `/tmp`를 명시적으로 bind한다. graphics가 매번 인증 파일·ready/status 경로를 준비하므로 이전 실행의 ready 파일을 부팅 성공 근거로 삼지 않는다.

## 운영 명령

```sh
systemctl start xenova-display.target
systemctl stop xenova-display.target
systemctl enable xenova-display.target
systemctl status xenova-display-feed xenova-display-graphics xenova-display-kiosk
journalctl -b -u xenova-display-graphics -u xenova-display-kiosk
```

부팅 자동 시작 해제와 표시만 중지는 다음과 같다. VM·Manager API 서비스는 중지하지 않는다.

```sh
systemctl disable --now xenova-display.target
```

실제 화면 적용 상태는 rootfs `/run/xenova-kiosk/display-runtime.json`에서 조회한다. 새 layout_epoch, 3초 미만 updated_monotonic, 각 출력의 physical_verified/renderer_alive/applied_revision을 함께 확인한다. start 명령 접수나 이전 부팅의 상태 파일만으로 성공으로 판정하지 않는다.

정상 종료 시 키오스크가 먼저 창과 idle 설정을 정리하고 그래픽 세션이 콘솔 VT/프레임버퍼를 복원한다. graphics 재시작 시 키오스크도 함께 다시 기동한다. 그래픽 강제 종료나 전원 차단까지 픽셀 단위 원본 복원을 보장하지 않는다.

## 검증 경계

2026-10-28 실제 두 HDMI에서 수동 기동, 제품 브라우저/감독 프로세스 장애 복귀(선행 시험), 기본 설정 정상 재부팅 후 자동 표시를 확인했다. 재부팅 후 별도 start 명령을 보내지 않았다. 사용자 육안 확인과 실제 출력 캡처를 남겼다. 실제 주행 데이터/앱 영상과 원격 전송 성능은 253·255·257의 별도 연동 범위다.
