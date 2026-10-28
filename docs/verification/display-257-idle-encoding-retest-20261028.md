# 257 실보드 절전 방지·720p 인코딩 보완 재시험 (2026-10-28)

이전 [실패 기록](display-257-board-soak-20261028.md)은 그대로 보존했다. 이번 재시험에서는 한 HDMI 모니터에 계기판 300초, 인포테인먼트 300초를 순차 표시했고, 실제 X11 framebuffer를 캡처해 인증된 SSH 경로로 PC까지 수신했다. 총 수신 시간 600.172초, 각 1502프레임이다. 두 출력 동시 시험은 아니다.

## 변경

- 전용 X11 키오스크에 `--prevent-idle-blank` 추가. 제품 `X11IdleGuard`가 xset 설정을 조회·보존하고 screen saver timeout=0, blanking=false, DPMS=false를 검증한다. 종료 시 기존 설정을 복원한다. SIGTERM은 키오스크 task 취소를 거쳐 정리한다. SIGKILL/전원 차단 원복은 보장하지 않는다.
- 원본 캡처는 1824×984 전체 영역을 유지하고, 전송만 비율 유지·letterbox 1280×720으로 변환한다. 실제 첫/끝 JPEG 4개의 SOF 크기도 모두 1280×720이다. `rectangle` metadata는 원본 영역, `width/height`는 인코딩 결과다.
- MJPEG yuvj420p, fast_bilinear 축소, encoder와 filter thread를 각각 1로 제한한다. `-threads`만으로 filter pool을 제한할 수 없으므로 별도로 설정한다([FFmpeg 공식 문서](https://ffmpeg.org/ffmpeg.html)). 어떤 변경 하나의 단독 효과인지는 분리 측정하지 않았다.

## 실측 비교

CPU 100%=1코어. 수집기·송신기·진단 sampler가 같은 Python 프로세스이므로 아래 CPU에 sampler도 포함된다. 시간 가중 평균이다.

| 항목 | 이전 계기판 / 인포테인먼트 | 보완 후 계기판 / 인포테인먼트 |
| --- | --- | --- |
| 송신기+인코더 평균 CPU | 228.76% / 228.85% | 88.26% / 89.55% |
| 수신 FPS | 4.923 / 4.943 | 5.003 / 5.001 |
| JPEG payload Mbps | 1.990 / 2.443 | 0.843 / 1.173 |
| 1초 초과 수신 간격 | 0 / 0 | 0 / 0 |
| 인코더 epoch 수 | 1 / 1 | 1 / 1 |
| unavailable sample | 0 / 0 | 0 / 0 |

평균 CPU는 약 61% 감소했다. 단일 출력 순차 시험에서는 평균 1코어 이내 목표를 충족했다. 순간 부하는 이를 넘으며 두 출력 동시 평균 1코어 목표의 통과를 의미하지 않는다. 최고 온도는 40.894°C, 최소 MemAvailable은 약 409MiB다. JPEG payload 수치는 HTTP/SSH 전체 대역폭과 다르다.

## 화면과 종료 확인

시작과 마지막 JPEG를 두 화면 모두 직접 열어 UI가 정상 표시되는 것을 확인했다. 동일 X11 세션이 600초 이상 유지된 뒤 마지막 인포테인먼트 JPEG도 검지 않았다. 전 프레임을 픽셀 디코딩 검사한 것은 아니며, 화면 변화나 원격 브라우저 표시 지연을 측정하지 않았다.

전용 세션 실제 조회: 시작 timeout/cycle=600, blanking=true, DPMS=true → 시험 중 timeout=0, blanking=false, DPMS=false → 종료 시 시작 설정과 동일하게 원복됐다. 원본 값은 [idle-settings.json](evidence/display-257-soak-optimized-20261028/idle-settings.json)에 있다.

정리 후 VT1, 그래픽 프로세스 0개, private mount 0개, listener는 SSH 22와 기존 Manager 8081뿐이었다. VM3 domid1, VM1 domid2, VM2 domid3가 그대로 유지됐다. VM·펌웨어·기존 서비스·자동 시작 설정은 변경하지 않았다. 시험 파일은 격리된 `/opt/xenova-display-probe/` stage에 남는다.

**원래 framebuffer와 종료 후 읽기 사이에는 48바이트 차이가 남아 `framebuffer_restored=false`다.** VT1 복귀와 프로세스 정리는 확인했으나 픽셀 완전 복원으로 판정하지 않는다. 콘솔 재그리기/커서 변화인지 원인은 이번 기록에서 확정하지 않았다.

## 검증 범위와 남은 작업

- router pytest 107개 통과: 설정 원복, cancellation 정리, 전체 영역 유지·전송 크기 구분, 짝수 해상도 검증 포함.
- 실제 시험은 제품 idle guard·stream encoder를 격리 graphics supervisor에서 실행했다. 상시 systemd 서비스의 설치/재부팅 시험은 하지 않았다. CLI SIGTERM 연결은 로컬 코드에 반영했고 task cancellation 경로를 테스트했으며 이번 실보드 종료는 guard의 직접 restore 경로다.
- fbdev가 `default` connector만 제공하여 명시적 virtual_x11 diagnostic mapping을 사용했다. 물리 2출력 EDID 매핑·동시 부하·HDMI 분리/복귀, end-to-end p95 500ms 목표는 별도 검증이다.
- 속도·배터리·내비·음악 송신기 미연결 값은 대기로 표시한다. VM1·VM2 주행/앱 readiness를 정상으로 대체하지 않는다.
- 이번 수정과 재시험 범위는 완료했으나 257 전체 수락 완료로 표시하지 않았다. 커밋·푸시는 아직 하지 않았다.

[측정 요약](evidence/display-257-soak-optimized-20261028/summary.json) · [원본 압축 자료](evidence/display-257-soak-optimized-20261028/samples.json.gz) · [종료 후 조회](evidence/display-257-soak-optimized-20261028/postflight.json)
