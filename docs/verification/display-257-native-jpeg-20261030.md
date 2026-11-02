# 257 캡처·변환·JPEG 분리 계측과 가속 후보 — 2026-10-30

## 범위와 현재 적용 상태

사용자는 캡처·색상 변환·압축 비용을 분리 측정하도록 요청했고, 이후 `libjpeg-turbo`를 격리된 임시 폴더에서 비교하는 범위를 승인했다. 시스템 패키지·기존 서비스 교체, VM 조작, CPU affinity·부팅·MQTT·Tailscale 변경은 하지 않았다. **기본 인코더는 여전히 FFmpeg이고 가속 경로는 임시 후보다.** 라이브러리의 운영 배포나 257 전체 수락을 완료한 기록이 아니다.

제품 코드에는 수신자가 50ms마다 프레임을 조회하던 것을 새 프레임/무효화 알림으로 깨우는 보완을 추가했다. 알림은 모든 수신자에게 전달하며 최신 JPEG 1장만 보관한다. 새 프레임이 없어도 0.5초마다 인증·종료·연결 상태를 확인하고, 알림 후에도 인증·출력 유효성을 검사한다. 동기 진단 수집기에는 기존 주기 대기를 유지한다.

## 보드 상태

- 실제 두 HDMI, 각 1280×720의 계기판·인포테인먼트 화면을 사용했다. 별도 headless 페이지를 만들지 않았다.
- dom0 RAM은 3GiB 설정이다. 첫 측정 MemTotal 2,988,188kB, MemAvailable 2,662,696kB다.
- dom0는 vCPU 4개지만 hard affinity는 물리 CPU `0,3`이다. Debian도 `0,3`, Zephyr A/B는 각각 `1`, `2`로 고정돼 있었다. 배치를 변경하지 않았다.
- 최초 분리 시험에서 온도는 관측 표본 기준 39.433~43.329°C였다. cpufreq/governor 파일과 vcgencmd가 노출되지 않아 실제 CPU 주파수나 스로틀 여부는 확정하지 않는다.
- VM의 CPU 누적 시간도 조회했다. 시험 중 Zephyr A/B의 누적 CPU 시간 증가는 각각 약 1.1초였다. 다른 VM의 실행을 중지해 만든 조건이 아니다.

## 1. 기본 경로 분리 시험

각 사례를 한 번씩 순차 실행했다. FFmpeg 입력은 5 FPS이고 영상 시간 15초를 요청했다. 실제 실행에는 준비·종료 시간이 포함되므로 wall time은 약 17~18초다. CPU는 종료된 자식을 reap하기 전 `/proc/<pid>/stat`의 최종 사용자·시스템·회수된 자식 CPU tick을 읽고 실제 wall time으로 나눴다. `resource` 모듈이 없는 보드에서 추가 설치 없이 계측했다. 코어 환산은 dom0 CPU 회계값이며 별도 물리 실행 시간 계측이 아니다.

| 경로 | 출력 수 | 관측 평균 코어 환산 |
| --- | --- | --- |
| X11 캡처 → 압축 없이 버리기 | 1 | 0.104 |
| X11 캡처 → 압축 없이 버리기 | 2 | 0.225 |
| X11 캡처 → YUV 변환 → null frame | 1 | 0.384 |
| X11 캡처 → YUV 변환 → null frame | 2 | 0.862 |
| X11 캡처 → 제품 JPEG 설정 → 버리기 | 1 | 0.567 |
| X11 캡처 → 제품 JPEG 설정 → 버리기 | 2 | 1.499 |
| 저장된 BGRX → 제품 JPEG 설정 | 2 | 1.821 |
| 저장된 YUV → 제품 JPEG 설정 | 2 | 0.794 |
| 2560×720 한 번 캡처 → 두 영역 분리 → JPEG | 2 | 1.378 |

원본 1번 화면과 2번 화면은 복잡도가 다르며 각 경로의 필터·입력·종료 시간이 완전히 같지 않다. 숫자를 단순 차감해 색상 변환의 순수 비용이나 보장된 절감량으로 쓰지 않는다. 저장된 입력은 같은 실제 화면을 반복 재생한 사례이며 실제 캡처 수락을 대신하지 않는다. HTTP 송신기·PC 수신자는 이 표에 포함되지 않는다. 단순 캡처 공유만으로 1코어 목표를 달성하지 못했고, 변환·압축을 우선 개선할 근거를 확보했다.

## 2. 임시 라이브러리와 화질 비교

공식 Debian ARM64 `libturbojpeg0 2.1.5-4`를 PC에서 내려받아 공식 다운로드 페이지의 SHA256을 확인한 뒤 필요한 라이브러리와 저작권 파일만 추출했다. dpkg/apt 설치나 ldconfig를 실행하지 않았다. 라이브러리는 시험별 전용 경로에서 절대 경로로 로드하고 시험 후 보드에서 삭제했다.

- 패키지 SHA256: `bfce9b617b7de2e313b701de3ad3611b764613820d690592e3e96f3a62f02cdf`
- ARM64 ELF 라이브러리 SHA256: `d8e4a12770bcdbcf027a401836f062c8596336082103543b1ecdb97c763037e4`
- [공식 패키지·체크섬](https://packages.debian.org/trixie/arm64/libturbojpeg0/download)
- [TurboJPEG 2.1.5 API](https://github.com/libjpeg-turbo/libjpeg-turbo/blob/2.1.5.1/turbojpeg.h)

FFmpeg q=7과 TurboJPEG quality=85는 같은 숫자 체계가 아니다. 두 실제 출력에서 보관한 **같은 원본 BGRX 프레임**을 각각 압축하고 RGB 공간 PSNR·SSIM을 비교했다. 모두 1280×720·4:2:0이다.

| 이미지 | 기존 q=7 PSNR / SSIM | 후보 quality=85 PSNR / SSIM | JPEG 크기 기존 / 후보 |
| --- | --- | --- | --- |
| 계기판 | 40.685dB / 0.990508 | 42.317dB / 0.993303 | 28,036 / 47,998 bytes |
| 인포테인먼트 | 39.029dB / 0.985950 | 40.999dB / 0.990695 | 39,385 / 65,895 bytes |

이번 두 표본에서 후보가 원본에 더 가까웠고 저장된 JPEG를 직접 열어 글자·색상·출력 구분을 확인했다. 모든 화면·텍스트 영역·동영상에 대해 화질을 보장한 결과는 아니다. quality 90·95도 비교했으나 용량이 더 커서 PC 전송 비교에는 85를 사용했다.

저장 프레임 두 개를 5 FPS로 변환·압축하는 후보는 약 0.480코어였다. 원시 영상을 FFmpeg→Python pipe로 이동하는 초기 실제 캡처 후보도 시험했지만 준비·종료 대기가 포함된 평균을 그대로 정상 운전 CPU로 해석하지 않았다. 다음 후보에서는 큰 원시 프레임의 반복 전달을 제거했다.

## 3. 실제 X11 공유 메모리 캡처 후보

기존 X11의 MIT-SHM API로 실제 root 출력 사각형을 읽고 BGRX 픽셀 버퍼를 TurboJPEG에 직접 전달했다. Ctypes prototype은 ARM64 XImage/segment ABI, 32-bit little-endian BGRX mask와 stride를 확인하며 지원하지 않는 구성을 거부한다. 같은 root source의 두 영역을 독립 작업자로 캡처한다. 기존 [X.Org API 절차](https://www.x.org/releases/X11R7.7/doc/xextproto/shm.html)를 따르고, 공유 메모리는 UID 소유 0600으로 생성해 서버 부착 확인 후 제거 예약한다. 정상 종료 시 서버 detach·이미지 해제·client detach를 수행한다.

동일한 실제 두 화면의 별도 비교에서 기존 FFmpeg JPEG는 약 1.451코어, 공유 메모리+TurboJPEG quality85 후보는 약 0.516코어, quality90 후보는 약 0.531코어였다. 이 값은 캡처·압축 작업자와 자식 프로세스의 비용이며 HTTP 송신기를 포함하지 않는다. 새로운 framebuffer나 독립 UI를 전송하는 방식이 아니다.

## 4. PC 전송 포함 비교

기존 제품 Capture의 출력 검증·최신 JPEG 보관·별도 인증·multipart HTTP·SSH 수신 경로를 재사용하고, 캡처 실행 명령만 임시 후보로 연결했다. 출력별 source/rectangle/epoch는 유지한다. API 송신기는 기존 호스트 Python/HTTP 의존성을, 후보 작업자는 기존 그래픽 rootfs Python/X11과 임시 라이브러리를 사용한다.

프레임 알림 보완 전의 가속 후보 60초 수신에서 CPU는 송신기 0.300코어+native 작업자 0.528코어=**0.828코어**였다. 프레임 계측 구간은 43.279초이며 Chromium 0.458코어와 Xorg 0.143코어는 위 합계에서 제외한다. 전체 dom0가 0.828코어만 사용한다는 뜻이 아니다.

출력별 PC 수신은 294장/60.094초=4.892 FPS, 296장/60.062초=4.928 FPS였고 JPEG payload 합계는 **4.463Mbps**였다. 최소 MemAvailable은 표본 구간 기준 약 2076.8MiB다. 인증 없는 상태 조회 401, 수동 재접속 51장/10.140초, 클라이언트 종료 후 kiosk/graphics active를 확인했다. 시작·종료 capture epoch도 유지됐다. 저장 JPEG를 직접 열어 두 화면의 내용과 글자·색상을 확인했다.

이 짧은 정적 UI 시험에서는 CPU·JPEG payload 목표 범위에 들었지만 설정 5 FPS를 관측 5.000 FPS 이상으로 확정한 결과는 아니다. 원격 브라우저의 실제 표시 지연이나 변화하는 영상의 재접속 최신성도 별도 측정이다.

프레임 알림 보완 후 10월 30일 FFmpeg 경로의 송신기 CPU는 약 0.192코어였다(기존 표본 약 0.248코어). 캡처·압축 포함 합계는 약 1.643코어여서 이 변경만으로 전체 목표에 도달하지 않았다. 마지막 가속 후보 비교는 로그인 전 사용 한도로 중단됐고, 11월 2일 실제 보드 상태를 새로 확인해 재개했다. [11월 2일의 같은 환경 비교·원복 확인](display-257-native-jpeg-20261102.md)을 현재 최종 결과로 사용한다.

## 경계·원복·남은 일

- 분리·화질·공유 메모리 비교 시험은 SSH 포워딩 정책을 변경하지 않았다. PC 전송 시험만 승인된 loopback 8083 임시 허용을 사용하고 종료 후 정책 원본 해시를 확인해 복원했다.
- 중간 PC 전송 재로그인 시간 초과는 원복 스크립트와 별도 재접속으로 SSH 정책·시험 폴더 정리를 확인한 뒤 다시 시험했다. 성공 결과에 합산하지 않는다.
- 각 완료 시험에서 그래픽 감독 원본 복원, 임시 프레임·라이브러리 삭제와 VT1 복귀를 확인했다. 키오스크/그래픽은 시험 전 inactive로 돌아갔고 API·feed·MQTT·Tailscale은 active다.
- 기본 FFmpeg 실행 경로와 운영 서비스 설정은 유지한다. 임시 native 후보는 기본값으로 활성화하지 않았다. 900프레임 제한과 진단 통계를 갖는 시험용 작업자이므로 그대로 상시 서비스에 배치하지 않는다.
- 운영용 backend로 정리·의존성 배포, 장애/분리/재접속 경계 검사, 장시간 및 동적 화면 부하, 실제 원격 표시 지연은 남는다. 257을 완료 전환하지 않는다.

## 보관 자료

- [9개 경로 분리 계측](evidence/display-257-native-20261030/stage-isolation.json.gz)
- [화질·TurboJPEG 비교](evidence/display-257-native-20261030/turbo-calibration.json.gz)
- [직접 공유 메모리 캡처 비교](evidence/display-257-native-20261030/xshm-comparison.json.gz)
- [알림 보완 전 가속 후보 PC 전송](evidence/display-257-native-20261030/turbo-before-notification.json.gz)
- [알림 보완 후 FFmpeg PC 전송](evidence/display-257-native-20261030/ffmpeg-with-notification.json.gz)
- [기존 계기판 JPEG](evidence/display-257-native-20261030/baseline-0.jpg) / [후보 JPEG](evidence/display-257-native-20261030/offline-85-0.jpg)
- [기존 인포테인먼트 JPEG](evidence/display-257-native-20261030/baseline-1.jpg) / [후보 JPEG](evidence/display-257-native-20261030/offline-85-1.jpg)
