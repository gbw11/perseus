# 253 실보드 사용 종료·인계 — 2026-10-29

사용자가 팀원에게 보드를 넘기도록 요청해 실보드 시험을 중단하고 복원했다.
**사용자가 다시 허용하기 전에는 보드에 접속하거나 배포·시험하지 않는다.**

## 최종 보드 상태

- 임시 상태 공유 drop-in 두 개 제거, 원래 관리 API·플랫폼 코드 경로로 복귀.
- 시험용 native identity 도구를 제거하고 원래 Python wrapper를 해시 확인 후 복원.
- `/run/xenova-state-reuse-253`, `/run/xenova-253-native-identity` 및 시험 상태 파일 정리.
- 기존 MQTT 인증·수신기 설정은 유지. MQTT 연결 성공, VM 3대 running/fresh,
  Debian Agent fresh·observed OS `debian` 확인.
- API·feed·kiosk·graphics·Mosquitto·Tailscale·recovery worker 모두 active.
- 원래 관리·플랫폼 코드와 브로커 설정 파일의 해시 유지 확인.
- VM·펌웨어·외부 서버·메모리 설정·CPU pinning은 변경하지 않았다.
- 미활성 수신 후보 `/etc/xenova/display-253-receiver/bindings-drive.pending.json`은 준비해 두었다.
  scope=candidate이며 현재 활성 bindings는 그대로다.

근거: 작업 공간 `outputs/display-253/state-reuse-20261029/handoff-final.json`.

## 로컬 작업에 남긴 내용

기존 목록 조회 약 7초 중 identity helper 6회에 약 5.3초가 걸렸다.
같은 고정 Xen ABI·검증·subprocess 시간 제한을 유지하는 C 도구를 만들어,
실제 세 VM에서 기존 도구와 동일한 식별자·상태를 확인했다.
표본별 Python 1.2~1.45초 대비 native 15~29ms였고, 이후 전체 검증 목록 조회의
한 표본은 약 1.84초였다. **이 도구는 인계를 위해 보드에서 다시 원복했다.**

상태 공유에는 단일 검증 목록에서 원래 관측 시각·incarnation을 전달하도록 수정했다.
VM별 snapshot 재조회는 제거했다. 같은 플랫폼 잠금 안의 읽기 전용 list에 한해
생명주기 메타데이터를 재사용하며 get·제어·복구의 검증 경로는 유지한다.
마지막 목록 최적화 후의 실보드 연속 신선도 시험은 인계 때문에 수행하지 못했다.
**상태 공유가 실보드 검증을 통과했거나 253이 완료됐다고 처리하지 않는다.**

인계 후 로컬 최종 검증: 606 tests와 78 subtests 통과. MQTT 관측 제공자 재시작 시
inventory 재구독, 공유 모드 opt-in·기존 기본 경로 유지, API 계약, 관리·복구·CPU·native ABI·
패키징 회귀를 포함한다. 임시 복사본에 계약 문서를 빠뜨린 7개 실패는 원본 계약 경로에서
8개 계약 테스트를 다시 실행해 모두 통과했다(중복 1개 제외 총 606개).
변경은 `feat/platform/S15P31E110-253-state-reuse-mqtt`에서 platform 대상 MR로 제출한다.
기준 platform 커밋은 `1ed0653`이며 실제 병합은 수행하지 않는다.

사용자는 VM1·VM2 새 펌웨어가 아직 적용 전이라고 확인했고 수신 준비까지만 요청했다.
실제 VM별 autonomous/manual heartbeat·readiness 수신 및 끊김·복귀 시험은
친구의 펌웨어 적용과 다음 보드 사용 허용 후 진행한다.
