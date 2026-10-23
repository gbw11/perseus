# 표시 파이프라인 5단계 — 통합·장애 복구·연속 동작

2026-10-22 · `docs/platform/S15P31E110-250-display-contract`.

## 검증 범위

실제 QEMU Xen 조회 → 상태 정규화 → 표시 정책 → 별도 Xvfb의 Chromium 창 두 개 → FFmpeg root 픽셀 캡처 → HTTP MJPEG → 수신 Chromium canvas 경로를 시험한다. 물리 HDMI·실제 Pi 출력이 아닌 WSL 가상 X11 시험이다. 외부 Spring의 REST/WS 연결은 [2단계](display-step2.md)의 검증 범위이며 이번 장시간 시험 경로에는 포함하지 않는다.

VM 시작/정지/재시작이나 주행 명령을 실행하지 않는다. 기존 QEMU VM과 기존 WSLg X 디스플레이는 시험 대상의 장애 주입에서 제외한다. 실제 게스트 앱 영상 소스·AI 보고·제어/복구 발행자 연결은 아직 미완료다.

## 시험 구성

도구는 [runtime probe](../../platform/services/display/router/tests/probe_display_runtime.py)와 [resilience probe](../../platform/services/display/router/tests/probe_display_resilience.py)다. `--soak-seconds 600`으로 장애 복구 및 10분 시험을 명시적으로 실행한다. 시험용 `psutil`이 필요하며 일반 제품 서비스 의존성에는 추가하지 않았다. [README](../../platform/services/display/router/README.md)에 재현 명령을 기록했다.

| 시험 | 구분 | 확인할 결과 |
| --- | --- | --- |
| 두 native 창·전체/상세·QEMU 상태 | 실제 가상 X11 + 실제 Xen 조회 | 두 사각형 배치, VM 세 개, 기대 OS와 관측 근거 구분 |
| 원격 영상 원본 | 실제 FFmpeg/HTTP/디코드 | 각 native 창의 다른 색상 표식이 대응하는 원격 canvas에만 도착 |
| 인코더 종료 | 실제 시험용 FFmpeg 종료 | 해당 인코더만 재시작, 다른 인코더·로컬 창 유지 |
| Xen 조회 단절 | adapter에서 ConnectionError 주입 | Xen source 단절·stale 화면, 정상/VM 정지로 오인하지 않음. 예외 해제 후 실제 조회 복귀 |
| 로컬 feed 경로 단절 | 두 로컬 브라우저의 presentation HTTP 차단 | 창 유지·마지막 값 표시, 차단 해제 후 재접속 |
| 한쪽 창 종료 | 실제 시험용 Chromium context 종료 | 해당 출력만 새 context 생성, 다른 로컬 창 유지·캡처 재개 |
| 원격망 단절 | 수신 브라우저 offline/online 에뮬레이션 | 원격 stale 안내, 로컬 창과 인코더 진행, online 후 자동 복귀 |
| 영상 지연 | 같은 호스트의 20개 표식 변화 | 로컬 변경부터 원격 픽셀 관측까지 단조 시계 측정 |
| 10분 연속 전송 | 5초 간격 관측 | 두 프레임/로컬 창/상태 HTTP/원격 수신 유지, CPU·RSS·FPS·바이트 원본 저장 |
| 송신기 종료 | 시험용 송신 서버 graceful shutdown | 원격 마지막 프레임 안내, 두 로컬 창 유지 |
| 종료 정리 | 시험 소유 프로세스만 종료 | 원격 브라우저 오류 없음, 실제 QEMU VM UUID·domain ID 유지 |

`live`는 표시/전송 경로의 준비 상태이며 게스트 앱·AI·모터가 정상이라는 주장이 아니다. source별 미수신/지연은 화면에 계속 표시한다. 연속 시험은 5초마다 표본을 확인하므로 모든 순간의 프레임 무누락이나 잠깐의 단절이 전혀 없었다는 보장은 아니다.

## 자동 회귀

Python 44개와 프론트 display/telemetry 22개 테스트를 통과했다. Python에는 수집 정규화 → 표시 정책 → 인증 REST/WS에 걸친 합성 전환 시험을 추가했다. AUTO→MANUAL이 VM1을 바꾸지 않고, VM1 crash만으로 VM2를 선택하지 않으며, 권위 control이 VM2를 확인한 뒤 follow_active가 전환하는지 확인했다. 새 domid에서 이전 앱 정상/ACK가 지워지는 시험, XRandR 응답을 모의한 분리/재연결 회귀도 포함한다.

이 합성 테스트는 실제 차량 승계나 물리 케이블 재연결의 증거가 아니다. Python TestClient의 기존 Starlette/httpx deprecation 경고는 남아 있지만 실패는 없다.

## 측정 결과

통합 수락 항목 **15개 통과**. 2026-10-22 11:20 KST에 최종 결과를 기록했다.
[최종 evidence](display-step5-runtime-evidence.json)와 [5초 간격 원본 120개](display-step5-soak-samples.jsonl)를 보관한다. 스크린샷은 작업공간 `outputs/display-step5/`에 있다. 시험 전후 실제 QEMU의 세 VM UUID·domain ID는 동일했고, 시험용 창·서버·Xvfb를 종료했다.

| 관측 | 결과 |
| --- | --- |
| 연속 구간 | 600.0초, 120개 표본 모두 두 캡처 live/두 원격 수신/두 로컬 상태 채널 연결 |
| 로컬 창 | 연속 구간 중 두 context 교체 없음 |
| 출력별 평균 FPS | 모니터 1: 5.0 / 모니터 2: 5.0 |
| JPEG payload | 3.017 + 2.860 = 5.877 Mbps |
| 전체 가상 시험 CPU | 1코어 100% 기준 평균 24.33%, 표본 최대 28.28% |
| 전체 가상 시험 RSS | 처음 2875.59 MiB → 마지막 2917.15 MiB, 최대 2944.81 MiB |
| 시각 변화 지연 | 표본 20개, nearest-rank p95 424.3ms, 최대 427.9ms |
| Xen 조회 장애 해제 후 fresh 복귀 | 약 3.418초 |
| 로컬 HTTP 차단 해제 후 연결 | 약 1.038초 |
| native context 종료 후 화면/전송 복귀 | 약 1.028초 |
| 원격 online 복귀 후 두 화면 연결 표시 | 약 1.037초 |

복구 시간은 이 한 번의 시험에서 정의한 상태 관측까지의 값이며 복구 SLA가 아니다. 메모리는 마지막 값이 처음보다 약 42 MiB 높았고, 이 길이의 시험으로 메모리 누수 부재를 증명하지 않는다. 10 FPS로 시험하지 않았고 초기 목표 달성으로 처리하지 않는다.

측정 CPU/RSS는 Python·수집기·Xvfb·로컬 Chromium·FFmpeg·수신 Chromium을 포함한 전체 가상 시험 프로세스 트리다. RSS 합계는 공유 페이지를 중복 집계한다. 지연은 동일 호스트에서 200ms 간격으로 판별한 20개 시각적 변화이며, 실제 물리 HDMI/별도 PC 네트워크의 p95가 아니다. 대역폭은 JPEG payload만 계산하고 HTTP/SSH overhead는 포함하지 않는다.

## 완료 경계와 남은 작업

[실보드 수락 절차](display-hardware-acceptance.md)에 실제 connector/EDID, 두 물리 화면과 fullscreen 배치, 케이블 분리/재연결, systemd 프로세스 단위 재시작, 원격 PC/실제 망, Pi CPU·온도·메모리·지연 측정을 분리했다. 같은 app/renderer를 두 실제 HDMI에 올리는 것과 실제 화면을 시연 PC로 보내는 것은 이 절차로 확인해야 한다.

이번 시험은 systemd를 통한 키오스크/송신기 전체 프로세스 kill/restart나 feed 프로세스 재시작을 검증한 것은 아니다. native context 종료, FFmpeg 종료, HTTP 채널 차단, 송신 서버 정상 종료를 각각 시험한다. 실제 게스트 장애/재시작·승계는 해당 제어 담당의 절차와 연결한 후 표시 결과를 검증한다.

Jira·platform 브랜치를 변경하거나 커밋·푸시·병합하지 않는다.
