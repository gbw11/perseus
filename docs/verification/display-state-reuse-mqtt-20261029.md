# 253 상태 공유·MQTT 인증 검증 — 2026-10-29

## 결과

| 항목 | 결과 |
| --- | --- |
| 기존 observer 인증으로 표시 수집기 연결 | 실보드 통과, 정식 환경 파일·별도 drop-in으로 유지 |
| 필요한 6개 토픽의 SUBACK | 모두 granted |
| 실제 Debian Agent heartbeat | 화면 상태에서 fresh 확인 |
| Debian OS 정보 | 같은 boot/Agent 세대의 heartbeat와 대조 후 `debian` 표시 확인 |
| 기존 상태를 공유하는 구현 | 로컬 통과, 실보드에서는 갱신 간격 기준 미달로 비활성화 |
| VM 제어·게스트 입력·MQTT 명령 발행 | 수행하지 않음 |

**중복 Xen 조회 제거가 실보드에서 완료된 것은 아니다.** 현재 보드는 기존 API와
원래 화면의 Xen 조회 경로로 복원했으며, 인증과 수신 순서 수정만 반영했다.
상태 공유는 플랫폼의 검증된 관측을 3초 안에 지속 갱신할 수 있어야 활성화할 수 있다.
VM1/VM2의 최신 펌웨어 보고·주행 준비·미러링·복구 완료도 이 시험으로 판정하지 않는다.

## 실제 수정과 검증

기존 브로커는 익명 연결을 거부하며 observer는 `xenova/v1/#` 읽기만 허용한다.
수집기 프로세스에는 MQTT 인증정보가 없었다. 기존 계정의 비밀값을 보드 안에서만
전달해 원래 연결·ACL을 유지했다. 인증정보를 PC·Git·시험 출력에 복사하지 않았다.

추가로 실제 초기화 순서 문제가 있었다. MQTT가 첫 Xen 조회보다 빨리 준비되면
retained inventory가 현재 VM 실행을 확인하기 전에 도착해 거절된다. heartbeat를
이후 수신해도 inventory가 재발행되지 않으면 OS는 계속 미수신으로 남았다.

수신기는 처음 **수용된** Agent heartbeat 후 inventory를 한 번 재구독한다.
브로커가 보관한 inventory를 다시 전달하며, 같은 boot ID와 Agent instance ID인 경우만
표시한다. 재접속 때도 같은 규칙을 적용하고, 매 heartbeat마다 재구독하지 않는다.
이미 정상 수신한 inventory가 있으면 추가 구독을 생략한다. 먼저 받은 inventory도
최대 4개·15초 동안만 보관하고 같은 연결·실행·Agent의 heartbeat가 올 때 반영한다.
retained 표시와 원래 수신 시각을 보존하며 재수신 중복으로 나이를 초기화하지 않는다.

실제 관측한 Debian inventory와 heartbeat는 동일 boot·Agent 세대였다.
최종 결과는 MQTT 연결 성공, 6개 구독 granted, VM 3대 running/fresh,
Debian Agent fresh, observed OS `debian`이었다.
`os.current_run_verified`는 계속 false로 유지한다. OS 보고를 검증된 실행 바인딩이나
주행 권한으로 확대 해석하지 않는다.

## 상태 공유 시험과 원복

관리 API의 보드 패키지는 저장소 기준보다 오래되어 전체 패키지를 교체하지 않았다.
실행 중인 코드를 복사한 `/run` 시험 패키지에 최소 훅만 넣고 관리 API와 표시 수집기에
임시 drop-in을 추가했다. 원래 파일·MQTT·Tailscale·VM 설정은 유지했다.

첫 구현은 조회 시작 시각을 사용해 긴 대기까지 보수적으로 만료시켰다.
이후 기존 플랫폼의 `snapshot`에서 검증 시각을 읽고, 목록과 VM·호스트 부팅·domid·상태를
대조하도록 보완했다. 파일을 읽은 시각을 새로운 관측 시각으로 사용하지 않는다.

재시험에서는 한 차례 정상 관측을 얻었지만 그 관측 나이가 약 2.6초에서 9.2초로 증가하는
동안 다음 갱신이 오지 않았다. 수집기는 이를 정상으로 유지하지 않고 미확인으로 바꿨다.
따라서 이 구성을 상시 적용하지 않고 임시 drop-in을 제거해 원래 API로 복원했다.

별도 키오스크 중지 비교에서도 VM 목록 조회는 약 5.36초에 성공했고, 다음 조회는
약 5.03초 뒤 503이었다. 비교 전에는 503, 더 이른 기준 측정에서는 약 6.95초에 200이었다.
표본이 적고 조건이 완전히 통제되지 않았으므로 성능 개선 수치로 사용하지 않는다.
화면 프로그램만이 조회 지연의 유일한 원인이라는 결론도 내리지 않는다.
비교 후 키오스크를 다시 시작했다.

다음 상태 공유 작업은 기존 플랫폼 목록 관측·직렬화·잠금 대기의 지연을 좁혀서
3초 기준을 만족시키는 것이다. freshness TTL을 몰래 늘리거나 별도 raw Xen 조회로
우회해서 이 기준을 통과한 것으로 처리하지 않는다.

## 최종 보드 구성과 보존 확인

- 활성 receiver overlay: `/opt/xenova-display-overlays/253-mqtt-receiver-20261029`.
  원래 표시 패키지를 복사하고 config/state/mqtt/app_reports만 반영했다.
  기존 main/entry의 상태 조회 경로와 HTML 화면은 보존했다.
- 인증·경로 환경 파일: `/etc/xenova/display-253-receiver/receiver.env`, root 소유 0600.
- 표시 수집기 drop-in:
  `/etc/systemd/system/xenova-display-feed.service.d/253-receiver-auth.conf`.
- 관리 API에는 시험 drop-in이 없다. 원래 관리 코드 4개 파일의 해시가 시험 전과 일치한다.
- Mosquitto credentials/passwd/ACL/config의 해시가 시험 전과 모두 일치한다.
  Mosquitto PID 643, Tailscale PID 625, recovery worker PID 1297도 시험 전과 같다.
- graphics·kiosk·feed·관리 API가 모두 active다. 원래 release 링크는
  `/opt/xenova-display/releases/20261028-ffed0f8` 그대로다.
- 비활성 상태 공유 시험의 `/run` 환경 파일 3개는 삭제했다. 시험 프로세스·임시 구독은 종료했다.
- 외부 Spring 서버, VM 설정, 펌웨어, dom0 메모리 설정은 이번 작업에서 변경하지 않았다.

receiver만 되돌리려면 위 **특정** receiver drop-in을 제거하고 daemon-reload 후
feed와 kiosk를 다시 시작한다. 다른 drop-in이나 단위를 일괄 제거하지 않는다.
이 경우 기존 인증 누락 상태로 돌아가므로 목적을 확인한 뒤 실행한다.

## 로컬 검증

서로 겹치지 않는 최종 검증 묶음:

- 화면 수집기 전체 + 상태 공유 테스트: **183 passed**.
- 기존 공개 API 계약·플랫폼 드라이버·웹 콘솔·API 흐름·제어 작업 회귀: **169 passed**.
- root 소유권·비밀 보존·observer export/ACL 방어 검사: **21 passed**.
- 합계 **373 passed**, `git diff --check` 통과.

테스트는 기존 WSL Python 가상환경으로 실행했다. 계정 테스트의 root는 WSL 임시 파일의
소유권 검사에만 사용했다. 로컬 broker 설치나 시스템 패키지 설치는 하지 않았다.
시험 중 사용한 비밀값을 제외한 JSON 근거는 작업 공간의
`outputs/display-253/state-reuse-20261029/`에 보관한다.
