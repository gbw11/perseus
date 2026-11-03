# 257 실제 제품 viewer 연속 수신·지연·HDMI 복귀 검증

2026-11-02, `feat/platform/S15P31E110-257-native-stream-runtime` 작업의 추가 검증이다. [제품 native 실행 코드와 선행 시험](display-257-native-runtime-20261102.md)에 이어 수행한다. 코드·라이브러리는 격리된 임시 경로에서 실행하며 기존 HDMI 서비스·API·VM·MQTT·Tailscale 설정을 변경하지 않는다. 실제 HDMI 케이블 분리는 사용자에게 포트와 대상을 안내한 뒤 수행한다.

## 수신 경로와 지연 계측

실제 HDMI 두 출력의 X11 root 영역 → 제품 native 캡처/압축 worker → 기존 인증 MJPEG 서버 → SSH direct-tcpip → PC loopback 시험 proxy → 제품 `/demo` viewer → 실제 PC Chrome canvas 경로다. 제품 `viewer.html` 소스는 변경하지 않는다. 별도의 headless 대시보드나 Pillow 수신기로 제품 브라우저 검증을 대체하지 않는다. 브라우저는 별도 임시 프로필의 headful Chrome이며 Playwright의 추가 headless 브라우저 설치는 하지 않는다.

viewer에서 접속 키를 제출해 실제 `/auth`와 cookie 인증을 사용한다. 키는 메모리에만 유지하고 입력란은 제품 코드가 비운다. proxy는 고정된 demo/auth/status/두 stream 경로만 전달한다. 시험용 색상 제어 경로는 loopback과 메모리상의 별도 nonce로 제한한다. 계측 자료에 인증 키·cookie·개인 키를 저장하지 않는다.

시연 PC는 Intel Core Ultra 9 185H(16코어/22 logical), 물리 RAM 약 63.50GiB, Windows 11 Enterprise 10.0.26300, Chrome 156.0.8078.74다. PC의 Wi-Fi 192.168.30.158에서 보드 192.168.30.96으로 기존 LAN SSH를 사용했다. 외부 Spring 또는 인터넷/Tailscale 망의 지연 측정으로 확대하지 않는다. 보드의 출력 geometry·X11/Chromium 실행 묶음·native 라이브러리 hash는 선행 runtime 기록과 같다. governor·온도 센서는 실행 환경에 노출되지 않아 해당 값의 안정성은 미확인이다.

수신 브라우저에서 색상 제어 요청 직전 `performance.now()`를 기록한다. 두 HDMI 모서리에 표시한 작은 X11 창의 실제 픽셀이 JPEG로 전달돼 제품 viewer의 `drawImage`에 도착하는 것을 확인하고, 이후 **두 번의 `requestAnimationFrame` callback** 시각까지 같은 브라우저 시계로 계산한다. 브라우저의 디코딩·canvas 갱신과 렌더링 기회를 포함하는 시험이다. 실제 LCD의 광학 응답/scanout을 계측한 값은 아니며 rAF 자체가 특정 픽셀의 물리 표시 완료를 보증한다고 해석하지 않는다.

색상 확인 구간에만 1픽셀 readback을 수행한다. 장시간 구간에는 프레임 수·최대 갱신 간격·1초 이상 간격 수만 기록하고 프레임 영상 큐를 만들지 않는다. requestAnimationFrame/renderer background throttling을 끄는 두 Chrome 실행 옵션을 명시적으로 사용했다. 임의 보드 CPU affinity나 우선순위를 변경하지 않는다. 운영자의 PC·망 조건이 달라지면 지연도 재측정한다.

## 연속 구간 판정

색상 지연 측정이 끝나면 표식을 숨기고 브라우저 계수를 초기화한다. 이후 600초 이상 두 화면을 수신한다. 이 구간에는 송신기 재시작·브라우저 재실행·HDMI 분리를 하지 않는다. 출력별 실제 draw 횟수, 최초/최종 시각, 최대 draw 간격, HTTP stream 연결 횟수와 표본을 남긴다. 설정 5 FPS를 실측 5.000 FPS 하한 통과로 반올림하지 않는다.

CPU·메모리는 보드 service cgroup의 프로세스만 2초 간격으로 수집한다. VM 재조회나 MQTT 추가 구독을 하지 않는다. 송신기+작업자와 기존 Chromium/Xorg를 분리한다. process RSS 합계에는 공유 페이지 중복이 있고 프로세스 생성/종료 경계 CPU는 일부 누락될 수 있다. JPEG 전송량은 encoded source byte counter 차이이며 실제 wire bytes가 아니다.

## HDMI 분리·복귀 판정

연속 시험 종료 뒤 수행한다. USB-C 전원에 가까운 micro-HDMI(HDMI0/드라이버 계기판), 먼 쪽 micro-HDMI(HDMI1/인포테인먼트)를 순차 시험한다. 전원이나 반대쪽 케이블은 분리하지 않는다.

사용자 분리/복귀 동안 DRM 연결 상태, fresh kiosk runtime, 출력별 MJPEG metadata, 제품 viewer 상태와 draw 횟수를 함께 기록한다. 분리된 출력이 unavailable로 표시되는지, 반대편 출력이 계속 갱신되는지, 원래 포트/EDID 복귀 후 현재 화면과 새 식별 색상이 수신되는지 대조한다. 마지막 정지 영상을 live 성공으로 계산하지 않는다. 게스트/주행 명령은 전송하지 않는다.

## 선행 짧은 시험

30초 설정의 계측 도구 시험에서 실제 제품 viewer 두 화면을 확인했다. 색상은 출력마다 30/30 확인, p95는 412.8/342.5ms였다. 시작 직후의 status polling과 프레임 수신 시점 차이를 고려해 본 시험은 두 badge가 실제 수신 중으로 바뀐 다음 계측한다. 이 짧은 결과를 10분 연속 수락으로 대체하지 않는다.

최종 수치와 HDMI 분리 결과는 전체 시험 종료 및 원복 후 아래에 추가한다.

## 첫 10분 연속 수신과 HDMI0에서 발견한 문제

`xenova-257-browser-c45b804c` 시험은 기존 보드 키오스크 상태로 수행했다. 브라우저 시계 기준 601.799초 동안 출력 1/2는 각각 2,984/2,983프레임(4.9585/4.9568 FPS)을 그렸고 stream 연결은 출력마다 한 번, proxy 오류와 1초 이상 draw 공백은 0회였다. 최대 draw 간격은 633.3/565.1ms였다. 색상 30/30 확인의 p95는 **371.8/384.7ms**였다.

CPU 표본 598.071초에서 HTTP 송신기 0.1926코어 + native 작업자 0.4634코어 = **0.6560코어**, 인코딩 JPEG counter 기반 합계 4.5069Mbps였다. pipeline RSS 합계는 처음/마지막 93.71/93.96MiB, 최소 MemAvailable은 1,582.55MiB였다. 이를 물리 화면의 광학 지연이나 엄격한 5.000 FPS 하한 통과로 확대하지 않는다.

이후 HDMI0를 분리·재연결했다. 사용자는 두 실제 화면이 정상 복귀했다고 확인했다. DRM 분리 표본 15개와 대상 unavailable을 확인하고, 복귀 후 새 색상 ID 31~35를 두 원격 화면에서 모두 확인했다. 그러나 반대편 monitor-2에도 약 **12.25초**의 draw 공백이 발생했다. runtime 상태 갱신 나이는 최대 13.22초였다. 따라서 이 실행의 **HDMI 분리 격리 수락은 실패**다. 소스별 영상 복귀 성공으로 반대편 중단을 숨기지 않는다.

보드의 키오스크가 출력별 Chromium 종료·시작·검증을 한 반복문에서 순서대로 기다리는 것을 확인했다. 해당 작업이 오래 걸리면 전체 status 파일이 늦어지고 정상 반대편 Capture도 신선도 제한에 의해 중단된다. source freshness 제한을 완화하지 않고, geometry 관측·출력별 renderer 검증·상태 기록을 독립 작업으로 분리했다. 각 renderer의 검증 시각을 따로 보관하여 오래된 상태를 새 파일 시각만으로 정상 처리하지 않는다. 시작은 lock으로 직렬화해 선택형 shared browser의 중복 생성을 방지한다. 정상 반대편의 검증은 그 lock을 기다리지 않는다.

기존 인코더·idle·shared-browser 검사와 새 시작/종료/검증 지연 격리·상태 신선도 검사를 합쳐 **35개 통과**했다. 실제 보드는 저장소보다 오래된 launch 구현이므로, 해당 보드의 원본 source에서 supervisor 부분만 추린 별도 최소 후보를 준비했다. 그 후보에도 격리 검사 4개를 별도로 통과시켰다. 기존 Chromium 실행 옵션·sandbox·인증은 바꾸지 않는다.

사용자가 임시 적용·키오스크만 재시작·시험 후 원복을 명시적으로 허용했다. 두 보드 소스의 원본 SHA256 `fa00da2fa4bc677ee572e9d2f325a7fb2b381f74e3cd0f997e0ceca8059bad60`을 확인하고, 최소 후보 `63f7813d9e2f01d31543565ba9bc518854c806024e22b862260dbe60ad21d0f2`를 임시 적용했다. 원본 백업·해시 방어·40분 자동 원복을 준비했으며 외부 변경이 발견되면 덮어쓰지 않는다. 기존 graphics/API/VM/MQTT/Tailscale은 재시작하지 않았다. 이 임시 후보의 재시험과 최종 원복은 별도 결과로 남긴다.

첫 시험의 native 송신기·marker·SSH 허용은 모두 정리했다. [첫 10분·실패한 격리 시험 요약](evidence/display-257-browser-20261102/xenova-257-browser-c45b804c-summary.json)과 [원자료](evidence/display-257-browser-20261102/xenova-257-browser-c45b804c.json.gz)를 보존한다.

## 출력 격리 수정 후 두 HDMI 재시험

`xenova-257-browser-914009fc`에서 최소 후보(상태 쓰기 0.5초)를 임시 적용하고 HDMI0·HDMI1을 각각 분리·복귀했다. 사용자는 두 경우 모두 실제 두 화면 정상 복귀를 확인했다.

| 항목 | HDMI0 분리 | HDMI1 분리 |
| --- | --- | --- |
| 대상 DRM 분리 표본 | 12개 | 12개 |
| 그중 대상 stream unavailable | 12개 | 12개 |
| 반대편 stream unavailable | **0개** | **0개** |
| 반대편 새 1초 이상 draw 공백 | **0회** | **0회** |
| 관측된 분리 중 반대편 draw 증가 | 109프레임 | 96프레임 |
| 단계 내 최대 runtime 갱신 나이 | 0.569초 | 0.626초 |
| 복귀 후 새 색상 ID | 31~35, 두 화면 모두 확인 | 36~40, 두 화면 모두 확인 |

반대편의 누적 최대 간격에는 해당 화면이 앞선 단계에서 직접 분리된 이력이 포함될 수 있다. 따라서 단계별 `gapsOver1s` 증가분과 API 표본으로 격리 여부를 판단했다. 대상이 분리돼 있는 동안의 timeout/재연결은 그 단계의 예상 기록이며 연속 전송 오류와 섞지 않는다.

그 뒤 같은 후보에서 브라우저 시계 604.379초 동안 연속 수신했다. 출력별 2,935/2,931프레임(4.8562/4.8496 FPS), 추가 연결 0회, 1초 이상 draw 공백 0회였다. 최대 draw 간격 788.2/827.3ms, CPU 표본 599.915초의 송신기+인코더 평균 0.7341코어, 인코딩 JPEG 합계 약 4.4309Mbps였다. pipeline RSS 합계는 처음/마지막 94.32MiB로 같았다. 서로 다른 키오스크 시작/캐시 상태의 MemAvailable 차이를 코드의 RAM 절감 효과로 해석하지 않는다.

단, 이 실행의 시작 시 색상 30회 p95는 **413.4/508.6ms**로 인포테인먼트가 500ms 목표를 넘었다. 이를 반올림해 통과 처리하지 않았다. 두 원본 소스 해시 일치·키오스크 정상 재기동·자동 원복 timer 해제·임시 백업 삭제까지 수행한 뒤 추가 보완을 준비했다.

다음 후보는 동일한 출력 격리·6초 신선도 제한을 유지하면서 status 파일 쓰기를 1초 주기로 낮춘다. PC loopback 중계 HTTP 소켓과 해당 시험의 SSH client 소켓에는 TCP_NODELAY를 명시한다. 시스템 전체 TCP 설정·보드 SSH 정책의 영구 변경은 아니다. 이 두 변경을 함께 시험하므로 개선 기여도를 각각 분리해 입증했다고 주장하지 않는다. 최종 후보는 같은 실행의 10분 전/후 색상 30회씩 모두 보고하며 이전 실패 기록도 보존한다.

## 추가 계측·최적화의 경계

상태 쓰기 1초·TCP_NODELAY 후보 `35f60617`도 10분 연속 수신과 양쪽 HDMI 분리 격리는 통과했지만, PC 제어 요청부터 두 RAF까지의 지연은 종료 시 532.0/556.7ms였다. 제품 캔버스 직접 readback의 부하를 줄인 `450907c8`에서도 같은 보수적 구간이 500ms를 넘었다. 이 기록들을 성공 표본으로 바꾸거나 버리지 않았다.

PC의 별도 정적 JPEG 비교에서 제품 캔버스 직접 readback의 동기 작업은 p95 2.9ms·최대 56.5ms, 1픽셀 CPU 진단 버퍼 방식은 p95 0.5ms·최대 0.8ms였다. 두 실제 스트림 전체의 계측 오버헤드나 보드 지연 감소량을 직접 증명하는 수치는 아니다. 이후 계측은 제품 canvas context를 바꾸지 않고 동일 decoded ImageBitmap의 1픽셀을 별도 버퍼에서 확인한다. 실제 제품 drawImage 호출 후 두 RAF를 기다리며, 각 구간 마지막 색상은 Chrome 합성 screenshot의 두 canvas 좌표에서도 별도로 대조한다. screenshot 작업은 개별 지연 표본 시간 밖에서 수행한다.

제어 요청 시작 기준 시간에는 원격 표식 명령의 전송·색상 준비 작업도 포함된다. 따라서 별도의 clock echo와 source draw 요청/완료 bracket을 기록해 실제 영상 구간을 구분한다. PC·보드 timestamp를 곧바로 빼거나 대칭 망 지연을 가정하지 않는다. 각 echo의 PC 송신/수신 시각 사이에 보드 echo가 존재한다는 인과 제약과, **120초 이하 구간에서 상대 시계 속도가 0.99~1.01 범위라는 명시적 공학 가정**을 사용한다. 모든 echo에서 이 가정과 인과 제약의 일관성을 확인하며 모순되면 결과를 거부한다. source paint 시점이 가질 수 있는 PC 시각 범위로부터 표본별 지연 하한·상한을 계산하고, p95 상한과 최대 시각 불확실성을 함께 보고한다. 이 가정은 oscillator 교정 인증이나 물리 LCD scanout 보증이 아니다.

합성 테스트는 알려진 실제 영상 지연 250ms, 추가 제어 지연 350ms 이상, 시계 비율 ±0.5%의 사례가 계산된 범위에 들어가는지 확인했다. 가정과 모순되는 5% 시계 비율 사례는 거부했다. clock echo 전용 SSH 연결은 영상 TCP 흐름과 분리해 측정용 control 메시지가 영상 큐에서 기다리는 영향을 줄인다. 동일한 장비/망의 측정 경계이며 WAN·외부 Spring 경로의 검증을 대신하지 않는다.

`c39f2915`의 6 FPS 후기 표본에서는 drawImage 이후 두 RAF 대기만 p95 약 1.48초였다. 당시 가시성 기록이 없으므로 이를 보드 인코더 지연으로 단정하지 않았다. 이후에는 실제 Chrome을 앞으로 가져오고, 각 draw/RAF 시점에 visible·focus 상태를 기록한다. 전경 조건을 잃은 batch는 raw 기록을 보존하고 전경 수락에서 제외한다. focus emulation으로 가시성을 위조하지 않는다.

제품 송신기에는 두 Capture가 같은 authoritative runtime 파일을 공유하는 `RuntimeStatus`를 추가했다. 최대 100ms 간격으로 stat을 확인하고 inode/mtime/크기가 바뀔 때만 파싱한다. 원본 `updated_monotonic`을 cache 조회 시각으로 바꾸지 않으며 소비 시마다 6초 만료와 출력 상태를 재검사한다. 파일 삭제·손상은 fail-closed다. 추가 관측 지연은 최대 100ms이며 새 VM 조회나 MQTT 연결을 만들지 않는다. 관련 검사 38개가 통과했고 동시 100회 요청의 단일 파싱·파일 교체·만료·분리 차단을 확인했다.

사용자는 6 FPS 비교를 별도로 허용했다. 짧은 `d3c080d6`(5 FPS)에서는 영상 지연 p95 상한이 시작 437.4/433.8ms, 종료 414.5/456.8ms였다. 전경을 확인한 `0e34ae62`(6 FPS)는 시작 414.3/406.4ms, 종료 522.8/486.7ms였고 pipeline CPU 표본 평균은 약 0.827코어였다. 순차 한 번씩의 짧은 비교이므로 FPS만의 인과 효과나 통계적 우월성으로 일반화하지 않는다. 이번 보드·망에서는 6 FPS의 지연 개선이 확실하지 않아, 기본 5 FPS를 유지하고 cache를 포함한 최종 연속 시험을 수행한다. 모든 비교 원자료와 실제 FPS를 보존한다.

준비 세션 `ca820171`은 재인증 응답 지연으로 전송 시작 전에 중단됐다. 해당 시험의 SSH 정책은 원본 해시로 복원하고 stage·환경 파일을 정리했다. 이후 동일 시험 안의 재연결만 프로세스 메모리상의 인증 값으로 처리하며 비밀번호를 파일·증거에 저장하지 않는다. 호스트키 검증은 매 연결 유지한다.

## 최종 5 FPS 연속 수신·지연 결과

`14dac246`은 제품 native backend·출력별 supervisor·1초 상태 쓰기·공유 runtime cache를 포함한 선택 구성이다. 720p·JPEG quality 85·설정 5 FPS를 유지했다. 영상과 clock echo는 별도 SSH 연결을 사용했고 전경 Chrome에서 실행 전후 각각 색상 60회·echo 300개를 기록했다.

| 항목 | 결과 |
| --- | --- |
| 브라우저 관측 구간 | 602.552초 |
| 계기판 수신 | 2,975프레임 / 4.9373 FPS |
| 인포테인먼트 수신 | 2,961프레임 / 4.9141 FPS |
| 추가 연결·proxy 오류 | 출력마다 0회 |
| 1초 이상 draw 공백 | 출력마다 0회 |
| 최대 draw 간격 | 707.9 / 762.1ms |
| HTTP 송신기 + native 작업자 CPU 표본 평균 | 0.1452 + 0.4842 = **0.6294코어** |
| 인코딩 JPEG 합계 | **4.4781Mbps** |
| 실행 전 영상 지연 p95 상한 | **455.70 / 461.47ms** |
| 실행 후 영상 지연 p95 상한 | **463.53 / 455.18ms** |
| 색상 확인 | 두 구간 모두 출력마다 60/60 |

지연 상한은 앞서 명시한 상대 시계 속도 가정과 echo 제약 아래의 계산값이며, 광학 LCD 측정이나 모든 망 조건에서의 보장이 아니다. 5 FPS는 설정값이고 실제 수신률은 표와 같다. 엄격한 실측 하한 5.000 FPS 통과로 보고하지 않는다. 대상은 현재 상태 패널과 작은 색상 변화이며 미연동 지도·카메라·게스트 동영상 전체 부하의 수락이 아니다.

이 실행의 물리 분리 대기는 15분 제한으로 끝났다. 기록 중 DRM 분리 표본은 0개였고 사용자의 복귀 답변은 종료 후 도착했다. 따라서 이 실행으로 캐시 포함 최종 구성의 HDMI 분리 수락을 주장하지 않는다. 앞선 실제 HDMI 검증과 구분하여, 같은 최종 코드의 짧은 재검증을 따로 수행한다. native 송신기·SSH 정책은 종료 시 원복됐고 키오스크 후보도 안전 timer로 원본 해시로 복원됐다. 만료돼 GC된 timer도 정상 정리로 취급하도록 원복 helper를 보완하고 남은 백업을 정리했다.

## 최종 캐시 구성의 실제 HDMI 재검증

`51848f83`은 동일 제품 코드·720p·quality 85·5 FPS에서 분리/복귀만 확인하는 짧은 별도 실행이다. 이 실행의 짧은 준비 관측을 10분 성능 결과로 사용하지 않는다. 성능 수치는 위 `14dac246`을 따른다.

| 항목 | HDMI0 계기판 | HDMI1 인포테인먼트 |
| --- | --- | --- |
| 실제 DRM 분리 표본 | 9개 | 8개 |
| 그중 대상 stream unavailable | 9개 | 7개 |
| 반대편 API unavailable | **0개** | **0개** |
| 관측 분리 중 반대편 draw 증가 | 67프레임 | 60프레임 |
| 단계 내 최대 runtime 나이 | 1.071초 | 1.026초 |
| 복귀 후 시각 확인 | 사용자 두 화면 정상 | 사용자 두 화면 정상 |
| 복귀 후 새 색상 변화 | 31~35, 두 stream 모두 확인 | 36~40, 두 stream 모두 확인 |
| 마지막 API 상태 | 두 출력 live | 두 출력 live |

HDMI1 분리 직후 한 표본은 authoritative kiosk 상태가 갱신되기 전이어서 대상 stream이 아직 live였다. 이것을 즉시 감지 성공으로 계산하지 않는다. 이후 대상은 unavailable로 전환됐고 복귀 후 live로 돌아왔다. cache는 producer 시각을 갱신하지 않으며 100ms 추가 관측 지연과 기존 약 1초 producer 주기를 가진다.

HDMI0 복귀 시 반대편 인포테인먼트의 canvas draw 간격이 **1049.4ms로 한 번 늘었다.** API unavailable이나 재로그인 요구 없이 연결은 유지됐으나, 무중단 프레임 갱신/1초 미만 최대 간격 통과로 주장하지 않는다. HDMI1 단계에서는 반대편의 새 1초 이상 draw 공백이 0회였다. 앞서 발견한 전체 상태 파일 13초 노후화·반대편 약 12초 중단과 구분하며, 이 짧은 복귀 jitter도 기록에서 제거하지 않는다.

각 시험 종료 시 native 송신기·색상 창·개인 환경 파일·라이브러리 stage를 삭제하고 SSH PermitOpen 원본 해시, graphics 소스 불변, VM UUID/domID 불변을 확인했다. 원본 키오스크 복원과 독립 종료 조회의 최종 결과는 별도 증거로 저장한다. 상시 native library 설치·unit enable·원격 접근 영구 허용·운영 배포는 수행하지 않았다.

최종 독립 조회에서 기존 여섯 서비스 모두 active, 두 실제 HDMI 모두 connected/mapped/renderer_alive/physical_verified였다. 키오스크 두 파일은 원본 `fa00da2f…` 해시로 돌아왔고 graphics 소스 `3a97e594…`와 graphics MainPID 543은 유지됐다. 임시 8083 listener·PermitOpen·시험 프로세스·키오스크 백업 디렉터리는 없었다. 남은 UID1000 SysV segment는 실행 중인 Chromium 소유이며 삭제하지 않았다. SSH 확인 세션과 PC 시험 Chrome도 종료했다. 수정된 제품 코드는 작업 브랜치에 남기고 보드에는 임시 시험 변경을 유지하지 않았다.

## 검토할 증거

- [최종 10분 수신 요약](evidence/display-257-browser-20261102/xenova-257-browser-14dac246-summary.json) / [전체 기록](evidence/display-257-browser-20261102/xenova-257-browser-14dac246.json.gz)
- [실행 전 지연 범위·시계 제약](evidence/display-257-browser-20261102/xenova-257-browser-14dac246-clock-latency-bounds-1.json) / [실행 후](evidence/display-257-browser-20261102/xenova-257-browser-14dac246-clock-latency-bounds-2.json)
- [실제 Chrome 색상 대조 1](evidence/display-257-browser-20261102/xenova-257-browser-14dac246-browser-marker-proof-1.png) / [대조 2](evidence/display-257-browser-20261102/xenova-257-browser-14dac246-browser-marker-proof-2.png)
- [최종 실제 HDMI 분리 요약](evidence/display-257-browser-20261102/xenova-257-browser-51848f83-summary.json) / [전체 기록](evidence/display-257-browser-20261102/xenova-257-browser-51848f83.json.gz)
- [최종 원복·독립 종료 조회](evidence/display-257-browser-20261102/postflight-acceptance-20261102.json.gz)

제품 기본 FFmpeg와 공개 HTTP/인증 규격은 유지하며 native backend는 명시적으로 선택한다. 6 FPS는 비교만 했고 기본 설정으로 채택하지 않았다. 이번 결과는 현재 장비·LAN·전경 viewer·표시 대기 UI의 1차 구현 검증이며, 실제 주행 데이터/동적 guest 영상 통합과 모든 네트워크 조건의 성능 보장을 대신하지 않는다. 엄격한 수신 FPS ≥5.000 또는 hotplug 중 최대 간격 <1초가 별도 수락 기준이면 추가 튜닝이 필요하다.
