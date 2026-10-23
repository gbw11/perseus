# 표시 파이프라인 4단계 — 출력 실행기·화면 전송

2026-10-22 · `docs/platform/S15P31E110-250-display-contract` 브랜치.

## 구현한 경로

```text
dom0 상태 피드 → 로컬 화면 정책 → Chromium 창 2개 → X11 두 출력
                                                       │
                                  출력별 실제 root 픽셀 캡처(FFmpeg)
                                                       │
                            JPEG 인코딩 → HTTP MJPEG → 시연 뷰어 canvas
```

외부 대시보드를 원격 PC에서 새로 그리는 방식이 아니다. 실제 X11 루트 화면의 지정된 두 사각형을 각각 캡처해서 보내고, 수신기는 JPEG 픽셀을 디코딩해 표시한다. VM 제어/키보드 입력은 원격 뷰어에 제공하지 않는다.

## 기술 선택과 구현 범위

X11/XRandR + Chromium을 첫 실행 백엔드로 구현했다. [XRandR 공식 문서](https://xorg.freedesktop.org/archive/X11R7.5/doc/man/man1/xrandr.1.html)의 connector/mode/pos 설정과 [FFmpeg x11grab 공식 문서](https://www.ffmpeg.org/ffmpeg-devices.html#x11grab)를 기준으로 한다. 브라우저 실행/세션 준비는 [Playwright Python API](https://playwright.dev/python/docs/api/class-browsertype)를 사용한다. Weston/Wayland와 이 백엔드를 혼합해서 지원한다고 주장하지 않는다.

| 구성 | 구현 |
| --- | --- |
| 출력 설치 매핑 | 두 connector, EDID SHA256, 해상도·좌표를 명시. 중복/겹침·외부 feed origin 거부 |
| 출력 초기화 | 실제 연결/EDID/지원 모드를 검사하고 XRandR 모드·위치 적용 후 재조회 |
| 로컬 실행기 | 출력별 별도 Chromium 컨텍스트/창, 위치 확인, 화면용 쿠키 준비·갱신, 실패한 출력만 재시도 |
| 분리/재연결 | 잘못된 EDID/분리된 출력은 표시·캡처 준비 상태로 인정하지 않음. 다른 출력은 유지 |
| 실행 관측 | 개인 권한의 원자적 runtime JSON, 출력별 상태·좌표·DOM revision. virtual은 physical_verified=false |
| 캡처/전송 | 출력별 독립 FFmpeg JPEG 인코더, 최신 프레임 1장 보관, 정지/실패 후 재시도 |
| 원격 수신 | 별도 접속 키/HttpOnly 쿠키, 실제 수신·디코드가 멈추면 마지막 프레임 표시, 재연결 |
| 서비스 경계 | 키오스크와 송신기를 별도 실행 프로그램/서비스 템플릿으로 제공. 상호 Requires/BindsTo 없음 |

캡처 프레임과 특정 DOM render_revision의 일대일 대응은 증명하지 못하므로 `render_correlation=unverified`, 프레임의 `render_revision=null`로 보고한다. 별도 `reported_dom_revision`은 당시 실행기가 보고한 값일 뿐 프레임의 정확한 내용 ID가 아니다.

현재 전송 기본값은 **1280×720, 출력별 5 FPS, 소프트웨어 MJPEG**다. 1단계의 10 FPS/지연/CPU 목표는 아직 확정 성능이 아니며, 실보드 측정 전 이를 달성했다고 처리하지 않는다. H.264/WebRTC·하드웨어 인코딩·음성·원격 입력은 구현하지 않았다.

## 검증 환경과 경계

실제 QEMU dom0에는 DRM/물리 HDMI 출력이 없다. 이번 시험은 WSL에 별도 Xvfb 루트 화면(2560×720)을 만들고, 두 Chromium 창을 각각 1280×720 영역에 배치했다. 기존 WSLg 디스플레이는 변경하지 않았으며 별도 local/abstract 소켓을 사용했다. 실보드의 커넥터·EDID·Xorg/그래픽 드라이버·풀스크린 창 관리자 경로는 아직 시험하지 못했다.

VM 데이터는 실제 QEMU Xen을 SSH로 읽었다. 캡처/원격 송수신은 WSL 가상 화면에서 실행했으며 별도 물리 시연 PC/네트워크의 성능을 측정한 결과가 아니다. QEMU VM 시작/정지/재시작은 수행하지 않았다.

도구: `platform/services/display/router/tests/probe_display_runtime.py`.
단위/회귀: 수집기 디렉터리에서 `python -m pytest`.

매핑·EDID·프레임 파서·캡처 원본·인증 테스트를 포함해 41개를 통과했다. 가상 화면 수락 시험 9개 항목도 통과했고 [최종 증거](display-step4-runtime-evidence.json)에 결과를 기록했다. 시험 표식은 두 네이티브 창에만 주입한 색상 패치이며, 수신 화면에서 각 표식이 해당 출력에만 나타나는 것으로 원본 경로를 확인했다. 표식 제거 후 일반 시연 화면도 별도로 캡처했다.

통과 항목은 두 native 창 배치, 실제 QEMU 상태 표시, 두 root 영역의 FFmpeg 프레임 생성, 시연 키 인증과 두 영상 디코드, 출력별 색상 표식 일치, 한 인코더 재시작 시 다른 인코더/로컬 창 유지, 송신 서비스 종료 시 로컬 창 유지/원격 stale 표시, 원격 브라우저 오류 없음, QEMU VM 실행 ID 유지다.

5초 구간의 인코더 카운터로 출력별 약 5 FPS를 관측했다. JPEG payload는 모니터 1 약 3.016 Mbps, 모니터 2 약 2.860 Mbps(합계 약 5.9 Mbps)였다. HTTP/SSH 오버헤드나 CPU·메모리·종단 지연 p95 측정값은 아니다. 이 수치를 Raspberry Pi 4 성능이나 별도 시연 PC 네트워크 성능으로 사용하지 않는다.

## 실보드에서 남은 확인

- 실제 dom0 이미지에 Xorg/XRandR, Chromium, Playwright 실행 환경, FFmpeg x11grab/MJPEG, 한글 글꼴이 준비돼 있는지.
- 두 실제 HDMI 모니터의 EDID/connector와 모드, 풀스크린 창 위치, 재연결·브라우저 재시작 시 각 화면 유지.
- Raspberry Pi 4의 CPU·메모리·프레임 드롭·지연·대역폭과 원격망 단절 중 로컬 화면 유지.
- 10분 이상 연속 실행과 송신/수신/키오스크 프로세스 단위 장애/복구. 5단계 통합 검증 대상으로 남긴다.

배포 템플릿은 추가했지만 실제 보드 서비스에 설치하거나 Yocto 이미지를 변경하지 않았다. 이 단계의 가상 화면 검증을 물리 HDMI 2출력 완료로 해석하지 않는다. Jira·platform 브랜치를 변경하지 않고, 커밋·푸시도 수행하지 않는다.
