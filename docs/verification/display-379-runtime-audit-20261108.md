# 379 최신 이미지 영상 전송 사전 점검 — 2026-11-08

SSH로 설치 상태와 실행 준비 상태를 확인했다. **사전 점검은 끝났지만 379의 두 HDMI 영상 전송·성능 수용 시험은 완료하지 않았다.** 모니터가 연결되지 않았고 X11·키오스크·인코더가 실행 중이지 않아 실제 영상 지연·전송량·인코딩 부하는 측정하지 않았다.

## 기준과 작업 범위

- 기준: `origin/platform`의 `73709a34`, 작업 브랜치 `feat/platform/S15P31E110-379-runtime-audit`.
- 보드: Raspberry Pi 4 Model B Rev 1.5, aarch64, Xenova 6.0.4 (wrynose), 커널 6.18.33-v8.
- OS 버전만으로 최신 이미지의 정확한 빌드 커밋까지 증명할 수는 없다.
- 조회와 TurboJPEG 압축 핸들 생성·해제만 수행했다. 영상 캡처·인코딩은 시작하지 않았다.
- 보드 파일·설정·VM·펌웨어·CPU 배치는 변경하지 않았다. 설치, API/Worker 재시작, SSH 허용 포트 변경도 하지 않았다.

## 설치 및 소스 확인

| 항목 | 확인 결과 | 확인 범위 |
| --- | --- | --- |
| xset | 1.2.5 | 버전 조회만 수행, DPMS/화면 꺼짐 설정 변경 없음 |
| Xorg | 1.21.1.24 | 실행 파일 버전 확인, X 서버 기동 없음 |
| Chromium | 152.0.7977.82 | 보드 브라우저 버전 확인 |
| FFmpeg | 8.0.3 | MJPEG 인코더 목록 확인 |
| Python | 3.14.7 | 실행 환경 확인 |
| TurboJPEG | libturbojpeg.so.0 로드 성공 | 필수 6개 심볼 존재, 압축 핸들 생성·해제 성공 |

TurboJPEG 실제 파일은 `/usr/lib/libturbojpeg.so.0.4.0`이고 SHA256은 `afd8901b761c01ba1d7bac46d9d78935b80e77bd83b21d4b691c11a0dd399cd5`이다. 파일명의 0.4.0을 라이브러리 제품 릴리스 버전으로 해석하지 않는다. 패키지 버전 메타데이터는 이번 조회로 확정하지 못했다.

설치된 `xenova_display`의 main, outputs, kiosk, native_session, native_capture, native_jpeg, native_x11, runtime_status, stream, display_mode 총 10개 모듈은 기준 platform 소스와 바이트 SHA256이 모두 일치했다. 모듈별 해시는 동반 JSON에 기록했다. 이것은 해당 10개 파일의 일치 근거이며 이미지 전체의 최신성을 보증하지 않는다.

## 현재 실행을 막는 준비 항목

HDMI 서비스의 `ConditionResult=no`, 상태는 inactive/dead이다. 서비스가 요구하는 파일 중 다음 세 개가 없다.

- `/etc/xenova/display-runtime.json`
- `/etc/xenova/display-Xorg.conf`
- `/etc/xenova/display-kiosk.env`

`/etc/xenova/display-bindings.json`과 `/etc/xenova/display.env`는 root 소유 0600으로 존재한다. 인증값이나 파일 내용은 보고서에 포함하지 않았다.

`/usr/share/xenova/display/examples`에 설정 예제가 설치돼 있다. 예제의 출력/EDID 값은 장비별 설정을 대신하지 않으므로 그대로 배치하지 않았다. 실제 두 모니터의 커넥터·EDID·해상도와 선택할 인코더를 확인해 설정해야 한다. 현재 설정이 없으므로 채택 FPS나 활성 인코더도 확정할 수 없다.

`/sys/class/drm`에는 version만 있고 `/dev/dri`와 DRM 커넥터 노드는 없다. X11 소켓과 표시/영상 전송 프로세스도 없다. 부팅 설정에는 `dtoverlay=vc4-kms-v3d`가 있고 vc4는 modules.builtin에 기록돼 있다. 따라서 vc4.ko 파일이 없다는 이유만으로 드라이버 미설치라고 판단하면 안 된다. 장치 노드가 없는 원인은 이번 조회만으로 확정하지 않았으며 overlay·모듈·부팅 설정을 임의로 수정하지 않았다.

kiosk와 stream 유닛은 `/usr/lib/systemd/user/`에 설치돼 있다. 시스템 관리자의 `systemctl show`에서 not-found가 나오는 것을 패키지 누락으로 해석하지 않는다. 실제 사용자 세션 및 영상 전송 실행 경로는 다음 배치 때 확인해야 한다. 현재 8082 표시 API는 loopback에서 수신 중이고 8083 영상 수신 포트는 관측되지 않았다.

## 조회 전후 보존 확인

같은 부팅 상태였다. 표시 feed, Manager API, Mosquitto, Tailscale의 PID는 조회 전후 같았고, HDMI와 Recovery Worker는 처음부터 끝까지 inactive였다. Gateway는 active 상태였지만 PID가 856290에서 858989로 바뀌었다. 이번 작업에서 재시작 명령은 실행하지 않았으며 그 변화의 원인은 조사하지 않았다. 동시 작업 여부나 시스템 전체 무변경을 PID 비교만으로 단정하지 않는다.

## 379 최종 수용 시험에 남은 것

1. 두 HDMI를 연결하고 실제 DRM 커넥터·EDID·사용 가능한 모드를 확인한다. 장치 노드가 없다면 이미지/부팅 담당과 원인을 확인한다.
2. 기존 인증·바인딩을 유지하면서 누락된 세 설정 파일과 실제 키오스크/송신기 실행 구성을 검토한다. 기동·설정 변경은 이번 읽기 전용 범위에 포함되지 않았다.
3. 각 출력 1280×720, 채택 FPS, TurboJPEG 실제 경로를 기록하고 계기판·인포테인먼트와 원격 브라우저 수신을 확인한다.
4. 실제 영상 표식으로 브라우저 표시 지연 p95 ≤500ms, JPEG 합계 ≤8Mbps, 송신기+인코더 평균 CPU ≤1코어를 측정하고 RAM 추이도 기록한다. REST 응답 시간을 영상 지연으로 대체하지 않는다.
5. 결과를 목표별 통과/실패로 기록한 다음 379 완료 여부를 결정한다. 이후 380의 10분 연속 전송·HDMI 분리/복귀 시험으로 이어간다.

과거 시험 기록 `display-257-browser-acceptance-20261102.md`는 측정 방법 참고용이다. 과거 결과를 현재 이미지의 성능 통과 근거로 재사용하지 않는다.

## 증거

동반 파일: `display-379-runtime-audit-20261108.json`. 로컬 원본 조회 결과는 `outputs/display-379-audit-20261108/`에 보관했다. 저장소 보고서에는 비밀 환경변수의 값·내용·해시를 포함하지 않았다.
