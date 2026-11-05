# 253 — 갱신된 보드의 warm-drive-1 실제 수신 검증

2026-11-05, 기준 `origin/platform` 4028599. 작업 브랜치
`feat/platform/S15P31E110-253-post-update-collection`.
[선별한 실측 JSON](display-253-warm-reports-20261105.json)을 함께 기록한다.

## 변경 이유와 구현

새 보드의 주행 앱은 SAFE_STOP·대기 상태에서도 `app.lifecycle`을 보낸다.
기존 표시 수집기는 heartbeat/readiness만 처리하므로 실제 보고를 화면 정책에 반영하지 못했다.
기존 `xenova_mqtt.lifecycle.parse`를 재사용하고 두 Zephyr binding에 명시한
`report_profile: warm-drive-1`에서만 해당 보고를 수락한다. 기존 verified profile의 동작은 유지한다.

VM UUID와 앱 ID를 함께 비교하고 QoS 0·retain 없음·1024바이트 제한·프로파일·필드·카운터를 검증한다.
감독 check와 계산 progress를 별도로 보관한다. 새 check만으로 READY 계산의 300ms 유효기간을
연장하지 않는다. publisher/실행 변경·단절·역전된 값은 기존 상태를 정상으로 되살리지 않는다.
화면은 준비 대기·대기 준비·종료·정리 미확정을 구분하며, 제어 권한·실행 신원을 승인하지 않는다.
100ms 감독 check와 그 봉투 sequence만 바뀔 때 render_revision을 바꾸지 않아 불필요한 렌더 ACK 무효화를 줄인다.

## 실제 보드 관측

| 구분 | 등록 UUID | 확인한 보고 |
|---|---|---|
| VM1 주 구동 | de113a23-d80e-4333-94b8-788babe0e41e | autonomous/manual 각각 app.lifecycle |
| VM2 백업 | 5002a22d-63d6-4f7a-9106-193b14499898 | autonomous/manual 각각 app.lifecycle |
| VM3 Debian | 17cad3c4-b3cd-4b29-8740-e679d0eaebd4 | agent.heartbeat, retained agent.inventory |

- VM1·VM2는 각각 두 VM-scoped 토픽을 사용했다. 첫 10초 캡처에서 네 토픽 각각 lifecycle 99개를 받았다.
  실제 payload는 warm-drive-1, generation 1, WAITING, NOT_READY, progress_seq `0`이었다.
  autonomous는 STATE_PREPARING, manual은 INPUT_UNAVAILABLE이었다.
- Debian은 inventory의 `os.id=debian`과 같은 Agent 세션의 live heartbeat를 구분해 수신했다.
  Zephyr의 OS inventory와 Debian interaction 앱 heartbeat는 관측하지 못했다. 설정된 OS를 관측값으로 복사하지 않았다.
- 기존 observer 읽기 계정을 비공개 임시 파일로 내보내 사용했다. 정확한 7개 토픽 모두 SUBACK granted.
  계정·ACL·비밀번호·브로커 설정은 바꾸지 않았고 publish/제어 명령은 0회였다.
- 기존 Manager 프로세스의 선택형 공유 관측 writer를 임시 활성화했다. 수집기는 이 파일만 읽었으며
  별도 Xen/Manager 조회를 생성하지 않았다. 실제 Xen 실행 incarnation과 원 관측 시각을 보존했다.
- Gateway의 기존 읽기 전용 context는 SAFE_STOP, DISARMED, output_permitted=false였다.
  선택 owner가 VM1이어도 표시의 active_vm_id는 null이었다. 두 drive target UUID의 검증도 통과했다.

## 단절·복귀와 API/WS

실제 broker에 연결한 수집기 진단 인스턴스에서 다음을 확인했다. 진단 인스턴스도 공유 파일을 읽었고
별도 Xen 조회는 0회였다. VM·브로커·Gateway를 멈추거나 네트워크를 차단하지 않았다.

1. 네 lifecycle source와 Debian Agent가 fresh인 상태를 확인했다.
2. **수집기 자신의 MQTT 연결만** 종료하자 네 lifecycle current가 모두 null,
   source는 disconnected가 됐다. Debian Agent도 disconnected가 됐다.
3. 다시 연결하자 네 앱은 증가한 check_seq의 새 보고로 fresh에 복귀했다.
   Debian Agent의 sequence도 증가했다. 오래된 보고를 연결 시점으로 갱신하지 않았다.
4. 수정된 실제 feed의 `/api/v1/display/state`와 `/api/v1/display/presentation`에서
   세 UUID와 두 Zephyr의 준비 대기 정책을 확인했다.
5. `/ws/v1/display/state`는 인증 후 HTTP 101, display.state 두 프레임의 sequence 3→4,
   세 UUID와 네 앱 보고를 전달했다. 상태 WS에는 게스트 명령을 보내지 않았다.

## 확인한 한계와 남은 배포

- **공유 VM 상태가 간헐적으로 3초를 초과하거나 unavailable이었다.** 단절/복귀 진단에서도 공유 파일 읽기 실패가
  3회 있었다. 3초 한도를 늘리거나 마지막 값을 fresh로 만들거나 별도 Xen 조회로 우회하지 않았다.
  재발 시 공유 관측 writer의 원 조회 소요·하위 관측 시각을 확인해야 한다. 팀원 Manager/플랫폼 코드는 수정하지 않았다.
- 재연결 후 Debian heartbeat는 회복했지만, 같은 sequence의 과거 retained inventory는 최신 근거로
  갱신되지 않았다. `os.observed=debian`은 마지막 보고값이고 inventory source는 disconnected로 남았다.
  새 inventory 근거 수신 전까지 현재 OS 재검증 완료라고 표현하지 않는다.
- 실제 실행은 SAFE_STOP이어서 계산 READY·progress 증가·AUTO/MANUAL 전환을 실보드에서 만들지 않았다.
  READY lease·QUALIFIED 구분·역전/중복·publisher/실행 변경은 로컬 시험으로 검증했다.
  이 자료는 실제 차량 출력이나 미러링 완료의 증거가 아니다.
- 보드에는 기존 display.env와 deployment bindings가 없었다. 수정 코드는 `/run`의 별도 패키지에서
  loopback 18082로 시험했으며 설치된 Python 파일을 덮지 않았다. 정상 패키지와 실제 bindings·읽기 계정·동일
  shared observation 경로를 배포하고 공통 feed를 웹 중계/HDMI 소비자에 연결하는 단계는 남아 있다.
  이 검증만으로 253 전체를 완료 처리하지 않는다.
- 기존 MO-04 콘솔의 외부 웹 출력·점유/재연결 검증은 251 자료를 재사용한다.
  이번 수집기는 xl console을 열지 않았고 로그인 정보·터미널 입력을 수집하지 않았다.
  실제 HDMI 렌더러·휴대폰·영구 배포는 이번 시험 범위가 아니다.

## 원복 결과

임시 feed와 진단 MQTT 연결을 종료하고 임시 root 전용 계정 파일·토큰·패키지·관측 파일을 삭제했다.
Manager의 `/run` 임시 observation drop-in을 제거하고 API를 재시작해 원래 설정으로 복귀했다.
기존 콘솔 활성화 설정은 유지했다. 마지막 인증된 VM 목록은 HTTP 200, 세 UUID 모두 running이었다.
설치된 수집기 파일 해시는 시작 전과 같았다. Mosquitto/Gateway/Recovery Worker/로그 수집기/Tailscale PID도
시작 전과 같았다. Gateway generation 1·SAFE_STOP·DISARMED·출력 허가 없음도 유지됐다. SSH 연결을 종료했다.

원복 확인 스크립트의 첫 VM 목록 요약은 응답을 dict로 가정해 TypeError가 났지만 HTTP 200은 수신했다.
이어 실제 list 응답 형식으로 다시 읽어 세 VM running과 임시 파일/override 부재를 확인했다.
원복 실패나 API 실패로 기록하지 않으며 해당 보정도 JSON에 남긴다.

## 로컬 검증

- 수집기 전체: 411 passed, 1 skipped. 변경 후 추가한 warm REST/WS 통합 시험 포함 warm 시험: 26 passed.
- Starlette/httpx 사용 중단 예정 경고 1개. 실패는 없으며 의존성을 변경하지 않았다.
- `git diff --check` 통과. 후보 bindings의 UUID는 가짜이며 실물 UUID로 배포해서는 안 된다.
- Jira 완료 처리는 하지 않았다. 정상 배포와 공유 관측 지연 확인은 위 남은 항목을 따른다.
