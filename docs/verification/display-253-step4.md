# 253번 4단계: 장애·재접속·재시작 검증

2026-10-22 · `feat/platform/S15P31E110-253-display-source-collection` · 기준 커밋 `21b22d2`.

**사용자가 확정한 수신·표시 범위의 4단계는 완료했다. 전체 연결 경로의 게스트 네트워크 자동 복귀는 미통과다.** 실패 때도 정상 화면을 되살리지 않았고, VM2를 명시적으로 재시작하면 실제 새 보고로 회복됐다.

## 사용자 확정 범위

2026-10-22 사용자는 “수신 범위 유지, 자동 복구는 담당자 의존성으로 기록”을 선택했다. 따라서 이번 단계의 완료는 수신·표시 안전성 검증을 뜻한다. VM2 앱·하위 통신의 자동 복구 수정은 별도 담당자 의존성으로 남기며, 전체 경로의 자동 복구 성공이나 253번 전체 완료를 뜻하지 않는다. 두 실험 JSON의 `passed=false`와 자동 복귀 실패 기록은 유지한다. 재현 조건·담당에게 요청할 내용·후속 확인 기준은 [인계 목록](../design/display-source-dependencies.md#자동-복구-담당자-인계)에 기록했다.

## 실제 시험 범위

[3단계](display-253-step3.md)의 VM2 heartbeat-only 펌웨어와 임시 인증 브로커/표시 수집기를 재사용했다. 사용자 허용 VM2만 제어했다. 주행 명령·입력·VM1/Debian 재시작·시스템 서비스 설치·실보드 변경은 하지 않았다.

- **실제 장애**: VM2의 정확한 UUID→domid→backend VIF를 대조한 뒤 해당 VIF만 down/up, 실제 수집기 MQTT stop/start, VM2 pause/unpause 및 시험 이미지 재시작.
- **시험 재전송**: 실제 게스트에서 받은 app.heartbeat를 복사해 중복 seq·낮은 seq·과거 boot를 임시 replay 계정으로 브로커에 발행했다. 고장 메시지를 게스트가 직접 생성했다고 표현하지 않는다. replay 계정은 VM2 manual/events만 쓸 수 있다.
- **주입 장애**: Xen 조회 실패는 시험 wrapper의 읽기 예외다. 실제 Xen 서비스 중지나 dom0 장애를 일으키지 않았다.
- **콘솔**: xl console을 호출하거나 콘솔 텍스트를 수집하지 않았다. 장애 전후 새 xl console 프로세스가 관측되지 않았다. 사람이 붙인 대화형 콘솔과 동시 조작하는 별도 시험은 하지 않았다.

[시험 확장](../../platform/services/display/router/tests/guest_resilience.py)은 opt-in runner가 임시 source 디렉터리에만 배치한다. `/test/replay`, `/test/mqtt/*`, `/test/xen/*`는 서비스 토큰을 요구하는 시험 앱에만 존재한다. 제품 `create_app`에서는 같은 토큰으로 요청해도 404다. 수신기·표시 정책의 제품 코드는 이번 단계에서 변경하지 않았다.

## 수신·표시 결과

| 상황 | 확인 결과 |
| --- | --- |
| VIF 단절, VM은 실행 중 | 앱 보고가 3초 이후 stale, app unknown, app_waiting. VM 장애/정지로 바꾸지 않음 |
| 중복·역순 seq를 실제 브로커로 재전송 | 마지막 accepted sequence/source_epoch/received_at 유지. 유효 시간 연장 안 됨 |
| 수집기 MQTT 연결 종료 | Xen running 유지, 앱 수신 경로 unavailable/disconnected, 정상 배지 제거 |
| 재접속 + SUBACK + 저장된 과거 retained 보고 | 실제 retained=true 수신을 별도 관측. 높은 seq여도 과거 캐시로 fresh를 되살리지 않음 |
| 단절 후 VM2 명시적 재시작 | 실제 새 guest 보고로 fresh/status 복귀 |
| 실제 VM2 일시정지 | 과거 앱 보고보다 paused 표시가 우선. unpause 후 복귀 확인 |
| Xen 읽기 예외 주입 | 앱 보고가 신선해도 VM 관측은 disconnected, 표시 stale, app unknown. 읽기 복구 후 회복 |
| 실제 VM2 정지 | stopped/unknown. 정지 중 늦은 앱 보고로 정상화되지 않음 |
| 실제 VM2 새 실행 + 과거 boot 재전송 | run_id와 실제 발행 세대가 변경됨. 이전 실행의 보고가 새 보고를 덮지 않음 |
| 콘솔 비점유 | 시험 코드의 xl console 미호출, 새 client 미관측 |

신선도는 수신기의 monotonic clock을 기준으로 한다. Zephyr의 1970년+uptime sent_at과 클라이언트 생성 boot_id를 동기화된 벽시계나 검증된 OS 부팅 근거로 바꾸지 않는다. 실제 domid는 증가한 실행을 관측했으며 동일 domid 재사용·조회 사이의 빠른 재시작을 완전 검증한 것은 아니다.

### retained 시험 방식 보완

초기에는 이미 구독 중인 feed에 retain=true로 발행하면 같은 표시가 전달된다고 가정해 시험이 실패했다. 실제 실시간 전달에서는 해당 수신 플래그가 false였다. 이를 제품의 과거 보고 거부 실패로 판정하지 않고, **feed를 먼저 끊고 저장된 값을 만든 뒤 다시 구독해 실제 retained=true 전달을 관측**하도록 수정했다. 실제 sender의 retain=false 계약은 그대로 유지한다. 수신자가 실시간 전달만 보고 원 발행의 retain 설정을 검증할 수 있다고 주장하지 않는다.

## 미통과: VIF 복원만으로 게스트 보고가 돌아오지 않음

두 프로필 모두 VIF를 다시 UP으로 올린 뒤 90초 안에 새 app.heartbeat가 도착하지 않았다. feed는 계속 미확인을 유지했다. 명시적 시험 VM2 재시작 후에는 신선한 실제 보고가 돌아왔다.

| 비교 | 결과와 근거 |
| --- | --- |
| A: 기존 main priority 0 | 수신/표시 안전성 통과, VIF 자동 복귀 실패. [전체 결과](display-253-step4-priority0-evidence.json) |
| B: 시험 main priority 10 | 같은 자동 복귀 실패. shutdown=6/netfront=7/main=10을 실제 빌드에서 확인. [전체 결과](display-253-step4-priority10-evidence.json) |

A의 우선순위는 시험 전 `.config` 조회로 확인했으며 A JSON에는 후속 추가된 compiled_thread_priorities 필드가 없다. B에는 해당 필드를 포함했다. 이 비교는 우선순위 변경 단독으로 문제가 해결되지 않았다는 근거이며, 특정 드라이버나 스케줄링을 원인으로 확정하지 않는다. 빌드 준비 도구의 기본 main priority는 기존 0을 유지하고 10은 `--main-priority 10` 비교 옵션으로만 제공한다. 차량 제어 스케줄링 기준을 정하지 않았다.

필요한 후속 확인은 **기존 C MQTT의 blocking send/keepalive·재연결 진행, netfront/netback worker의 단절 후 상태·이벤트 처리, 지원하는 링크 복귀 범위**다. 앱·하위 통신 담당과 원인을 좁히거나 수정 범위를 별도로 정해야 한다. 이번에는 원본 sender/하위 드라이버를 임의 수정하지 않았다. 재현용으로 첫 실패와 우선순위 비교 결과를 보존했다.

## 회귀·원복·재현

- Python 전체 회귀 87개 통과. 이후 시험 관측 주소의 제품 404 경계 확인을 추가해 해당 HTTP/WS 시험 1개만 재확인했고 통과했다. 기존 TestClient/httpx 폐기 예정 경고 1건이 있었으며 실패는 없었다.
- 두 실제 시험 모두 `verification_completed=true`, `display_safety_checks_passed=true`, `live_vif_recovery_passed=false`, `passed=false`로 기록했다. 실패한 자동 복귀를 전체 통과로 숨기지 않는다. 최신 runner도 passed=false이면 CLI 종료 코드 1을 반환한다.
- VM2의 instance.json·원본 firmware·runtime cfg SHA256 복원 확인. 최종 VM2 domid 24이며 원본 콘솔 Starter/NIC 없음으로 복귀했다. VM1 domid 3·Debian domid 1은 그대로다.
- [최종 상태](display-253-step4-final-state.json): 시험 runner 0개, 시험 MQTT/API listener 없음, 기존 SSH만 존재. 시험 바이너리/cfg는 정리했고 원본 보관 사본은 유지했다. QEMU와 원본 VM은 켜 두었다.

```sh
# router 디렉터리, 사전 허용한 VM2 시험 환경과 ARM64 wheels가 필요함
python -B tests/probe_zephyr_ethernet.py --execute --resilience \
  --build /path/to/authorized-trial-build --wheels /path/to/arm64-wheels \
  --output /path/to/evidence.json
```

실보드·운영 이미지·VM1 송신·Spring 외부 REST/WS·실제 HDMI 렌더/원격 영상 수락은 이번 결과에 포함하지 않는다. 외부 중계와 화면 소비는 5단계 범위이고, 현재 자동 복귀 미통과는 별도 의존성으로 남긴다. 커밋·푸시·Jira 변경은 수행하지 않았다.
