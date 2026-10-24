# Codex 구현 인계 — Zephyr를 기준 Telemetry Publisher로 만들기

## 지시

S15P31E110의 하위 플랫폼·Zephyr 범위에서 `CONTRACT.md`의 Telemetry Publisher 2.0을 구현하세요. 이미 완료한 Diagnostics Core를 재작성하지 말고 실제 계측값을 정확한 의미로 발행하는 adapter와 Publisher를 추가하세요.

기존 백엔드가 허용하는 단위나 자료형에 맞추려고 내용을 줄이거나 잘못된 단위·시각·ID로 바꾸지 마세요. **새 v2 계약은 발행자 측의 제공 계약입니다.** Spring·FastAPI·프론트·Recovery Worker의 수신·저장·판정·표시는 각 담당자에게 문서로 인계합니다. 그 코드를 대신 수정하지 마세요.

`AGENTS.md`를 읽고 작업방 변경을 보존하세요. 소스/원격/브랜치/미커밋 상태를 다시 확인하세요. 첨부 Core 보고서의 기준은 `chore/platform/next-work` HEAD `87c6269…`이며 당시 push하지 않았습니다. GitHub의 예전 Starter를 현재 로컬 Core로 착각하지 마세요. `vehicle` 브랜치에 있던 실제 `auto_step()`/`manual_step()` 코드도 별도 존재하므로 기본 브랜치가 비었다고 새 주행 알고리즘을 만들지 마세요.

## 1. 구현 범위

| 구현 | 구체 책임 |
|---|---|
| 공통 발행 adapter | Core snapshot → v2 측정·상태·collection 문맥. Kernel heap/raw32/스택 high-water/분위수 의미 보존 |
| 식별/시간 adapter | 실제 VM UUID, boot 단위 공유 ID, Publisher 세션, per-stream seq, nullable UTC, host binding 수신 |
| MQTT transport | 기존 표준 Zephyr MQTT 기능 재사용. **Xen netfront→xenbr0→dom0 broker 직접 연결** |
| bounded worker | MQTT client 단일 thread 소유, queue/backoff/expiry/loss/종료 기한, thread-safe snapshot |
| inventory·heartbeat·metrics | 2.0의 5 stream 중 기본 정기 보고 경로 |
| events·snapshot | 실제 원 사건의 식별·시각·원 rc, 제한된 상세 수집/페이지, 유실 표시 |
| Network Starter 연결 | 기존 네트워크·graceful 종료·diag/stats/hook 보존. TCP/MQTT 필요한 설정 통합 |
| 독립 수신 검증 | 생산자 실제 bytes를 schema·semantic validator와 대조. 기존 backend가 없어도 시험 가능 |
| 인계 | 실물 검증 범위, 미지원/blocked, consumer 확장표, golden payload 실제 출력 비교 |

### 이번 범위가 아닌 것

- CPU pinning 재구현/명령 변경.
- Spring/FastAPI/frontend의 저장·표시·HTTP 구현.
- 동일 앱 A/B 미러링·Gateway 제어권 로직·ESP32 출력 로직.
- 임의 원격 스캔/명령/부하 주입 API.
- U-Boot/Xen/fatal 로그의 영구 저장소나 tracing engine.
- 운영 서비스에 승인 없이 broker 설정/계정/펌웨어/이미지 배포.

## 2. 구현 순서

### P1. 로컬 데이터 모델과 직렬화

1. `CONTRACT.md`, schema, catalog, fixture를 먼저 읽고 구조/의미를 테스트로 고정하세요.
2. pure Core는 MQTT에 의존하지 않도록 유지합니다. Core의 실제 C 구조체와 README의 설명을 대조하고 필드 매핑표를 작성하세요. 문서만 보고 존재하지 않는 멤버를 가정하지 마세요.
3. 없는 CPU 사용량, 실제 앱 loop, thread runtime, crash history는 unsupported/not_registered/null로 내보냅니다.
4. 부팅/오류에 stable event_seq가 없으면 발생 시점에 부여하도록 최소 보완합니다. event 입력 경로에서 MQTT·heap allocation·과도한 로그를 수행하지 않습니다.
5. JSON bytes 한도, u64/i64 문자열, finite float, status/rc/null, UTF-8 stage byte 길이를 검사합니다. 작은 writer나 기존 C 유틸을 재사용하되 대규모 신규 런타임 의존성을 추가하지 않습니다.

### P2. 신원·인증·시각

1. 영구 VM UUID를 실제 own-domain XenStore/기존 주입 계약에서 읽습니다. Kconfig의 공통 fallback UUID로 발행하지 않습니다.
2. guest boot ID와 Publisher ID를 분리합니다. test RNG가 여러 VM/재부팅에서 같은 ID를 만들 가능성을 검토합니다. 신뢰할 생성/host 주입 방법이 없으면 identity 오류로 송신을 보류하세요.
3. host 실행 문맥이 없으면 unbound/null을 사용하세요. 임의 execution_id를 발급해서 Xen 신원처럼 사용하지 마세요.
4. UTC가 없으면 null이며 절대 uptime를1970년날짜로 인코딩하지 않습니다.
5. 실제 인증정보는 개별 VM용으로 주입하고 source/build artifact/log/Golden에 평문 비밀을 남기지 않습니다. 기존 안전한 경로가 없으면 필요한 최소 platform bootstrap adapter와 검증을 명시하세요.

### P3. MQTT·부하·종료

1. 기존 주행 client를 깨지 않는 별도 Telemetry instance로 시작하세요. 기존 `xm_zclient`를 일반화하는 경우 기존 caller 회귀를 유지하고 정적 parser scratch의 동시 호출 문제를 해결해야 합니다.
2. 네트워크 연결이 지연되거나 broker가 없어도 Core와 trial loop는 계속 실행 가능해야 합니다. 단순히 `poll(timeout)`에 값이 있다고 내부 connect/send까지 모두 bounded라고 가정하지 마세요.
3. 5 stream 각각 QoS/retain을 정확히 구현하세요. live samples는 offline 재생 금지, 사건은 원 발생 ID/시각 보존·bounded RAM queue.
4. diagnostics on/off와 Publisher on/off를 별도로 기록하세요. 직렬화/전송 비용과 Core hook 비용을 구분하세요.
5. 기존 quiesce·TX drain·Xen 종료 경로와 조화시키고 broker 부재로 종료를 무한 대기하지 마세요.
6. 스레드 분석은 기본 자동 실행하지 않습니다. 시험 중 interrupt latency를 유발하는 상세 scan을 가볍다고 가정하지 마세요.

### P4. 독립적인 발행 수락

- `native_sim`에서는 실제 producer가 생성한 메시지를 수신·검증합니다. JSON fixture를 그대로 publish해 제품 구현을 통과했다고 하지 마세요.
- 격리 broker와 read-only reference subscriber로 테스트합니다. 운영 broker·계정·망에는 승인 없이 적용하지 않습니다.
- packet submit 성공, PUBACK, subscriber 수신·semantic PASS, backend 저장을 각각 별도로 기록합니다.
- broker ACL은 타 VM 쓰기 거절을 실제 subscriber 측으로도 확인합니다. MQTT 3.1.1에는 publish 권한 거절이 항상 실패 ACK로 오지 않습니다.
- 실제 Xen/보드 시험은 승인된 창에서 수행하고 UART·Wi-Fi·기존 UUID·게시 firmware를 보존하세요.

## 3. 수락 시험 C-01~C-15

| ID | 시험 | 통과 기준 |
|---|---|---|
| C-01 | 실제 inventory·heap·net·진단값 | 같은 실행의 로컬 Core snapshot과 wire 값·단위·오류가 일치 |
| C-02 | unsupported/not_registered/no_sample | null+사유. CPU/loop 없는 곳을 정상0으로 표시하지 않음 |
| C-03 | reporter만 생존 | 실제 loop 완료 정체를 보고. app healthy 선언/완료 수 대행 증가 없음 |
| C-04 | 부팅/오류 source ring overflow | 원 total·retained·capture 이전 loss·publisher loss를 구분 |
| C-05 | 큰 payload·page | 4096byte 상한, JSON 중간 절단 없음, 누락 part는 incomplete |
| C-06 | 64비트·음수·wrap | 2^53 이상 보존, negative jitter 보존, raw32 wrap 확대 위장 없음 |
| C-07 | hist/no sample/saturation | 누적·근사 상한·표본 수 보존. 열린 버킷·포화에서 null |
| C-08 | A/B 동시 발행 | 동일 논리 metric/seq에도 토픽·신원·ID·latest 값 독립 |
| C-09 | guest reboot·publisher restart | 각각 boot/publisher ID 규칙 준수, 옛 메시지 current 재채택 방지 |
| C-10 | broker 단절·재접속 | live backlog 폐기, 새 수집, event 역사 처리, bounded memory·재시도 |
| C-11 | 인증/ACL 실패 | 잘못된 VM/command 쓰기 거절, credentials 유출·익명 fallback 없음 |
| C-12 | UTC 없음·시간 동기화 변화 | null 유지/품질 반영, 서로 다른 clock domain 차분 금지 |
| C-13 | MQTT 소유권·경쟁 | Core snapshot·event·MQTT buffer/정적 scratch race 없음 |
| C-14 | graceful stop/reboot | broker 가용/불가 모두 종료 기한 준수, 현재 실행 제거·새 세션 구분 |
| C-15 | 계측·발행 비용 | 같은 입력에서 off/on 비교, RAM/stack/jitter·손실·수신을 별도 보고 |

## 4. 검증·보고 원칙

첨부 `tools/validate_contract.py`는 개발 PC용 **시험 오라클**입니다. 타깃 Zephyr의 runtime dependency가 아닙니다. 필요하면 같은 사례를 C 테스트로 옮기되 생산 코드와 테스트가 같은 오해를 반복하지 않도록 Golden의 정의를 기준으로 대조하세요.

이번 계약 패키지의 30 정상·30 의도적 거절·11 시퀀스 결과는 제품 구현 시험이 아닙니다. 새로운 Publisher 구현에 대해 별도로 실제 산출물을 검사해야 합니다.

보고서는 다음을 구별하세요.

- 코드 구현/단위 검증.
- native_sim 실제 생산→broker→시험 구독기.
- ARM64 부분 빌드.
- 실제 Xen/RPi4 수신 및 비교.
- 실제 주행 앱 연결.
- 소비자(Spring/API/화면) 수락 — 이번 담당 범위 밖.

기존 5개 G1 문서 경로 관련 전체 회귀 ERROR 등은 이번 작업으로 자동 해결됐다고 하지 마세요. 기존 보고서와 재실행 근거를 유지하세요.

## 5. 최종 산출물

1. 공통 Publisher·adapter와 최소 Starter 연결 diff.
2. schema/catalog와 구현 필드 대응표. 계약 변경이 필요하면 사유·예시·검사 모두 같이 갱신.
3. 실제 생성·수신된 payload와 오류/단절/재부팅 증거. 합성 fixture와 폴더를 분리.
4. 명령·toolchain/소스/옵션/firmware hash·측정 환경 영수증.
5. RAM/stack/수집/직렬화/전송 비용과 한계.
6. backend 담당자가 연결할 정확한 topic/type/unit/ID/time/failure 목록.

최종 이미지 빌드·SD 기록·운영 broker 교체·기존 immutable bundle 덮어쓰기·commit/push·실보드 적용은 이 인계서만으로 수행하지 마세요. 조회/로컬 검증/부분 빌드와 실제 변경 승인 범위를 분리하세요.
