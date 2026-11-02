# 257 프레임 알림·임시 JPEG 가속 최종 비교 — 2026-11-02

## 결과와 적용 경계

**새 프레임 알림과 임시 가속 후보를 조합한 두 실제 HDMI→PC 전송에서 CPU 0.643코어, 수신 4.960/4.984 FPS, JPEG 합계 4.518Mbps를 관측했다.** 기존 방식의 같은 날 비교는 1.759코어, 4.390/4.310 FPS, 합계 2.345Mbps였다. 이번 짧은 정적 UI 표본에서 후보는 CPU 평균 1코어·JPEG 8Mbps 목표 안에 들었으나 관측 FPS가 엄밀하게 5.000 이상인 것은 아니다. 정식 성능 수락과 257 전체 완료를 주장하지 않는다.

제품에는 `stream.py`의 프레임 알림 보완과 회귀 검사를 작성했다. 기존 기본 인코더 FFmpeg와 공개 HTTP/인증 규격은 그대로다. **native 인코더는 운영 backend로 편입하거나 보드에 상시 배포하지 않은 시험용 후보**다. 사용자가 허용한 임시 디렉터리에서 라이브러리를 로드해 기존 Capture의 실행 명령만 어댑터로 연결했고 종료 후 삭제했다. `[900프레임 제한 + 진단 통계]`가 있는 prototype을 그대로 상시 서비스에 올리면 안 된다.

## 재개 시 확인한 환경

10월 30일 마지막 비교는 SSH 로그인 전 한도로 중단됐다. 그 실행은 원격 설정을 변경하는 단계까지 가지 않았다. 11월 2일 기존 호스트키를 확인하고 SSH 조회를 다시 수행했다.

- 실제 두 HDMI가 connected이고 기존 kiosk/graphics/feed/API/MQTT/Tailscale이 active였다.
- 기존 X11 세션 :5, 1280×720 출력 두 개와 layout epoch `50826224-8795-4568-88b8-c1f787dcb615`를 사용했다.
- MemTotal 2,918,184kB, 최초 MemAvailable 2,078,924kB였다. 이전 날짜의 메모리·프로세스 상태와 같다고 가정하지 않았다.
- dom0 vCPU 4개가 physical CPU 0·3에 제한되고 Debian도 0·3, Zephyr A/B는 각각 1·2인 구성을 조회했다. affinity·VM 실행 설정은 변경하지 않았다.
- 이미 실행 중인 그래픽 세션과 소스 파일을 그대로 재사용했다. 그래픽 파일 patch, VT 전환, HDMI/Kiosk 재시작을 하지 않았다.

이전 날짜와 조건 차이를 구분하려고 가속 후보와 FFmpeg 기본 경로를 같은 현재 환경에서 각각 재시험했다. 순차 단일 실행이며 무작위 순서·반복 통계·동적 영상 부하 시험은 아니다.

## 실제 PC 수신 비교

두 시험 모두 프레임 알림 보완이 있는 동일 `Capture`·인증·multipart HTTP·SSH 수신 코드를 사용했다. JPEG 캡처/압축 명령만 다르다. 둘 다 실제 X11 root 출력 영역을 사용하고, 별도 headless UI를 렌더하지 않는다.

| 항목 | FFmpeg 기본 | MIT-SHM + TurboJPEG 후보 |
| --- | --- | --- |
| 목표 설정 | 각 1280×720·5 FPS·q=7 | 각 1280×720·5 FPS·quality=85 |
| CPU 계측 구간 | 47.497초 | 40.495초 |
| HTTP 송신기 평균 CPU | 0.193코어 | 0.166코어 |
| 캡처·변환·압축 작업자 평균 CPU | 1.566코어 | 0.478코어 |
| 송신기+작업자 합계 | **1.759코어** | **0.643코어** |
| PC 출력 1 수신 | 264장/60.141초 = 4.390 FPS | 298장/60.079초 = 4.960 FPS |
| PC 출력 2 수신 | 259장/60.094초 = 4.310 FPS | 300장/60.188초 = 4.984 FPS |
| JPEG payload 합계 | 2.345Mbps | 4.518Mbps |
| 표본 구간 최소 MemAvailable | 1752.9MiB | 1923.1MiB |
| 인증 없는 상태 조회 | HTTP 401 | HTTP 401 |
| 출력 1 재접속 | 51장/10.125초, 오류 없음 | 51장/10.156초, 오류 없음 |
| 클라이언트 종료 후 기존 HDMI 서비스 | kiosk/graphics active | kiosk/graphics active |

CPU는 `/proc`의 사용자·시스템 tick과 실제 단조 시각 차이를 이용한 코어 환산 가중 평균이다. 송신기와 **모든 native 작업자·FFmpeg 자식·기타 pipeline 자식**을 합산했다. native Python 작업자를 누락해 0으로 계산하지 않았다. Chromium·Xorg·진단 sampler CPU는 합계에서 제외한다. 따라서 이 수치를 전체 dom0 CPU로 해석하지 않는다. CPU·가용 메모리 표본 구간은 PC 60초 전체와 다르며 최소 메모리도 표본 구간 값이다.

표본상 pipeline CPU는 약 63% 낮았으나 이 비율을 다른 이미지·부하·시간대에 보장하지 않는다. 기본 FFmpeg가 더 작은 JPEG를 만들었다는 차이도 있다. q=7과 quality=85의 숫자는 서로 대응하지 않으며, [같은 원본에 대한 사전 PSNR/SSIM·크기 비교](display-257-native-jpeg-20261030.md)에서 이번 두 UI의 후보 화질이 기존보다 높음을 확인한 뒤 사용했다. 일반 동영상·임의 게스트 화면의 화질 보장을 대신하지 않는다.

PC는 JPEG bytes를 연속 수신하고 저장한 표본을 이미지로 열어 1280×720의 두 화면 내용·색상·한글을 확인했다. 모든 프레임의 디코드/화면 표시 시간을 측정한 시험이 아니며 프레임 간격·`frame_age_ms`를 종단 지연으로 대체하지 않는다. 재접속 시험이 정적 UI였으므로 변화하는 화면의 최신성도 별도 수락이 남는다.

## 프레임 알림 동작과 회귀 검사

기존 50ms 주기 snapshot 조회를 actual Capture의 프레임 생성·무효화 알림 대기로 바꿨다. 수신자가 기다릴 이벤트를 먼저 구독하고 최신 snapshot을 읽어 중간 publish의 알림 손실을 피한다. 하나의 알림을 여러 클라이언트가 소비하지 않고 모두 깨어나며 과거 JPEG 큐를 만들지 않는다. 알림이 없어도 최대 0.5초마다 인증·종료·연결 상태를 재확인한다. 출력 유효성·신선도 검사는 프레임을 제공하기 전 그대로 수행한다.

`test_outputs.py`, `test_frame_signal.py`의 **17개 검사 통과**를 11월 2일 재확인했다. 캡처 thread→두 수신자 broadcast, 늦은 구독의 busy loop 방지, 구독/대기 사이 publish, 출력 무효화 시 프레임 미제공, 인증 제거 후 새 프레임 차단과 event loop 종료 후 알림을 확인했다. 동기 진단 수집기의 기존 주기 대기는 유지한다. Python compile·diff whitespace 검사도 수행했다.

## 원복과 종료 확인

PC 전송 동안만 기존 SSH PermitOpen에 loopback 8083을 임시 추가하고 설정 검사·reload 후 새 세션으로 재인증했다. 기존 게스트 SSH·API 포트 허용 목록을 보존했다. 종료 시 **SSH 정책 원본 해시 일치, 그래픽 파일 해시 불변, VM UUID/domID 불변, 임시 stage/환경 파일/라이브러리 삭제**를 확인했다. API·VM·MQTT·Tailscale·부팅·CPU affinity 변경이나 재시작은 하지 않았다.

마지막 독립 조회에서 영상 포트 8083 listener와 임시 허용은 없고 시험 프로세스도 없었다. kiosk/graphics/feed/API/MQTT/Tailscale 모두 active다. 남은 UID1000 SysV segment의 생성 PID 1513·1730은 사전 조회에 있던 Chromium이며 생성 시각도 시험 전이다. 이를 시험 누수로 오인해 삭제하지 않았다. graphics source SHA256은 시작·종료 모두 `3a97e594403fea7f6f1dfc142377380378a3c25156dbaf1bb10484e5a8226067`이다. 시험 SSH 연결은 종료했다.

## 남은 작업

1. native 후보를 상시 backend로 정리하고 라이브러리·ABI·설정·프로세스 수명 관리와 배포 방식을 확정한다. 기본 경로를 임의로 바꾸거나 prototype의 900프레임 제한을 숨기지 않는다.
2. hotplug·송신기 실패/재시작·동적 화면의 재접속 최신성과 장시간 부하를 최종 구성으로 확인한다.
3. 원격 브라우저 디코딩·표시를 포함한 실제 지연 p95 500ms를 별도로 측정한다. 현재 CPU·수신 FPS 개선만으로 지연 통과를 주장하지 않는다.

검증 당시 작업 브랜치는 `feat/platform/S15P31E110-257-dual-hdmi-verification`이었다. 이전 MR이 반영된 뒤 제출 변경은 최신 `origin/platform` 기반의 `feat/platform/S15P31E110-257-frame-signal-native-verification`에 분리했다. `platform`을 직접 변경하거나 이 검증으로 Jira 257을 완료 전환하지 않았다.

## 증거와 재현 조건

- [가속 후보 요약](evidence/display-257-native-20261102/turbo-with-notification-summary.json) / [전체 수신·계측·원복 기록](evidence/display-257-native-20261102/turbo-with-notification.json.gz)
- [같은 날 FFmpeg 요약](evidence/display-257-native-20261102/ffmpeg-with-notification-summary.json) / [전체 기록](evidence/display-257-native-20261102/ffmpeg-with-notification.json.gz)
- [사전 조회](evidence/display-257-native-20261102/preflight_20261102.json.gz) / [독립 종료 조회](evidence/display-257-native-20261102/postflight_20261102.json.gz)
- [PC 수신 계기판](evidence/display-257-native-20261102/monitor-1.jpg) / [PC 수신 인포테인먼트](evidence/display-257-native-20261102/monitor-2.jpg)
- [라이브러리 URL·SHA256·ARM64 정보](evidence/display-257-native-20261102/library-provenance.json) / [후보 소스 hash](evidence/display-257-native-20261102/source-hashes.json)
- [PC 진단 controller](evidence/display-257-native-20261102/dual_physical_20261102.py.gz), [단계 비교 runner](evidence/display-257-native-20261102/split_runner_20261030.py.gz), [단계 비교 controller](evidence/display-257-native-20261102/run_split_20261030.py.gz), [native worker](evidence/display-257-native-20261102/turbo_worker_20261030.py.gz), [X11 binding](evidence/display-257-native-20261102/xshm_capture_20261030.py.gz), [다운로드/검증](evidence/display-257-native-20261102/prepare_turbojpeg.py.gz), [집계](evidence/display-257-native-20261102/summarize_stream_trial.py.gz), [기존 pinned SSH helper](evidence/display-257-native-20261102/board_prepare.py.gz).

압축된 소스는 당시 진단 절차 기록이다. controller의 worktree·rootfs·호스트키·접속 주소를 실제 검증 환경에 맞춰 확인하고, 테스트 라이브러리를 공식 checksum으로 준비해야 한다. standalone 설치 도구나 범용 운영 서비스로 배포하지 않는다. 압축 소스에 비밀번호·시연 토큰·개인 키는 없으며 라이브러리 binary도 Git에 넣지 않는다.
