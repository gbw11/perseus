# 257 native 송신기 상시 실행 코드 검증 (2026-11-02)

## 변경 범위

최신 `origin/platform` 5a9c839에서 분리한 `feat/platform/S15P31E110-257-native-stream-runtime` 작업이다. `platform` 직접 변경·merge·Jira 완료 전환을 하지 않았다. 기본 FFmpeg 경로를 유지하고 명시적으로 선택하는 `native_jpeg` backend를 추가했다.

이전 prototype의 900프레임 종료와 프레임별 진단 배열을 제거했다. X11 shared-memory 캡처와 JPEG 압축 버퍼를 재사용하며 JPEG마다 malloc/free하지 않는다. HTTP 경로는 기존 Capture·인증·최신 프레임 알림을 그대로 사용한다. 상태 수집기·MQTT 연결·화면 렌더러를 추가로 실행하지 않는다.

[실행 프로파일](../../platform/services/display/router/deploy/board-kms/STREAM.md)은 기존 graphics root와 UID1000 worker를 사용한다. worker 환경에는 표시 접근 정보와 패키지 경로만 넘긴다. 라이브러리·출력 geometry 검사를 통과해야 실행하며 자동 backend 전환은 하지 않는다. 서비스 템플릿은 작성했지만 상시 설치·enable하지 않았다.

## 시험 방법과 해석

실제 두 HDMI 1280×720의 X11 출력 영역을 각각 캡처해 PC에서 모든 JPEG를 디코딩한다. 기존 HDMI 서비스와 그래픽 파일은 수정·재시작하지 않았다. native 라이브러리는 기존에 승인된 임시 경로에만 준비했다. loopback 8083 SSH 허용도 시험 중에만 추가하고 원본 해시로 복원한다.

지연 시험은 작은 X11 override-redirect 창 두 개를 사용한다. 입력 focus를 가져오지 않고 30개의 식별 가능한 색상을 순서대로 표시한 뒤 창을 닫는다. PC의 제어 요청 시작부터 수신 JPEG의 실제 색상 픽셀을 디코딩해 확인할 때까지 **같은 PC 단조 시계**로 계산한다. 원격 시계 차감·프레임 간격·상태 메타데이터를 영상 지연으로 대체하지 않는다. 제어 요청의 왕복 중 송신 구간도 포함한다. LCD 발광 시점·웹 브라우저 paint·전체 주행 데이터 지연 측정은 아니다.

CPU는 service cgroup에 속한 PID만 2초마다 읽는다. 기존 관리 서비스와 달리 VM을 다시 조회하지 않는다. PID 생존 구간의 tick을 표본 간 실제 시간으로 가중 평균하며 생성/종료 경계 CPU는 일부 누락될 수 있다. HTTP 송신기와 native 작업자의 합계로 판단하고 Chromium/Xorg는 별도로 기록한다. RSS 합계는 공유 페이지가 중복될 수 있어 독점 사용량으로 해석하지 않는다.

전송량은 수신한 JPEG payload 합계다. HTTP multipart·SSH·TCP/IP overhead를 포함한 실제 wire 대역폭으로 표기하지 않는다. 5 FPS는 송신 설정이며 수신 FPS는 전체 관측 시간의 수신 프레임 수로 계산한다. 엄격한 5.000 FPS 하한 충족으로 반올림하지 않는다.

## 선행 확인

| 항목 | 1분 연속 전송 | 2분·송신기 재시작 포함 |
| --- | --- | --- |
| 전체 PC 관측 시간 | 60.092초 | 120.291초 |
| 출력 1 / 2 수신 FPS | 4.976 / 4.992 | 4.431 / 4.414 |
| pipeline CPU 평균 | 0.611코어 | 0.674코어 |
| JPEG payload 합계 | 4.529Mbps | 4.021Mbps |
| 색상 지연 p95 | 미실시 | 402.1 / 420.6ms |
| 색상 확인 | 미실시 | 출력마다 30/30 |
| 재연결 | 단일 연결 유지 | 두 출력 모두 두 번째 연결 성공 |

2분 시험은 재시작 중 약 11.5~11.6초의 수신 공백을 포함한다. 전체 FPS를 이 구간을 뺀 정상 FPS처럼 제시하지 않는다. 수신기의 EOF·연결 거부 기록은 의도한 송신기 재시작 중 발생했다. 1분 시험의 두 출력에는 오류·1초 이상 수신 공백이 없었다.

최초 표식 시험 두 번은 준비 대기 시간이 짧아 중단됐다. 대기 시간을 10초에서 45초로 늘린 뒤 색상 30/30을 확인했다. 이 실패를 영상 지연 통과 표본에서 누락하거나 성공으로 집계하지 않는다. 각 실패 시험도 종료 시 임시 파일·SSH 정책을 원복했고 기존 HDMI/VM은 유지했다.

관련 출력 매핑·FFmpeg 호환·인코더 할당/해제·프레임 알림 회귀 검사 **26개 통과**. 기존 Starlette/httpx deprecation warning 한 건은 이 변경 범위에서 수정하지 않았다.

## 10분 관측·송신기 재시작

같은 제품 코드로 600.465초 동안 두 출력의 수신을 관측하고 약 300초에 **시험용 송신기만** 재시작했다. HDMI/graphics/API/MQTT/VM은 재시작하지 않았다.

| 항목 | 결과 |
| --- | --- |
| 출력 1 수신 | 2,927프레임, 4.8746 FPS |
| 출력 2 수신 | 2,920프레임, 4.8629 FPS |
| JPEG payload 합계 | 4.4229Mbps |
| CPU 표본 시간 | 598.693초 |
| HTTP 송신기 / native 작업자 | 0.1860 / 0.4617코어 |
| pipeline 합계 | **0.6476코어** |
| 별도 Chromium / Xorg 표본 평균 | 0.3325 / 0.1392코어 |
| pipeline RSS 합계 처음 / 마지막 | 93.05 / 93.98MiB |
| 표본상 최소 MemAvailable | 1,688.63MiB |
| 색상 확인 / p95 | 출력마다 30/30, **435.6 / 388.5ms** |
| 의도한 재시작 중 최대 수신 공백 | 10.421 / 10.803초 |
| 1초 이상 수신 공백 | 출력마다 1회 |
| 최종 연결 횟수 | 출력마다 2회, 재연결 성공 |

재시작 전·후 작업자 모두 이전 prototype의 900프레임 제한을 넘겨 실행됐다. RSS 끝점 차이는 약 0.93MiB이며 누수가 없다는 장기 보장으로 확대하지 않는다. 메모리 표본은 restart로 프로세스가 교체되는 조건이다.

이는 **10분 관측 중 장애 주입 1회**이며 중단 없는 연속 10분 시험은 아니다. PC 진단 수신기의 재접속 성공을 제품 브라우저 viewer의 자동 복구 성공으로 대체하지 않는다. 지연 30개는 재시작 전 짧은 색상 변화 구간의 표본이며 10분 전체 지연 분포가 아니다. 나머지 화면은 실제 UI의 수신 대기 상태로, 동적 지도·영상·전체 주행 부하를 재현하지 않았다.

시험 종료 시 기존 여섯 서비스 active, SSH 정책 원본 해시 일치, graphics 소스 불변, VM UUID/domID 불변, 임시 라이브러리·설정·송신기 파일 삭제를 확인했다. Tailscale 설정·상태 수집 경로는 변경하지 않았다.

별도 SSH 종료 조회에서도 8083 listener·임시 PermitOpen·시험 프로세스가 없었다. kiosk/graphics MainPID는 시작 전과 같은 1278/543이며 기존 공유 메모리 segment 2·3·7·8만 남았다. 이 segment는 기존 Chromium 소유이므로 삭제하지 않았다. 조회 SSH 세션도 종료했다.

## 남은 수락 범위

후속 [실제 제품 viewer·최종 HDMI 검증 기록](display-257-browser-acceptance-20261102.md)에서 아래 연속 수신·브라우저 계측·실물 분리/복귀를 추가 수행했다. 해당 기록의 수치와 제한을 최종 판정에 사용한다. 이 문단 아래 목록은 선행 runtime 시험 종료 시점의 상태다.

- 최종 선택 설정에서 중단 없는 두 출력 10분 수신과 제품 viewer의 재연결·브라우저 표시 지연 확인.
- native 경로의 실제 HDMI 분리·복귀, 동적 게스트 앱 영상의 부하/화질 검증. 기존 HDMI hotplug 결과를 새 인코더 검증으로 대체하지 않는다.
- 상시 라이브러리·unit 배치 결정. 현재는 임시 비교만 승인된 범위이며 보드에 상시 설치하지 않았다.

이 결과만으로 Jira 257을 완료 처리하지 않는다.

## 증거

- [1분 요약](evidence/display-257-runtime-20261102/xenova-257-runtime-71d3d5ae-summary.json) / [전체](evidence/display-257-runtime-20261102/xenova-257-runtime-71d3d5ae.json.gz)
- [2분 요약](evidence/display-257-runtime-20261102/xenova-257-runtime-c8966d6e-summary.json) / [전체](evidence/display-257-runtime-20261102/xenova-257-runtime-c8966d6e.json.gz)
- [10분 관측 요약](evidence/display-257-runtime-20261102/xenova-257-runtime-6ecaaf5c-summary.json) / [전체](evidence/display-257-runtime-20261102/xenova-257-runtime-6ecaaf5c.json.gz)
- [독립 종료 조회](evidence/display-257-runtime-20261102/postflight-runtime-20261102.json.gz)
- [계측 controller](evidence/display-257-runtime-20261102/runtime_probe_20261102.py.gz) / [표식 도구](evidence/display-257-runtime-20261102/latency_marker_20261102.py.gz)

native binary·인증 토큰·비밀번호는 증거에 포함하지 않는다. 라이브러리 출처와 checksum은 [동일 날짜 비교 기록](display-257-native-jpeg-20261102.md)을 참조한다. 계측 도구의 고정 경로·호스트키는 당시 보드 전용이며 범용 설치 스크립트가 아니다.
