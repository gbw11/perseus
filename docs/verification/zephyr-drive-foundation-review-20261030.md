# Zephyr 공통 실행 기반 정합성·최적화 검토

기준: `eca59e1ba272496b7c661bcd5088b73624ed0763`, 검토 브랜치
`feat/platform/zephyr-drive-foundation-sub`. 2026-10-30에 로컬 소스·봉인 자료·추가 반례를 대조했다.
HEAD 이후의 후보는 아래 diff·새 시험·후보 봉인으로 구분한다. 실보드·제품 종단 완료 보고가 아니다.

## 결론

계획 AUTO 전환의 명시적 재개 우회를 수정했고, 원자료 회수의 반복 출력량을 줄였다.
별도로 C 입력 수신의 **중립 교체와 같은 원 sample의 나이 갱신**을 재현했다.
두 C 결함은 수신 guard와 앱 instance에 묶인 중립 근거 전달로 보완했다. 최종 원 조건 native A/B는 각각 42단계를 통과했다. 실제 UI 생산자·장치·실보드 수락은 별도다.
Gateway의 별도 출력 보호, 기존 신원·기한·종료 계약은 유지한다.

## 기준과 근거 재사용

- [주행 설계](../platform/drive-pipeline-architecture.md)와
  [복구 시나리오](../platform/recovery-scenarios.md)는 제공된 Windows 파일과 해시가 같았다.
  설계·시나리오·필수 시간값은 변경하지 않았다.
- 앞선 [기반 검증](zephyr-drive-foundation-20261029.md)의 마지막 후속 절까지 읽었다.
  SOURCE_SEAL 81개 파일이 기준 소스와 일치했다. seal의 HEAD는 당시 미커밋 기준 d488b84이고,
  COMMIT_MAPPING의 최종 eca59e1 및 실제 파일 해시로 커밋 후 동일 후보임을 확인했다.
- 앞선 Gateway 122, 공용 MQTT 149/4 opt-in skip, 플랫폼 91, Engine/Manager/rejoin 9,
  host C 5/5, native warm main/context 5/5와 최종 session pair의 통제 범위를 재사용한다.
  전체 dom0-manager 557은 선행 후보 근거이며 최종 후보 전체 재실행으로 바꾸지 않는다.

## 보장별 대조

| 보장·기준 | 실제 생산/소비·확인 지점 | 판정 |
|---|---|---|
| H1·H3, 모드/owner/출력 구분 | Authority → planning → WarmDriveMachine → VerifiedGatewayRunner | 원 준비는 generation 불변, 원자 커밋 후 새 계산/적용 전 출력 금지. R01의 우회 추가 수정 |
| F1·F5·G2·GO1의 원 기록 보호 | Authority transition/spec/deadline, commit/replay, 저장 미확정 시험 | 원 ID·spec·기한·COMMITTED 보존. 조회/replay 추가 저장·물리 동작 없음. Worker 저장 수명 정책과 혼동하지 않음 |
| A4·AO4, 현재 실행/앱 바인딩 | Bootstrap·receipt → BindingCache.guard → Gateway/Worker lifecycle | seq 전에 현재 VM/boot/앱 instance를 검사. 앱별 교체·가용성과 다른 앱 보존 근거 유지 |
| B1·B2·C1·C2, 독립 실행/정리 | main → workers.offer/read → 실제 adapter, cancel/join/restart | callback/Store를 metadata lock 안에서 기다리지 않음. 원 release·최신 작업·잔존 재사용 금지. 기존 소유권 유지, 원 표본·중립 근거 전달 추가 |
| WAITING·실제 계산·출력 | C warm_report → app.lifecycle → warm Gateway/Worker | QUALIFIED는 계산/출력 아님. 진행 없는 READY가 계산 근거를 무한 갱신하지 않음. stateful 계획 연결은 의도된 후속 |
| DEC-12·ARCH-T16, 중립 보존 | on_publish/latest_slot → io snapshot → worker → manual gate | **R02 수정.** 수락한 중립 사본을 계산까지 보존하고 현재 앱 instance/generation/session 검사 |
| A5·AO4·MQTT 7.1, 원 표본 신선도 | 위 C 경로, Python SessionEvidence 비교 | **R03 수정.** 원 sample 중복·역전은 cache 교체 전에 거절. 원 수신 시각과 Gateway 별도 보호 유지 |
| 원자료 동일 실행·창·페이지 | collector anchor → 반복 데이터 페이지 → analyze | O01 최적화 후 실행/창 교체·누락·미확정·기한 거절과 실제 PTY framing 회귀 유지 |
| 운영/실험 배포 구분 | operations/experiment/warm profiles, Kconfig·BUILD_RECEIPT | 선택 recorder/상세 scan 분리 유지. 최종 ARM 3개 프로필과 번들 검사 통과. 이미지/설치 수락 아님 |
| 장치/제품/복구 책임 | Gateway serial 제출, Worker 원 복구, 담당 요청서 | serial acceptance는 장치 적용 아님. 실제 UI 입력·stateful 전달·운영 수습·보드/이미지 수락은 별도 |

## 발견과 반영

### R01 — 계획 AUTO가 기존 재개 승인을 우회: 수정

MANUAL → AUTO_READY 이후 별도 `on_resume` 호출 없이 `planning.begin(AUTO)` → refresh → commit을
수행하면 0.2초의 fixture 구간 뒤 AUTO/출력 허가와 실제 ForwardDrive가 확인됐다.
A/B 각각에서 같은 VM·다른 VM 대상을 합친 네 사례 모두 수정 전 assertion이 실패했다.
이는 제품 HTTP가 노출됐다는 의미가 아니라 현재 로컬 actor 연결부의 계약 위반이다.

`planning.py`의 새 계획 수락과 커밋 직전 유효성 검사에서 AUTO 복귀 우회를 차단했다.
계획 AUTO는 이미 AUTO인 상태의 인계에 사용하고, MANUAL/AUTO_READY의 복귀는 기존 명시적
재개·준비 유지·3초 카운트다운으로 진행한다. 저장된 미완료 준비도 재검사한다.
원 COMMITTED replay는 guard보다 먼저 원 결과를 반환하므로 이후 모드/사용자 stop과 섞이지 않는다.
새 회귀는 대칭 4건, 과거 PREPARING/PREPARED 2건, 정상 재개 후 인계 2건, 실제 actor 1건이다.
현재 정상 MANUAL owner를 거절만으로 멈추거나 새 generation을 발급하지 않는다.

### R02 — 수신된 중립이 계산 전 교체됨: 수정

실제 `xm_zclient.c:on_publish/latest_slot`을 그대로 추출하고 실제 C decoder·순서 검사·
앱 adapter를 연결했다. 외부 Zephyr MQTT payload read/커널 시각과 저장 구조만 대역이다.
중립(sample 1/action 1)과 up(sample 2/action 2)을 모두 수락한 뒤 계산하면 최신 up만 남아
속도는 0이다. 같은 수락 표본을 각각 계산에 전달하면 속도 1이다.
`manual_session.h`의 latch가 계산 호출에서만 갱신돼 수신 단계의 중간 중립을 보존하지 않는다.
정식 7.1절의 최신 슬롯과 별도 중립 보존 및 기존 담당 요청의 수락 조건과 불일치한다.

이 차단은 정지 방향이며 실제 장치의 잘못된 이동을 입증하지 않는다. 수정은 v1 수신 소유자가
검증된 각 원 표본의 중립 근거를 제한된 사본으로 보존하고, session/generation 및 현재 앱
instance와 묶어 계산 작업의 불변 사본에 전달한다. 기존 worker lifetime 검사는 그대로 사용한다. 수신 횟수를 계산 진행으로 바꾸지 않는다.

### R03 — 같은 원 sample의 새 봉투가 C 나이를 갱신: 수정

같은 C 경로에서 원 sample 1을 1000ms에 수락하고, 같은 sample/action/내용을 새 봉투 seq로
1300ms에 수락했다. cache 나이는 다시 0ms가 됐고 CONTINUE_SESSION 계산은 속도 1을 냈다.
원 수신 기준 300ms로 계산하면 INPUT_STALE·0 출력이다. 같은 원 sample이 현재성을 갱신하지
않는다는 7.1절과 불일치한다. generic envelope seq 중복 제거는 원 sample 중복 제거가 아니다.

Python SessionEvidence는 중복 원 sample을 거절하므로 Gateway의 독립 입력 만료 보호는 유지된다.
이 결과를 최종 차량 출력·장치 적용 위반으로 확대하지 않는다. R02와 같은 수신 경계에서
`manual_input.h`의 수신 guard가 원 session/sample 중복·역전을 거절해 latest의 최초 수신 시각을 유지한다.
앱 instance 교체는 중립 근거를 철회하되 원 sample의 수신 시각을 새로 만들지 않는다. invalid-session
통지 뒤 새 유효 입력이 겹쳐도 같은 전환의 차단이 없어지지 않는다. 새 제어 문맥의 정당한 새 표본은
다시 수락하며 통신 수신 횟수는 계산·준비 진행에 포함하지 않는다.

C 수정 위치와 별도 제품 수락 조건은 [플랫폼·Gateway 직접 구현](../requests/20261029-request-gateway.md)에
기존 DEC-12·ARCH-T16으로 누적했다. 외부 담당자에게 C 결함을 떠넘기지 않는다.

### O01 — anchor의 불필요한 데이터 반복: 반영

매 데이터 페이지 전후의 신원/창 anchor를 기존 CLI가 지원하는 `records 0 1`로 읽는다.
신원·summary·창·total 검사는 같으며 실제 데이터는 기존 4행 페이지를 두 번 읽는다.
CLI/wire/schema·전체 300초·페이지별 재시도 수·누락 거절을 바꾸지 않았다.

같은 6+6행 fixture에서 요청 18회·페이지 4개와 최종 원자료 SHA-256은 같았다.
반복 전달된 XD_ROW는 52→22행, 응답은 13,282→9,892바이트(약 25.5% 감소)였다.
이는 fixture의 출력량 비교이며 PV/UART 회수 시간·지터·실보드 성능 개선 측정이 아니다.

## 변경·후속 수정 위치

| 항목 | 코드·시험·소유 문서 |
|---|---|
| R01 반영 | [planning.begin/valid/commit](../../platform/services/gateway/xenova_gateway/planning.py), [새 회귀 9건](../../platform/services/gateway/tests/test_planning_resume.py), [Gateway 사용 조건](../../platform/services/gateway/README.md) |
| R02·R03 반영 | [on_publish/latest_slot](../../platform/apps/common/xenova-mqtt-c/ports/zephyr/xm_zclient.c), [입력 snapshot](../../platform/apps/common/xenova-drive-runtime/src/io.c), [manual gate](../../platform/apps/drive/zephyr/src/manual_session.h), [현재 지원 제한](../../platform/apps/drive/README.md) |
| O01 반영 | [collector](../../platform/apps/common/xenova-drive-runtime/tools/collect_console.py), [회수 회귀](../../platform/tests/test_xenova_drive_collection.py), [회수 절차](../../platform/apps/common/xenova-drive-runtime/README.md) |

## 추가 native 결과의 해석

수정 Gateway와 기존 봉인 native 파일을 실제 격리 broker/독립 구독기/serial encoder에 연결했다.
수정 전 시험 A 시작은 31단계 후 `fallback-auto-manual-new-action-moves`에서 실패했다.
원자료에서 해당 중립은 한 표본(약 50ms 간격)이고 뒤에는 607개 같은 generation 명령이 모두
0 출력이었다. 독립 구독기 수신은 C 수신 자체의 ACK가 아니므로 이 실패가 C의 교체인지
QoS0 전달 문제인지까지 해당 자료 하나로 확정하지 않는다. R02·R03은 별도 결정적 C 반례다.

중립을 네 원 표본 동안 유지하는 별도 조건 실험에서는 42단계가 통과했으나, 이 결과로 원래
조건의 결함을 마감하지 않는다. 그 fixture 변경은 제품 트리에서 원복하고 실험 소스를 별도
감사 경로에 보존했다. 입력 유지 시간·생산자 주기·소비자 ACK 계약을 임의로 바꿔 통과시키지 않는다.
후속 보완·platform 병합 후보의 원 조건 native pair는 A/B 각각 **42단계 통과**했다.
같은 최종 native SHA-256 `879cdc4ab2d4fe70d9722f18453b57add778af14c804e884add13336d9dec9f2`를
두 실행에 사용했다. 원 sample 생산 주기·중립 유지 시간을 바꾸지 않았고, 별도 telemetry-UUID
계정/ACL은 최신 platform과 합쳤다. 실제 Spring/UI 세션 생산과 장치 적용·Xen 보드 수락은 미완료다.

## 저장·잠금·비용 검토

실제 WSL ext4 계열 소유 임시 디렉터리의 Authority를 5회 계측했다.
새 begin/최초 refresh/commit은 각각 원자 저장 1회, 중복 refresh/get/원 commit replay는 0회였다.
cache current 조회는 순서대로 2/3/6회이며 get·replay는 0회다. commit의 refresh와 마지막 guard는
서로 다른 경계이므로 단순 중복으로 제거하지 않았다. MQTT/Manager/Store 호출은 추가하지 않는다.

15회 원자 저장의 관측 범위는 9.005~13.888ms였다. 공유 호스트 부하·계측 비용을 포함한 표본이며
보장 상한이나 보드 수치가 아니다. 이 fsync는 actor에 남는 비용이므로 1ms drain budget이나
5ms tick이 전체 판정 지연 상한이 되지는 않는다. 필수 내구 저장·신원 검사를 줄이지 않았다.
32건/64KiB 전환 이력의 용량 차단은 이미 선언된 운영 제한이다. 자동 삭제/DB/새 데몬을 추가하지 않았다.

## 검증·패키징과 후보

아래 시험은 기존 격리 도구 경로를 사용하는 `run_checks.py`로 실행했다.
실행 인자·종료 코드·소요시간은 각 JSON에, assertion과 출력은 같은 이름의 log에 보존한다.
기존 환경을 재사용했으며 새로운 의존성을 설치하지 않았다.

```sh
python3 -B /home/ssafy/xenova-foundation-review-20261030/run_checks.py gateway-all-after platform/services/gateway/tests
python3 -B /home/ssafy/xenova-foundation-review-20261030/run_checks.py collection-after-tests platform/tests/test_xenova_drive_collection.py platform/tests/test_xenova_drive_integration.py
```

위 이름의 로그는 이미 있으므로 그대로 재실행하면 보존 guard가 거절한다. 재실행 시 첫 인자에 새 로그 이름을 쓴다.


| 구분 | 실제 결과 |
|---|---|
| 수정 전 AUTO 반례 | 4 failed, 모두 별도 재개 없이 AUTO 출력 허가 assertion |
| 수정 후 관련 Gateway | 47 passed |
| 수정 후 Gateway 전체 | 131 passed (기존 122+신규 9) |
| collector·분석 | 33 passed (기존 32+신규 1), 실제 POSIX PTY 포함. 아래 관련 121건에 포함 |
| 병합 후 관련 Python | 121 passed, MQTT 계정/정책·Bootstrap·telemetry·초기화/종료/네트워크·회수/분석 |
| C host | sanitizer 포함 5/5, 실제 adapter와 원 표본/중립·앱 교체·invalid-session assertion 포함 |
| 실제 Zephyr thread 회귀 | I/O 29, worker 10, main 2, warm main/context 5, 신규 수신 경계 1 통과 |
| C 수신/어댑터 감사 probe | sanitizer 포함 compile/run rc 0, **위반 관측 2건**. 기능 수락 성공을 뜻하지 않음 |
| native 원 조건 | 수정 전 A 31단계 뒤 실패를 보존. 최종 후보 A/B 각각 42단계 통과 |
| native 조건 변경 실험 | A 42단계 통과, 원 조건 수락에서 제외 |
| Gateway wheel | 의존성 설치 없이 13개 .py archive 바이트 일치, SHA-256 `f7d9f1a6f131d1eebb14c7ad2f858f378069cdac82eadcaf846f448ea9e63306` |
| C/ARM | platform 병합 후 operations/warm-operations/warm-experiment 3개 프로필 신규 부분 빌드·ELF/DTS·스택·번들 검사 통과 |

최종 ARM 링커 출력의 RAM 사용은 operations 1040 KB, warm-operations 1056 KB,
warm-experiment 3552 KB이며 모두 기존 16 MB 프로필이다. 실제 동적 메모리·실보드 계상은 아니다.
worker 직접 프레임은 2656바이트/설정 stack 8192바이트, netfront 송신 직접 프레임은 48바이트다.
직접 프레임 검사는 전체 호출 경로의 실측 최대 stack 보장을 대신하지 않는다.
각 `build-arm-*-merged/BUILD_RECEIPT.json`의 source_files에는 새 manual_input.h도 포함한다.

병합 후 관련 Python의 최초 일반 사용자 실행은 root 전용 fixture 22개가 skip되고 CLI 저장 결과
시험 1개가 root 진입 전에 거절돼 실패했다. 제품 코드를 바꾸지 않고 소유 임시 경로의 root 조건으로
실행한 `contract-merged-root`는 121개 전부 통과했다. warm main의 지연 주입은 새 observed 진입점으로
대역을 다시 연결했으며 기존 assertion·지연·실제 알고리즘은 유지했다. 최초 실패 로그는 보존한다.

## 브랜치 통합과 충돌 해결

검토 브랜치는 `codex/review/zephyr-drive-foundation`에서
`feat/platform/zephyr-drive-foundation-sub`로 이미 이름을 바꿨으며 원격 codex 검토 ref는 없었다.
국소 수정 3개 커밋을 원격 feature 기준 eca59e1 위에 그대로 합쳤다. 스쿼시·rebase·force push는 사용하지 않는다.
최신 platform `ef2cb8d1157f2bc192ea9a250dfaf91580409744`의 충돌 5곳은 다음과 같이 해결했다.

- recovery-scenarios: 검토한 최신 warm 시나리오와 구현/미연결 구분을 보존.
- native pair: warm·manual-session 시험을 보존하고 최신 v1/v2 별도 계정·ACL을 결합.
- Mosquitto README/accounts/dom0-broker.sh: 최신 platform의 공통 정책·단일 Python 작성자 구성을 유지.

병합 코드 기준은 `34e3b437409297254bb6106d560dcd01af806a2e`이며 위 최종 ARM/native/Python을 연결한다.
원 feature의 develop 기반 선행 Backend·Frontend·CI 커밋도 platform 대비 diff에 포함된다.
이는 이번 검토에서 새로 수정한 제품 코드가 아니며 Java/프론트/전체 CI 실행을 이 로컬 결과로 대신하지 않는다.
충돌 원문·해결 선택은 MERGE_REVIEW.json과 merge-platform-review/에 보존한다.

변경 Markdown의 로컬 파일·anchor와 `git diff --check`도 확인했다.

새 로그·baseline·문서 입력·C probe·비용·wheel·최종 후보 봉인은
`/home/ssafy/xenova-foundation-review-20261030/`에 별도로 둔다. 기존 PC/WSL 증거와 실패는 보존한다.
최초 검토 후보의 HEAD/diff·실패는 FINAL_CANDIDATE.json을 보존하며, 후속 수정·병합 후보의
HEAD/원래 기준선 대비 diff·소스·ARM/native·시험 hash는 FINAL_INTEGRATION_CANDIDATE.json을 따른다.
기존 firmware 후보의 source hash와 이번 Gateway/tool 변경을 분리한다.

Windows 메인·WSL 원본/빌드 워크트리·진행 빌드·상위 제품·운영 상태는 변경하지 않았다.
새 의존성, 실보드·실제 VM/운영 broker·CPU/네트워크·host/SD·이미지 작업은 하지 않았다.
커밋·feature 통합·platform 충돌 해결은 후속 사용자 요청에 따라 수행한다. MR은 platform 대상이며
기존 커밋을 유지한다. 소유한 WSL 검토 워크트리 밖의 작업 파일은 수정하지 않았다.

## 동기화 반영 후 원 커밋 보존 확인

동기화 MR [!298](https://lab.ssafy.com/s15-final/S15P31E110/-/merge_requests/298)이 반영된
platform `6b2ffbe09a201dbc6f260ae2606afa97939bab18`을 기준으로 원 작업 15개 커밋의
조상 관계를 각각 확인했다. 원 SHA·작업 단위·시험/문서 내용은 유지되며, 중단된 분리
이관의 복사 커밋으로 원 작업을 대체하지 않았다.

최종 대조에서 기존 MQTT launcher 본문은 같지만 실행 권한을 0755에서 0644로
되돌리는 불필요한 차이를 찾았다. 최신 platform의 0755를 보존해 이 경로를 MR diff에서
제외했다. 순수 foundation 차이는 platform/docs 107파일이고 Backend·Frontend·CI는 없다.
이 수정은 launcher 본문·주행/복구 제품 코드·시간값을 바꾸지 않는다. 앞서 통과한
Gateway 131건·관련 Python 121건·native A/B 각 42단계·ARM 3개 프로필의 소스 해시를
대조해 근거를 재사용하며, 새 빌드나 실보드 수락으로 표시하지 않는다.

중단한 cherry-pick과 복사본 전용 로컬 review 브랜치는 스냅샷 일치와 원본 보존을
확인하고 사용자 승인 후 정리했다. 기존 sub·다른 브랜치와 원 실패 자료·검증 영수증·
최종 펌웨어·공용 캐시는 보존한다. 최종 제출 후보·원 커밋·정리 결과는 별도 증거의
`develop-sync-20261030/FOUNDATION_MR_READY.json`에 연결한다.
