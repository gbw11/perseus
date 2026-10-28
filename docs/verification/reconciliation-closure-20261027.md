# D03 하위 수습 구현·로컬 검증과 담당자 인수

## 마감 단위와 기준선

이번 마감은 **소유 하위 플랫폼·Bootstrap의 증거 기반 수습 구현·로컬 검증·부분 패키징과
Worker/Coordinator·Gateway 담당자 요청**이다. D03 제품 종단·실보드·Authority 운영 복원 완료가 아니다.
D01은 플랫폼·Zephyr 구현·인계 범위로 유지하며 Linux/Worker 구현, D02·D08 상위 연동은 계속 대기다.

착수 HEAD는 `fd17d12c3ce6b66616c7b53ee777d98d3018980c`, 브랜치는
`feat/platform/health-and-recovery-contracts`다. Windows의 기존 문서 변경 8개(새 문서 2개 포함)를
별도 보존하고 그 위에서 작업했다. WSL은 같은 clean HEAD였으며 허용 경로만 동기화했다.
원격 platform은 `8b19f05d8fb380d2ecb83d21aabbb1042eb22a8c`로 진행돼 있었다.
소유 runtime·Bootstrap에는 차이가 없고 Worker arming/watcher 차이는 타 담당 영역이다.
브랜치 전환/병합·팀원 제품 소스 반영을 임의 수행하지 않았다.

지시서는 `C:/Users/SSAFY/Downloads/CODEX_D03_RECONCILIATION_AND_HANDOFF_PROMPT.md`다.
시나리오 기준은 저장소 밖 `C:/Users/SSAFY/Downloads/XENOVA_MAIN_AND_FAILURE_SCENARIOS.md`를
읽었으며 동명의 두 번째 정식 기준본을 만들지 않았다. 입력 해시·원 diff/새 파일·시험/패키지는
PC `C:/Users/SSAFY/Documents/Codex/2026-10-27/xenova-reconciliation-d03/`,
WSL `/home/ssafy/xenova-reconciliation-d03-20261027/`에 보존한다.
`SOURCE_MANIFEST.json`과 `candidate.patch`로 미커밋 후보를 식별하며 HEAD만으로 동일 후보라 하지 않는다.

## 구현과 원본 소유

| 소유 파일·함수 | 실제 보완 |
|---|---|
| `platform/scripts/provision-xenova-telemetry.py:XenStore.apply/inspect` | synchronous transaction 반환/미종료를 구분하고 실제 값·권한 전체를 read transaction에서 대조. 원문·비밀 해시 반환 없음 |
| 같은 파일 `receipt_lock/reconcile/current/main` | 기존 실행별 lock과 원 receipt/credential 원천·guest boot authority를 사용. CLI `--reconcile` 기본 진단, `--execute`에서 마지막 실행·의도/자산 세대·원본/Store guard 후 같은 receipt 적용 확인. 원 phase·ID 유지 근거 추가 |
| `xenova_recovery.py:Recovery._once/reconcile` | 원 stop의 미제출→제출→synchronous 반환 경계를 기존 journal에 기록. 원 attempt의 제한적 진단/종결과 원 오류·phase 보존. scope 확인은 observe-only |
| `xenova_management.py`, `xenova_management_cli.py`, `xenova_vm.py:main` | 같은 envelope·전체 관측 예산·실행/admission 잠금과 새 `recovery-reconcile` 경로. 원 attempt만 선택하며 새 key/force 거절 |
| `platform/tests/test_xenova_reconciliation.py` | 실제 제품 파일·flock·writer·Manager, Store/Xen·시간/FS 오류 경계만 대체. 별도 프로세스 SIGKILL과 기존 상위 Adapter/Engine 읽기 fixture |
| `package-xenova-xenstore-g1-handoff.py:DOCUMENTATION` | 새 안내 링크가 가리키는 담당별 요청·공통 계약을 같은 소스 revision의 인계 묶음에 포함 |

새 DB·SQLite·원장·daemon·의존성/버전·MQTT wire 필드를 추가하지 않았다. Worker/Engine/Coordinator,
Gateway·Agent·FastAPI·Spring·화면 제품 파일은 변경하지 않는다. 실제 함수·입력/반환·오류·운영 절차의
원문은 [recovery-reconcile 명세](../platform/cli-manager-spec.md#cmd-recovery-reconcile)와
[receipt 적용 확인](../../platform/guests/zephyr/telemetry/README.md#uncertain-receipt의-재주입-없는-적용-확인)에 둔다.

## 판정표와 변경 전후

| 대상·확인한 조건 | 제공 결과 | 계속 남는 경계 |
|---|---|---|
| 원 receipt uncertain, 같은 현재 실행·원 credential 원천·원 synchronous 호출 반환·배타 lock, 기대 값/권한 전체 일치 | Store 재주입 없이 같은 receipt에 현재 적용 확인 저장. receipt/boot/instance 불변·Store 쓰기 추가 0 |
| receipt가 이미 같은 근거로 applied, 저장 응답 유실/재시작 뒤 재요청 | 같은 결과의 내구 재확인. 새로운 receipt/세션 0 | 원 Store commit의 성공 시각을 안다고 기록하지 않음 |
| 구형 receipt의 반환 근거 없음·원 producer/transaction 잔존·값/권한/원천 충돌·부분 관측 | 차단/관측 오류 보존 | lock 획득·현재 값 일부 일치만으로 원 쓰기 종료를 추측하지 않음 |
| stopping의 원 미제출이 기록되고 동일 대상/실행/의도 확인 | 원 attempt failed/not_submitted | 현재 VM을 정지하거나 새 VM을 만들지 않음 |
| stopping/그 단계 uncertain의 synchronous 반환·원 종료 대상 부재·미확정 launch 없음·scope 정리 확인 | failed/stopped_without_replacement, 원 오류·phase 보존 | 새 실행 관측을 뜻하는 recovery COMPLETED가 아님 |
| 새 실행이 원 owner·run·configuration·완료된 CPU 검증에 연결 | 기존 요건으로 원 attempt completed, 앱 준비 unknown | 임의 새 domain 출현으로 인과를 추측하지 않음 |
| legacy stopping에 종료 근거 없음·launching 결과 불명·원 scope 활동/관측 실패 | 기존 차단 유지, 부족한 근거/예산 오류 반환 | 운영 수습의 모든 분기를 닫은 것이 아님. 원 실행자·Store/host 추가 확인 필요 |
| 다른 host·현재 실행·새 user stop/의도 revision·원본 변화 | 이전 요청의 적용 거절, 원 D/ID 유지 | 새 실행이 합법적으로 검증되면 그 실행의 일반 공급은 가능 |

기존 감사의 stopping 존재/부재 보호와 receipt uncertain 재시도 차단은 계속 유효하다.
새 지원 성공 경로는 추가 근거가 있는 경우에 한정된다. 원 기록 삭제·수동 phase 편집·새 신원/기한·
자동 force는 수습으로 사용하지 않는다. 조회가 일반 물리 이행기로 바뀌지 않는다.

## 시험 대응과 실제 대체 경계

| 추적 ID | 실제 시험·assertion | 판정 범위 |
|---|---|---|
| T01 | receipt/attempt 진단 전후 원 ID/spec/hash/D/intent·원본 bytes, Store/VM 물리 호출 수 불변 | 관측 cache/잠금은 기존 경로를 사용. 원본 종결이나 공급 없음 |
| T02 | 실제 `XenStore.inspect`의 Python 로직·Store 경계 메모리 대역. 정확 일치→동일 receipt 적용 확인, telemetry/drive shared boot·별개 instance | 실제 libxenstore 서버/권한 동작의 보드 수락 아님 |
| T03 | 값·ACL·원천·legacy 근거·active transaction·부분 결과·읽기 실패·실제 receipt flock 경합 | 근거 부족/오류에서 확정 0, 비밀 출력 0 |
| T04 | module 재로딩 및 별도 프로세스, 기록 전/후 SIGKILL→새 프로세스 재확인 | 같은 receipt ID·결과, 추가 Store 쓰기 0. 전원 상실 아님 |
| T05 | 미제출 stopping, 반환 후 부재, 실제 종료 반환 뒤 응답 오류, accepted/removed | 같은 원 operation/spec/D 유지, failed 정확 종결·새 물리 호출/COMPLETED 오표시 0 |
| T06 | 원 launch의 observed/bound/CPU complete와 다른 owner/새 execution 분리 | 인과가 맞는 새 실행만 completed, 물리 호출 추가 0 |
| T07 | legacy stopping 존재/부재, 미확인 launch, 조회 실패, 실제 scope 조회 분기의 활동/기한 | scope 정보는 systemctl 경계 대역. 신호/force/종결 0 |
| T08 | 실제 Manager.fork stop 수락이 실행 flock 중 새 의도 저장, 마지막 guard 거절. CLI의 같은 execution/new intent revision도 거절 | 신원·의도·원 receipt 바뀜에 stale 확인 적용 0 |
| T09 | receipt/journal write·file fsync·replace·parent sync 오류, 저장 뒤 중단·새 프로세스 재확인 | 저장 미확정 성공 반환 0, 같은 원 ID·추가 물리/Store 효과 0 |
| T10 | 실제 flock 두 FD의 경쟁·실행 lock 충돌, 별도 B receipt lock 획득 | thread/C 작업의 강제 취소나 실제 B binding 제품 종단 수락으로 확대하지 않음 |
| T11 | same-host 원 D 만료 후 새 관측 예산, 작은 budget 소진, host 교체 거절 | 원 물리 기한 재발급·서로 다른 host 단조 시각 비교 0 |
| T12 | 검증된 새 execution의 일반 receipt 공급·새 user start 수락, 과거 receipt/결과의 새 실행 적용 거절 | UUID·자산 유지, old owner 자동 원복 0. 장치 적용 아님 |
| T13 | 실제 `PlatformRecovery.attempt`가 하위 수습 completed를 읽음. 실제 Engine.tick은 final FAILED를 그대로 두며 `_task_replay`의 기록 조회 분기는 원 결과를 읽을 수 있음 | 소비 진입점의 공백을 확인한 인수 fixture. Worker final 결과 반영·Coordinator 재개는 **담당자 구현 대기** |
| T14 | Gateway의 기존 원본 손상/저장 미확정/중복 소유/retained·재시작 disarmed 회귀와 실제 함수 대조 | 새 Authority 복원 도구·과거 백업 최신성의 제품 수락 아님 |
| T15 | 하위 전체, 실제 dom0-manager·Gateway suite와 G1 인계 문서 검사. 기존 C/Zephyr 입력의 불변 해시 대조 | 전체 시험 수를 별도 suite·ARM·실보드 수락으로 대체하지 않음 |

### 실행 결과

최종 실행 명령·개수·skip·후보 대응은 `FINAL_VERIFICATION.json`과 아래 최종 결과 표를 함께 읽는다.
검증 중간 결과와 실패도 같은 증거 경로에 보존한다.

<!-- FINAL-RESULTS-START -->
| 실행 | 최종 결과·원자료 |
|---|---|
| `python3 -B -m unittest discover -s platform/tests -p test_xenova_reconciliation.py -v` | 32개 통과·skip0, `d03-7.log/json` |
| `python3 -B -m unittest discover -s platform/tests -p 'test_*.py'` | 961개, 실패0·skip2, `platform-2.log/json` |
| 실제 dom0-manager `pytest -p no:cacheprovider` | 451개 통과, 기존 warning1. `manager-2.log/json/xml`. S01/S02·Engine·drive rejoin 별도 suite |
| 실제 Gateway `pytest -p no:cacheprovider` | 84개 통과, `gateway.log/json/xml`. 권한/retained·재시작/출력 guard·A/B 대칭 별도 suite |
| 고정 BitBake `-c package_qa xenova-platform`, `-c package_write_rpm xenova-platform` | 둘 다 통과, 최종 source/staging/RPM 6개 모듈 바이트 일치. `ARTIFACTS-final.json` |
| 문서·보존 검사 | 로컬 링크/anchor 356개·Python 구문·diff 검사 통과, 담당 요청 ID와 기존 8개 문서 사본 보존 |

플랫폼 skip2는 기존 고정 Xen console source 미지정 시험과 root Wi-Fi consumer 시험이다.
최종 RPM은 169954B, SHA-256 `bdeec40175fe3058774b1d8614e0f0730188176ab6cbb907dc942c7c220dfe87`다. 의존성/recipe 변경·전체 이미지·target 설치는 없다.
<!-- FINAL-RESULTS-END -->

초기 별도 프로세스 시험은 test fixture가 mount 대역을 적용하기 전에 임시 경로를 읽어 실패했다.
대역 적용 순서를 바로잡고 같은 제품 guard를 유지한 채 통과했다(`d03-3` 실패, `d03-4` 통과).
첫 전체 하위 회귀는 새 Worker 요청 링크의 G1 묶음 누락 한 건으로 실패했다(`platform.log`).
링크를 제거하거나 검사를 약화하지 않고 문서 포함 목록을 보완했다.

## 후보·패키지·이력 보존

부분 패키징은 고정 `build-rpi4-latest-image-20261026-121448`의 `xenova-platform`만 대상으로 한다.
기존 runtime/debug/dev RPM 세 개와 중간 D03 RPM을 별도 보존했다. recipe와 RDEPENDS는 바꾸지 않는다.
최종 `ARTIFACTS-final.json`의 소스·staging·RPM 여섯 모듈 바이트 대조는 변경 모듈 5개와
변경하지 않은 공통 Execution을 포함한다. 이 검사는 최종 이미지/WIC·설치본 수락이 아니다.
guest C·헤더·빌드 설정을 바꾸지 않아 Zephyr ARM 재빌드는 수행하지 않는다. 이전 local/native·ARM
결과는 당시 후보의 근거로 유지하며 현재 문서 변경을 새 ARM/이미지 수락으로 표시하지 않는다.

## 담당자 요청과 운영 후속

- [Worker D03-W01~W04](../requests/2026-10-27-recovery-health-ctrl.md#d03-worker): final 원 결과 재확인,
  동일 applied receipt 재사용·slot/lease 재획득, 예산/의도·준비 단계·S01/S02 보존. 담당자 미구현.
- [Gateway D03-G01~G04](../requests/2026-10-27-authority-recovery-ctrl.md): 원본/retained/백업 최신성,
  단일 소유 아래 제한적 복원·disarmed 유지·저장 오류/재시작 수습. 담당자 미구현.
- [HTTP·Spring·화면](../platform/management-integration-request.md): D02·D08 대기 유지. 원 결과/수습 후
  재조회 의미만 기존 권한·허용 details로 연결하도록 요청. 새 웹 수습 API·버튼 구현 없음.
- [Linux D01-L01~L05](../requests/2026-10-27-linux-health-agent.md): 구현 대기 유지. 고부하의 정상 가능성과
  합의된 자원 여유/지속 조건을 명확히 했으며 D03 때문에 Linux 필드·supervisor 계약을 추가하지 않음.

정식 사용법의 순서는 원 자료 확인 → 기본 진단 → 근거 검토 → 명시적 종결 → 같은 원 기록 재조회다.
성공 뒤 새 일반 동작이 필요하면 현재 intent·건강·신원·허가·예산을 별도로 검사한다.
불충분한 기록은 그대로 보존하고 공개 checks/원 ID·실행 참조로 담당자에게 escalation한다.
private receipt/credential·원 Store 본문을 요청서·공개 로그에 붙이지 않는다.

시나리오 M01/M05, SC-025/026/027/039/043/051~056, GATE-02/05는 위 T01~T15와 대응한다.
SC-053의 소비 갱신과 SC-054의 Authority 복원은 담당자 대기이며 기존 사건을 새 미구현 ID로
중복 등록하지 않았다. 실제 Xen/libxenstore 정체·늦은 host 효과·전원/SD 내구성·서비스·장치 출력은
미검증이다. 운영 `/run`·`/srv`·실보드·서비스/VM·broker/ACL·CPU/네트워크·host/이미지/SD는
변경하지 않았고 commit/push·MR·후속 D 항목으로 자동 진행하지 않는다.
