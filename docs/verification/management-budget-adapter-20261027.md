# 원 요청 조회·시간 예산 어댑터 검증

## 범위와 판정

하위 Manager의 원 요청 키 조회·독립 stop 수락 객체·호출별 잔여 예산을 구현했다.
**D02·D08의 제품 HTTP 종단 연결은 미완료다.** 사용자 범위 조정에 따라 FastAPI·Worker·
Spring·프론트 제품 소스는 변경하지 않았고, 담당자 구현·수락은
[연동 요청서](../platform/management-integration-request.md)에 정리했다.

선행 [S01·S02 마감](recovery-store-closure-20261027.md)의 소스·증거 해시를 인수했다.
손상 후 차단·같은 사건 ID 재시도·상태와 pending 공동 저장은 재설계하지 않았다.
기존 감사의 실패 결과는 보존한다.

| 기준 | 내용 |
|---|---|
| 작업 기준 | `refactor/platform/recovery-contract-closure`, HEAD `602ace1bde26c893166e9fe984e2a14ed1460e3b` + 아래 수정·새 파일 |
| 증거 | PC `C:/Users/SSAFY/Documents/Codex/2026-10-27/xenova-management-d02-d08/`, WSL `/home/ssafy/xenova-management-d02-d08-20261027/` |
| 원본 소유 | 하위 control journal이 수락·실행·결과 원본. 요청서의 상위 추적안은 새 실행 원장이 아님 |
| 실행 환경 | WSL Ubuntu 24.04, 실제 Manager/Execution·파일 잠금, 소유 임시 경로와 FakeXen. 실제 Xen·운영 상태는 사용하지 않음 |
| 원격 읽기 대조 | `git ls-remote`의 platform `3c7e844e8bad9e873da5c20c8100a3d5da80e642`, develop `3de2deeec6cb78a28b8026a5e415329cb3c29509`. 브랜치 전환·병합 없음 |

## 실제 변경과 유지한 경계

| 파일·책임 | 변경 |
|---|---|
| `xenova_management.py:Manager.fork` | 요청별 Platform·trace 상태를 분리하는 Python 객체. 긴 실행과 stop admission/원 기록 조회를 연결하기 위한 어댑터이며 모든 action의 동시 실행 허가가 아님 |
| `Manager.call/_validate_call` | 선택 호출 `budget_ms`, get/list의 선택 `timeout_ms` 허용. 기본 CLI/Worker 예산과 원 spec/hash 유지 |
| `control-operation` | 원 operation ID 또는 제출 전 key로 read-only 조회. 선택 VM UUID 대조, admission 잠금·기한 적용. 원 기록과 현재 host의 기한 비교 근거 반환 |
| stop 오류 details | 원 `phase/host_boot_id/lifecycle_generation`을 수락 정보와 함께 전달. uncertain을 완료나 재시도 가능한 pending으로 바꾸지 않음 |
| `xenova_execution.py` | 기존 결과 재확인·미제출 launch 정리의 관측 예산을 이번 호출 잔여값으로 제한. 새 물리 실행 권한·원 기한 연장은 없음 |
| `test_xenova_management_budget.py` | 실제 제품 Manager/Execution과 flock·thread를 사용하는 정규 회귀 |
| 목적별 문서·요청서 | 현행 하위 입력은 CLI·Manager 명세, 미구현 제품 연동은 담당자 요청서, 특정 실행 결과는 이 보고서로 분리 |

`budget_ms`의 유효 범위 1..105000ms는 HTTP 대기시간 권고가 아니다. 물리 실행은 기존 90초와
남은 호출 예산 중 짧은 기한을 사용한다. 재전달은 저장된 원 기한도 대조한다. 만료 뒤의
기존 결과 확인은 호출 잔여값 안에서만 수행하며 create/reboot를 다시 제출하지 않는다.
syscall/fsync·기제출 Xen 효과를 선점하는 엄밀한 wall-clock 취소 기능은 아니다.

원 key 조회는 실행을 만들거나 현재 Xen 상태를 조회하지 않는다. 검색 부재는 그 시점의
미관측이며 미수락의 증거가 아니다. 현재 host와 원 host가 다르면 단조 시각을 비교하지 않고
원 실행 기한 만료 여부를 null로 반환한다. 실제 물리 이행은 원 key/spec의 제어 호출과
신원·의도·lifecycle·기한 guard를 다시 통과해야 한다.

## 수정 전후 실제 HTTP 반례

`probe_before.py`는 기존 감사의 실제 HTTP 경로를 소유 임시 경로에서 재실행한다.
도구 종료 코드 0과 계약 통과를 혼동하지 않고 JSON assertion을 판독했다.

| 관측 | 수정 전 | 하위 보완 후 |
|---|---|---|
| 경로 | FastAPI → Service → PlatformDriver → Manager/Execution, 실제 execution flock | 동일. 상위 제품 소스 불변 |
| HTTP | 두 stop 모두 503, 원 details·operation 참조 유실 | 같은 실패 유지 |
| 실제 의도 revision | 2 → 3 → 3 | 2 → 3 → 3 |
| 원 operation | 같은 HTTP 재전달에서 불변 | 불변 |
| 물리 shutdown | 잠금 중 0 | 0 |
| 개별 판정 | `FAIL_HTTP_PENDING_CONTRACT` | `FAIL_HTTP_PENDING_CONTRACT` |

원자료는 `HTTP_BEFORE.json`·`HTTP_AFTER_LOWER.json`이다. 하위 멱등성을 새로 만든 것이
아니며, HTTP 503을 202로 바꿨다고 보고하지 않는다. 같은 FastAPI VM mutex/SerializedManager
앞에서 막히는 stop도 담당자의 후속 구현·수락이 필요하다.

## 실행한 명령과 결과

아래 명령은 저장소 루트에서 실행했다. pytest만 `platform/services/dom0-manager`에서
실행했다. 시험 도구 경로·환경·소요시간은 증거 JSON에 고정했다.

| 명령 | 결과·근거 |
|---|---|
| `python3 -B -m unittest discover -s platform/tests -p 'test_*.py'` | 최종 927개, 실패 0·skip 2. `platform-3.log/json`. 앞선 924개 실행은 별도 보존 |
| `python3 -B -m unittest discover -s platform/tests -p test_xenova_management_budget.py -v` | 최종 15개 통과, 9.057초. `lower-new-4.log/json` |
| `python3 -B -m pytest -p no:cacheprovider` | 기존 dom0-manager 451개 통과·skip 0, 42.11초. 기존 Starlette 경고 1개. `python.log/json` |
| `python3 -B platform/tests/check_xenova_bringup_regression.py` | 통과, `bringup.log/json` |
| `python3 -B platform/tests/check_xenova_first_install_units.py` | 정상 그래프 수락·주입 순환 거절, `first-install-units.log/json`. 실제 서비스 시작 시험 아님 |
| `bitbake -c package_qa xenova-platform` | 2298 task 중 2287개 재사용, 전부 성공 |
| `bitbake -c package_write_rpm xenova-platform` | 2298 task 중 2297개 재사용, 전부 성공 |

원 key 조회의 마지막 검토에서 중복 key 기록을 검색 부재로 오인하지 않도록
`RECONCILE_REQUIRED` 분기를 추가하고 별도 시험을 통과했다. 기존 924개 실행과 최종 후보의
재실행은 원자료를 구별해 보존한다. dom0-manager 제품 소스는 전후 불변이다.
451개 회귀에는 실제 Engine·Manager fixture의 `tests/test_drive_rejoin.py`도 포함된다.
이는 새 실행 provisioning·바인딩·Standby 준비의 로컬 회귀이며 실제 broker/보드 수락은 아니다.

skip은 고정 Xen 콘솔 소스를 지정하지 않은 `test_actual_xen_ring_to_two_ptys_while_writer_stalls`와
root 전용 `test_actual_unprivileged_consumer_reads_network_file_but_not_psk`다. 두 항목을
통과로 세지 않았다. 이번 변경은 콘솔·Wi-Fi 코드를 바꾸지 않는다.

첫 전체 실행은 systemd 259 분석기 환경을 지정하지 않아 923개 중 실패 4·오류 8·skip 2였다.
보존된 실제 259.5 분석기를 QEMU로 실행하는 기존 argv를 두 fixture 환경변수에 설정한 뒤
동일 회귀가 통과했다. 제품 unit이나 검증 assertion을 완화하지 않았다. 최초 새 시험의 실패
1건은 합법적인 accepted 재전달 사건 추가까지 journal 전체가 불변이라고 기대한 시험 오류다.
원 operation/spec/hash/revision/deadline 불변과 물리 호출 수를 확인하도록 수정했고 최초 로그도
보존했다. 고정 테스트 수를 목표로 성공 결과를 만들지 않았다.

## 검증 대응과 남은 종단 수락

| 지시서 조건 | 이번 근거 | 남은 수락 |
|---|---|---|
| T01 | 실제 HTTP 반례를 전후 재현. 하위 pending/key 조회는 실행 잠금 중에도 동작 | Driver/HTTP의 accepted·원 details 전달 |
| T02 | 실제 thread의 긴 하위 start 중 forked stop 수락, 옛 create 0 | 동일 FastAPI VM mutex·직렬화 경합 |
| T03 | pending 동일 key 이행 뒤 필요한 shutdown 1회, 완료 재전달 추가 0 | 상위의 후속 책임·결과 전달 |
| T04 | admission 잠금 만료 뒤 수락·의도 변경 없음 | threadpool/VM mutex/SerializedManager의 잔여 예산 |
| T05 | registry 비용·여러 VM이 하나의 get/list 기한 사용, 실패를 빈 성공으로 바꾸지 않음 | 실제 HTTP에서 잔여 timeout 전달 |
| T06 | opid 없이 원 key 조회, 조회 무부작용·원 기록 유지 | 실제 HTTP의 수락 전후·제출 뒤 응답 유실/단절 |
| T07 | 기존 key/spec·host/lifecycle guard 회귀, 잘못된 VM 조회 거절 | 상위 request key 보존·권한 결합 |
| T08 | 원 기한 불변, uncertain 재확인 예산 제한·추가 shutdown 없음 | 만료/미확정의 제품 표시·지원 이행 |
| T09 | busy 상태의 completed replay가 vm=null/warning·원 결과 유지 | HTTP·Spring·UI 해석 |
| T10 | 기존 의도/완료 저장 실패·uncertain 회귀 유지 | details 안전 전달·상태 DTO |
| T11 | 원 journal/key의 read-only 회수 지원 | FastAPI/Spring 재시작 후 원 key 회수·이행 책임 |
| T12 | 기존 최신 intent가 옛 요청을 차단하는 하위 회귀 | 상위의 응답 역전·현재 화면 보호 |
| T13 | 사용자 범위 조정으로 Java 실행·구현 안 함 | 실제 Spring client/service → FastAPI |
| T14 | 사용자 범위 조정으로 프론트 실행·구현 안 함 | 타입·단위·실제 브라우저 수락 |
| T15 | 하위 선택자 검증·다른 VM 거절 | 인증·요청 주체 정책·허용 details 목록 |
| T16 | S01·S02 소스 인수와 기존 recovery/stop 회귀 | 실제 Xen·제품 종단·보드 수락은 별도 |

## 패키지·후보 식별과 제한

`SOURCE_MANIFEST.json`과 `candidate.patch`는 HEAD 외의 수정·새 파일을 식별한다.
`ARTIFACTS.json`은 하위 두 모듈의 실제 RPM·설치 staging 바이트와 후보를 대응한다.
`FINAL_VERIFICATION.json`은 실행한 명령·결과와 상위 제품 소스 보존을 기록한다.
이 자료는 저장소 밖 증거 폴더에 보존하며 인증정보·운영 원문을 포함하지 않는다.

| 최종 부분 산출물 | 값 |
|---|---|
| recipe | `xenova-platform_1.0.bb`, 변경 없음 |
| RPM | `xenova-platform-1.0-r0.cortexa72.rpm`, 164501 bytes |
| RPM SHA-256 | `4c4155871df66c7fb646ba20bd4d394b0e421462c21d51d1ce7e60f87dfdc677` |
| Manager 소스·staging·RPM | `6009d095ba875fa278f2fb84ff87c8e3d94bddd04f8da27ba3cdb98c8f9d97ba` |
| Execution 소스·staging·RPM | `58e0df04f4be960cfdbd15aa85533cd28f24fe0e5e168b9e27091644e20c43e7` |
| 기존 산출물 | 덮어쓰기 전 runtime/debug/dev RPM 3개를 증거 경로 `before-packages`에 보존 |

BitBake의 공유 sstate·호스트 I/O 계상 경고 2개는 남아 있다. 패키지 QA 실패는 없으며,
새 의존성·커널·unit 변경이나 이미지 조립을 수행한 결과가 아니다.

기존 dom0-manager 패키지·Worker·guest·unit·의존성은 변경하지 않는다. guest를 바꾸지 않아
Zephyr ARM을 다시 빌드하지 않았다. 이미지/WIC·설치본·실보드·운영 DB 수락은 수행하지 않았다.
commit/push·MR·브랜치 전환도 이 묶음의 실행 범위에 포함하지 않았다.

제품 연동 담당자는 [요청서](../platform/management-integration-request.md)의 실제 변경 지점과
T01~T16을 이어서 검증해야 한다. D01 장애 판단, D03 journal/receipt/Authority 수습, S03 영구
저장 모드와 범용 메시지 큐는 후속으로 자동 진행하지 않았다.
