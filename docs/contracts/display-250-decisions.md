# S15P31E110-250 계약 확정 기록

2026-10-22 사용자 응답 반영. 별도 표시 상태의 1초 보고와 전송 1차 목표는 사용자 선택으로 확정했다. 보드는 아직 준비되지 않았고 제어 담당 팀원과의 합의는 필요하다. 사용자 선택을 팀 전체 합의나 보드 검증으로 기록하지 않는다.

## 이미 정해진 범위

- 외부 대시보드·웹 콘솔의 페이지 라우팅은 별도 담당. 이 작업은 연결/데이터 파이프라인을 담당한다.
- HDMI 두 화면의 선택·구성·실행과 동일 화면의 원격 시연 전달은 이 작업 범위다.
- 모니터 1은 세 VM 전체 상태, 모니터 2는 선택 VM 상세. 기본 VM1 pinned, 명시적인 follow_active만 활성 VM을 따른다.
- VM1/VM2는 같은 Zephyr의 주 구동/백업, VM3는 Debian. 주행 모드와 활성 VM은 별개다.
- 표시 서비스는 주행 승계/VM 재시작을 실행하지 않는다. 상태 미수신을 정상으로 추정하지 않는다.

## 1. 권위 있는 제어 상태

현행 수신기는 `source_id=dom0-gateway`의 확인된 `active_vm_id`·`mode`를 읽는다. 사용자는 **제어 담당 팀원과 합의가 필요함**을 확인했다. 이 source_id의 실제 권위 발행자/구현 담당 확정은 아직 남아 있다. 제어 담당에게 메시지를 전송하거나 합의를 대신 기록하지 않았다.

**발견한 차이와 처리:** 기존 [MQTT 계약](mqtt-contract-v1.md)은 `xenova/v1/vehicle/control`, 변경 시 발행, QoS 1, retain=true다. 이전 표시 수신기는 같은 토픽의 비-retained 입력에 3초 freshness를 적용해 상태 변화가 없어도 미확인이 되는 문제가 있었다. 이번에 표시 수신 경로를 별도 토픽으로 분리했다. 기존 차량 제어 토픽/발행자/공용 계약/실제 ACL은 변경하지 않았다.

사용자는 **별도 표시용 상태 토픽을 1초마다 발행**하는 방향을 선택했다. 수신 계약은 `xenova/v1/display/drive-state`, `type=display.drive_state`, QoS 1, retain=false로 구체화했다. 매 보고 seq를 올리고 3초 미수신 시 live 주장을 제거한다. 발행자는 마지막 제어 이벤트를 무조건 주기 복사하는 것이 아니라, 제어 엔진이 현재도 확정할 수 있는 상태를 보고해야 한다. [팀원 검토용 규격](display-drive-state-v1.md)에 payload·발행/수신/ACL 역할을 정리했다.

확정할 최소 규격:

| 항목 | 검토 기준 |
| --- | --- |
| 권위 | 실제 주·백업/주행 모드를 결정하는 제어 담당. 표시 서비스가 active_app/VM 장애로 추론하지 않음 |
| payload | `active_vm_id`=등록 VM1/2 UUID 또는 null, `mode`=AUTO/MANUAL/SAFE_STOP/AUTO_READY |
| 주기/freshness | 사용자 선택: 1초 보고, 수신 후 3초 미수신이면 mode=unknown·active_vm_id=null |
| 중복/재시작 | 공용 봉투의 boot_id·agent_instance_id·seq. 매 보고 seq 증가, 이전 세대/역순 폐기 |
| 인증 | 브로커 계정/ACL로 권위 발행자만 발행. source_id 문자열만으로 인증을 대신하지 않음 |
| AI·복구 상태 | 연결 전 unknown/null 유지. AI 장애만으로 활성 VM 변경을 만들지 않음 |

**상태: 사용자 방향 선택 및 수신 구현 완료. 제어 담당의 토픽/권위/발행 구현/ACL 합의 대기.**

## 2. 대상 보드의 실행 가능성

저장소의 `xenova-xen-bringup-image.bb`는 최소 Xen 테스트 이미지이며 이 레시피에서 Xorg·Chromium·FFmpeg가 명시적으로 설치되지 않는다. `local.conf.fragment`의 dom0 메모리는 1024M이다. 실제 설치 이미지/패키지/그래픽 장치와 같다는 근거는 아니며, 이 설정만으로 렌더/캡처 가능 또는 불가능을 확정하지 않는다.

표준 Python만으로 실행 가능한 [환경 점검 도구](../../platform/services/display/router/tools/probe_board_environment.py)를 준비했다. 보드 설정·패키지·서비스·VM을 바꾸거나 GUI를 시작하지 않고 기존 도구/DRM/세션을 조회한다.

```sh
# 실제 dom0에서 실행. 가능하면 기존 그래픽 사용자 세션을 사용한다.
python3 probe_board_environment.py > display-board-environment.json
# 기존 X11 DISPLAY를 알고 있는 경우에만 지정한다.
python3 probe_board_environment.py --display :0 > display-board-x11-environment.json
```

결과 수집 성공은 HDMI 수락 성공이 아니다. 도구가 모두 있어도 ARM/Xen의 GPU 접근·실제 두 창 배치·FFmpeg 캡처는 별도로 시험한다. 사용자는 **아직 보드가 준비되지 않음**으로 답했다. 보드에 접속하거나 패키지/이미지/서비스를 변경하지 않았다.

**상태: 보드 준비 대기. 점검 도구와 수락 절차 준비 완료, 실제 보드 확인 미수행.** 실보드 수락 세부 항목은 [검증 절차](../verification/display-hardware-acceptance.md)를 따른다.

## 3. 원격 전송·측정 기준

실제 구현된 프로토콜은 X11 root 영역별 FFmpeg x11grab → MJPEG/HTTP → 원격 canvas 디코드다. 키오스크와 송신기는 별도 실행한다. 시연용 키는 feed 키와 다르고 HttpOnly 쿠키로 연결하며, 기본 loopback 바인딩과 SSH 포워딩 또는 준비된 HTTPS 경계를 사용한다. 원격 단절/송신 종료가 로컬 창을 종료시키지 않는 가상 시험을 통과했다.

사용자가 선택한 1차 기준:

| 항목 | 사용자 확정 목표 |
| --- | --- |
| 출력/전송 | 출력별 1280×720, 5 FPS, JPEG quality 7. 10 FPS는 별도 개선 |
| 지연 | 실보드/실제 시연 PC에서 로컬 화면 대비 p95 ≤500ms 목표. 시계 오차/표본 수 기록 |
| 대역폭 | 두 출력 JPEG payload 합계 ≤8Mbps 목표. HTTP/SSH 포함 실제 망 전송량도 별도 기록 |
| CPU | Pi 송신기 + 두 인코더 평균 ≤1코어 상당(1코어=100%) 목표. 로컬 렌더러와 총 시스템도 분리 측정 |
| 독립성 | 원격망/송신기 장애에도 로컬 두 출력 유지 |
| 연속 시험 | 10분 이상, 양쪽 출력/원격 디코드·FPS·CPU·메모리·단절 횟수 기록 |

사용자는 **이 기준을 1차 목표로 사용**하기로 했다. X11/MJPEG는 실보드 호환 확인 전까지 구현 기준이며, 보드가 해당 스택을 지원한다고 확정한 것은 아니다. 사용자 확정 목표와 실제 달성 여부는 따로 기록한다. 가상 검증은 [5단계 evidence](../verification/display-step5-runtime-evidence.json)를 따른다. 전체 가상 시험 RSS 약 2.8GiB를 1GiB dom0의 실제 사용량으로 대입하거나 Pi 자원 목표 달성으로 간주하지 않는다.

**상태: 사용자 1차 목표 확정 완료. 실제 보드 달성 여부는 미검증.**

사용자 선택 반영 후 Python 58개와 새 토픽의 실제 loopback MQTT/REST/WS 수락 6개를 통과했다. [후속 검증 결과](../verification/display-250-followup.md)에 구체적인 시험/합성 입력의 경계를 기록했다.

## 250 완료 판단

위 3은 사용자 기준 확정으로 정리했다. 1의 사용자 선택/수신 준비는 끝났지만 **제어 담당 합의**가 남고, 2는 **보드 미준비로 실제 환경 확인**이 남는다. 기존 Jira 완료 조건대로 250을 아직 완료 처리하지 않는다. 실제 HDMI 전체 수락은 후속 출력·전송·통합 이슈에서 수행하되, 250은 적용할 구성과 그 근거를 먼저 확정한다. 현재 Jira 상태/완료 조건은 변경하지 않았다.
