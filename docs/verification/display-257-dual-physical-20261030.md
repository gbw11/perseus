# 257 실제 두 HDMI 원격 전송 재시험 — 2026-10-30

## 현재 결과

**실제 두 HDMI의 PC 영상 수신·인증 거부·재접속을 확인했다. 성능 수락은 미통과이며 257 전체는 진행 중이다.** 새 주행 펌웨어와 Spring DB 등록 없이 기존 HDMI 출력 소스의 캡처·전송을 시험했다. VM 제어·펌웨어·MQTT·Tailscale·운영 DB는 변경하지 않았다.

## 실보드 사전 확인

- 두 DRM 커넥터 `card0-HDMI-A-1`, `card0-HDMI-A-2`가 모두 connected다.
- dom0 MemTotal 2,988,188kB, 첫 조회 MemAvailable 2,679,624kB다.
- API·표시 feed·Mosquitto·Tailscale은 active다. 키오스크·송신기는 시험 전 inactive다.
- 기존 X11 설정은 실제 EDID 두 개와 출력별 1280×720, 좌표 (0,0)/(1280,0)을 사용한다.

## 시작 장애와 제한된 보완 시험

첫 실행은 그래픽 서비스 의존성 실패로 중단했다. 전용 rootfs의 Xorg 로그에는 `Server is already active for display 5`가 남았다. 실행 중인 Xorg는 없었으며 전용 X5 소켓은 접속에 응답하지 않았다. 종료 코드에는 실행 묶음에 없는 `usr/bin/chvt`를 호출한 오류도 있었다.

다음 실행에서는 그래픽 서비스 정지·Xorg 부재·소켓 접속 거부를 확인한 후 전용 rootfs의 오래된 X5 lock/socket만 정리했다. 그래픽 감독 코드의 VT 복원은 호스트 `/dev/tty0`의 VT_ACTIVATE/VT_GETSTATE ioctl과 최대 5초 확인으로 대체해 임시 적용했다. 원본 내용 일치를 확인한 뒤 적용하고, 종료 시 원본 해시를 확인해 복원하는 방식이다. VM이나 시스템 패키지를 교체하지 않았다.

재실행에서 두 출력의 `mapped`, `renderer_alive=true`, `physical_verified=true`를 관측했다. 이 값은 X11 장치 매핑·브라우저 프로세스 관측이다. 당시 `render_revision`과 `applied_revision`은 null이므로 DOM 내용 표시나 육안 검증 성공으로 해석하지 않는다. 사용자에게 확인을 요청한 시점에는 시험 종료·원복이 진행되어 사용자는 두 화면이 보이지 않는다고 답했다. 따라서 육안 성공 증거는 없다.

## 원격 수신을 막은 조건

시험용 loopback 포트 18083의 SSH direct-tcpip 연결이 `Administratively prohibited`로 거절돼 수신 단계는 시작하지 못했다. 실제 SSH 정책을 읽어 확인한 결과 기존 게스트 SSH 허용 목록과 loopback 8081·8082만 PermitOpen에 있다. 사용자는 정식 영상 포트인 loopback 8083을 시험 중에만 추가 허용하고 종료 후 원복하도록 승인했다. 별도 exec 터널로 이 제한을 우회하지 않는다.

정책 reload만으로 이미 인증된 SSH 세션의 PermitOpen은 바뀌지 않았다. 따라서 임시 설정 검사·reload 후 새 SSH 세션으로 재인증하도록 시험 절차를 보완했다. 원본/후보 해시와 기존 서비스 상태를 기록하고, 최대 240초 후 원복하는 systemd 타이머도 등록한다. 종료 시 정책 원본 해시, 그래픽 감독 원본 해시와 서비스 상태를 확인하고 임시 환경 파일을 삭제한다. 승인된 변경은 시험용이며 8083을 운영 허용 목록에 영구 추가하는 배포는 아니다.

## 원복과 다음 시험

임시 송신기·파일을 정리하고 그래픽 감독 원본을 복원했다. 키오스크와 그래픽 서비스는 inactive, API·표시 feed·Mosquitto·Tailscale은 active임을 재확인했다. 상시 시작 설정은 변경하지 않았다.

## 실제 동시 수신 결과

그래픽 rootfs에는 uvicorn이 없어 rootfs Python으로 HTTP 송신기를 바로 시작하지 못했다. 추가 패키지를 설치하지 않고 기존 호스트 Python/HTTP 의존성을 재사용했다. 제품 `Capture`, 인증·multipart 송신 코드를 사용하되, FFmpeg 실행 명령에 기존 Xorg의 mount namespace 진입과 chroot·UID/GID1000 전환을 붙인 시험 어댑터를 사용했다. 다른 headless 페이지를 렌더하지 않고 실제 X11 두 출력 사각형을 캡처했다. 상시 송신 서비스의 배포 프로파일을 검증한 것으로 확대하지 않는다.

PC HTTP 수신기의 Paramiko ChannelFile에 `read1`이 없어 처음에는 프레임을 받지 못했다. SSH 채널을 RawIOBase/BufferedReader로 감싼 뒤 재실행했다. 아래 수치는 그 수정 후 성공한 시험만 사용한다. 앞선 실패를 숨겨 통과율에 합산하지 않았다.

| 항목 | 기존 인코딩 | 고정 Huffman 테이블 후보 |
| --- | --- | --- |
| 두 출력 설정 | 각 1280×720, 5 FPS, JPEG quality 7 | 동일 |
| monitor-1 수신 | 60.046초 / 257장 / 4.280 FPS | 60.000초 / 247장 / 4.117 FPS |
| monitor-2 수신 | 60.015초 / 236장 / 3.932 FPS | 60.000초 / 240장 / 4.000 FPS |
| JPEG payload 합계 | 2.202 Mbps | 2.694 Mbps |
| 송신기+인코더 평균 CPU | **1.656코어** | **1.678코어** |
| CPU 계측 구간 | 47.776초 | 48.855초 |
| 최소 MemAvailable | 1919.52 MiB | 1915.94 MiB |
| 인증 없는 상태 조회 | HTTP 401 | HTTP 401 |
| monitor-1 수동 재접속 | 10.063초 / 48장, 오류 없음 | 10.469초 / 49장, 오류 없음 |
| 수신 클라이언트 종료 후 로컬 서비스 | kiosk/graphics active | kiosk/graphics active |

CPU 100%=1코어다. 약 1초 간격을 목표로 `/proc`를 읽었지만 실제 계측 간격은 더 길었으므로 실제 단조 시각 차이로 가중 평균했다. 송신기·인코더와 별도로 기존 인코딩에서 Chromium 평균 47.09%, Xorg 15.49%를 관측했다. CPU 계측은 60초 수신 구간 전체가 아니며 최소 가용 메모리도 표본 구간의 값이다. 진단 sampler 자체는 송신기+인코더 CPU 합계에 포함하지 않았다. 공유 페이지를 포함한 RSS 합을 고유 물리 메모리로 해석하지 않는다.

PC는 두 JPEG multipart 스트림을 동시에 수신하고 파일에 저장했다. 저장된 두 JPEG를 실제 이미지로 열어 드라이버 계기판과 인포테인먼트가 다른 출력임을 확인했고, Windows 이미지 디코더로 각 1280×720임을 확인했다. 모든 연속 프레임을 디코딩하거나 원격 브라우저 canvas 표시 시간을 측정한 시험은 아니다. 사용자도 실행 중 두 물리 화면이 정상 표시됨을 확인했다. 센서·주행 모드·미디어가 미수신인 부분은 대기/미확인으로 표시되며 실제 차량 데이터 수락으로 취급하지 않는다.

두 시험의 시작/종료 metadata 모두 출력별 동일 capture_epoch와 live 상태를 유지했다. 그러나 프레임 번호는 모든 수신 JPEG에 개별 대응시켜 수집하지 않았으므로 누락 0이나 특정 프레임 지연을 주장하지 않는다. 정적 화면의 수동 재접속 성공만으로 오래된 동적 화면 배제까지 완전히 수락하지 않는다.

설치된 FFmpeg의 `-h encoder=mjpeg`에서 기본 Huffman 전략이 optimal임을 확인해 default 후보를 시험했다. 후보는 동일 해상도·목표 FPS·양자화 설정을 유지했지만 CPU 개선 없이 전송량만 증가했다. 따라서 **제품 인코딩 변경은 채택하지 않고 원래 명령으로 되돌렸다.** 후보의 JPEG 양자화 설정 유지가 전체 영상 품질의 별도 정량 평가를 의미하지는 않는다. 로컬 기존 출력·송신 경계 회귀 검사 13개 통과, 변경 파일 컴파일 및 diff whitespace 검사 통과다.

두 시험 모두 임시 SSH 설정·그래픽 감독 코드의 원본 해시 일치, 임시 파일 삭제, 시험 전 inactive였던 kiosk/graphics 복귀를 확인했다. API·feed·MQTT·Tailscale은 active다. 서비스 enable 상태와 VM·통신 설정을 변경하지 않았다. 모든 시험 SSH 연결을 종료했다. 286번은 사용자 지시로 완료 전환했으며 이 기록을 근거로 257을 완료 전환하지 않았다.

## 남은 수락 범위

- 실제 수신 FPS와 송신기+인코더 CPU 1코어 목표는 미통과다. 현재 두 출력 JPEG payload 8Mbps 이내만 확인했다. CPU 목표를 임의로 완화하지 않았다.
- 원격 브라우저 디코딩·표시를 포함한 실제 화면 전환 지연 p95 500ms는 이번에 측정하지 않았다. frame_age나 프레임 간격을 종단 지연으로 대신하지 않는다.
- 송신 실패/재시작 중 로컬 HDMI 유지, 변화하는 실제 출력의 재접속 최신성·정확한 대응, 최종 구성의 장시간 실행이 남는다.
- 호스트 HTTP 의존성+그래픽 namespace 인코더의 상시 배포 설정과 최종 시연 PC 뷰어를 연결하는 수락이 남는다. 이번 임시 프로파일을 운영 배포 완료로 해석하지 않는다.

## 증거

- [기존 인코딩 요약](evidence/display-257-dual-physical-20261030/baseline-summary.json)
- [기존 인코딩 전체 수신/계측/원복 결과](evidence/display-257-dual-physical-20261030/baseline-result.json.gz)
- [Huffman 후보 요약](evidence/display-257-dual-physical-20261030/fixed-huffman-summary.json)
- [Huffman 후보 전체 결과](evidence/display-257-dual-physical-20261030/fixed-huffman-result.json.gz)
- [PC 수신 계기판 JPEG](evidence/display-257-dual-physical-20261030/monitor-1.jpg)
- [PC 수신 인포테인먼트 JPEG](evidence/display-257-dual-physical-20261030/monitor-2.jpg)
- [시험 소스](evidence/display-257-dual-physical-20261030/trial-source.py.gz): 당시 로컬 경로·기존 pinned SSH helper를 사용하는 진단 스크립트 기록이다. 독립 실행 설치 도구가 아니며 비밀번호·시연 토큰은 포함하지 않는다.
