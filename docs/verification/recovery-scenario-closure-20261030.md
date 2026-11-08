# 복구 시나리오 국소 마감: Authority 진단·출력 검증·조회 예산

최신 상태는 [R65 중단·준비 대기 의미와 적용 범위](#r65-pause-20261107)다. 후속: [R64 FW30 유지·통계](#clock-r64-retained30-20261107), [stop 요청 내 사전 검증](#stop-request-prevalidation-local-20261107), [netfront TX 회수·원 오류 보존](#netfront-tx-reap-r2-local-20261107), [R60–R61 수락 전 거절·국소 검증·복원](#clock-r60-r61-20261107), [R58–R59 전체 기록 검사](#clock-r58-r59-20261107), [R57 상세 계측 비용 대조](#clock-r57-20261107), [R56 TCP 구간 관측·정리](#clock-r56-20261107), [R55 broker 대조·복원과 TCP 후속 관측](#clock-r55-20261107), [R54 런처 검증 정정](#r54-launcher-guard-erratum), [11월 7일 R54 clock 구간·복원](#clock-r54-20261107), [17:55 보드 복원·중단과 재개 기록](#parallel-resume-20261106), [병렬 binding·feedback 구간 계측](#parallel-binding-feedback-20261106), [보드 예산 시험·등록/수습·연결 슬롯 보완](#budget-board-observations-20261106), [E4 안정 구간·VM 누적 예산](#worker-budget-closure-20261106). 이전 [Worker 반복 운영·상태 발행 보완](#worker-status-closure-20261105), [문서 현재화·작업 커밋](#branch-commit-closure-20261105), [다음 이미지1.0.8 선택](#image-default108-20261105), [RAM 최근 관측](#ram-recent-observation-20261104), [수동 버퍼·관리 RAM](#manual-buffer-managed-ram-20261104)은 당시 후보와 범위로 읽는다.

<a id="현재-상태-요약"></a>
## 현재 작업 묶음과 확인 수준

이 표는 기존 `feat/platform/guest-clock-sync`의983fa12 이후 제공물과 후속 보완을 모은 것이다.
이번 후속 작업의 검토 시작점은 `5d05101e`와 당시 미커밋 diff였다. 현재 소스 인계는
`feat/platform/recovery-drive-runtime-hardening`의 작업별 커밋이며, [R65 중단 상태와 적용 범위](#r65-pause-20261107)를 우선 읽는다.
이전 단계의 설계·실패·수락을 보존하며 소스 제공·로컬 검증·이전 보드 수락을 하나의 완료로 합치지 않는다.

| 작업 묶음 | 현재 제공 | 수락 수준·남은 경계 |
|---|---|---|
| 연속 Linux reboot·원 stop 수습 | 신원/디스크를 검증한 새 실행 결합, 원 key 재조회, 직접 후속 force의 부재 근거로 옛 stop 실패 종결 | 로컬 회귀·이전 보드 근거. 신원 불명/잔존 소유 자원은 계속 보류 |
| create 게시·실행 binding | 원 기한 안의 게시 잠금 대기, identity-only 관측·제한된 재확인·동일 실행 공급, 관측 안의 중복 검증 재사용·A/B 조회 예산 분리 | 기존 복사1회/원 소유·기한 유지. [후속 계측](#parallel-binding-feedback-20261106)의 국소 수정과 로컬 회귀를 제공하나 전체 병렬 조건의 binding·지도 진행은 미수락 |
| Worker/Gateway 설정 공급 | 기존 MQTT 자격 재사용, factory UUID별 설정·최초 mock 초기화, client 의존성과 heartbeat 선언 호환 | 실제 차량 출력 승인과 별개. 실제 Worker·A/B·장치 종단은 미수락 항목을 유지 |
| Worker 반복·부가 상태 | 기본 판정 원문 큐 제거·닫힌 ID 정리, 부가 sink의 유한 실행자·원 표본 시각 보존, 상태 파일2의 host boot 검증 | 집중174·Manager 전체1022 로컬 통과, 고정 ARM Python/QEMU 실행 확인. 전체 게스트 부팅·운영 설치는 별도 |
| E4 반복 복구 | 명시 프로필의 연속 안정·VM별 누적 논리 시도·공동 저장. 원 replay 추가 차감0, 과거 예산 미확인 보호 | 소유 Debian의 실제 Xen/Agent/Worker/Store에서 반복 장애·원 결과 회수·재시작·소진·안정 갱신 뒤 정상 복구 통과. 운영 수치와 기존 설치본/주행 종단 수락은 별도 |
| XenStore 거래 회복 | 늦은 응답 회수·고아 거래만 abort, 원 commit 재전송 금지 | native/ASan·UBSan·보드 후보 근거 |
| Gateway STOP | 단일 원 제출의 pending/완료 회수·출력 우선·기존10ms 직접 호출 보호·장치 참조 보존 | PTY·보드 후보 확인. 실제 장치/물리 정지나 모든 부하의 기한 보장은 아님 |
| 앱 메모리·Telemetry2.1 | AUTO4+MANUAL4 실제 owner, 고정 예약/점유/최고값·원 시각·결과 보관 구분, 유한 공정 발행 | native·ARM/QEMU·8-region 로컬 MQTT와 실보드 지도·검색·경로 점유 증가 확인. READY·실제 PTY 주행·입력 만료 뒤 전체 반환은 미수락 |
| RAM 최근 관측 | 같은 봉투의 total−page-free, 6초 원 lease·최초 오류·역전/퇴역 boot 보호 | 계약·참조 소비자 제공. Spring/화면 후보는 별도, 스택 화면은 보류 |
| 펌웨어·다음 이미지 | 기본 lock·factory A/B의1.0.8 선택 유지. 후속 진단·통계·TX 회수 후보를 불변 산출물로 분리하고 시험 B에는1.0.30 유지 | [R65](#r65-pause-20261107)의 원 통신·Worker 복구 확인. 부하용1.0.31은 로컬 ARM 포장만 완료. 전체 READY·PTY 주행·입력 만료 후 반환·세 압력 조건과 전체 이미지/RPM 수락은 별도 |
| 문서 현재화 | 예산 헤더·조건부 fork·key stop200/202·원 key 조회의 실제 제공을 반영 | 과거 설치본/미연결 설명은 이력으로 보존. 실제 배포값은 재조회하지 않음 |

기본 선택은 Linux1.0.4/주행1.0.8이다. 과거 보드 메모리 후보1.0.7과 이번 소유 시험 B의1.0.9는
별도 후보이며 기본 이미지 선택 변경을 뜻하지 않는다. 진단1.0.28의 시험 뒤 원1.0.8로 복원한 이력을 보존한다.
R64/R65 이후에는 TX 회수를 보완한1.0.30을 시험 B에 유지하고 원 통신 설정을 별도로 복구했다.
이전 표본의 시간·source hash·operation과 실패 기록을 최신 후보가 이미 다시 수락한 것으로 확대하지 않는다.

## 기존 단계별 기준선과 후속 연결

| 구분 | 현재 판정 |
|---|---|
| 2번 최종 구현 | `a15bc3f`: Linux 사전 전체 payload 해시 0회·검증 결합 복사 1회·원 작업과 부분 실패 보존. 설명/기존 측정 기록은 `5f3dba6` |
| 로컬 근거 | 기존 최종 57개 집중·449개 관련 회귀와 세 모듈 부분 설치 바이트 대조. 이후 Debian·Ubuntu 명시 사례 4개를 추가해 집중 61개 통과, 관리·Zephyr 54개 재실행 통과 |
| 보드 상태 | 앞선 2회 처리 후보의 Debian 생성 477.203초만 측정. 최종 1회 후보는 미적용·성능 미측정 |
| 2번 공통 적용 | 기본 lock의 Debian·Ubuntu ID/버전·출처와 실제 카탈로그/준비 경로를 축소 payload로 검증. 실제 고정 대용량 산출물·Ubuntu 부팅 수락은 별도 |
| 실패 후 종결 | 5번 하위 후보에서 원 host/기한·명시 private 수습·동일 수습 재개·내구 예약 반환과 다음 신규 생성까지 로컬 검증. 운영 시간 정책과 상위 연결·설치본 수락은 별도 |
| 3번 로컬 마감 | 단일 Linux 준비 슬롯·원 실행 소유권·private 준비/게시 분리·요청별 출력. 최종 관련 회귀 481개 통과, 두 OS 모두 A 복사 해제 전 B 조회/실제 stop 완료 원자료 확인. 부분 설치 29개 Python 파일·독립 import 일치 |
| 4번 하위 로컬 마감 | 요청별 관측·공통 잠금 밖 조회·유한 재관측·관측 실행량/지연 표본 보호. 기존 541개 후 슬롯 부모 중단/정상 반환·파일 안전 검사 보완 후보로 22 suite 543개 통과. 두 중심 barrier·양 OS polling, 부분 설치 29개 바이트·6개 격리 import 재확인 |
| 1번 최종 마감 | **기능별 기준선·비용·소유권 대응 정리 완료**. 5번 커밋 `4a585d4`와 원자료를 대조한 [기준선](platform-management-baseline.md)에 관리 전체·실패 종결·비용/담당 근거를 연결. 후속 개념 서식 복원은 내용/12장 유지, 5.5번 실행과 구분 |
| 5.5번 현재 범위 | 첫 [P55](#stage55)의 반례와 [lifecycle/CPU 후속](#stage55-followup)을 보존한다. 최신 [좁은 교차 보완](#stage55-cross-boundaries)은 firmware·자원 usage·lifecycle DHCP의 무관한 stop 대기를 분리하고 CPU 연쇄/오류 회복·추가 비용을 확인한다. 하위 로컬 수락이며 실보드/상위 제품 종단은 별도 |
| 입력·진단 후속 | [2026-11-01 후속](#defaults-errors-review): CLI/Manager의 선택 자원 생략·원 입력 replay, 포화 trace/비활성 형식화/오프라인 예외·STOP 전 로그 대기 보완. 오류 구분·필수 내구 저장·기존 P55 보호 유지. HTTP/Spring/Agent/Worker 제품·보드·운영 정책은 미변경 |
| create 입력 호환 보완 | [최신 의미 비교](#create-compatibility): 두 자원의 생략↔원 확정값 명시를 양방향 허용. 과거 형태 비교 선택을 대체하고 기존 완전 명시 호출·지원 원장 혼재를 유지. 내부 저장 버전 선택을 호출자에게 요구하지 않음 |
| 6번 인계·배포 준비 | [현재 정리](#stage6): 담당자 현재 지시/이력 분리, Gateway 정규 recipe·비활성 unit·이미지 포함 연결, 고정 환경 RPM/QA·ARM Python import·systemd loader·영향 회귀 완료. 운영값·제품 연결·실제 설치와 7번은 별도 |
| Gateway 제공자 후속 | [G1/G2/G3](#gateway-provider)의 실제 process/UDS·원 결과·보호 종결·codec·PTY 기반을 유지한다. 실제 지도형 planner·반복 이력·feedback-2는 아래 최신 후속에서 추가했다. 상대 제품·실물 수락은 별도 |
| 이력·실제 주행앱 최신 후속 | [2026-11-02 결과](#history-drive-final): 원 관측 시각을 보존하는 bounded lattice/충돌 검사/tracker·상태 적용, 독립 MANUAL, 최대 지도·대칭 실행 교체/재합류·동일 Authority 40회 운영을 로컬 확인. 고정 ARM·정규 RPM/QA·설치 ARM codec/client를 검증. 합성 입력/차량·실제 입력 생산자·운영 수락을 구분 |
| CLI·조회 후속 | [2026-11-02 구현](#cli-query-consistency): 0단계 조사 재사용, Xen 없는 등록/의도 조회·출력과 실행 분리·원 key CLI·독립 조회 연결. 실제 CLI/barrier·고정 recipe RPM/QA·ARM Python/QEMU·격리 설치 wrapper 확인. Worker/API 소비 변경과 실보드 수락은 별도 |
| VM 공급·등록·신호 후속 | [2026-11-02 제공자 구현](#vm-provisioning-integration): 일반 생성/factory의 영구 자격 공급·실행별 Store/receipt, 독립 1회 등록 통지와 CLI, 초기/다음/현재 프로필, 실제 ARM drive template 선택을 연결. Spring 등록-only 수신 구현·운영 설정·새 Linux 전체 rootfs·실보드 수락은 별도 |
| 남은 범위 | 운영 create 시간값 공급·보드 수락, 상위 제품 연결·실제 HTTP polling/정규 설치/보드 수락·API cgroup의 guest backend 문제는 미완료. 모든 부하/스케줄의 무기아나 지연 상한 수락은 아님 |

아래는 후보별 이력이다. 과거 2회/439개/보드 적용 기록을 최종 1회/449개 이후 후보의
실보드 수락으로 읽지 않는다. OS별 보완과 3번 수정 전 반례는 마지막 절에 연결한다.

## 후보와 수락 범위

기준은 `eca59e1ba272496b7c661bcd5088b73624ed0763`, 작업 브랜치는
`feat/platform/recovery-scenario-closure`다. 후보는 기준 HEAD에 미커밋 diff·새 파일·소스별
해시를 더해 식별한다. 앞선 Zephyr 독립 실행·준비/커밋 검증은
[선행 보고서](zephyr-drive-foundation-20261029.md)에 보존한다.
아래 국소 구현·검증은 기존 4개 커밋의 범위이며, 후속 검토 브랜치 병합과 제품 수락
준비 결과는 후속 절에서 별도로 구분한다. 아래 초기 국소 마감의 미수행 범위는 당시
범위이며, 승인 후 실보드 시험과 이후 Linux 단일 검증 복사 후보는 각 절의 상태를 따른다.

제품 소스는 플랫폼 하위 Manager/Execution과 Gateway만 수정했다. FastAPI·dom0-manager·
Worker·Spring·프론트·Linux/Agent·장치 제품 소스는 수정하지 않았다. 실보드/서비스/VM/
broker/CPU/네트워크·전체 이미지·SD를 조작하지 않았다. SQLite·새 daemon·runtime 의존성·
MQTT type/필드·프로토콜을 추가하지 않았다. 게스트 소스를 바꾸지 않아 ARM 재빌드는 하지 않았다.

이 보고서는 아래 국소 보장의 구현·로컬 회귀·부분 패키징이다. 전체 복구 시나리오와
실제 차량·제품 종단 수락을 완료한 것은 아니다.

## 요구별 구현과 남은 경계

| 시나리오·요청 | 실제 변경 | 보장과 별도 수락 |
|---|---|---|
| D03-G01·G03·G04, H3·G2 | `authority_ops.py:inspect/revalidate`, Authority marker/원본 읽기 | 진단은 생성/쓰기/출력 0. 현재 정상 원본만 동일 lease·기대 checksum으로 내구 재확인, 원 ID/결과/pending/기한/generation 보존·재무장 요구. 손상 백업 복원은 아님 |
| G2·H3 | `AuthorityStore:initialize/load/verify/read_current` | 잔존 tmp/marker를 최초 빈 상태로 취급하지 않음. symlink·FIFO·공개 marker/원본 거절. 원본 검증 완료 전 RAM 값 채택 없음 |
| D03-G02 | 기존 원본/백업 비교와 증거 부족 차단 | checksum·같은 generation만으로 최신 backup 승인 안 함. marker가 최신 generation/hash를 보존하는 추가 설계는 별도 결정 전 미구현 |
| B3·BO3 | `output_guard.py`, `config.py`, `__main__.py:open_sink` | root 소유 실측 설정을 serial의 필수 입력으로 검증. 원 요청·양자화 출력·연속 제출 목표 변화율 검사. 실제 차량 값·운동 상태·ACK는 별도 |
| B3·H2 | `verified_runner.py:_submit`, `frame.py`, `machine.py:valid_command` | 위반/쓰기 실패 시 RAM의 DRIVE 철회·같은 회차 STOP을 failure journal fsync보다 먼저 시도. STOP 실패도 별도 기록. 극단 유한 값의 scaling overflow와 큰 JSON 정수의 변환 오류 차단. 시리얼 제출은 장치 적용이 아님 |
| F1·F2·F5·FO1 | `Execution.requests(deadline=...)`, Manager의 key 조회·짧은 stop admission·busy 완료 replay | 기존 관측 기한을 순회 중에도 검사. 불완전 조회의 부재/유일성 확정 0. 원 물리 deadline·key·intent revision은 유지 |
| B2·B4 | 시나리오 현재 처리 갱신 | 이미 구현한 독립 worker/cancel/join/앱 신원을 직렬 계산으로 설명하던 과거 표기 정정. 새 계산 알고리즘이나 자동 앱 복구를 구현한 것은 아님 |

`revalidate`는 운영자가 기존 Gateway 실행을 종료해 동일 lease를 확보한 상태의 로컬 도구다.
root의 비협조적인 외부 파일 수정까지 원자적으로 통제하지 않는다. 원본 부재·손상·stale
진단·lease 교체는 거절하며, backup 입력으로 대체하지 않는다. 실제 운영 상태에서 실행하지 않았다.

## 상태 판정

| 파일/관측 | 처리 |
|---|---|
| 검증된 데이터 저장소의 디렉터리 부재/빈 디렉터리 | 읽기 전용 FIRST_INSTALL 관측. 초기화/출력 허가 아님 |
| 정상 private marker·정상 원본 | VALID와 현재 hash/세대/선택·기록 개수 제공. 원본 내용 불변 |
| 원본 없음 + marker/tmp/backup 흔적 | BLOCKED. 정상 최초 빈 상태로 승격하지 않음 |
| marker/control 손상·권한 오류·FIFO·symlink·동시 변경 | BLOCKED, 초기화/출력/세대 발급 없음 |
| 정상 원본·같은 lease·같은 기대 hash | rearm_required=true 내구 재기록. 원 기한/ID/결과 유지 |
| write/fsync/replace 효과 미확정 | 성공 반환 안 함. 낮은 세대나 과거 RAM 사본으로 덮지 않음 |
| 재확인 직후 프로세스 종료 | 실제 다른 프로세스 로드에서 원 요청/세대·재무장 유지, lease 회수 확인 |

## 실행한 검증

원자료는 PC `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-recovery-scenario-closure/`,
소유 WSL 시험 사본은 `/home/ssafy/xenova-recovery-scenario-closure-20261030/`에 둔다.
`run_checks.py`와 모드별 JSON이 실제 argv·rc·소요시간을 기록한다. 새 의존성을 설치하지 않고
기존 격리 pytest/FastAPI 환경과 기존 systemd 259 검사기를 재사용했다.

| 시험 | 결과·근거 |
|---|---|
| Gateway 전체 | `gateway-5.log`: 142 passed. 정상 제출/거절, 양자화·변화율·시계·부분 쓰기·저장보다 앞선 같은 회차 STOP, marker/FIFO/원본 오류·실제 프로세스 종료, 기존 warm/계획 전환 포함 |
| 하위 관리 4개 suite | `platform-2.log`: 102 passed. 순회 중 예산 소진, 부분 조회의 거짓 부재/유일성 거절, 미수락 stop의 intent 불변·재전달 뒤 원 90초 보존, 기존 fork/잠금/replay |
| dom0-manager 전체 | `manager-1.log`: 557 passed, 기존 Starlette 경고 1. S01/S02·Worker/Engine·Driver/HTTP 로컬 회귀. 제품 네트워크/DB/보드를 사용한 종단 수락 아님 |
| 복구·reconcile·lifecycle 5개 suite | `recovery-3.log`: 179 passed. 실제 자식 SIGKILL·원 journal/receipt replay·force 승인·systemd 259 loader·부분 초기화/종료/앱 Bootstrap. Xen/Store/장치는 명시적 대역 |

초기 실패도 보존했다. Gateway 첫 실행은 새 설정 시험에서 기존 필수 VM 쌍을 생략한
fixture 오류(139 pass/1 fail)였고 제품의 필수 인자를 완화하지 않았다. 하위 첫 실행은
기존 오류 주입 wrapper가 새 선택형 deadline 인자를 전달하지 않아 생긴 TypeError였으며
대역 signature만 수정한 뒤 102개를 통과했다.

확장 회귀의 초기 5개 실패는 `/mnt/c` 복사본의 정책 권한·자식 PYTHONPATH·systemd 259
검사기 지정 누락이었다. Git 모드로 만든 Linux 사본과 기존 검사기를 사용해 재실행했다.
후속 1개 SIGKILL 자식의 first-install 공용 모듈 import 경로도 시험 launcher에 추가했다.
제품 권한/force 승인 조건·원 결과 assertion·시간값은 낮추지 않았다. SIGKILL/파일 오류는
실제 SD 전원 상실·Xen·장치 수락이 아니다.

## 패키지·소스 연결

Gateway wheel은 기존 setuptools/wheel로 소스 사본에서 빌드하고 archive의 모든 `.py`를
원본과 대조한다. 하위 두 모듈은 기존 recipe의 설치 경로/0644 모드와 실제 바이트를
대조한다. `PACKAGES.json`과 소스 봉인이 경로·해시를 연결한다. 이것은 BitBake RPM·전체
이미지·설치본 검사가 아니며 Gateway의 정규 Yocto recipe/제품 unit 편입은 여전히 남아 있다.

## 남은 종단 수락

- D03-G02의 손상 원본 backup 복원에는 최신성 증거 확정이 필요하다. 현재 initialized는
  설치 흔적이며 최신 세대 증명이 아니다. 추가 신뢰 장치는 기존 요청대로 별도 합의한다.
- 계획 전환 actor queue는 프로세스 내부 연결부다. 별도 Worker/운영자와의 전송·결과 연결,
  입력 없는 MANUAL 선택과 이동 허가, 복구 전 실제 보호 확인의 종단 수락은 남아 있다.
- 차량 한계는 측정·설정 후 수락해야 한다. 실제 가속도/조향/제동·장치 ACK·watchdog/E-stop·
  남은 출력 예산을 만족한 연속 승계는 이번 통제 시험으로 대체하지 않는다.
- 큰 Debian 생성의 전체 자산 검증/복사·전역 실행 잠금은 바꾸지 않았다. key 순회 예산
  보완을 과거 8GiB 등록 비용이나 전체 응답 시간 해결로 해석하지 않는다. 개별 syscall/
  fsync·정렬 지연을 선점하는 hard realtime 한계도 제공하지 않는다.
- Worker의 A1 첫 무수신·D1/D2 보고자/앱 분류·E4 안정 구간/episode 예산·D4 공통 기반,
  G2 host 재시작 보존 정책과 D03 원 결과/Standby 연결은 담당 요청서에 누적했다.
- Spring의 원 key/pending/조회·재시작 회수·조건부 동일 이행, 실제 MANUAL 세션·Jetson
  지도/상태 전달·Linux/Agent·제품 화면 연결은 담당자 수락 전이다.

정식 사용법은 [Gateway](../../platform/services/gateway/README.md), 현재 시나리오는
[복구 시나리오](../platform/recovery-scenarios.md), 담당별 후속은
[요청 색인](../requests/README.md)을 따른다. 기존 ID·미완료 상태와 과거 실패를 보존한다.

## 검토 브랜치 통합과 기본 관리 수락 준비

기존 4개 커밋 `357b6c7`·`d1c87f3`·`e59cd39`·`96231d0`을 보존하고, 검토 완료 ref
`feat/platform/zephyr-drive-foundation`의 `89e37d3e11c0e14eb996fbf894aac813aaac1044`를
일반 merge `33ab19d9358f95913c75cad3e31949d78544c5d7`로 통합했다. 스쿼시·rebase·force push는
하지 않았다. 이 ref에는 검토 4건과 최신 platform의 MQTT·display 선행 변경이 포함된다.
검토 작업방의 MR은 선행 변경 포함 범위 확인으로 제출 전이며, MR 제출 완료로 보고하지 않는다.
검토 자체의 C/native/ARM 원자료·실패 이력은
[검토 보고서](zephyr-drive-foundation-review-20261030.md)에 유지한다.

병합 후보는 별도 Linux 사본의 Git 파일 모드·소스 SHA-256으로 고정했다. 원자료는
PC `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-vm-e2e-acceptance/`의
`MERGED_SOURCE.json`·`SUPPLEMENTAL_SOURCE.json`, `merged_checks.py`와 suite별 JSON/log에 둔다.

| 병합 후 검증 | 실제 결과·경계 |
|---|---|
| Gateway 전체 | `merged-gateway.log`: 151 passed. 기존 Authority/출력 한계와 추가 AUTO 재개 보호를 함께 확인 |
| 하위 관리·복구·MQTT·신원·회수 관련 15개 suite | `merged-platform.log`: 377 passed. Xen/Store·파일 오류 등 기존 대체 경계 유지 |
| dom0-manager 전체 | `merged-manager-r2.log`: 575 passed, root 권한 특성으로 1 skipped, 기존 Starlette 경고 1. skip한 쓰기 권한 실패 1건은 일반 사용자 `merged-permissions.log`에서 별도 통과 |
| 최초 Manager 실행 | 571 passed/4 failed/1 skipped. 시험 사본에서 기존 Spring DTO 원문을 빠뜨린 준비 오류. 제품/시험 assertion은 바꾸지 않고 Git 원문 210개를 추가한 뒤 같은 전체 suite를 재실행 |
| Python 부분 패키징 | Gateway wheel의 15개 .py와 하위 recipe 설치 파일 2개의 후보 바이트 일치. `PACKAGES.json`, wheel SHA-256 `0b823b1edcb41c11ce572927c8685fc3564cde25289e47f0abed66b268ce2ea1`. BitBake RPM·전체 이미지·설치 검사는 아님 |
| 게스트 후보 | `platform/apps`·`platform/guests/zephyr`는 검토 ref와 차이 0. 검토 ref의 C/native·ARM 3개 프로필 근거를 재사용하며 이번 병합에서 다시 ARM을 빌드하지 않음 |

EC2에서는 기존 PEM과 검증된 호스트키로 읽기 전용 확인을 수행했다. backend 이미지
`xenova-backend:13`의 실제 container health는 HTTP 200/UP이며, 배포 OpenAPI의 VM
목록/상세·생성·start/stop/delete·metrics/processes·templates/events 경로와 생성 schema를
확인했다. schema 조회와 health 통과는 VM 기능 통과가 아니다. 환경 전체나 비밀값은
출력하지 않았고 허용한 시간 설정의 명시적 override는 없었다. 소스 기본값이 곧 실제
전체 적용값이라고 확정하지 않는다.

첫 사전 확인에서 보드 `192.168.123.123:22`는 timeout이었으며, EC2에서도 설정된 보드 Tailscale
주소의 22·1883·8081이 timeout이었다. Tailscale peer는 offline이었다. 전원·이미지·Wi-Fi
중 무엇이 원인인지는 확인하지 못했다. 당시 로그인 전 관리 화면과 연결 불가 상태에서 VM
생성/조회/제어/삭제·실계측/Agent 전이·이벤트/화면 갱신·웹 콘솔은 **실행하지 못했다**.
이 상태를 제품 기능 실패나 종단 수락으로 분류하지 않는다.

이 시험 단계에서 새 VM·계정·DB 행·remote helper 파일을 만들거나 운영 설정·서비스를
바꾸지 않았다. SSH stdin으로 실행한 조회 도구의 결과만 PC에 보존했다. 이후 기본 관리
수락의 두 시작점·담당·assertion은
[HTTP·Spring·화면 요청](../requests/20261029-request-http-spring-ui.md#기본-vm-관리의-제품-종단-수락)에 연결한다.

## 설치본 FastAPI·EC2 직접 호출

이후 사용자 접속 확인에 따라 재연결했고, 같은 boot
`fc091b7f-05b3-4fcc-9744-05620cde9e7d`의 설치본을 조회했다. 연결이 다시 끊긴 구간과
복구된 구간 모두 보존했다. 사용자는 FastAPI 직접 시험을 먼저 선택했으며 Spring 공개
API 로그인과 프론트 입출력은 아직 수락하지 않았다. EC2에서 FastAPI를 직접 부른 것을
Spring→FastAPI 종단 수락으로 쓰지 않는다.

### 기능별 실제 결과

| 호출·확인 | 관측·원자료 | 판정 |
|---|---|---|
| 인증된 `/healthz` | `board_baseline-r2.stdout`: 200, 약 0.098초 | 서비스 health 통과. 제어·guest 준비 수락 아님 |
| 인증된 `GET /api/v1/vms` | 같은 자료: 200, 약 7.739초, 기존 실행 3개 | 목록 1회 통과. 반복 경합 없는 시간 상한 아님 |
| 새 Zephyr 생성, dom0 localhost | `board_zephyr_create-r2.stdout`: 70.995초, 503 `create waited 30s for the manager` | 공유 Manager 차례 대기에서 거절. 새 등록/실행 없음 |
| 동일 UUID·명세 생성, EC2→FastAPI | `ec2_zephyr_create.stdout`: 58.729초, 503 `get waited 30s for the manager` | 생성 전 조회에서 거절. 다른 UUID나 자동 재시도 없음 |
| 기존 Linux VM 상세 | `board_read_functions.stdout`: 30.091초, 503, 같은 Manager 차례 대기 | 실패를 재현. 기존 VM은 계속 실행 |
| `GET /api/v1/stats` | 같은 자료: 200, 약 0.368초, 실제 Xen CPU 누적 시간·메모리·네트워크/디스크 계수 | 별도 통계 경로 통과. Linux Agent CPU%·프로세스·차트 수락 아님. Zephyr 디스크는 null/NO_DEVICE 유지 |
| Debian·Ubuntu 생성·상태 제어·삭제 | 공통 진입 경합을 확인한 뒤 아직 제출하지 않음 | 미수행. Zephyr 성공이나 전체 템플릿 지원으로 확대하지 않음 |
| Agent 전이·제품 사건·화면 갱신·웹 콘솔 | 소유 VM 생성과 제품 로그인 미완료. 설치 console 환경 항목은 없고 config 기본은 false | 미수행. 기본 VM의 Agent/서비스를 정지해 대신 시험하지 않음 |

원 UUID는 `bd784cbd-a711-4e52-9913-c13b3e6374d7`, 표시 이름은
`e2e-20261030-direct-zephyr`다. `board_owned_observe*.stdout`에서 이 UUID의 등록·
원 작업 journal이 없고, Xen에는 기존 3개 guest만 있으며 CPU 정책 두 파일 해시와
기본 VM 배치가 최초 관측과 같음을 확인했다. 실패 요청의 새 key/UUID나 강제 수습은 하지 않았다.

### 원인 경계와 설치본 차이

`board_contention.stdout`에서 PID 1267이 platform.lock을 보유했고, 그 자식 Python
identity helper가 domid 1/2/3 순으로 바뀌었다. 관측한 helper는 R 상태·CPU 시간 증가였으며,
한 PID의 영구 syscall 대기를 확인한 것은 아니다. API 접근 기록에는 반복 list와 그중
일부 503이 있었다. 즉시 실패는 공유 `SerializedManager.call`의 대기이며, 반복 신원 조회
비용과 경합의 연관 근거다. 모든 대기 시간을 단일 helper에 귀속하거나 과거 장애까지 같은
원인으로 확정하지 않는다. 유효 busy 설정 30초를 전체 HTTP 예산으로 취급하지 않는다.

`RUNTIME_MATCH.json`의 설치 모듈 8개 중 현재 작업트리와 일치한 것은 Python identity 모듈
1개였다. 나머지는 Git의 이전 모듈 바이트와 일치했다. 개별 모듈 일치로 설치 전체 HEAD를
추정하지 않는다. 설치 OpenAPI에는 key stop 200/202와 control-operation 조회가 없었다.
FastAPI·Worker·Spring 제품 소스·배포 설정은 이 대조를 위해 수정하지 않았다.

### 기존 native helper의 ARM·실제 Xen 대조

[기존 인계 보고서](display-253-board-handoff-20261029.md)는 native helper를 비교한 뒤
보드 인계를 위해 Python wrapper로 복원했다고 기록한다. 현재 소스의 같은 C와 wrapper를
재사용했다. 기존 고정 Yocto compiler/sysroot로 helper 한 개만 ARM 부분 빌드하고 ELF와
loader를 확인했다. 소유 출력 경로의 권한 때문에 실패한 첫 준비 시도도 보존했으며, 기존
빌드·캐시 권한을 바꾸지 않고 별도 사용자 소유 경로로 출력했다.

`NATIVE_IDENTITY.json`의 ELF SHA-256은
`72c27e497df7f8738ce930dcd5c609e2732b46a3de7d6f867b556bc2f8cc8aab`다.
`board_identity_memfd.stdout`에서 익명 메모리 FD로만 실행하여 2회 × domid 1/2/3,
6쌍 모두 기존 신원 JSON과 같았다. 관측 시간은 Python 0.711~0.988초, native
0.0083~0.0119초였다. 순차 소수 표본이며 전체 관리 작업 개선·최악 지연 보장은 아니다.
새 보드 파일·의존성·VM·서비스를 만들지 않았고 FD는 닫았다.

`identity-runtime-candidate.tar.gz`에는 ELF와 정규 wrapper 두 대상 및 manifest만 담았다.
실제 계획 모드에서 기존 wrapper 0755/root:root·해시, native/백업 부재를 확인했다.
적용·원복 도구의 소유 WSL 임시 루트 6개 사례는 `IDENTITY_ROLLOUT_TOOL_TEST.json`에 둔다.
**공용 helper 설치는 별도 확인 전이며 이 결과만으로 기본 관리가 정상화됐다고 마감하지 않는다.**

누적 담당자 요구는 [기존 HTTP 요청](../requests/20261029-request-http-spring-ui.md#설치본에서-재현한-직렬화-경합과-배포-대조)에
연결한다. EC2의 파일·컨테이너·DB·env와 실보드의 서비스·CPU·네트워크는 변경하지 않았다.

## 생성 전 용량 검사와 단계 내 해시 재사용

아래는 앞선 설치본 관측 이후, 사용자가 요청한 기본 관리 개선 2번의 별도 수정·적용 결과다.
기존 관측의 미수행/승인 대기 표시는 당시 결과로 보존한다. 이 수정으로 큰 이미지 준비의
전역 잠금, create 전체 deadline, HTTP·Spring·화면 종단이 마감됐다고 해석하지 않는다.

### 변경과 검증 순서

`xenova_management.Manager._selection`에 내부 선택 인자 `full`을 추가했다. 신규 create는
metadata·manifest·자원·주소 검증 후 기존 용량 계산으로 사전 검사하고, 전체 payload 검증과
기존 최종 용량 검사를 이어서 수행한다. 사전 검사는 예약이 아니며, 전역 잠금과 operation
기록·부분 작업의 소유·재전달 경로는 변경하지 않았다. 손상 payload와 용량 부족이 동시에
존재하면 payload 해시 오류보다 용량 부족이 먼저 나올 수 있지만, 손상 metadata는 여전히
사전 검사 전에 거절한다. 용량 부족을 이유로 정상 게시하거나 검증을 생략하는 경로는 없다.

`xenova_catalog.describe(full=True)`는 `root.raw`를 한 번 해시해 manifest의 두 hash 필드와
각각 대조한다. 이전에는 같은 호출에서 두 번 읽었다. 모든 payload의 전체 검증과
`Manager._prepare` 및 `prepare_instance`의 독립된 준비·복사 전후 검증은 유지했다.

시험은 `platform/tests/test_xenova_template_preflight.py`에 추가했다. 작은 실제 파일과
실제 Manager/catalog/용량 계산을 사용하고 Xen·게스트 준비만 기존 fixture로 대체한다.

| 근거 | 실제 결과·assertion |
|---|---|
| `template-focused-baseline.log` | 수정 전 새 기준 5 failed/5 passed. payload hash 두 번 뒤 용량 검사, 용량 부족에서도 payload 읽기 확인. 새 사전 검사 뒤 재확인 조건은 수정 전 존재하지 않음 |
| `template-focused-candidate.log` | 10 passed. 용량 부족 시 payload hash 0·journal/복사/Xen create 0, 전체 검증의 payload별 hash 1, 두 root hash 필드 검증 유지 |
| 같은 집중 시험 | 검사 순서 capacity→root hash→capacity, 검증 중 여유 감소·원본 변경 거절, 손상 metadata 우선 거절, 완료 replay의 재복사·재검증 없음 |
| `template-regression-candidate.log` | 383 passed, 93.02초. 위 신규 10개 포함. 기존 management/review/boundary/budget/platform/first-install/Zephyr/lifecycle/recovery/reconciliation 관련 suite |
| `template-installed-candidate.log` | 보드 기존 Manager에 동일 함수 수정만 이식한 후보로 10 passed. 이 시험도 로컬 Linux 격리 실행임 |

명령·고정 입력·파일별 해시는 PC 증거 경로
`C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-vm-e2e-acceptance/`의
`TEMPLATE_BASELINE.json`, `TEMPLATE_CANDIDATE.json`과 각 시험의 `.json`에 보존했다.
기준 HEAD는 `5009cc4`이며 미커밋 제품 diff와 새 시험이 후보의 일부다. HEAD만으로 후보를
식별하지 않는다. 별도 도구·의존성·firmware·커널 수정은 없다.

### 부분 설치 묶음과 실보드 적용

두 모듈은 기존 `xenova-platform_1.0.bb`의 SRC_URI·0644 설치 목록에 이미 포함된다.
정규 소스용/기존 설치본 이식용 두 변형을 `xenova-template-preflight-modules-20261030.tar`에
각각 넣고 꺼낸 바이트를 대조했다. `TEMPLATE_STAGING.json`에 해시를 기록했다.
이는 소유 파일의 설치 묶음 검증이며 BitBake RPM 생성·전체 이미지·SD 검사는 수행하지 않았다.

보드 `192.168.30.96`의 boot ID `3acf2435-a597-460e-a2ae-5d39f2cb7678`에서
두 대상 모듈을 root 전용 `/root/xenova-template-preflight-20261030`에 백업한 뒤 적용했다.
기존 catalog 전체와 Manager 수정 대상 함수는 수정 전 소스와 일치했다. Manager의 다른
설치 버전 차이는 그대로 보존했으며, API·Worker 제품 소스/unit/env는 교체하지 않았다.

| 모듈 | 정규 수정 소스 SHA-256 | 실제 보드 적용 SHA-256 |
|---|---|---|
| `xenova_catalog.py` | `2acd0e8efc4a7b40e5531b6aff0c6d596e0c7405012acb09949cdd74c6c9f804` | 동일 |
| `xenova_management.py` | `13a03ed66f4dbd33d79107730365bf4c0cc8cecb69f991940c148cd7335cad55` | `78321ce280646727369ac961883b44caa2dc5e75638c522d07628e6522ca667c` |

실제 unit의 종료 hook/중지 전파가 없고 API cgroup에 API PID 한 개만 있음을 확인했다.
기본 VM backend·xenstored·xenconsoled는 다른 unit 소유였다. 기존 operation→admission
순서로 잠금을 얻어 진행 중인 물리 작업이 없는 구간에서 파일을 원자 교체하고,
메모리에 로드된 모듈 갱신을 위해 `xenova-dom0-manager.service`만 정상 재시작했다.
API PID 1254→27273, health 200을 확인했다. timeout/KillMode 변경이나 force는 없다.

`board_template_apply-r2.stdout`, `TEMPLATE_BOARD_FINAL.json`에서 두 파일의 실제 해시·
0644/root:root, boot ID, 기존 Debian/Zephyr A/B의 domid·메모리·vCPU 유지와 CPU 정책
두 파일 해시 동일을 확인했다. 인증 파일 바이트도 전후 동일했다. Worker·MQTT·Tailscale
PID는 유지됐고 SSH·네트워크 설정·host는 변경하지 않았다. 백업의 `manifest.json`과
`rollback.py`는 소유한 두 파일만 복원하는 경로이며 새 부팅/후속 파일 변경은 재검토한다.

새 VM 생성과 전체 실보드 시험은 재개하지 않았다. 실제 용량 실패 재시험이나 대용량
Linux 생성 시간 개선도 측정하지 않았다. 약 6분 47초는 이전 요청부터 용량 부족 반환까지의
시간이며, 이 변경 전후 해시 함수만의 정밀 비교 수치로 사용하지 않는다. 마지막 읽기 전용
파일·보호 상태 확인을 마친 뒤 보드 사용을 종료했다.

## Linux 준비 검증과 복사 I/O 결합

사용자의 후속 요청에 따라 기본 관리 개선 2번의 남은 Linux 검증·복사 경로를 보완했다.
앞 절의 보드 적용 이후 수행한 로컬 변경이며 보드에는 적용하지 않았다. 전역 잠금 분리,
create 전체 deadline, 상위 제품 연결과 실제 Linux/화면 종단 수락은 여전히 별도다.
작업 설계와 수정 전 입력은 PC의 `2026-10-30/xenova-template-io-design/`에 보존했다.

### 구현과 유지한 경계

- `xenova_catalog.describe`: root의 두 기대 해시와 boot 참조·hash/size 형식을 payload
  읽기 전에 대조한다. metadata로 확정할 수 있는 오류는 전체 디스크 읽기로 넘기지 않는다.
- `Manager._prepare`: Linux의 manifest/metadata 재검증은 유지하고 준비 단계 전체
  payload 검증을 실제 복사 스트림에 결합한다. 생성 선택 단계의 전체 검증과 Zephyr의
  기존 전체 bundle 검사는 유지한다. 공개 action/spec/envelope·원 operation은 변경하지 않는다.
- `prepare_instance.make_instance`: 제공된 기대 manifest hash를 현재 값으로 덮어쓰지
  않고 대조한다. 같은 metadata 바이트에서 JSON과 digest를 얻고, root·부팅 자산 전체의
  복사 바이트를 검증한 뒤 tune2fs·guest 설정을 수행한다. 신규 일반 파일은 0600/O_EXCL로
  만들고 reflink하지 않는다. 짧은 read/write를 처리하며 조기 EOF·진행 없는 write·
  ENOSPC/EIO/fsync·hash 실패는 게시 성공이 아니다.
- sparse hole은 논리적인 0으로 hash하고 출력의 크기·hole을 유지한다. 비지원 파일시스템은
  전체 스트림 검증으로 처리한다. 관련 표준 API는 [Python os 문서](https://docs.python.org/3/library/os.html#os.SEEK_DATA)를 따랐다.
- 복사 전후와 게시 직전 dev/inode·크기·mtime/ctime·mode·소유권 등으로 원본 변경을
  거절한다. atime은 정상 읽기로 갱신되므로 비교하지 않는다. 이 guard는 같은 준비 작업의
  연결 근거이며 영구적인 검증 cache나 root 외부 변경의 원자적 통제를 뜻하지 않는다.
- 실패한 stage/workspace와 원 journal을 보존한다. 복사 대상의 물리 매체 재읽기 검증이나
  전원 상실 수락은 아니다. 기존 `validate_record`, guest/SSH 검증·필수 저장·게시도 유지한다.

### 실제 경로의 처리 횟수와 회귀

동일한 작은 실제 파일과 실제 Manager→make_instance 경로에서 외부 Xen·mount·chroot만
대체했다. 별도 `sha256sum` 호출과 catalog 해시, 복사를 각각 계측했다.

| root 전체 입력 처리 | 앞 절 수정 뒤 기준선 | 이번 최종 후보 |
|---|---:|---:|
| catalog 전체 검증 | 2 | 1 |
| 준비 함수의 별도 원본 해시 | 2 | 0 |
| 외부 cp | 1 | 0 |
| 전체 검증 결합 복사 | 0 | 1 |
| 합계 | 5 | 2 |

`cost-baseline.json`과 `cost-candidate-final.json`의 결과다. 논리적인 전체 입력 처리
횟수이며 물리 SD 읽기량이나 실보드 지연 개선율이 아니다. 이 작은 fixture의 실행 시간도
성능 개선 근거로 사용하지 않는다. 생성 전 전체 검증이 여전히 남으므로 장시간 전역 잠금이
해결됐다는 뜻도 아니다.

`test_xenova_verified_copy.py`는 실제 파일의 dense/sparse/trailing-hole, 짧은 read/write,
비지원 fallback, 입력 symlink/hardlink/FIFO, 동일 inode 갱신·mtime 복원, 경로 교체,
hash/size 불일치, I/O/fsync 실패, 기존 출력 보존을 확인한다. 실제 준비 경로에서는
manifest 결합·수락 후 payload 변경 거절·게스트 설정 중 원본 변경·정상 생성·구형 manifest·
factory seed의 pending 신원과 전용 어댑터 사용을 확인한다. 복사 중 소유 자식을 SIGKILL한
뒤 새 Manager로 같은 요청을 호출해 원 operation·부분 디스크 inode/내용 보존과 재복사·
자동 초기화가 없음을 확인한다. 이는 SD 전원 상실이나 Xen 실기동 시험이 아니다.

집중 시험은 `focused-candidate-final.log`에 47 passed로 기록했다. 앞선 후보의 전체 관련
회귀는 `regression-candidate.log`에 436 passed이며, seed·구형 manifest·수락 후 변경 시험을
보완한 최종 후보는 `regression-candidate-final.log`에 **439 passed, 97.94초**로 기록했다.
skip·실패는 없었다. 명령·반환 코드는 같은 이름의 `.json`에 보존했다.

### 부분 설치 묶음과 미수행 범위

기존 recipe의 `prepare_instance.py`, `xenova_catalog.py`, `xenova_management.py` 세 파일을
정규 설치 경로·0644로 staging하고 후보/출력 바이트를 비교했다. `SUBSET_PACKAGE.json`과
`xenova-template-verified-copy-files-20261030.tar`에 보존했다. 새 의존성·recipe·버전은 없다.
BitBake RPM·전체 이미지·ARM 실행 검증을 수행한 것으로 확대하지 않는다.
세 파일은 같은 후보로 배포해야 하며 Manager만 별도 교체해 검증 결합 복사가 반영됐다고
수락하지 않는다. 실제 설치가 필요한 시점에는 후보·영향·원복을 다시 대조한다.

| 최종 실행 소스 | SHA-256 |
|---|---|
| `prepare_instance.py` | `6dbb9134018913df72ad0df6d9bc523aa22055f6622b67a4aa48497d82c04a63` |
| `xenova_catalog.py` | `2987e3cd3829f0eaf8eb3b1a34710963c4128a55f4458b75d041d6ef95cbb10f` |
| `xenova_management.py` | `d6d30df3350ef88e57e8cb0c003c6b39ff6f33c9c644306acb232c974f511ff5` |

설치 묶음 SHA-256은 `f857dc8f54b143070486be000e0f3da09ff08ec55c73efc0eaaa4af4b5aa4380`이다.
`CANDIDATE-FINAL_SOURCE.json`의 고정 입력과 작업트리의 관련 실행 소스·시험을 대조했다.

소유한 sparse fixture는 논리 크기 33,554,449바이트를 8,192바이트의 실제 data 읽기로
복사하고 논리 해시·크기와 8,192바이트 할당을 유지했다. dense fixture는 4,194,304바이트를
같은 양의 data 읽기로 복사해 해시가 일치했다. 두 경우 모두 별도 inode다. 이 수치는
해당 로컬 파일시스템의 fixture 결과이며 실제 템플릿/SD의 할당·처리량 보장은 아니다.

이번 추가 후보는 실보드·EC2·서비스·VM·네트워크·실제 broker에 적용하지 않았다.
보드에는 앞 절의 용량 사전 검사·단계 내 중복 해시 제거만 적용된 상태로 인계했다.
최종 소스는 미커밋 diff와 새 시험을 포함하므로 HEAD만으로 식별하지 않는다.

## 검증 결합 복사의 승인 후 실보드 측정

앞 절의 로컬 후보에 대한 후속 승인으로 세 모듈을 보드에 적용하고 신규 Debian 한 건을
기존 FastAPI로 생성했다. 이전의 보드 미적용 표시는 당시 단계이며, 이번에 승인·실행한
범위는 아래와 같다. EC2·Spring·프론트 제품 경로는 호출하거나 변경하지 않았다.

### 적용 전 발견과 기본 VM 복원

동일 boot ID의 API PID 34258 cgroup에 기본 Debian `debian-dev`의 qemu PID 37757
(`-xen-domid 9 -name debian-dev`)와 xl PID 37824가 있었다. KillMode=control-group이므로
API를 바로 재시작하면 backend도 영향을 받는다. 기존 VM 보존 승인으로 확대하지 않고,
사용자에게 기본 Debian 한 대의 정상 정지→적용/API 재시작→같은 UUID/자산/CPU로 복원을
추가 승인받았다. Zephyr A/B는 정지하지 않았다. console drop-in도 그대로 보존했다.

기본 Debian의 정상 stop은 원 operation `cb0385b0-a702-4ff3-84d7-ad291aaf1d15`로 성공했다.
소유 확인 도구가 빈 list에 `is not None`을 사용해 잘못된 종료 코드 3을 낸 기록도 보존했다.
도구만 bool 판정으로 고쳤고 stop을 반복하지 않았다. 적용 도구에서 Xen 부재·API cgroup의
backend 종료를 다시 확인한 뒤 진행했다. 제품 stop 실패나 force 사용으로 분류하지 않는다.

원본 세 파일·manifest·원복 도구는 root 전용
`/root/xenova-template-verified-copy-20261030`에 보관했다. 실제 설치 Manager는 다른 함수의
버전 차이를 유지하고 `_prepare`만 이식했다. 이식본은 세 모듈을 명시적으로 먼저 import한
경로/해시까지 기록해 로컬 집중 회귀 47 passed를 확인했다(`board-candidate-final.log`).
이전 이식 시험의 경로 우선순위 추정에 의존하지 않는다.

보드 적용 hash는 prepare_instance `6dbb9134018913df72ad0df6d9bc523aa22055f6622b67a4aa48497d82c04a63`,
catalog `2987e3cd3829f0eaf8eb3b1a34710963c4128a55f4458b75d041d6ef95cbb10f`,
Manager `8d601b4a6aa5a9c9743373ada8007d69dbd05bff6a4d5bd558fe6546ea4006df`다.
API PID 34258→48046, health 200·인증 파일 바이트·CPU 정책 동일을 확인했다.
기본 Debian 복원은 기존 FastAPI POST 201, 약 11.74초였다. instance.json 해시
`08c0d5563c6628baa851816684c291488cc8467bee1189d4b3e4d1cd1405a29c`,
디스크 inode 524292와 논리 크기 8GiB가 정지 전후 같았다. 등록 UUID·MAC·IP·자원은 유지했고
실행은 정상 정지/기동으로 교체됐다. 이후 관측 domid는 12였다.

### 실제 생성 요청 한 건

- UUID `53c7cf07-1529-49fe-a516-5e992b47bc43`, 표시 이름 `io-verified-copy-debian-20261030`.
- `debian-13-arm64` 1.0.2, vCPU 1, 512MiB. 원본 root 논리 크기 8GiB.
- 원 create operation `2a3c1c67-4498-4614-9c1c-691c80217e5f`, key `create:53c7cf07-1529-49fe-a516-5e992b47bc43`.
- POST 한 번, **HTTP 201 / 477.203초(7분 57초)**. 등록 journal SUCCEEDED/complete,
  execution `4555a1e6-5174-4a2b-8977-6817963c91a2`, domid 13을 확인했다.
- 할당 IP `192.168.77.17`의 TCP 22 연결도 확인했다. SSH 인증·OS 전체 준비·Agent·
  Spring/화면 종단을 수락한 결과는 아니다. 시험 요청의 긴 HTTP 대기는 제품 timeout 변경이 아니다.

실제 설치 `PlatformProvisioner.ensure_instance`는 create execute=True 한 번을 호출했다.
계획 호출 중복으로 추정하지 않는다. 적용 전 소스 경로는 전체 root 해시 4회+복사 1회였고,
새 후보는 선택 단계 전체 해시와 검증 결합 복사로 나뉜다. 약 2.31초 간격의 외부 관측에서
첫 단계와 복사 단계의 API 원본 FD를 확인했고, 이후 별도 전체 root 읽기는 관측되지 않았다.

| 요청 진입 이후 관측 경계 | 시간 |
|---|---:|
| 첫 preparing 기록 | 222.762초 |
| 첫 published 기록 | 456.473초 |
| 첫 등록 complete 기록 | 458.707초 |
| HTTP 201 반환 | 477.203초 |

원본 root FD는 사전 검증의 4.32~218.05초, 검증 결합 복사의 222.76~435.70초에 관측됐다.
약 3분 40초씩 두 구간에 시간이 남아 있다. 이것은 함수의 정확한 시작/끝 계측이 아니며,
관측 주기와 읽기 전용 관측 부하가 포함된다. 사용자 관측의 이전 6분/12분과 동일 조건의
A/B를 다시 실행한 결과가 아니므로 개선율·보장 상한·최적 후보 순위를 확정하지 않는다.
긴 전역 잠금·UI 대기·상위 timeout이 해결됐다는 의미도 아니다.

원자료 `board_io_measure.jsonl`은 PC의 `2026-10-30/xenova-template-io-design/`에 보존했다.
SHA-256은 `742f1e4adc8bb675c1e6eeb414d4fcced9e548b33c008899b96cfeb96ac761ab`이며,
`BOARD_IO_RESULT.json`에 분석·제약·최종 확인을 연결했다. 타 장치 단조 시각을 빼지 않았다.

### 승인한 시험 자산만 정리

위 신규 UUID 하나만 정상 stop→retire→동일 UUID 확인 purge했다. 모든 단계가 첫 호출에
성공했고 force는 없었다. lifecycle PURGED, 등록/디스크와 비어 있는 소유 workspace 제거를
확인했다. 실제 반환 여유는 1,273,544,704바이트(약 1.19GiB)였다. 원 create journal과
정상 수명주기 기록·측정 원자료·원복 백업은 보존했다. 다른 기존 시험 VM은 삭제하지 않았다.

마지막 확인에서 기본 3개 VM 실행·구성, CPU 정책 두 파일 hash, API health 200과 기본
Debian TCP 22를 확인하고 보드 사용을 종료했다. 최적화 구현은 한 세트이며 과거 버전은
원복 백업으로만 남겼다. backend의 API cgroup 귀속 문제는 이 세 파일 변경으로 고치지 않았다.
이 측정 후보에는 사전 전체 검증이 남아 있었다. 아래 후속 구현은 수락·예약·실패 기록
경계를 함께 조정해 검증 결합 복사로 이동한다. 이 실보드 측정 결과를 덮어쓰지 않는다.

## Linux 사전 전체 해시 제거와 원 작업 보존

사용자가 복사 중 전체 검증 유지와 사전 전체 해시 제거를 승인한 뒤 구현한 로컬 후속
후보다. HEAD `5009cc4c026d2e9e94c4374a9d1f482405fae08f`에 기존 미커밋 I/O 후보와 이번
diff·새 시험을 합친 상태다. 이전 439개 회귀와 477.203초의 실보드 결과는 앞선 두 단계
후보의 결과로 보존한다. **이번 단일 검증 복사 후보는 보드에 적용하지 않았다.**

원자료는 PC `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-template-single-pass-20261030/`,
시험 소스는 `/home/ssafy/xenova-template-single-pass-20261030/candidate-final/`이다.
`BASELINE.json`·`baseline-*`·`preexisting.diff`에 앞선 미커밋 상태를 보존했고,
`CANDIDATE-FINAL_SOURCE.json`에 시험 입력 파일 해시를 봉인했다. 제품·시험 코드와 이후
정리한 문서의 최종 바이트는 `FINAL_CANDIDATE.json`과 `final.diff`로 구분한다.

### 변경 책임과 판정

`Manager._mutate`의 Linux 선택은 metadata·용량까지만 검사한다. 원 operation·명세·
manifest·UUID/MAC/IP·workspace를 먼저 저장하고 `_create`→`_prepare`→실제
`make_instance`의 검증 결합 복사로 진행한다. 게시 도구의 전체 검증과 Zephyr bundle
검증은 유지했다. 실제 source guard·복사 함수는 앞선 후보와 동일 바이트다.

`_create`는 수락 후 Linux 준비 오류를 기존 `RECOVERY_REQUIRED`로 분류하고 검증 이유·
I/O errno를 기존 error에 남긴다. 실패가 기록된 부분 workspace의 재전달은 `_mutate`에서
차단하여 원 error를 덮지 않는다. `_call`의 실패 기록 저장 오류도 숨기지 않고 응답에
내구 저장 미확정을 표시한다. schema·status·원 키·공개 action은 추가하지 않았다.

| 확인된 상태 | 현재 처리 |
|---|---|
| metadata·인자·명백한 용량 부족 | 비싼 복사 전 거절. 신규 operation/디스크 없음 |
| Linux 계획 성공 | metadata·용량·소유 검사의 결과. payload 무결성·생성 완료 아님 |
| 복사 hash 불일치·ENOSPC/EIO | 원 operation에 RECOVERY_REQUIRED/error 기록. 부분 파일 보존, 게시·guest 설정 진행 금지 |
| 실패 기록 저장도 ENOSPC/EIO | 응답에 저장 미확정. 기존 preparing 원장·부분 경로로 재시작 뒤 재복사 차단 |
| 기록된 실패 + 부분 workspace | 원 key 조회/계획은 읽기 전용. 실행 재전달도 최초 오류·부분 inode/내용 유지 |
| workspace 생성 전 실패 | 원 manifest를 다시 확인하고 같은 operation·예약으로만 재개 |
| 복사 도중 프로세스 중단 | 실제 남은 부분 경로로 차단. 새 UUID/디스크/원 작업 발급 없음 |
| 게시 후 결과 기록 전 중단 | 같은 operation·자산 소유권을 재확인해 후속 등록. 재복사 없음 |

### 검증과 대체 경계

실제 Manager/catalog/make_instance/파일 복사를 사용했다. 기존 fixture가 외부 Xen·
mount/chroot·게스트 설정·DHCP 경계를 대체한다. 파일 쓰기·동기화 오류는 명시적으로
주입하며 부분 실패 재확인은 별도 Python 프로세스에서 실제 원장을 읽는다.

| 명령·자료 | 결과와 assertion |
|---|---|
| `io_checks.py cost baseline` / `cost candidate-final` | 같은 작은 Linux 생성 fixture에서 root 논리 전체 처리 2회→1회. 최종 catalog root hash 0, 독립 digest/cp 0, 검증 복사 1. SD 물리 읽기 횟수·실보드 성능 측정 아님 |
| `io_checks.py focused candidate-final` | `focused-candidate-final.log`: **57 passed**, 7.53초. 용량·metadata·계획, 검증 복사·원본 변경·SIGKILL, 실제 생성·부분 실패·새 프로세스 재확인 |
| `io_checks.py regression candidate-final` | `regression-candidate-final.log`: **449 passed**, 101.13초. 관리·예산·기존 lifecycle/복구·first-install·Zephyr bundle·템플릿·SSH 관련 17개 suite. skip 없음 |
| `test_xenova_creation_failure.py` | 손상 root/Image, 원 실패 보존, 다른 명세 충돌, workspace 전 실패의 동일 예약 재개, 부분 쓰기 ENOSPC/EIO, 실패 원장 저장 ENOSPC/EIO, 실패 저장 직후 중단, 게시 후 재전달 |
| `package_probe.py` | 기존 recipe의 세 모듈 포함 확인·0644 설치·소스/설치/tar 바이트 동일. sparse/dense 사본 SHA·독립 inode 확인 |

계획이 내용을 읽지 않도록 바뀌었으므로 이전의 ‘손상 payload를 수락 전 거절’ assertion을
그대로 유지하지 않았다. metadata 손상은 계속 수락 전 거절하고, 실제 payload 손상과
수락 후 변경은 원 작업이 남는 실패로 옮겨 검증했다. 전체 검증 중 여유 공간 소진·
원본 변경을 가정하던 두 사전 검사 전용 시험은 제거하고 실제 복사 실패/중단·원 기록
재확인으로 대체했다. 실패 기록을 성공으로 바꾸거나 검증을 생략한 결과가 아니다.

### 부분 설치 바이트와 남은 수락

`SUBSET_PACKAGE.json`과 `xenova-template-single-pass-files-20261030.tar`를 보존했다.

- `prepare_instance.py`: `6dbb9134018913df72ad0df6d9bc523aa22055f6622b67a4aa48497d82c04a63`.
- `xenova_catalog.py`: `2987e3cd3829f0eaf8eb3b1a34710963c4128a55f4458b75d041d6ef95cbb10f`.
- `xenova_management.py`: `f49836aef185ab45ef509d717456ec86dfd5a70037f8c50080b6150d50a536c7`.
- 묶음 SHA-256: `33c8334e0d27fb2d6c616144f8b414bc11b4cade419392a2320252e8deb5475b`.

이는 recipe 소유 Python 파일의 부분 설치 스테이징이다. BitBake/RPM·전체 이미지·ARM
실행·보드 설치 수락은 아니다. 게스트 C를 바꾸지 않아 Zephyr ARM 재빌드는 하지 않았다.
원본 선택과 복사 사이의 전체 root 해시를 제거했으나 저장장치/CPU 간섭·전역 잠금·
create deadline·상위 HTTP 후속 책임을 마감한 것은 아니다. 대용량 보드 생성 시간과
전원 상실은 미검증이며, 위 477.203초에서 특정 시간을 뺀 예상치를 실측으로 쓰지 않는다.

현재 사용법은 [CLI 생성 계약](../platform/cli-manager-spec.md#creation-contract),
상위 연동 미완료 항목은 [HTTP·Spring·화면 요청서](../requests/20261029-request-http-spring-ui.md#linux-생성의-복사-검증-실패와-원-작업-회수)에 반영했다.
보드·실제 상태·다른 담당자 제품·진행 중 이미지 빌드·commit/push는 변경하지 않았다.

## Debian·Ubuntu 공통 적용 근거와 실패 수습의 후속 책임

`a15bc3f`/`5f3dba6` 이후의 보완이다. 제품 Python 세 모듈은 수정하지 않았다. 과거 최종
비용 fixture는 `test_xenova_management.ManagementTest`가 만든 Ubuntu 출처/1.0.0 자료를
사용했다. Debian의 실보드 결과는 이전 두 단계 후보이므로, 두 OS의 최종 경로를 직접
구분하는 네 시험을 `test_xenova_linux_template_paths.py`에 추가했다.

새 시험은 `platform/guests/default-templates.lock.json`의 공개 ID·source ID·1.0.2 버전과
`sources.lock.json`의 distribution·출처·kernel release를 읽는다. 실제 조립 함수
`assemble.copy_management_catalog`로 카탈로그를 게시한 뒤 실제 Manager/catalog/
`make_instance`/검증 복사를 수행한다. payload는 작은 ext4 표식 fixture이고 manifest
해시도 그 시험 바이트에서 계산한다. 운영 lock의 대용량 payload 해시·실제 OS 내용까지
검증한 것으로 확대하지 않는다. mount/chroot·guest 설정·Xen/DHCP는 기존 대역이다.

| 입력·시험 이름 | assertion |
|---|---|
| `debian-13-arm64` / `debian-13-20261015` 1.0.2, `test_debian_default_catalog_uses_one_verified_copy_without_preflight_hash` | 조립 시 게시 전체 검증은 유지. 이후 계획/실행의 catalog root hash 0·독립 digest/cp 0·검증 root 복사 1. 다섯 payload 각각 한 번·별도 inode·바이트 동일, 선택/등록 결과의 ID·버전·출처 일치 |
| `ubuntu-24.04-arm64` / `ubuntu-24.04-20260926` 1.0.2, `test_ubuntu_default_catalog_uses_one_verified_copy_without_preflight_hash` | 위와 같은 생성 경로·횟수·출처·독립 사본 검증 |
| `test_debian_failed_copy_preserves_origin_without_an_unpublished_asset_cleanup_api` / `test_ubuntu_failed_copy_preserves_origin_without_an_unpublished_asset_cleanup_api` | 각 OS의 root 손상은 공통 RECOVERY_REQUIRED. guest 설정·게시 없음, 원 key 조회·최초 오류·부분 inode/내용 보존, 재복사 0. 미게시 get/retire/purge는 VM_NOT_FOUND이며 종결 기능이 아님 |
| 기존 `ManagementTest.test_zephyr_real_independent_creation_has_no_ip_disk_and_fixed_profile`와 `test_xenova_zephyr.py` | 실제 별도 bundle 생성/검증 경로 유지. Linux 복사를 적용하지 않음 |

PC 원자료는 `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-linux-common-closure-20261030/`에
둔다. `CANDIDATE_SOURCE.json`의 로컬 입력으로 `io_checks.py focused candidate`를 실행해
**61 passed / 8.07초**, `compatibility candidate`에서 **54 passed / 7.74초**를 확인했다.
앞선 57개와 관리/Zephyr 시험이 포함·중복되므로 이를 449개에 더한 새로운 전체 suite
통과 수로 보고하지 않는다. 기존 449개 근거는 같은 제품 바이트의 선행 결과로 재사용한다.
제품이 바뀌지 않아 ARM·부분 패키지·전체 이미지를 다시 빌드하지 않았다.

미게시 실패의 조회는 `xenova-vm operation --idempotency-key "create:VM_UUID" --json`이다.
부분 파일을 보존한 채 차단하는 기능과 운영자가 그 실패를 종결·정리하는 기능은 다르다.
현재 후자는 제공되지 않으며, [CLI 생성 계약](../platform/cli-manager-spec.md#creation-contract)과
[기존 상위 요청](../requests/20261029-request-http-spring-ui.md#linux-생성의-복사-검증-실패와-원-작업-회수)에
후속 5번의 하위 플랫폼 책임·상위 표시 책임·수락 조건을 연결했다. 원 요청·신원·자산·
실행자 잔존과 사용 여부를 확인하기 전 해제하지 않고, 종결 뒤 재개/새 요청의 조건까지
확정해야 한다. 원 파일 삭제·새 UUID 우회를 정상 운영 절차로 안내하지 않는다.

3번의 수정 전 로컬 반례는 `concurrency_baseline.py`와 `CONCURRENCY_BASELINE.json`이다.
실제 Manager를 별도 프로세스에서 preparing에 멈춰 두면 다른 VM의 list/get과 원 create
operation 조회가 OPERATION_IN_PROGRESS였다. key stop은 원 의도·operation을 수락했지만
실제 이행은 pending이었다. 준비를 풀어 준 뒤 생성은 같은 원 작업으로 성공했다.
소유 pipe로 대기한 표본 1개이며 실제 복사 시간·FastAPI/Spring·보드 성능 결과가 아니다.
이 반례는 **3번 수정 전 잠금 분리가 미완료였다는 근거**다. 감사 실행 성공을 제품의
진행성 통과로 처리하지 않는다. 아래 수정 후 barrier 수락과 구분하고 API cgroup/backend
문제는 별도 미완료로 유지한다.

## 장기 Linux 준비와 공통 잠금 분리

### 1. 기준선 대비 동작과 중심 수락

기준 HEAD는 `b3aad9e`(`b3aad9e` 이전의 2번 제품 변경 `a15bc3f` 포함), 브랜치는
`feat/platform/recovery-scenario-closure`다. 기준 커밋으로 되돌리지 않고 기존 2번 근거를
보존했다. 이번 후보는 이 HEAD와 diff·추가 파일의 조합이다. 원자료는 PC
`C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-stage3-20261030/`, 소유 시험 사본은
`/home/ssafy/xenova-stage3-20261030/candidate-validated/`에 둔다.

수정 전 `CONCURRENCY_BASELINE.json`에서는 preparing을 막은 동안 다른 VM list/get·
원 create operation 조회가 OPERATION_IN_PROGRESS였고 key stop은 수락 후 pending이었다.
수정 후 `test_xenova_preparation_concurrency.py`의 S3-T01은 실제 root 사본 첫 쓰기 안에서
A만 barrier로 멈춘다. A를 해제하기 전에 아래 순서를 assertion으로 확인한다.

1. 원 operation·UUID/MAC/IP·workspace가 기록되고 root 사본이 존재한다.
2. 다른 등록 VM B의 list/get과 A의 원 key operation 조회가 성공한다.
3. B key stop이 실제 하위 control→stop_many→`xl shutdown` 외부 경계에 도달한다.
   현재 domain 부재와 원 control phase=completed까지 확인한다.
4. 이때 A release=false·준비 thread alive이고 원장/부분 사본 inode·내용은 동일하다.
5. 그 뒤에만 A를 해제하고 같은 operation으로 생성 완료·SUCCEEDED를 확인한다.

| 입력·표본 | A 원 operation | A 해제 전 B 완료 | A 해제 후 |
|---|---|---|---|
| Debian 기본 선택, 로컬 1건 | `38e87fb2-7ff6-4123-b37e-2a1cd2df57be` | list/get/원 조회 성공·RUNNING, control `cf409ffd-3438-42d2-8348-a8c77e7a73ec` completed, shutdown 호출 1회 | 같은 원 operation 성공 |
| Ubuntu 기본 선택, 로컬 1건 | `9f630951-a29e-4d70-8004-37363d933eed` | list/get/원 조회 성공·RUNNING, control `bd10816c-62c9-4e36-89da-1ff79ea4c23b` completed, shutdown 호출 1회 | 같은 원 operation 성공 |

`assertions-regression-candidate-validated/S3-T01-*-before-release.json`과
`*-after-release.json`에 원 envelope·바리어 상태·물리 경계 호출을 기록했다.
`CORE_ACCEPTANCE.json`은 네 원자료의 해시와 순서 검증을 연결한다. 실제 Manager·파일·원장·
flock·검증 복사이며 Xen/mount/chroot·게스트 설정·DHCP의 외부 경계는 기존 대역이다.
같은 호스트의 순서 검증으로, 실제 SD 처리량·종료 지연·HTTP/보드 성능 수락이 아니다.

### 2. 소유·잠금·게시와 출력

| 실제 변경 | 책임·보존 |
|---|---|
| `xenova_management.py:_dispatch_locked/_mutate/_create/_complete_private` | 신규 슬롯 확보는 공통 잠금 밖. 수락 전 원 key·현재 자원/주소·gate 재검사. private 준비 뒤 현재 원장·등록부·원 manifest·결과의 UUID/MAC/IP/target/workspace를 확인하고 공통→admission 잠금 안에서 source guard·게시. 다른 VM의 최신 변경을 재사용 전 사본으로 덮지 않음 |
| `prepare_instance.py:make_instance/PreparedInstance.publish` | 복사·guest 설정·자원 정리까지 private 결과로 반환. helper의 조기 public 배치 제거. 독립 seed는 기존 경로를 유지하고 초기화된 live 저장소의 직접 instance helper 우회는 명시적으로 거절 |
| `xenova_preparation.py:LinuxSlot/owner_state` | Linux 준비 1개, 신규 경합은 확인된 미수락. 전용 원 실행 잠금의 원 operation/spec/manifest/VM/workspace와 host·프로세스 시작값·실제 보유 FD/inode를 대조. PID나 임의 잠금 busy만으로 활성 판정하지 않음 |
| `request_output/run`, `xenova_vm.py`·`prepare_rootfs.py` 출력 연결 | 프로세스 전역 FD/stdout/cwd/env 변경 제거. 기존 `_thread._local`과 같은 요청별 sink·명령 스트림 각 64KiB 제한. 파이프는 비우고 누락량을 남기며 데이터 stdout 잘림은 오류. 같은 프로세스 다중 thread의 요청별 fork와 예산/trace를 시험 |
| `Workspace` 자원 기록, `ensure_quiet/drain`·VM 종료 진입점 | mount/loop 할당·정리 미확정 기록과 자식에게 전달한 슬롯/원 실행 FD 유지. 부모 사망·슬롯 부재를 자원 정리로 취급하지 않음. quiesce 뒤 공통 잠금 밖에서 제한 drain, 불확실 자원의 저장소 해제 거절 |
| `xenova-platform_1.0.bb`, `assemble.platform_root_ok` | 새 모듈 SRC_URI·0644 설치·필수 설치본 바이트 대조에 포함. 코드만 PYTHONPATH에 추가한 후보로 패키징 완료를 주장하지 않음 |

구체적 소유/락 표와 사용 흐름은 [관리 구조](../platform/management-boundaries.md#linux-준비의-소유권과-게시),
종료는 [부팅·종료 계약](../platform/management-boot-boundaries.md#종료와-재시작)에 반영했다.
admission unit의 기존 상한 10초를 늘리지 않고 내부 drain 9초로 반환 여유를 남겼다.
platform shutdown-all/poweroff/reboot-board의 준비 drain과 VM 종료는 기존 90초를 공유한다.
fsync/커널 I/O를 선점하는 엄밀한 반환 상한이나 systemd 강제 종료 뒤 자원 보존 보장은 아니다.

### 3. 공개 의미와 소비자 영향

wire schema·원 ID/key·spec·예약·원 기한은 유지한다. 확인한 같은 Linux 준비의 관측에
`effective_status=RUNNING`을 제공하며, raw operation.status를 조회 때문에 다시 쓰지 않는다.
workspace 전 실패를 같은 요청으로 명시 재개할 때만 실행 전이를 RUNNING으로 기록하고
원 error·신원·예약을 보존한다. 부분 workspace 실패는 계속 재복사 차단 대상이다.

SUCCEEDED는 원 성공으로 유지한다. 부모가 사망한 잔존 자식/미완료 효과는
RECOVERY_REQUIRED, 소유권 관측 오류는 OBSERVATION_UNAVAILABLE이다. 예산 소진으로
검색을 끝내지 못하면 DEADLINE_EXCEEDED이며, 원장 읽기·JSON 형식 실패는 ARTIFACT_INVALID,
무결성 불일치는 RECOVERY_REQUIRED, 중복 key는 RECONCILE_REQUIRED로 구분한다.
어느 오류도 정상 부재나 유일한 원 작업으로 확정하지 않는다. 신규 슬롯 미수락에만 accepted=false·operation_id=null을
사용한다. 일반 503/timeout으로 이 값을 추정하지 않는다. asset operation과 control-operation은
별개 원장이며 조회가 실행/청소를 하지 않는다.

정식 필드는 [CLI·Manager 원장 계약](../platform/cli-manager-spec.md#operation-fields),
담당자 수정 위치·조건부 직렬화 개정·실제 예시는 [기존 HTTP 요청서의 3번 절](../requests/20261029-request-http-spring-ui.md#linux-준비-중-조회제어의-동시-연결)에
누적했다. 기존 상세 인계의 ‘get/list는 긴 직렬화 유지’는 새 단일 하위 후보 확인 시의
요청별 fork 지원으로 조건부 개정했다. 현재 FastAPI ensure_instance는 활성 create 응답을
VMView로 다루지 않도록 연결이 필요하다. 상위 제품 소스는 수정하지 않았다.

### 4. 검증·실패 이력·부분 설치

`io_checks.py regression candidate-validated`의 실제 argv와 rc는 JSON, 전체 출력은
`regression-candidate-validated.log`에 보존했다. **19개 관련 suite, 481 passed / 113.60초,
skip 없음**이다. 이 한 명령의 수이며 기존 449·61·54·별도 3번 시험 개수를 더한 숫자가 아니다.
기존 격리 Python/pytest·고정 systemd 검사기를 재사용했으며 새 도구를 설치하지 않았다.

| 수락 ID | 실제 assertion·근거 |
|---|---|
| S3-T01 | 양 OS의 위 중심 시험. A 실제 사본 쓰기를 계속 막은 상태에서 B 실제 stop·원 완료 확인 |
| S3-T02 | 같은 key/spec의 별도 thread/새 Python 프로세스 활성 재전달, 두 최초 요청이 수락 전 검사를 통과하는 경합의 재검사. 원 operation/예약/복사 1개 |
| S3-T03 | 다른 신규 Linux 슬롯 경합 accepted=false·신규 원장/예약/workspace 0. 완료 replay는 슬롯을 기다리지 않음. 슬롯 반환 후 명시 제출은 정상 성공 |
| S3-T04 | 다른 spec·동일 미게시 VM의 start/stop/retire는 거절. 원장·부분 사본 불변, 중복 물리 효과 0 |
| S3-T05 | A 준비 중 B autostart 구성 갱신과 새 Zephyr 등록을 실제 수행. A 채택 뒤 두 변경 보존, 원 IP 예약의 재배정 거절 |
| S3-T06 | 원 성공 기록 후 소유 FD 해제를 멈춰도 SUCCEEDED 우선. 완료 replay의 현재 관측 실패는 원 결과와 null/진단을 보존 |
| S3-T07 | owner 파일 읽기 실패와 조회 예산 소진에서 부재/활성 추정·원장 변경 0 |
| S3-T08 | 기존 recorded/복사 SIGKILL/published 중단 회귀와 새 private-ready·before-publish 중단, 잘못된 private target 거절. workspace 전 실패의 같은 원 요청 재개가 실제 RUNNING을 거쳐 성공 |
| S3-T09 | 소유 부모 SIGKILL 뒤 실제 자식이 FD 보유. 잔존 소유로 재실행·저장소 unmount 차단. 자식 종료 후 슬롯이 없어도 자원 기록 미확정이면 해제 계속 거절 |
| S3-T10 | 기존 실제 복사 ENOSPC/EIO·fsync·실패 원장 저장 ENOSPC/EIO 및 새 프로세스 재확인 suite 재실행. 원 오류·부분 파일·저장 미확정 보존 |
| S3-T11 | 복사 중 quiesce와 공통 잠금 밖 정상 drain. 늦은 게시 0. gate를 닫았다 다시 열어도 이전 준비 문맥 채택 거절 |
| S3-T12 | 같은 프로세스 thread의 stdout/FD/예산/trace 분리. 실제 자식 출력 폭주를 제한 메모리로 회수, 데이터 잘림·명령 실패를 정상값으로 반환하지 않음 |
| S3-T13 | 두 OS prehash 0/검증 복사 1, 원 manifest·source guard·sparse·seed·Zephyr bundle 회귀. 정규 recipe/조립기의 새 필수 모듈 포함·독립 import |
| S3-T14 | CLI의 활성 create 원 결과 replay와 legacy stop이 A 해제 전 완료. 초기화된 live 저장소의 원 실행 소유권 없는 직접 helper 우회는 안전 거절. 기존 mutation/복구/CPU guard 관련 회귀 유지 |

실패 이력도 보존했다. `stage3-candidate-3.log`의 최초 동시 수락 시험은 비차단 공통
잠금에서 한 thread가 먼저 거절되어 의도한 barrier에 도달하지 못한 시험 순서 문제였다.
sleep/timeout 확대 대신 첫 사전 검사 종료를 event로 확인한 뒤 두 번째 요청을 시작했다.
`regression-candidate-3.log`는 8 failed/445 passed: 새 필수 모듈이 빠진 설치 대역,
조기 public 배치/None을 반환하던 creator 대역, 전역 stdout 변경에 기대던 noisy 대역이었다.
설치 대역에 필수 모듈을 추가하고 creator는 실제 private make_instance를 실행하며
noisy 도구는 명시 sink/실제 캡처 명령을 사용하도록 고쳤다. JSON 단일 응답 assertion은 유지했다.

`resume-baseline-2-candidate-4.log`에서는 실제 재개 실행인데 RECOVERY_REQUIRED로 보이던
상태 전이 결함을 재현했다. 실제 재개 시 RUNNING 전이를 내구 기록해 수정했다. 첫
`resume-baseline-candidate-4.log`는 시험 데이터가 provenance까지 바꾼 잘못된 주입이었으므로
제품 결함 재현으로 확대하지 않는다. ContextVar 후보는 기능 회귀를 통과했으나 고정
Python manifest상 추가 패키지가 필요해 채택하지 않았다. 기존 trace와 같은 thread-local로
바꾸고 최종 481개를 재실행했다. 이 기록을 성공으로 덮어쓰지 않았다.

`SUBSET_PACKAGE.json`·`xenova-stage3-runtime-files-20261030.tar`는 recipe 소유 Python
라이브러리 29개를 0644로 실제 스테이징하고 source/staging/archive를 대조한 결과다.
변경한 다섯 모듈은 Python `-I` 독립 import에서 모두 설치 경로로 로드됐다. 원본 recipe의
RDEPENDS·layers.lock은 유지했다. 고정 OE commit `0f03c732a0a2d0506b41b1abe60b3f368b2300dc`의
Python manifest와 기존 전이 의존성을 `STDLIB_DEPENDENCIES.json`/설치 검사에 연결했다.

| 변경 runtime | SHA-256 |
|---|---|
| xenova_management.py | `606a9693078c462f3db248d5f0cff474e15cc1de4ca9587df12e5cbf6fd190cc` |
| xenova_vm.py | `b2773efa4d350489b76926ab53e99d839dcd7a78f8ec8267a2f3164e450378d7` |
| xenova_preparation.py | `73468b6cc7b6b7c80c43d1bb6aa15a3c69b55363405d5066a09613eddb051854` |
| prepare_instance.py | `a3f72845a365af8043ca5a9209a57b044b797e0591830b0be8bf62b987cb91f6` |
| prepare_rootfs.py | `2a671c0d4e59391786cc0a412717d59e87f8b8310c28457874e4f66e59ebc5f6` |

묶음 SHA-256은 `08e7104f225b71d76be7bc35aaa4e359ea385ecfd5d9a41de700880f35da5874`다.
`CANDIDATE-VALIDATED_SOURCE.json`은 실행한 소스, `FINAL_CANDIDATE.json`/`final.diff`는
최종 문서·추가 파일까지 구분한다. 위 스테이징은 BitBake/RPM·전체 이미지·ARM 실행·실제
설치본이 아니다. 게스트 코드는 바꾸지 않아 Zephyr ARM 재빌드는 수행하지 않았다.

### 5. 남은 범위

4번의 전체 polling/공정성 최적화, 5번의 create 전체 deadline 및 미게시 실패 자산의
정식 종결·정리·예약 해제는 그대로 미완료다. 소유 잠금/자원 기록을 삭제하거나 원 ID를
재발급하는 우회·새 자동 작업 시스템·force는 만들지 않았다. FastAPI/Spring/화면의
실제 연결·HTTP 동시성 수락, 정규 후보 설치·보드 저장장치 부하/시간 개선은 미수행이다.
API cgroup의 guest backend 귀속 위험도 별도 미완료다. 단일 설치 후보의 모든 관련
모듈·import 경로를 맞추기 전 임의 부분 배포로 제품 종단 수락을 닫지 않는다.
이번 작업에서 보드·운영 상태·다른 담당자 제품·진행 중 이미지 빌드·commit/push는 변경하지 않았다.

### 6. 3번 마감의 교차 경계 확인

후속 검토의 두 항목은 `dc3861c`와 이번 시험·문서 diff를 기준으로 좁게 확인했다.
3번 잠금 구조나 제품 코드를 다시 변경하지 않았다. 기존 481개 결과와 부분 설치 원자료는
그대로 보존하고, 다음 assertion을 기존 시험 파일에 추가했다.

| 확인 | 시험·실제 결과 |
|---|---|
| 같은 VM의 준비 중 복구 진입 | `test_S3_T04_debian_recovery_entries_do_not_reclaim_live_unpublished_preparation`, Ubuntu 대칭 시험. 실제 root 복사 첫 쓰기를 멈춘 동안 `recover_once` 계획/실행, `recovery-reconcile` 진단/실행과 `recovery-operation`을 호출. 미게시 VM은 아직 등록 실행/복구 attempt가 없으므로 VM_NOT_FOUND. 원 create를 중단 작업으로 수습하거나 대신 실행하지 않음 |
| 원 작업과 자산 불변·정상 진행 | 위 호출 중 원 operation·owner 파일 및 workspace 파일의 inode/내용·집합 불변, 새 recovery attempt/control/public 자산·추가 Xen 호출 0. A는 release=false·thread alive·root 쓰기 1회, 원 key 조회는 RUNNING. 그 뒤 barrier를 해제하면 같은 operation으로 정상 생성 완료 |
| 검색 실패 원인 구분 | `test_S3_T07_asset_scan_read_format_integrity_and_duplicate_errors_are_distinct`. 원장 읽기/부분 directory 열람·JSON 형식 실패는 ARTIFACT_INVALID, 무결성 불일치는 RECOVERY_REQUIRED, 중복 key는 RECONCILE_REQUIRED. 모두 data=null 오류이며 정상 부재나 유일한 원 작업으로 반환하지 않음. 주입을 해제하면 같은 원 준비는 RUNNING |
| 예산·소유권 관측 실패 | 기존 `test_S3_T07_owner_read_failure_and_expired_lookup_do_not_change_the_origin`을 재실행. 예산 소진은 DEADLINE_EXCEEDED, 소유권 읽기 실패는 OBSERVATION_UNAVAILABLE. 원 작업 변경 0 |

이 복구 교차 시험은 **신규 미게시 VM에 원 복구 attempt가 없는 경우**를 다룬다. 이를
등록 VM의 전체 Recovery/Worker 종단이나 기존 uncertain attempt의 새로운 수습 기능으로
확대하지 않는다. `Recovery.conditions`의 등록 수명·의도·미완료 자산 소유 확인과 기존
복구 회귀는 유지한다. 추가 교차 시험에서 제품 결함은 확인되지 않았다.

같은 PC 원자료 경로의 `io_checks.py stage3 candidate-closure`를 실행해 **31 passed /
15.39초, skip 없음**을 확인했다. 기존 28개에 추가 3개를 포함한 한 suite 결과이며 선행
481개에 합산하지 않는다. `CANDIDATE-CLOSURE_SOURCE.json`에 실행한 사본의 해시,
`stage3-candidate-closure.json/.log`에 실제 명령·반환·출력, `STAGE3_CLOSURE.json`과
`closure.diff`에 최종 추가 후보와 근거 대응을 보존한다. 두 OS의 복구 전 raw envelope와
조회 오류는 `assertions-stage3-candidate-closure/S3-T04-*-recovery-before-release.json` 및
`S3-T07-asset-lookup-errors.json`에 있다.

정식 [자산 작업 조회 오류 설명](../platform/cli-manager-spec.md#cmd-operation)도 위 실제
분기로 좁혔다. 기존 담당자 요청서의 key 조회 오류 구분은 유지하며 새 오류/응답 필드를
추가하지 않았다. recipe 소유 29개 소스는 기존 부분 설치 manifest와 해시가 같아 패키지·
ARM·전체 이미지를 다시 빌드하지 않았다. 문서 링크와 diff 공백 검사도 확인했다.

3번은 **하위 구현·로컬 검증 마감**이다. 4번의 반복 polling 비용·공정성, 5번의 전체
deadline·실패 자산 수습, 상위 연결·실제 설치본/보드·API cgroup 수락은 별도 미완료로
유지한다. 이 후속 확인에서 보드 적용·다른 담당자 제품 수정·commit/push는 하지 않았다.

## 반복 조회의 비용·일관성과 제어 진행성

### 기준선·후보와 범위

4번은 3번 후속 보완까지 커밋된 `72868ebe7d15a493279faf6b49a5b242170c19b4`,
`feat/platform/recovery-scenario-closure`의 깨끗한 작업트리에서 시작했다. 과거 HEAD로
되돌리지 않았다. 실제 실행 후보는 이 HEAD와 제품/시험 diff·새 시험 파일의 조합이다.
PC 원자료는 `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-stage4-20261030/`,
소유 실행 사본은 `/home/ssafy/xenova-stage4-20261030/candidate-proof/`다.
`BASELINE_SOURCE.json`·`CANDIDATE-PROOF_SOURCE.json`·최종 `FINAL_CANDIDATE.json`과
`final.diff`로 입력·실행 소스·최종 문서를 구분한다.

직접 수정한 제품은 하위 `xenova_management.py`, `xenova_execution.py`, `xenova_vm.py`,
`xenova_runtime.py`, `xenova_preparation.py`다. FastAPI/Worker/Agent/Spring/프론트 제품,
두 기준 설계서, 보드·운영 서비스/VM/상태·네트워크/CPU·진행 중 이미지 빌드는 변경하지 않았다.
새 TTL cache·daemon·DB·공용 영구 작업 큐나 새 공개 action/필드는 추가하지 않았다.

### 4-A·4-B: 같은 조건의 비용과 유지한 검증

두 등록 Linux VM·두 원 자산 기록·두 시작 제어 기록, 같은 외부 FakeXen runner와
list/get/asset operation/control-operation 호출을 사용했다. 전체 UUID/name/state 목록의
전후 대조와 대상 incarnation 두 확인은 서로 다른 검증 경계여서 유지했다. 표시 조회에
불필요한 상세 디스크 설정 목록·짧은 목록을 제거하고, 검증된 등록부를 UUID로 나눠
builder가 VM마다 전체 dict를 재검색하지 않게 했다. get은 다른 VM의 identity를 호출하지 않는다.
asset 원장 조회는 전 VM 등록부를 읽지 않으며 control 원장은 별도 원본/의미를 유지한다.

`paired_cost.py candidate-proof`는 자체 회귀/스테이징 종료 후 기준선/후보를 번갈아 3쌍
순차 실행했다. `PAIRED_COST-FINAL.json`, `metrics-finalpair*-*-facts.json`에 입력·응답·
명령 계수·텍스트 읽기 바이트/JSON 파싱·trace·짧은 잠금 대기/보유·FD를 기록했다.
시계는 이 비교에서 두 후보 모두 같은 호스트의 실제 단조 시계다. 다른 이미지 빌드·OS cache는
제어하지 않았고 하드웨어 Xen/SD 성능 시험이 아니다.

| action | 외부 명령: 기준선 → 후보 | 텍스트 파일 읽기 / JSON parse | 읽은 바이트: 기준선 → 후보 | 응답 중앙값 ms, n=3 |
|---|---|---|---|---|
| list, 대상 2개 | verbose 목록 2·상세 1·짧은 목록 1·identity 4 → verbose 2·identity 4 | 12/17 → 28/32 | 10,202 → 31,762 | 31.122 → 33.805 |
| get, 대상 1개 | verbose 2·상세 1·짧은 목록 1·identity 2 → verbose 2·identity 2 | 10/13 → 14/16 | 10,625 → 15,841 | 19.636 → 17.278 |
| asset operation | 양쪽 Xen/helper 0 | 4/4 → 4/4 | 10,678 → 17,560 | 2.162 → 1.579 |
| control-operation | 양쪽 Xen/helper 0 | 2/2 → 4/4 | 8,509 → 17,018 | 1.590 → 1.730 |

파일 바이트는 이 임시 fixture의 텍스트 입력이며 이미지 payload·pipe·/proc 읽기를 합친
물리 I/O량이 아니다. 잠금 해제 뒤 현재 구성/실행과 검색 완전성을 다시 확인하므로 메타데이터
읽기·파싱은 증가했다. 작은 fixture의 list/control 원장 응답시간도 줄었다고 주장하지 않는다.
전체 응답시간 개선률·p99·최악 지연 보장은 제시하지 않는다. 확정한 효과는 불필요한 외부
목록/반복 builder 검색 제거와, 아래 보호를 유지하면서 공통 대기를 끊은 것이다.

| 보호 구간 | 기준선 | 후보·writer와의 관계 |
|---|---|---|
| `platform.lock` | list/get의 외부 관측·응답 및 asset 원장 검색 동안 유지 | 네 일반 조회는 획득하지 않음. cost 세 표본 모두 hold=0. 실제 변경·예약/게시와 제출 guard는 기존 실행 잠금 유지 |
| admission | control 원장의 전체 검색 동안 유지 | 원장 검색 밖으로 분리. 원자 기록을 완전 검색·대조하며 첫 일치에서 유일성을 확정하지 않음. 실제 stop 수락/제출의 짧은 writer 잠금은 유지 |
| observation-VM | 기존 파생 snapshot 저장 | 해당 VM의 현재 구성/수명·실행 근거를 대조하고 필요한 snapshot만 게시. 전체 등록부/Xen/helper 대기는 이 잠금 밖 |
| 요청 객체 | 가변 예산/진단을 공유하면 안전한 동시 사용 계약 아님 | 객체별 무대기 guard. 실제 병행 호출은 fork. 기존 lock-free snapshot은 내부 fork로 격리해 보존 |
| `observation-slot-0..3` | 없음 | list/get의 외부 관측 네 개, 공통/admission 밖 무대기. helper FD 상속·실제 종료 확인. 원장 조회/실제 제어·Linux 준비와 다른 보호이며 전체 API 동시수/QPS 보장은 아님 |

원자 JSON과 private/public 게시 규칙, 단조 수명/실행 신원을 사용해 현재 대상과 다시
결합한다. 모든 파일/Xen을 같은 시각의 단일 트랜잭션으로 관측했다는 보장은 아니다.
새 reader 잠금만 붙이고 writer를 보호했다고 주장하지 않으며 외부 root/xl 변경을
원자적으로 차단하지 않는다. 자세한 읽기/쓰기 대응은 [관리 구조](../platform/management-boundaries.md#일반-조회와-변경-작업의-진행)를 따른다.

### 4-C·4-D: 두 중심 수락과 오류·게시 순서

수정 전 `slow-baseline-facts.json`은 지정된 identity 조회가 계속 막힌 동안 다른 VM의
asset operation이 OPERATION_IN_PROGRESS, stop이 내구 수락 후 pending이며 shutdown
경계 호출은 0이었다. `poll-before-baseline.log`의 양 OS·1/2 폴러 네 건은 polling을
유지한 상태의 stop 완료 assertion에서 실패했다. 4 폴러 두 건은 그 전 단계에서 정상
operation 조회가 한 번도 완료되지 않아 실패했고 stop까지 도달하지 않았다. 같은
barrier/호출 mix에서 확인한 진행성 반례이며
과거 보드 helper 지연이나 SD 시간을 현재 소스의 원인으로 대입하지 않았다.

후보의 `S4-T02-get/list.json`은 느린 조회 release=false·thread alive인 동안 다른 VM의
asset 원 조회 성공, key stop의 실제 하위 shutdown 명령 경계와 원 control phase=completed를
확인한다. list에 포함된 B가 그 사이 종료되면 오래된 목록은 명시 관측 오류로 끝날 수 있다.
관측 해제 뒤 정상 list도 성공한다. get의 무관한 대상 조회는 그대로 성공한다.

S4-T03은 A의 실제 검증 root 사본 첫 쓰기를 막아 둔 상태에서 폴러를 시작한다. 각 폴러가
list/get/A operation을 실제 호출하고, B 정지 후에도 polling 완료 수가 더 증가한 것을
event/condition으로 확인한 뒤에만 폴러와 A를 해제한다. 다음은 원 완료 확인 시점의
완료된 정상 조회 수다. 전체 실행의 총 호출 수나 운영 QPS가 아니다.

| A 입력 | 폴러 | 정상 list/get/operation | 오류 | B 정지·원 완료 / A 상태 |
|---|---|---|---|---|
| Debian | 1 | 8/8/8 | 0 | completed, polling alive·A blocked |
| Debian | 2 | 11/11/11 | 0 | 동일 |
| Debian | 4 | 21/18/18 | 0 | 동일 |
| Ubuntu | 1 | 10/10/10 | 0 | 동일 |
| Ubuntu | 2 | 13/11/13 | TARGET_CHANGED 2 | 동일; B 전환 중 명시 오류와 정상 읽기 동시 존재 |
| Ubuntu | 4 | 17/16/15 | 0 | 동일 |

각 조합에서 shutdown 경계 호출은 하나이며 A root 쓰기는 1회다. 그 뒤 A도 같은 원
operation으로 성공함을 실제 create fixture의 종료 assertion으로 확인했다. 원자료는
`assertions-regression-candidate-proof/S4-T02-*.json`, `S4-T03-*.json`과 같은 소스의
시험 assertion이다. 10ms는 로컬 폴러의 회차 간 시험 간격이며 제품 설정이 아니다.
Xen·mount/chroot·guest 설정·DHCP는 외부 대역이고 Manager/원장/복사/잠금/runner/
thread/프로세스/FD는 실제다. 이 결과는 HTTP·화면·실제 Xen 종료 수락이 아니다.

추가로 확인한 경계와 고친 실패 이력은 다음과 같다. 모든 실패 로그를 보존했다.

| 근거 | 수정·수락 |
|---|---|
| `fault-before-candidate-3.log` | selector I/O 실패 뒤 소유 read 자식이 남는 반례. timeout/예외 때 해당 직접 자식을 제한 회수하고 원 오류는 유지. guest backend나 다른 PID를 정리하지 않음. FD/자식 누수와 정상 조회 회복 시험 |
| `deadline-before-candidate-closed.log` | 실제 helper가 원 읽기 예산을 소진했는데 일반 관측 오류로 반환. 읽기 문맥의 만료는 DEADLINE_EXCEEDED로 보존. 빈 원장/VM 검색의 만료도 정상 부재로 바꾸지 않음 |
| `timestamp-before-candidate-final-check.log` | 늦은 성공이 게시 시각을 새 표본 시각으로 쓰고, 늦은 실패가 새 성공 cache를 unknown으로 덮는 두 반례. 자기 원 관측 시각 유지·더 새 성공/오류의 파생 기록 보호 |
| `per-vm-before-candidate-audit-final.log`, `absence-clock-before-candidate-final-verified.log` | 뒤 VM의 대기가 앞 VM/부재의 시각까지 밀어 올리는 반례. 존재는 대상의 마지막 incarnation 검증 시각, 부재는 두 번째 완전 목록 시각을 보존 |
| `focused-candidate-1.log` | 변경 중인 목록의 명시 오류를 허용하지 않은 기대값과 FakeXen의 중간 ID 제거 뒤 중복 할당을 구분. 실제 source stop/restart를 유지하고 외부 대역의 빈 domid 재사용만 수정. 정상 읽기 성공은 별도로 강제 |
| `regression-candidate-3.log`, `regression-candidate-validated.log` | 목록 표시 순서·공유 요청 객체 보호·기존 동시 snapshot 호환성 회귀를 수정. 옛 상세 목록/긴 잠금에 묶인 비용 assertion은 현재의 두 관측/집합 재확인에 대응시키되 원 예산/제어 guard assertion은 유지 |

### 4-E: 최종 검증·계약·부분 설치

`run_checks.py regression candidate-proof`는 **22개 관련 suite, 541 passed / 224.37초,
skip 없음**이다. 실제 argv/rc/출력은 `regression-candidate-proof.json/.log`다. 같은 후보의
집중 시험 29개는 그 안에 포함되므로 기존 481/31개나 집중 결과와 합산하지 않는다.

| 수락 ID | 연결한 실제 시험·경계 |
|---|---|
| S4-T01 | 2-VM 비용/명령/JSON 계수·builder의 단건 입력. 완전 목록 2·identity 2/대상은 유지. 대상별·부재의 고정 시각과 원 게시 시각 보존 |
| S4-T02/03 | 위 두 중심 수락·양 OS·폴러 1/2/4, 정상 읽기와 실제 stop 경계/원 완료·A 원 작업 성공 |
| S4-T04/05 | domid 재사용을 포함한 새 실행, 대상 구성 변경의 최대 2회 관측, 계속 변경 시 TARGET_CHANGED·회복. 다른 VM autostart 변화는 get을 다시 시작하지 않음. 새 등록을 목록에서 조용히 누락하지 않음 |
| S4-T06/07 | registry/legacy 열람 실패·뒤늦은 중복 key와 기존 S3-T06/T07/T09 재실행. 실패를 empty/absence/unique로 바꾸지 않고 성공/활성/잔존 자식/미확인을 구별 |
| S4-T08/09 | 빈 검색·registry/원장/외부 helper의 잔여 예산, 실제 자식 EOF·selector 오류 후 회수. 화면 관측 뒤 실제 stop의 상세 자산/신원 제출 guard가 새로 수행됨 |
| S4-T10/11 | 설정 IP·unknown 앱 준비 보존, fork에 memo 상속 0·예산/trace/출력 분리. 같은 객체의 동시 호출 거절·동시 snapshot 호환, 늦은 성공/실패/시각의 최신 파생 기록 침범 0 |
| S4-T12/13 | 네 read 슬롯의 초과 거절 및 다른 프로세스 참여, 슬롯 점유 중 원 조회/stop 진행·해소 후 읽기 성공. 잘린/잘못된 identity의 정상값 fallback 0·기존 native/Python 64-bit/오류/timeout ABI 회귀 |
| S4-T14/15 | 원 완료 replay/현재 관측 실패 분리와 모든 기존 2·3번·복구/종료/trace 회귀. 두 OS prehash 0·검증 root 복사 1, 원 manifest·부분 파일/오류·private 게시·소유권·quiesce 보호 유지 |
| S4-T16 | 아래 정규 recipe 소유 source/staging/archive와 실제 설치 경로 import. 상위 호출/권한/오류·버전 인수 요청과 연결 |

부분 설치는 `SUBSET_PACKAGE-CANDIDATE-PROOF.json`과
`xenova-stage4-runtime-files-20261030-candidate-proof.tar`다. recipe의 기존 Python 29개를
0644로 스테이징하고 source/staging/archive를 바이트 대조했다. 변경 5개 모듈과 준비
모듈의 총 6개가 Python `-I`에서 설치 경로로 import됐다. recipe/RDEPENDS·고정 표준
모듈 의존성은 유지하며 새 잠금은 기존 core builtin `_thread.allocate_lock`이다.
native wrapper·Python/native 원본은 바뀌지 않았다. wrapper는 실행 가능한 기존 native를
exec하고 그 실패를 약한 Python 성공으로 fallback하지 않는다. native 부재 때의 기존
엄격한 Python 경로도 유지한다. 무관한 Zephyr/ARM 재빌드는 하지 않았다.

| runtime source | SHA-256 |
|---|---|
| xenova_management.py | `cfdc49c69ee6885dffed43c916b7a8b645e263ecb48a999ba8bea707ab4e6067` |
| xenova_execution.py | `6b978b786c621f965b501d65482bd53c8b47d05101b1b605b4805d17bccbe245` |
| xenova_vm.py | `40708ac8fedceb86642a848fc84c32a8a85f3f612f8910c2d0207e9422b7b810` |
| xenova_runtime.py | `43dee327af2ad13ad7d10b2a59449c67e7a2af33290f99f9523cc3995807d99d` |
| xenova_preparation.py | `1f039585daca5b4c22a9133b241b0383dc9b8c9dad8b504e7168d80020be8d3f` |

묶음 SHA-256은 `9113c1553798405ddccd4a9b58002db70d997b45ec9d128009f1935d50a8c6f4`다.
이는 Python 라이브러리 부분 설치/묶음과 host ABI 검증이며 BitBake/RPM·전체 이미지·실제
ARM/Xen/SD 설치본 수락이 아니다. 정규 이미지/설치 때 같은 후보의 전체 관련 모듈·helper
실제 경로·서비스 연결을 대조해야 한다. 일부 모듈 혼재 상태의 동시 호출은 지원으로 간주하지 않는다.

정식 의미는 [일반 조회 계약](../platform/cli-manager-spec.md#read-concurrency), 담당자
수정 위치·fork/예산·threadpool/선행 get·실제 polling/응답 역전·권한/허용 details는
[기존 HTTP·Spring·화면 요청서](../requests/20261029-request-http-spring-ui.md#반복-조회와-제어-진행성-연결)에 누적했다.
Worker의 관측 오류/옛 intent를 부재·새 복구 허가로 바꾸지 않는 연결은
[기존 Worker 요청](../requests/20261029-request-recovery-worker.md#일반-조회-변경의-소비-연결)에 추가했다.
D01/D03·D02/D08의 담당자 미완료를 대신 닫지 않는다.

4번의 **하위 구현·로컬 수락·부분 패키징**을 마감한다. 모든 실행 스케줄의 무기아나
저장장치/커널 I/O 선점 상한을 보장하지 않는다. 남는 정당한 대기는 실제 변경의 실행
소유권, 해당 VM의 짧은 관측 게시/필수 저장과 Xen/Store 자체의 응답이다. 5번의 create
전체 deadline·미게시 실패 자산 종결/예약 해제, 상위/Worker 연결·실제 HTTP/화면/보드,
API cgroup/backend 귀속 수락은 별도다. 다음 단계·보드 적용·commit/push로 자동 진행하지 않았다.

## 4번 후속: 재검증 상한·슬롯 중단과 반환

### 1번 기준선·비용 복원의 기능별 대응

1번은 2~4번에서 주요 기준선·비용·설치본 차이를 확인한 상태다. 아래는 그 자료와 현재
진입점을 묶은 표이며 시작/삭제의 실제 보드 비용이나 제품 종단까지 완료했다는 뜻은 아니다.
이전 설치 모듈 8개 중 1개 일치는 위 초기 `RUNTIME_MATCH.json` 당시 결과이고, 이번에
보드 설치 상태를 다시 조회하지 않았다.

| 기능·현재 진입점 | 잠금·원장·외부 효과/저장 위치 | 기존 근거와 이번 확인 | 미확인·후속 |
|---|---|---|---|
| 생성 `Manager._mutate/_create/_complete_private` → `_prepare/make_instance` | 공통 수락/예약·게시와 잠금 밖 단일 Linux 준비. `operations`, `.operation-ID` workspace·public instance·tombstone·control 초기화·DHCP | 2번 양 OS prehash 0/검증 복사 1, 3번 원 실행·private/public·중단, 4번 polling 진행성 | 현재 단일 복사/동시성 후보의 대용량 보드 성능·원 create 기한·미게시 실패 종결은 5·7번 |
| 조회 `_query/ReadContext/_read_states` → `Execution.observe_many` | 네 일반 조회는 공통 실행 잠금 없음. 외부 관측 4슬롯, VM별 snapshot 게시. 원장 별도 완전 검색 | 위 같은 조건의 외부 명령/메타데이터/잠금 전후 표·S4-T02/03, 아래 비용 목적/상한 | 설치 VM/원장 수·helper·SD의 실제 비용은 7번 |
| 시작 `_dispatch_locked` → `request_control/Execution.control` → `Platform.start` | 기존 공통 실행·admission·원 `control-requests/control/runs`·launch 기록. Xen create/scope·CPU guard·DHCP | management_budget의 원 key/기한과 replay, preparation_concurrency의 같은 VM 충돌, recovery/reconciliation의 launch/scope/receipt 회귀·현재 source 경로 | 보드의 시작 단계별 비용과 API 재시작 backend 보존·Agent/화면은 7번 |
| 정지 `_accept_stop/_dispatch_locked` → `Execution.control/Platform.stop_many` | 짧은 의도 수락 뒤 실행 잠금·현재 자산/신원 재검증·shutdown·부재 확인·원 control 결과 | 3·4번 A/느린 조회 미해제 중 B 실제 하위 종료 명령/원 completed, 예산·의도·미확정 회귀 | 로컬 Xen 대역. 실제 Xen 종료 및 상위 원 key/후속 이행 수락은 6·7번 |
| 삭제/복원 `_transition/_purge` → `_stopped/_usage/_ownership` | 공통 실행 잠금. retire 숨김 보관, restore 현재 IP/용량·Linux e2fsck, retired만 purge. 원 operation/tombstone·inode inventory·unlink/fsync·DHCP | management의 이동 중단·partial purge 재개·EIO·링크/Xen/loop 보호 시험과 현재 함수 대조. 이번에 삭제/이동을 실행하지 않음 | 미게시 workspace 수습은 기존 retire/purge가 아님: 5번. lifecycle 비용·화면 용어/공간은 6·7번 |

### 추가 읽기의 목적과 상한

관측 정합성에 필요한 다른 경계의 확인을 동일 단계 중복으로 합치지 않는다. 위 비용 표의
증가한 JSON 읽기는 아래 목적에 연결된다. 함수/회차의 상한이지 전체 VM/원장 수나 커널 I/O
시간에 무관한 고정 비용·최악 지연 보장이 아니다. 파일별 추가 호출은 trace로 먼저 대조한다.

| 구분·함수 | 재사용/재검증의 목적 | 현재 상한·범위 |
|---|---|---|
| 같은 회차의 외부 사실: `_observe_many/_view` | 완전 목록을 모든 대상에 공유, UUID별 builder 입력·사전 읽은 intent/run을 응답 조립에 재사용 | 비어 있지 않은 한 회차에 `xl list -v` 2회·존재 대상별 identity 2회. builder의 재호출/상세·짧은 목록 0 |
| 잠금 밖 조회 후 등록/수명 대조: `ReadContext.validate/validate_one` | 최초 구성/수명과 현재 대상 집합·record/tomb·실행/의도를 결합. 오래된 구성+새 실행 혼합 방지 | get 등록부 1회, list 최초+집합 재확인 2회/회차. 대상별 채택 직전 metadata 대조 1회, 외부 helper/전체 등록부 재검색 없음 |
| 원 key 검색 완전성: `_query.search/_operation_view` | 첫 일치가 유일함을 추정하지 않고 전체 검색 결과·원 실행 소유 변화 확인 | asset/control 각각 완전 검색 2회/회차. asset 원 결과/소유 대조 최대 2회/회차. Xen/VM 등록부 enrichment 0 |
| 문맥 변경의 재시도: `_query` | 계속 바뀌는 입력을 무한 재관측하지 않음 | ReadChanged에만 새 회차 최대 1회, 합계 최대 2회. 최대 외부 목록 4회·identity는 회차별 2×존재 대상 수의 합, 원장 완전 검색 4회. 같은 잔여 예산, 기한 연장 없음 |
| 효과 제출의 별도 guard | 표시 근거를 start/stop/삭제/복구 허가로 사용하지 않음 | 실제 제어의 상세 자산/신원 검사는 별도 기존 경계. 제거할 같은 회차 중복으로 취급하지 않음 |

호출 간 cache/새 DB/helper는 만들지 않았다. 4번 최초 비용 비교의 세 표본은
`candidate-proof` 당시 값으로 보존하며 아래 파일 모드 검사 보완 후보의 새 지연 실측으로
바꿔 표기하지 않는다. 관측/검색 구조와 계수 assertion은 아래 최종 회귀에서도 유지했다.
실제 설치 자료로 단건/전체 비용을 판정하는 것은 7번이다.

### 슬롯 소유자 중단·파일 보호의 직접 시험

`test_S4_T12_owner_exit_keeps_helper_slot_until_actual_exit_then_recovers`는 세 슬롯을
계속 보유하고 나머지 하나에 실제 Manager get과 Python helper를 진입시킨다. Xen 출력만
기존 대역이며 실제 runner·fork·pipe·flock·상속 FD·부모 SIGKILL/waitpid·자식 pidfd를 사용한다.
부모 중단 뒤 helper 생존과 슬롯 FD 하나를 확인한 채 신규 list는 미수락 경합,
원 operation 조회는 성공한다. helper를 해제해 실제 종료를 확인한 뒤 **동일 inode의 슬롯**을
획득하고, 다른 세 슬롯을 해제하기 전 정상 get도 성공한다. FD 누수는 없다.
Linux 준비의 부모/자식 시험을 이 새 관측 슬롯의 근거로 대체하지 않았다.

`test_S4_T12_slot_files_reject_unsafe_paths_modes_and_owners_without_repair`는 symlink·
hardlink·directory·FIFO·0666 모드·다른 uid·연결된 runtime/locks 경로를 각각 거절한다.
uid만 fstat 경계에서 대역으로 바꾸고 파일/링크/모드와 FD는 실제다. inode/mode/외부 원문
보존과 정상 0600 파일 획득, FD 회수를 확인했다. 실제 타 uid 침입·적대적 root의 경합까지
시험한 보안 감사라고 확대하지 않는다.

확인한 실제 차이는 관측 슬롯이 0666 기존 파일을 수락하던 한 곳이다.
`xenova_runtime.observation_slot`에 기존 Linux 준비 `_plain`과 같은 0600 검사를 추가했다.
잘못된 파일을 삭제/chmod하거나 다른 슬롯으로 건너뛰지 않는다. 초기 실패
`focused-followup-before.log` 2건은 vm.run에 없는 pass_fds 인자를 준 시험과 runtime 부모
디렉터리 누락이라는 fixture 오류였다. 이를 고친 `focused-followup-repro.log`는
**1 failed/30 passed**, 실제 permissive-mode 수락만 재현했다. 두 실패 이력 모두 보존한다.

### 후속 후보·회귀·부분 설치와 인계

후속 후보는 같은 HEAD에 기존 diff와 runtime 검사/새 시험/문서를 더한 조합이다.
원자료 폴더는 기존 `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-stage4-20261030/`,
실행 소스는 `/home/ssafy/xenova-stage4-20261030/followup-final/`, 소스 봉인은
`FOLLOWUP-FINAL_SOURCE.json`이다. 기존 `FINAL_CANDIDATE.json/final.diff`는 앞선 후보의
자료로 보존하고 후속은 `FOLLOWUP_CANDIDATE.json/followup.diff`로 구분한다.

`run_checks.py regression followup-final`: **22 suite, 543 passed / 135.09초, skip 없음**.
기존 541개와 추가 집중 결과를 합산하지 않는다. 같은 후보의 두 중심 barrier·양 OS
polling·2/3번 비용/소유권/게시/종료·신원/예산/native/seed/Zephyr 회귀가 포함됐다.
신규 원자료는 `assertions-regression-followup-final/S4-T12-owner-exit.json`과
`S4-T12-slot-files.json`, 실제 argv/rc는 `regression-followup-final.json/.log`다.

`package_check.py followup-final`: recipe 소유 Python 29개 source/staging/archive의 바이트·
0644 모드와 6개 Python `-I` import를 재확인했다. recipe/의존성/native/게스트는 무변경이다.
`SUBSET_PACKAGE-FOLLOWUP-FINAL.json`과 `xenova-stage4-runtime-files-20261030-followup-final.tar`,
묶음 SHA-256 `dbe68291756460538993b4580d519e8bd947546cb2f4ec818360e07a78421b0c`로 연결한다.
이것은 부분 설치 묶음이며 정규 RPM/이미지/ARM/Xen/SD 설치 수락이 아니다. 무관한 ARM 재빌드는 안 했다.

정식 조회 계약/관리 구조에 슬롯의 파일 조건과 부모 중단 의미를 반영했다. 기존 HTTP 요청서에
다른 VM stop·원 key/ID 조회의 권한 거절, 내부 경로/비밀 오류 노출 차단, 실제 threadpool 포화 중
정상 읽기+제어 진행의 음성 수락을 명시했다. Worker 요청에는 이 입력을 사망/새 복구 허가로
바꾸지 않도록 누적했다. 두 요청서의 제품 수락은 계속 대기이며 타 담당자 코드는 변경하지 않았다.

5번 제안은 같은 작업 폴더의 `STAGE5_IMPLEMENTATION_PROPOSAL.md`에 둔다. 원 create 기한·
기존 원 결과 회수·명시적 미게시 수습·예약 반환·schema/권한/삭제의 미확정 결정을 구분한다.
큰 설계서에 일시 작업 계획을 추가하지 않았다. 5번 구현·실제 자산 정리·보드·commit/push는 하지 않았다.

## 원 create 기한·실패 종결과 예약 반환

### 기준선과 변경 책임

2026-10-31, 기준 HEAD `69387c119f3ca66b59a2b5b450503721a74bad7b`, 브랜치
`feat/platform/recovery-scenario-closure`의 작업트리 후보다. 위 4번의 543개는 선행 근거이며
이번 결과와 합산하지 않는다. 착수 시 tracked/untracked 변경은 없었다. 과거 후보로 되돌리지 않았다.
원 입력 계획과 원자료는 저장소 밖 `C:/Users/SSAFY/Documents/Codex/2026-10-31/xenova-stage5/`
(이하 P5)에 보존한다. `BASELINE_SOURCE.json`, `CANDIDATE-FINAL_SOURCE.json`,
`FINAL_CANDIDATE.json`의 HEAD·diff·새 파일·해시로 후보를 식별한다.
시험 snapshot 뒤의 문서 수정은 최종 봉인에 따로 포함하며 시험/패키지의 제품 바이트와 대조한다.

| 변경 위치 | 책임과 유지한 경계 |
|---|---|
| `xenova_creation.py`(새 내부 모듈) | 원 생성의 유한 정책/host/기한, 버전형 수습 검증, private inventory·현재 사용 검사, 명시 삭제 계획/재개, 공통 예약 반환 판정. 새 원장·DB·자동 실행기를 만들지 않음 |
| `xenova_management.py` | 수락 저장 전후 기한, private 준비/게시와 원 결과 정산, 원 조회의 RUNNING/RECONCILING/FAILED, 같은 create key의 실패 replay. 원 UUID/spec/할당/의도·단일 Linux 슬롯을 유지 |
| `xenova_preparation.py`, `guests/common/prepare_instance.py` | 요청별 실행/정리 예산, 실제 sparse 읽기·부분 write·guest 명령/반환·게시 전 검사, 자기 자식/FD·Workspace 역순 종료. 전역 stdout/cwd/env 변경·추가 prehash 없음 |
| `xenova_execution.py`, `xenova_cpu.py`, `xenova_resources.py`, `xenova_recovery.py`, `xenova_zephyr_control.py` | 기존 미완료 자산 guard가 같은 `creation.holds`를 소비. 단순 status != SUCCEEDED로 수습 완료를 영구 차단하던 판단을 교체. 복구 정책/CPU 배치/사용자 의도/실행 신원은 변경하지 않음 |
| `xenova_vm.py`, `xenova_management_cli.py`, recipe | 하위 `operation-reconcile` 진입, create 한정 호출 상한과 설정 경로. 새 모듈 포함과 비활성 예시 설치 |
| 관련 platform 시험·bridge 및 dom0-manager `tests/test_platform_integration.py` | 명시 유한 시험 정책과 실제 private 준비 결과를 fixture에 제공. dom0-manager/FastAPI/Worker/Agent/Spring/프론트 **제품 소스는 변경하지 않음** |

두 기준 설계 문서의 설계나 MQTT 공개 계약은 변경하지 않았다. 새 runtime 의존성·SQLite·
daemon·청소 스케줄러·영구 큐는 없다. 실제 정리는 이번 시험이 만든 격리 fixture만 대상으로 했다.

### 5-A: 시간·호환·공개 의미

정식 사용법은 [operation-reconcile](../platform/cli-manager-spec.md#cmd-operation-reconcile)와
[생성 기한](../platform/cli-manager-spec.md#creation-deadline),
[관리 경계](../platform/management-boundaries.md#원-생성-실패와-예약-반환)에 둔다.
새 호출을 HTTP에 자동 노출하지 않았다. root 하위 CLI/Python과 사용자 HTTP 권한은 별개다.

| 계약 | 이번 확정 |
|---|---|
| 정책 | `/etc/xenova/creation-policy.json`: schema 1.0, 명시 양수 call_ms/execution_ms/cleanup_ms. call/실행 입력 상한 86460000ms, cleanup 상한 60000ms는 직렬화 한계이며 운영 기본값이 아님. 누락/무효 정책은 새 Linux create 수락 전 거절 |
| 세 시간 | 새 수락에서 `D_create=min(t_accept+execution_ms,D_call-cleanup_ms)`. 호출 대기는 D_call을 소비. 같은 원 요청·새 caller·재시작·조회가 D_create를 연장하지 않음. 종료 여유는 원 실행의 여러 단계에서 한 번 시작한 동일 예산을 공유 |
| 저장 | 새 Linux create는 asset journal schema 1.1의 `creation.version=1`에 host boot·수락 시각·원 기한·정책을 저장. spec_hash에 timeout을 넣지 않음. 수습도 원 JSON의 reconciliation.version=1에 결합 |
| 호환 | 완료된 1.0 결과 replay 유지. 원 기한 불명/다른 boot의 미완료 생성은 자동 재개 금지, 현재 host의 명시 private 수습은 가능. 구형 writer와 혼합 배포는 지원하지 않음 |
| 진단/명시 적용 | 원 operation ID 또는 key·VM UUID·원 spec hash·discard-private. execute에는 진단 context hash와 안정 reconciliation_id 필요. 임의 경로/PID/device/force 입력 없음 |
| 결과 | 원 생성 실패와 수습 complete/release를 분리. 원장 status/phase와 관측 effective_status는 별개. 같은 create key는 원 실패+수습 기록, 새 복사/신원/기한 0. 별도 신규 생성은 새 UUID/current 자원 검사를 거침 |

recipe는 값이 null인 `creation-policy.json.example`만 `/usr/share/xenova`에 넣는다.
**운영 정책은 이번에 활성화하지 않았다.** 동일 후보를 설치하더라도 승인된 유한 정책을
공급하기 전에는 새 Linux create가 거절된다. 보드 실측 없는 숫자를 제품 기본값으로 정하지 않았다.
수습의 기본 15초/호출 상한 105000ms는 생성 운영 기한이 아니다.

### 5-B·5-C: 원 기한과 정상 종료·원 결과 회수

같은 조건의 수정 전후 반례는 `run_checks.py audit-before baseline`과
`audit-sealed candidate-final`이다. 실제 Manager/검증 복사/원장을 실행하고 첫 root write 뒤
제어 시계를 10초에서 100초로 이동했다. 시험 정책은 call 60000/실행 10000/정리 1000ms로 동일하다.

| 관측 | 기준선 | 최종 후보 |
|---|---|---|
| 원 host/실행 기한 기록 | 없음 | 최초 수락의 원 기한 20000ms 보존 |
| 동일한 원 root write 뒤 시계 이동 | 계속 진행·public 게시·ok=true | DEADLINE_EXCEEDED·public 없음·원 operation/부분 파일/오류 보존 |

두 감사 도구의 rc=0은 각각 반례/수정 후 assertion 확인이다. 기준선의 제품 성공을 수락한
결과가 아니다. `audit-before-facts.json`/`audit-sealed-facts.json`에 원 operation과 응답을 남겼다.
로컬 제어 시계 분기이며 실제 SD 생성 시간/최악 지연/전원 상실 결과가 아니다.

수락 저장 직후, 복사 반복문, guest 반환, 게시 직전 만료를 직접 주입했다. 기한 뒤 새
준비/public 게시는 없다. 정상 실패는 자기 실행 자원을 제한적으로 정리하며 private 이미지를
자동 폐기하지 않는다. 실제 Workspace.close의 역순 umount/loop detach와 자식 종료 미확정 시
보류를 시험했다. mount/loop 명령은 대역이며 실제 커널 해제 수락은 아니다.

자식 timeout의 추가 반례(`cleanup-before-candidate-8.log`)에서 wait 인자는
`[1, 약 0.05]`였다. 정리 여유와 별개의 1초 wait를 없애 공통 소유 자식 종료 경로에서
남은 정리 예산만 쓴다. 이 수정은 실제 소유 subprocess와 wait 인자를 검사하며 통과했다.
이후 시간 검사는 협조적이며 fsync/커널 I/O의 강제 선점이나 실제 종료 상한을 보장하지 않는다.

public 이후는 원 자산 검증/등록 정산과 새 외부 효과를 나눈다. **dhcp-applied가 이미
확인된 원 작업**은 원 기한 뒤에도 현재 호출 예산으로 결과를 마감하고 최신 stopped 의도를
보존했다. DHCP 재제출/launch는 0이다. 확인되지 않은 DHCP는 만료 뒤 새 효과를 허가하지 않고
미확정으로 남긴다. 이 시험은 미완료 create에 대한 HTTP stop 지원을 가정하지 않으며 실제
짧은 Execution.accept 원시 연산으로 선행 수락된 stopped 의도를 구성한다.

### 5-D: 수습·잠금·예약 판정

| 현재 근거 | 수습/반환 판단 |
|---|---|
| 확인된 active 원 준비 또는 다른 cleanup | 원 실행/원 조회로 연결, 탈취·삭제·예약 반환 없음 |
| 부모 부재여도 상속 FD·private inode·mount/loop 등 사용 잔존 | 원 실행 종료/미사용 확인 전 차단. 슬롯 파일 부재/PID/시간으로 대체하지 않음 |
| 관측 실패·불완전 열람·원장/schema 손상 | 없음/정상/반환으로 추정하지 않고 해당 오류로 종료 |
| workspace 전 실패·소유 효과 없음 | 명시 수습에서 삭제 0회로 원 실패와 예약 종결 가능 |
| 부분/완성 private·소유 확인·현재 미사용 | 원 계획 내구 저장 뒤 고정 항목만 폐기. source 손상/부재를 재해시하며 폐기를 막지 않음 |
| public/등록 충돌·원인 모를 부재·교체 inode·링크/특수 파일/권한 불일치 | 삭제/성공/예약 반환 금지. 진단 이후 변경도 실행 시 재확인 |
| 계획 저장 실패 | 삭제 0. 최초 create 오류와 수습 저장 오류는 별도 |
| unlink/부모 fsync/최종 저장 뒤 중단 | 저장한 deleting/root_pending과 남은 항목으로 동일 수습 재개. 자기 진행 revision은 새 요청으로 보지 않음 |
| complete JSON만 보이고 동기화 미확정 | 조회는 기록 내용을 표시. 실제 예약 소비 writer의 파일/부모 fsync와 동일 원장 재확정 실패 시 자원 소비 금지 |
| 현재 미사용·자산 부재·내구 완료 확인 | 해제 가능한 활성 예약 제외. 원 실패/key/spec/UUID 이력과 stable lock은 보존. 별도 정상 생성 허용 |

순서는 기존 Linux 슬롯(공통 잠금 밖) → 원 작업 확인/계획 저장(짧은 공통 잠금) →
열거·현재 사용 확인(잠금 밖) → 각 삭제 marker 저장 → inode/gate 확인 후 개별 unlink/부모
fsync(짧은 admission) → 진행 저장 → 잔여 사용/충돌 검사 → 내구 완료다. 긴 정리 전체로
공통 잠금을 점유하지 않으며 quiesce 뒤 다음 파괴적 단계를 차단한다. 재개는 같은 명시
수습의 새 호출이고 옛 생성의 게시 허가를 부활시키지 않는다.

`_reserve_space/_ip_inventory/_mutate/_accept_stop`, Execution.control, resource guards,
Recovery.conditions, CPU.guard_stopped, Zephyr reboot가 동일 holds 판정을 사용한다.
원 UUID 이력 충돌은 계속 막고 IP/활성 용량 예약은 완료 후 반환한다. CPU의 전역 guard가
수습 전에는 차단하고 수습 뒤에는 통과하는 직접 시험도 포함한다. sparse 실제 블록 차감은
기존 식을 유지했고 기존 용량/IP/lease·복구/자원 suite를 함께 재실행했다. 모든 action의
운영 조합/실제 DHCP·CPU 변경을 이번에 수락한 것은 아니다.

기존 2GiB 증가분 여유와 128KiB 수습 기록 상한은 유지하나 별도 물리 공간을 보장하지
않는다. 저장소 ENOSPC/EIO에서는 계획/완료를 확정하지 않고 호출을 끝낸다. 원장/다른 VM/
로그 삭제나 reserve-file로 우회하지 않았다. 저장소 회복 후 동일 수습·새 생성 성공까지 시험했다.

### 5-E: 수락 대응과 원자료

새 시험은 `platform/tests/test_xenova_creation_reconcile.py`, 기존 근거는 같은 최종 후보의
preparation_concurrency/read_progress/verified_copy/creation_failure 및 관리/복구 suite다.

| 요구 | 실제 assertion·근거 |
|---|---|
| S5-T01 | 정책 부재에서 원장/준비 0·정상 읽기, 수락 저장 직후 만료에서 원 기한/ID 보존·준비 0. 기존 management_budget/atomic_write/failure_record 시험으로 미수락/저장 미확정 구분 |
| S5-T02 | 양 OS 실제 검증 복사 내 만료, Ubuntu guest 반환·Debian 게시 직전 만료, 공유 cleanup margin·소유 child wait 한도·실제 Workspace 역순 종료. sparse/부분 write/source guard는 기존 verified_copy/두 OS 경로 시험 |
| S5-T03 | 새 caller/동일 boot 원 기한 보존, legacy·다른 boot 재개 금지/명시 폐기 성공. 기존 완료 replay 회귀 유지 |
| S5-T04 | 실제 열린/unlinked FD, loop 사용·관측 EIO에서 삭제 0; 해소 후 같은 수습 성공. 자식 미확정 시 close 보류. 상속 FD 부모 중단·host unmount 차단은 기존 S3/S4 프로세스 시험 재실행 |
| S5-T05 | workspace 전 실패 삭제 0 종결, private 만료 게시 0, dhcp-applied 이후 원 결과 정산·stopped 의도 유지. 기존 recorded/private/public 중단 회귀 |
| S5-T06 | 진단 뒤 다른 inode·원인 모를 다른 항목 부재·public broken symlink·hardlink/FIFO/모드·열린 파일 거절. 자기 private 폐기와 정상 후속 생성도 성공 |
| S5-T07 | 계획 ENOSPC 삭제 0, 완료 부모 fsync EIO에서 보이는 complete만으로 예약 소비 0, unlink/루트 제거 뒤 실제 SIGKILL 및 새 프로세스 회수. 같은 원 오류·수습 ID 보존 |
| S5-T08·T10 | 동일 수습 병행은 슬롯 미수락, 완료 응답 유실 replay/다른 수습 ID 거절. 원 create는 원 실패+수습 완료·재복사 0. 완료된 원 성공/현재 관측 실패 분리는 S3/S4 회귀 |
| S5-T09 | 양 OS 수습 barrier를 해제하지 않은 채 정상 list/get/원 조회·B 실제 하위 graceful stop/원 completed. polling 계속 성공. 준비 중 polling은 기존 S4-T03 양 OS·1/2/4 폴러 회귀. quiesce 뒤 삭제 차단/명시 재개 |
| S5-T11 | 아래 29 suite와 별도 dom0-manager 전체 회귀. prehash 0/검증 복사 1·seed/Zephyr·S01/S02·receipt/복구/CPU/의도 유지 |
| S5-T12 | 하위 strict CLI 진단/임의 path 거절만 확인. 실제 HTTP/Spring/화면 권한·DTO·명시 확인·사용성은 담당자 구현/종단 수락 대기 |
| S5-T13~16 | 실패 후 공통 잠금 반환, workspace 0개 종결, source 손상/부재 폐기, 저장소 회복 뒤 같은 수습 완료, 공통 예약 guard/원 UUID 이력·반환 IP 재사용과 별도 정상 생성 |

중심 양 OS 흐름은 실제 원 root write EIO → 부분 파일/원 오류 → cleanup unlink 뒤 중단 →
새 Python 프로세스에서 동일 수습 완료 → 같은 create key 원 실패 replay/복사 0 → 새 UUID
생성 성공·반환 IP 재사용이다. `assertions-regression-candidate-final/S5-T08-*`에 원/새
operation·오류·수습·동일 key 응답을 연결했다. `S5-T07-kill-*`은 실제 SIGKILL 두 지점,
`S5-T09-cleanup-barrier-*`는 cleanup_alive/released=false·polling_alive·정상 조회 수와
B의 physical_result/원 control operation completed를 남긴다. 전체 기록에 비슷한 상태가
있었다는 추정 대신 해당 barrier 안에서의 원 요청·응답 인과를 assertion으로 검사한다.

최종 실행은 기존 고정 WSL 도구·격리 경로에서 수행했다. `run_checks.py`의 각 JSON에 전체
pytest argv/rc/실행 시간을, 같은 이름의 log에 원 출력을 보존했다.

| 명령(모두 P5의 run_checks.py) | 최종 결과 |
|---|---|
| `focused candidate-final` | 새 수습+S4 집중 66 passed, 32.00초, rc=0 |
| `regression candidate-final` | 관련 29 suite, 696 passed, 197.70초(pytest)/198.10초(실행기), rc=0, skip 없음 |
| `manager candidate-final` | dom0-manager 전체 576 passed, 58.85초/59.65초, rc=0, skip 없음. 기존 Starlette/httpx 사용 deprecation warning 1개 |
| `audit-sealed candidate-final` | 원 기한 뒤 public 없음·원 기한/부분 파일 보존 assertion 통과 |
| `check_docs.py`, `git diff --check` | 6개 소유 문서의 로컬 링크/앵커 289개 오류 0, diff 공백 오류 0 |

집중 시험은 관련 회귀에 포함되므로 수를 합치지 않는다. 새 dom0-manager 의존성이나
httpx 버전을 설치/변경하여 warning을 숨기지 않았다. 기존 S01/S02의 손상 차단·동일 사건 ID·
pending 원자 저장 시험은 이 별도 suite에서 재실행했다.

시험은 실제 하위 Manager/원장/파일/검증 복사/flock/요청별 runner/subprocess를 사용한다.
Xen/Store·mount/loop·chroot/게스트 설정과 storage_check는 명시 외부 대역이다. 새 수습
시험의 전역 `/proc` 열람은 자기 프로세스(새 프로세스 시험은 자기/부모)의 실제 FD로 제한했다.
따라서 전체 root 프로세스 접근·실제 Xen/SD/게스트 backend·권한/전원 상실 수락은 아니다.
테스트의 짧은 timeout·제어 시계/축소 payload를 운영 시간/보장 상한으로 옮기지 않는다.

초기 실패 원자료를 삭제하지 않았다. candidate-1의 evidence 파일명 충돌, candidate-2의
추가 fixture 정책 누락, candidate-3의 해제된 inode가 새 소유 파일에 재사용된 오탐을
구분했다. inode 오탐은 현재 FD 경로/nlink까지 확인해 수정했다. candidate-4의 HTTP stop
가정·candidate-5의 임의 key 가정은 실제 기존 계약에 맞춰 시험만 고쳤다. candidate-6의
bridge public/None 대역은 기준선에서도 재현(`bridge-before-baseline.log`)되어 실제 private
결과 fixture로 교정했다. dom0-manager 7개 실패도 정책/private 시험 경계만 보완했다.
최종 후보의 제품 통과를 이 초기 실패 로그로 대체하지 않는다. 최초 독립 cleanup-wait
명령의 pytest 경로 누락은 환경 실패이며, 고정 도구를 사용한 재실행에서 wait 반례를 재현했다.

### 부분 패키지·인계와 남은 수락

`package_check.py candidate-final`은 recipe의 실제 install 목적지/모드를 따라 Python
**33개와 비활성 설정 예시 1개**를 소유 staging/archive로 구성했다. 이전 29개 subset에
없던 기존 하이픈 이름 Python 도구도 실제 목적지로 포함했으므로 파일 개수를 직접 비교하지
않는다. source/staging/archive 바이트·모드와 **11개 설치 경로의 `python -I -B` import**를
확인했다. `SUBSET_PACKAGE-CANDIDATE-FINAL.json`에 각 파일 해시/모드/출처를 둔다.
archive SHA-256은 `cd7e9d164107d5e7708066c8754f684eec50d3f60ad707b70c28570521b59b0c`다.

RDEPENDS는 기준선과 동일하다. 새 내부 모듈의 contextlib/hashlib/json/os/pathlib/re/stat는
기존 설치 모듈에서 이미 쓰는 표준 모듈이다. 고정 OE `0f03c732a0a2d0506b41b1abe60b3f368b2300dc`의
기존 표준 패키지 closure/manifest 근거를 재사용했다. 새 표준/runtime 패키지는 추가하지 않았다.
recipe의 새 module/example 포함을 확인했으며 native identity/guest 소스는 그대로다.
이 결과는 **부분 설치 묶음**이며 BitBake/RPM/전체 이미지/실제 설치본 검증이 아니다.
호스트 Python만 바뀌어 무관한 Zephyr ARM 재빌드는 하지 않았다.

[HTTP·Spring·화면 요청](../requests/20261029-request-http-spring-ui.md#원-create-기한과-실패-자산-수습-연결)에
실제 `_BUDGET_ACTIONS/SerializedManager/PlatformDriver` 위치, 권한/명시 확인, 원 key·
context·수습 ID 보존, 허용 응답 필드와 같은 시험의 실제 HTTP 인수 조건을 누적했다.
[Worker 요청](../requests/20261029-request-recovery-worker.md#원-create-실패-종결의-소비)은
수습을 VM 사망/재시작·Standby 재합류·출력 허가로 소비하지 않도록 연결했다. 요청서 작성은
담당자 제품 구현 완료가 아니다.

5번의 하위 구현·로컬 검증·부분 패키징 범위는 여기서 마감한다. 운영 유한 시간값 공급/
실측, 동일 모듈 후보 설치, 실제 HTTP/Spring/화면 권한과 응답 유실/명시 삭제 수락,
Xen·SD/전원 상실·실제 mount/loop·API cgroup/backend 소유는 별도 미완료다.
이번에는 보드·서비스·운영 상태·전체 이미지·commit/push를 변경하지 않았으며 6·7번은 실행하지 않았다.

## 1번 최종 마감

2026-10-31, 5번을 커밋한 `4a585d49a25d12459000306ffa74c42d0d473dcb`의 깨끗한
작업트리에서 [기본 관리 기준선](platform-management-baseline.md)을 작성했다.
위 「4번 후속」의 대응표는 **당시 후보의 근거**로 보존하고 최신 표로 덮지 않았다.
개념/책임·정식 계약·실제 소스·시험/후보·설치 범위를 구분하며 이번에는 문서만 수정했다.

문서 착수 전 P5 최종 source map의 platform 915파일, 봉인 변경 25파일과 바이트가 일치했다.
그 시점의 시험 snapshot 이후 차이는 최종 보고서 한 파일이며 봉인과 일치했다. 네 커밋의 대응은
P5 COMMIT_MAPPING에 보존돼 있다. 현재 제품과 같은 696개 관련·별도 Manager 576개
결과를 재사용하며 전체 suite/대용량 생성/ARM/부분 패키징을 다시 실행하지 않았다.
P5 시험 log/JSON·S4/S5 raw assertion과 부분 tar/manifest 34항목을 실제 열어 대조했다.
P4 3쌍 비용 원자료는 당시 두 VM/외부 Xen 대역 조건으로만 재사용한다.
P5 회귀 argv에 없던 Network Zephyr의 신규 카탈로그 등록·협조 stop/reboot 두 기존 시험만
같은 소스의 격리 fixture로 보충해 **2 passed**를 확인했다. 최초 pytest 도구 경로 누락은
환경 실패로 보존하고 기존 고정 경로를 사용했으며 새 의존성을 설치하지 않았다.

소스 대조로 관리 구조의 ‘HTTP stop 전체 미연결’을 key 있는 FastAPI 경로 제공과
Spring/신규 소비 미연결로 분리했고, 위 5번 소비자 목록의 `_control_view`를 실제
`_accept_stop`으로 교정했다. DEADLINE_EXCEEDED 오류 표도 이미 구현한 생성/수습 범위를
반영했다. 제품 API/schema나 기대 계약을 바꾼 정정이 아니다.

소유 범위 색인과 기능 카드, 정보 출처, 공통 잠금/저장/시간/예약, 비용의 반복 단위·
상한/미측정, 불일치와 후속 담당/수락은 기준선 한곳에 둔다. 사용자 제공 개념 원문은
[개념적 아키텍처](../platform/conceptual-architecture.md)에 0~11장을 보존하고 12장만
현재 소스/계약·원자료에 맞춰 갱신했다. 이번 검증에서 원문 전반부 동일성을 확인한다.

이번 읽기 비교·문서 검사 원자료는
`C:/Users/SSAFY/Documents/Codex/2026-10-31/xenova-stage1-closure/`에 둔다.
`check_documents.py`는 문서 7개의 로컬 링크/앵커 379개, 소스 정의 줄 11곳·기호 참조
77개, 개념 원문 12장 이전 48,266자 동일성과 제품 915파일 불변을 검사한다. 오류 0이며
`git diff --check`도 통과했다. 최종 문서 diff/해시는 P1 문서 마감 자료로 별도 보존한다.
제품 코드·정책·보드/운영 상태·배포·commit/push는 변경하지 않았다. **1번의 현행 구조
설명 범위는 마감**한다. 비용 의심은 5.5, 생산자/소비자와
정책 공급은 6, 동일 후보 설치/보드/제품 종단은 7번으로 연결하되 실행하지 않았다.

### 1번 편집 보완과 5.5번 계획

1번 문서 마감의 네 커밋 `8e29c74`·`9abac05`·`5ba97c4`·`9b69cdf` 뒤,
사용자 검토를 반영해 [개념 문서](../platform/conceptual-architecture.md)의 Markdown
읽기 구조를 복원했다. 제목 단계, 관계/수명/파이프라인의 `text` 코드 블록,
S01~S18 출처 링크와 실제 소스 링크만 편집했다. 전체 문장·흐름도 원문 및 12장 내용은
서식 제거 후 이전 커밋과 동일하며, 12장도 제목 표식 외에는 변경하지 않았다.
위의 48,266자 동일성 검사는 최초 문서 마감 당시 결과로 보존한다.
이번에는 서식/링크를 포함한 문자 동일성이 아니라 내용 보존과 읽기 구조를 검증한다.

**1번은 기능별 기준선·비용·소유권 대응 정리 완료로 마감한다.** 성능 최적화·운영 정책·
설치본/제품 종단 완료로 확대하지 않는다. [기준선](platform-management-baseline.md)의
후속 범위는 비용/대기 원인 추적과 직접 수정 권한을 구분하도록 명확히 했다.

[5.5번 계획](platform-cost-concurrency-plan.md)은 남은 공통 실행 잠금, `creation.holds`의
반복 내구 확인, registry/원장·수습 inventory 규모 증가를 우선으로 한다. 전체 소유
플랫폼 색인을 따라 runtime/통신·Telemetry·Bootstrap/Gateway·콘솔·recipe/공유 기반도
점검하며, 실제 의존의 타 담당자 원인은 기존 요청으로 인계한다. 같은 조건의 전후
계수·barrier/지속 polling·정상 성공과 오류 보호·비용 회귀를 마감 조건으로 계획했다.

이번 추가 변경은 문서 네 파일이다. 제품/시험·정책·보드/EC2·서비스·실데이터·배포·
commit/push는 변경하지 않았고, 비용 실험·최적화 구현·5.5/6/7번 실행은 하지 않았다.
서식/문서 링크·기호/제품 불변 확인의 후속 원자료는 P1의 `*FOLLOWUP*` 자료로 남긴다.

기존 번들 `marked 17.0.5`로 개념 문서를 파싱해 제목 0→84개·코드 블록 0→14개와
표 19개 유지를 확인했다. 읽기 문서 8개의 로컬 링크/앵커 560개·정의 줄 11곳·기호
77개 검사 오류는 0이고, 5번 후보의 platform 915파일은 바이트가 같다.
`git diff --check`와 새 계획 파일의 공백 검사도 통과했다. 서식 제거 후 전체 내용/12장
동일성은 `MARKDOWN_FOLLOWUP.json`, 변경 범위·해시/제품 불변은
`DOCUMENT_CHECK_FOLLOWUP.json`에 기록한다. 의존성 설치나 제품 회귀/비용 실험을 새로 수행한 결과가 아니다.

<a id="stage55"></a>
## 5.5번: 동일 근거의 비용 축소와 남은 공통 대기

### 후보·판정과 실제로 줄인 일

착수 HEAD는 `9b69cdf1df65408403e148868d19ab51cdaa9179`다. 1번 편집 후속의 문서
네 파일 diff/untracked를 보존했다. 현재 후보는 그 HEAD+이번 diff/새 파일이며,
원자료 P55는 `C:/Users/SSAFY/Documents/Codex/2026-10-31/xenova-stage55/`다.
`ENTRY.json/.diff`, `BASELINE_SOURCE.json`, 후보별 `*_SOURCE.json`이 입력 바이트를 고정한다.
보존한 baseline과 후보는 별도 WSL 소유 사본이다. 운영 `/srv`/`/run`, 보드/EC2는 사용하지 않았다.

**현재 판정은 5.5번 부분 마감이다.** 중복 작업 네 곳의 하위 국소 수정·로컬 검증·부분
포장을 수행했다. 전체 소유 영역의 조사 깊이와 미측정도 아래에 공개한다. 그러나 긴
restore/lifecycle 공통 잠금 때문에 다른 VM의 **실제 stop**이 기다리는 문제는 재현만
했고 해결하지 않았다. 이를 ‘필요한 대기’나 완료로 간주하지 않는다.

| 실제 경로·변경 | 줄인 처리와 유지한 경계 |
|---|---|
| `Manager._mutate` 신규 create | 현재 공통 잠금에서 `_operations`의 완전 검증/파싱 결과를 재사용. 원장을 쓰거나 callback/준비에 진입하기 전의 주소·용량 판정에 같은 `pending` 결과 전달. 슬롯 대기 뒤 새 `_mutate`, private 완료/게시, 새 호출에는 다시 확인 |
| `Manager._mutate` 원 key 완전성 | 별도 반례에서 조회는 중복 key를 거절하지만 create 재전달은 첫 기록을 골라 성공하는 기존 결함을 baseline에서 재현. 이미 읽은 집합에서 유일성을 검사해 공통 create/retire/restore/purge도 기존 `RECONCILE_REQUIRED`로 거절. 추가 I/O·원장 변경 없음 |
| `Manager._ip_inventory` | 독립 호출도 한 번 검증한 원장·tombstone 집합에서 소유 MAC/예약을 조립. 현재 DHCP/lease는 별도 읽기. 외부/legacy 호출의 검증 기본 경로를 생략하지 않음 |
| `Manager._mutate/_transition/_filesystem_revision` restore | 수락 전 read-only fsck는 유지. 같은 잠금 구간의 같은 root 경로/dev/inode/크기/mtime/ctime를 이동 전에 대조해 그대로일 때만 두 번째 fsck 생략. 원장 저장 callback 뒤 변경이면 다시 검사, 재전달에는 앞 호출의 결과를 전달하지 않음 |
| `AuthorityStore.save` | 크기 검사한 JSON을 독립 사본으로 파싱해 실제 저장과 성공 뒤 RAM 채택에 함께 사용. 같은 입력의 두 번째 `json.dumps` 제거. 실제 `json.dump`·file/parent fsync는 유지. 호출자 가변 객체의 뒤늦은 변경과 저장 미확정 보호도 검사 |

새 공개 필드/schema·원장·서비스·TTL cache·범용 검증 토큰은 없다. 현재 JSON 가시성만으로
내구 반환을 확정하지 않으며 다른 프로세스는 자기 writer의 근거를 다시 확보한다.
원 UUID/key/spec·기한·의도/실행·CPU/네트워크 정책·주기/품질·Linux 슬롯 상한은 바꾸지 않았다.

### 같은 조건의 작업량과 메모리

P55 `measure_mem.py`를 같은 도구/fixture·0/1/10/100 이력 축에 각각 실행했다.
완료/수습 이력은 현재 원장 검증기를 통과하는 합성 과거 기록이며 UUID/MAC과 원 결과를
함께 정규화했다. 추가 100개의 실행 VM이나 완전한 제품 DB를 뜻하지 않는다.
각 표본은 새 fixture로 시작하며 setup 뒤 실제 Manager/파일/fsync를 계수한다.
Xen/guest는 기존 외부 대역이다. 계수 hook+tracemalloc이 켜진 시간/peak이며 보드 성능이 아니다.

아래 100은 **추가 이력 수**이고 원 기록 하나를 합쳐 총 101개다. `fsync`는 파일/부모를
각각 센 실제 호출 횟수다. payload hash·물리 디스크 I/O·전체 생성시간과 합치지 않는다.

| 경로·이력 상태 | 원장 read 전→후 | JSON parse 전→후 | fsync 전→후 |
|---|---|---|---|
| 독립 IP inventory·SUCCEEDED | 202→101 | 205→103 | 0→0 |
| 신규 생성 계획·SUCCEEDED | 505→101 | 517→112 | 0→0 |
| 독립 IP inventory·수습 complete | 303→202 | 304→203 | 202→202 |
| 신규 생성 계획·수습 complete | 707→202 | 714→209 | 404→202 |
| 원 asset key 조회·양 상태 | 202→202 | 202→202 | 0→0 |
| Linux restore·불변 root | 14→14 | 36→34 | 17→17, fsck 2→1 |

원 key의 두 완전 검색은 실행/소유 종료·늦은 중복 기록 교차를 검출하는 별도 관측이므로
줄이지 않았다. callback 뒤 root 변경 시 fsck 2회, fsck 오류의 수락 전 거절도 유지한다.
첫 후보의 ‘선행 fsck를 수락 뒤로 미루는’ 방식은 실패 수락 경계를 바꾸므로 채택하지 않았다.

메모리 교환관계도 있다. 수습 complete 이력 100개의 생성 계획은 계측된 peak가
**1,649,772→2,506,768 bytes**였다. 성공 이력은 **1,502,351→2,429,969 bytes**다.
완전 검증된 이력을 현재 메타데이터 판정 동안 유지하는 임시 비용이며 영구 cache는 아니다.
원장 수의 기존 O(N) 입력 비용을 없앴다고 하지 않는다. 이 측정 범위에서는 추가 임시
메모리를 감수하고 반복 전체 파싱/내구 동기화를 줄였으며, 보드 여유/큰 이력 수락은 별도다.

Gateway는 같은 0/32/128 request 프로필을 각 3회 실행했다. `json.dumps` **2→1**,
`json.loads` 1·실제 `json.dump` 1·fsync 2는 유지했다. 128건의 계측 peak는
**30,684~30,772→42,504~42,592 bytes** 범위다. 저장 전 독립 사본을 유지하는 비용이며 입력 64KiB 상한은
그대로다. authority 갱신 경로의 비교이지 매 판정 주기의 비용이나 운영 지연 개선율이 아니다.

`MEM-COST-BASELINE/FINAL6.json`, `GATEWAY-COST-BASELINE/FINAL3.json`에 원 표본을 둔다.
wall time과 inclusive helper 시간을 합산하지 않고, 과거 P4/보드의 최솟값으로 개선율을 만들지 않았다.

### 규모 증가와 남은 비용·대기의 구분

`scale.py`는 실제 Manager로 정지된 **비네트워크 Zephyr** 자산을 0/1/10/100개 생성하고
등록/retire 집합을 각각 조회했다. 주소 풀/실행 RAM을 위반하는 100 Linux running VM
fixture를 만들지 않았다. 새 fixture와 같은 입력에서 baseline/후보 모두 정상 조회했다.
등록 N=1/10/100의 단건 get read는 **17/35/215**, list는 **19/118/1108**이다.
보관 N=1/10/100의 get은 **15/24/114**, list는 **16/88/808**이다. 두 후보의 해당 계수는 같다.
단건 get의 초기 전체 중복 신원 확인, 목록의 외부 관측 뒤 집합 검증은 그대로 남긴 비용이다.
원 key 이력 말단의 손상/중복·열람 실패 보호는 기존 S4 시험과 최종 회귀에 연결했다.

수습 `creation.usage`에는 자기 시험 프로세스의 FD 관측만 제공했다. 무관한 소유 FD를
0/1/10/100개 열면 열람 항목은 **4/5/14/104**로 증가했다. 실제 전역 `/proc`·다른 사용자
프로세스의 비용은 측정하지 않았다. 삭제 계획은 정적이지만 사용 FD/mount/loop는 검사 사이
새로 생길 수 있어 현재 사용 열람을 이전 표본으로 대체하지 않았다. `SCALE-*.json`에 조건을 둔다.

`ManagementCostTest.test_restore_barrier_exposes_remaining_physical_serialization`은 실제
공통 잠금을 거친 restore의 fsck만 막았다. 해제 전 B get/원 create 조회는 성공했고,
B stop은 stopped 의도와 원 control key를 수락했지만 **pending**, 실제 종료는 미완료였다.
A 해제 뒤 같은 stop key의 허용 이행은 정상 완료했다. `P55-T06-remaining-restore-lock.json`은
이 한계를 통과한 독립 종료라고 포장하지 않는 반례다.

단순 unlock은 해법이 아니다. 현재 `_mutate`의 같은 restore key는 미완료 journal을
재개하며, create와 달리 활성 lifecycle 실행 소유를 조회하는 경계가 없다. 공통 잠금을
해제하면 살아 있는 restore를 재개해 중복 효과를 제출할 수 있다. 다음 수정에는 원
operation에 결합한 lifecycle 실행 소유·같은 key의 활성/재개 판정·관련 writer/legacy·
host drain/자원 종료를 함께 확정해야 한다. 이는 현재 create/discard-private 소유 규칙의
확장이므로 새 writer 규칙을 결정하지 않은 채 구현하지 않았다. 플랫폼 책임의 미완료이며
상위 timeout 조정으로 넘기지 않는다.

### 전체 소유·공유 영역의 점검 깊이

아래는 [기준선 범위 색인](platform-management-baseline.md#scope)의 모든 행에 대한 이번
대응이다. 소스에서 확인한 주기/상한과 실제 host assertion을 구별한다. guest IRQ/실제
주기 worker·SD 성능을 host core 통과만으로 실측 완료 처리하지 않는다.

| 영역·실제 경로/기호 | 평상시·실패 비용 확인과 근거 | 판정/남은 깊이 |
|---|---|---|
| `guests/common/prepare_instance.py`, `first-install` | `_copy_verified/_ranges`의 1MiB stream·chunk deadline, seed/조립의 별도 검증. `check_creation`은 요청별 문맥/시계만 읽고 chunk마다 원장/정책/host를 다시 읽지 않음. 양 Linux 선택·prehash0/root 복사1·sparse/독립 inode·ENOSPC/원 오류/재전달 시험 | 비용 보호 유지. 정상 create를 다른 helper의 full payload 재읽기로 바꾸지 않음. 고정 대용량/OS 시간7 |
| `recipes-core/xenova-platform/files` Manager/Execution/creation/CPU/resources/recovery | 위 신규 수락·IP/restore 수정과 0~100 원장/자산 계수. 실제 제어·자원/firmware/CPU·복구 guard는 효과 경계 검사를 유지 | 국소 중복 수정. key 2회 검색/현재 guard는 필요한 재검증. 장기 공통 물리 대기는 위 미완료 |
| `boot/network`, bringup·VM unit·`Platform.check_network/prepare_dhcp` | DHCP 준비는 start/is-active/reload와 bridge/IP 확인. registry 변경 뒤 hosts 게시·무관한 정적 예약 보존. boot/quiesce·unit graph·network fixture 회귀 | source/로컬 외부 대역. 실제 systemd/DHCP 대기는 운영 환경7. 하위 저장 대기 원인은 조사 범위에 포함 |
| Zephyr `xen-runtime/src/runtime.c`, `xen-store`, netfront/grant | 실제 C의 공유 세션에서 1,000 concurrent reads·watch 소비자2·session open/close 각1, 보유 lock timeout 후 정상 재개. ASAN/UBSan·부분 초기화/잔존 grant 회귀 | 소유/공유 Store 비용은 현재 직렬 전송 계약. 세션을 호출마다 다시 만들지 않음. 실제 Xen ring/IRQ·전체 guest stack 비용7 |
| `apps/drive`, 공통 runtime/MQTT | 실제 adapter/controller·health·상태/입력 벡터, 실제 C payload 120,000 concurrent parses. main의 독립 task/현재 준비·release를 source 대조 | 현재 host 알고리즘/파싱·원 수명 보호. 임의 idle 계산/주기 하향 없음. native kernel worker 전체 동적 비용은 이번 미측정 |
| `diagnostics`, `telemetry/src/worker.c` | Core/histogram·생산 모델/직렬화·reset/잘림·오류 보존 host 시험. worker의 통상 200ms capture/20ms sleep, threads=false 기본 수집과 명시 thread snapshot 분리 확인. CONNACK 후 재수집은 새 inventory 근거 | source의 cadence와 host Core 확인. 실제 Zephyr port의 wakeup/stack/수집 CPU·burst 총부하는 미측정; 중복 결함으로 확정하지 않음 |
| `xenova_drive_binding.py`, `provision-xenova-telemetry.py` | 관측 전후 state·receipt/Store 대조와 짧은 admission, busy의 제한 재시도·다른 오류 즉시 종결. 앱별 교체/uncertain 재주입 금지 시험 | 외부 관측 사이의 새 실행/의도 검사는 유지. 안정 binding의 실제 주기 총부하7, 옛 표본으로 lease 갱신 없음 |
| `services/gateway` | Authority 직렬화 계수·같은 입력의 파일/RAM 독립성·sync 실패·상한 거절, actor/준비/전환·실패 DRIVE 철회/STOP 순서 전체 시험 | 소유 코드 수정. serial/device ACK·실제 actor tail 지연7. 부가 failure journal보다 STOP 시도가 먼저인 경계 유지 |
| broker/Tailnet/SSH·Wi-Fi 공통 기반 | broker CLI 저장/미확정·root 경계와 policy/unit fixture, Wi-Fi status의 wpa/ip별 외부 호출, wrapper/native identity 선택의 source/회귀 | 공유 운영 제품/네트워크 변경 없음. 실제 broker/auth·Wi-Fi/Tailnet 대기/정책은 소유 확인·7. 환경 실패를 제품 수정으로 숨기지 않음 |
| Xen console async·`xenova_telemetry_evidence.py`·trace/collector | 실제 pthread/eventfd queue의 stall/lock/failure/exit/unavailable, 64 job/32 file 상한. archive의 새 관측/회전 gap·retention 계획 저장·원 PTY 보호 시험 | 부가 I/O는 비대기 제출. 실제 pinned Xen PTY 결합 시험은 source 미지정으로 미수행. 원문 폐기/표본 품질 하향으로 개선하지 않음 |
| Yocto recipe/wrapper·조립/scripts | 정규 하위 install 경로33 Python+비활성 example, 11 import·바이트/모드·archive 대조. unchanged native wrapper·RDEPENDS 유지 | 부분 포함 확인. Gateway는 README /opt source-layout 묶음이며 정규 recipe/unit 수락 아님. 전체 이미지/진행 중 빌드 건드리지 않음 |
| dom0-manager/Worker/Agent·backend/frontend·장치 | 기존 소비자 576 회귀와 요청 위치 대조. 하위 get/list·원 key/stop 의도 소비와 남은 상위 mutex/선행 get/후속 이행을 구분 | 타 담당자 제품 무변경. [HTTP](../requests/20261029-request-http-spring-ui.md#55번-로컬-비용-보완의-소비-범위)·[Gateway](../requests/20261029-request-gateway.md#55번-authority-저장-비용-보완) 영향 누적. 6·7 자동 실행 안 함 |

### 검증·실패 이력·부분 포장

시험은 원장/파일/잠금을 실제로 실행하며 Xen/guest/mount/Store 외부 대역을 명시한다.
`test_xenova_cost_progress.py`는 같은 구간의 계수, 독립 호출의 재동기화/EIO,
fsck 수락 전 실패/중간 변경 재검사, 남은 restore barrier를 검사한다.
`test_authority_cost.py`는 0/32/128 상태의 작업량·독립 사본·오류/상한 보호를 짝지었다.
기존 S3/S4/S5의 양 Linux 장기 준비/polling 중 B 실제 stop·원 결과 확정, 같은 key의
thread/process 재전달·owner/FD·quiesce·원 기한/수습 후 새 생성은 관련 회귀로 확인했다.

실행 명령·rc·환경/경과는 P55 `run_checks.py`와 모드별 JSON/log, assertion은
`assertions-*`, native stdout은 `native-*`에 둔다. 시험 수는 집중/포함 회귀를 합산하지 않는다.
최종 결과/해시의 연결은 `FINAL_CANDIDATE.json`에 둔다.

최종 `regression-final6`는 **704 passed**다. 정규화한 비용 fixture·중복 key 반례를 포함한
하위 비용/보호 8개도 여기에 들어 있다. `focus-final-final3`의 **164 passed** 중 당시 하위
7개는 최종 회귀로 갱신했고, Gateway 157개만 같은 제품/시험/공유 MQTT 바이트를 대조해 재사용한다.
`coverage-final6`는 **134 passed, 2 skipped**(실제 root mount 경계와 pinned Xen PTY source
미지정), 별도 `manager-final6`는 **576 passed, 기존 warning 1**이다. 관련 파일이
최종 후보와 같은 범위만 재사용하며 개수를 합산하지 않는다.
`HOST-CC-FINAL3`는 실제 C/ASAN·UBSan 다섯 실행 case가 모두 성공했다.

중간 실패도 보존했다. 기존 Gateway helper의 import 경로 누락과 로그 출력 폴더 누락은
소유 시험 runner만 보완했다. 넓힌 시험 두 건은 baseline에서도 실패했다. G1 독립 로더
fixture에 현행 필수 `xenova_preparation.py`가 빠졌고, broker 저장/미확정 fixture는 non-root에서
의도한 fault 주입 전 권한 거절로 끝났다. 각각 실제 의존 파일 추가와 인증 경계 주입만
수정했다. broker/Agent/권한 제품 정책은 바꾸지 않았다. cmake 부재는 설치/환경 수리 없이
기존 CMake 소스 목록을 기존 cc로 직접 빌드해 다섯 host case를 실행했으며 CTest 실행이라 하지 않는다.

하위 부분 묶음은 `SUBSET_PACKAGE-FINAL6.json`의 **33 Python+비활성 example 1개**,
독립 설치 import 11개다. archive SHA-256은
`46e43ebe2fff9c5dc028427967a2cb64adc4ef5c25c6e666a4e359911cec3713`다.
`GATEWAY-SOURCE-PACKAGE.json`은 문서상 `/opt/xenova/gateway`·MQTT dependency의
소스 26파일과 import를 대조한 별도 묶음이다. Gateway archive SHA-256은
`5ce77977120ecd0f59e95e09eab6f21ad7ae0ea5f649700d68b4c523aa41e06b`다.
둘 다 source/staging/archive가 같고 mode를 확인했지만 RPM/전체 이미지/실제 설치본은 아니다.
정규 Gateway recipe/unit은 이 저장소 범위에서 확인되지 않아 새로 만들거나 완료로 표시하지 않았다.

### 남은 장애물과 다음 수락

| 항목·현재 근거 | 필요한 다음 행동·책임 |
|---|---|
| restore/lifecycle의 B 실제 stop 대기: 재현된 미완료 | 플랫폼: 원 lifecycle 실행 소유/재개·관련 writer/legacy·host 종료 규칙을 먼저 확정하고, 같은 barrier에서 A 미완료 중 B 실제 종료까지 통과시킬 것. 단순 unlock/원장 삭제/timeout 상향 불가 |
| registry/원 key·수습 사용 검사 성장: 계수는 확보, 필요한 전체 확인 유지 | 플랫폼: 현재 identity/완전 검색/외부 사용의 무효화 범위를 보존할 구체 대안이 있을 때만 추가 축소. 직접 조회 index/새 cache를 이번에 만들지 않음 |
| guest port/실제 주기 총부하·콘솔 pinned PTY·SD 간섭: 미측정 | 7번 입력: 같은 후보/설정에서 collect·wake/queue·CPU/stack/drop/표본 나이, 동시 관리/주행, actual Xen/SD/mount/loop 단계별 비용. host Core 결과를 보드 보장으로 확대하지 않음 |
| 운영 create 정책·상위 소비/권한·API cgroup/backend·Gateway 설치 | 기존 담당 요청과 6·7번 책임 유지. 비활성 example이나 소스 묶음만으로 사용 준비/VM 보존/실제 출력 수락을 선언하지 않음 |

따라서 반복 비용의 확인된 국소 개선과 관련 로컬 보호는 제공하지만, **5.5번 전체의
불필요한 대기 해소를 완료로 닫지 않는다.** 1번의 구조 마감·2~5번의 보호는 보존한다.
보드/EC2·서비스·실제 VM/운영 자산·정책·배포·commit/push를 변경하지 않았고 6·7번은 실행하지 않았다.

<a id="stage55-followup"></a>
## 5.5번 후속: lifecycle 독립 진행·OS 공통 정적 pinning·동등 관리

### 기준과 판정 범위

사용자가 승인한 F1/F2/C1/E1/E2 후속이다. 첫 P55의 부분 마감·반례·후보별 실패를 위에
보존한다. 출발 HEAD는 `feat/platform/recovery-scenario-closure / 9b69cdf1df65408403e148868d19ab51cdaa9179`이며
기존 미커밋 14개 경로도 포함했다. 새 commit으로 후보를 가장하지 않는다.
원자료는 `C:/Users/SSAFY/Documents/Codex/2026-10-31/xenova-stage55-followup/`의
`ENTRY_SOURCE.json`, `FINAL7_SOURCE.json`, `FINAL_CANDIDATE.json`·diff에 고정한다.
제품 최적화와 CPU 지원 확장은 다른 변경 묶음이다. 운영 정책/설치/실장치 수락은 아니다.

### F1: 실행 소유를 대체한 뒤 긴 공통 대기를 분리

`xenova_preparation.AssetLease`, `guard_asset/asset_state/assets_quiet`와
`Manager._lifecycle_io/_transition/_purge`가 실제 경계다.

| 구간 | 보호·확인과 실제 효과 |
|---|---|
| 검사 전 | UUID별 asset flock 무대기 획득. 아직 operation/예약 수락 아님. 공통 잠금 아래 원 key·대상·현재 세대 확인 |
| 긴 검사 | 같은 대상 lease를 유지하고 fsck/usage/inventory는 공통 잠금 밖에서 실행. 최초 fsck 오류는 원 restore journal 생성 전 거절 |
| 복귀·수락 | 해당 tomb·의도·원 journal·host/barrier를 대조. restore 주소/용량은 최신 registry 사용. 원 journal 기록 후 operation/spec/세대를 lease에 결합 |
| 게시 | 검사 root revision·metadata·정지 상태·gate 재확인. 짧은 공통/admission 아래 moving→rename→moved→세대/DHCP/원 결과 확정 |
| purge | 현재 소유 inventory/사용 확인은 유지. 항목별 nofollow/inode/link 검사와 unlink/fsync는 대상 소유 아래 공통 잠금 밖. 잔여 기록은 공통 구간 |
| 반환·재전달 | 활성 같은 key는 원 RUNNING 진행 회수, 두 번째 효과 0. 실제 flock 반환 후만 기존 journal 재개. 부모 종료·상속 FD 잔존은 재개 가능한 부재가 아님 |

획득은 **대상 lease → lifecycle I/O 슬롯 → 공통 → 짧은 admission**, 경쟁 writer와 슬롯은 무대기다.
`lifecycle-io.lock`은 기존 lifecycle I/O 동시 1개를 유지하고 조회/다른 VM stop은 사용하지 않는다.
Linux create 슬롯과 별개이며 소유자·상속 FD의 대상/슬롯 drain을 각각 확인한다. 무제한 I/O
병렬화·자동 대기열은 추가하지 않았다.
공통 잠금으로 lease 반환/자식 종료를 기다리지 않는다. 실제 보유 trace를 분리해
`lock.hold`에 외부 검사 시간을 포함하지 않고 `lock.released.lifecycle`로 기록한다.
자산 제어·자원/firmware·복구·CPU·legacy config/firmware·first-boot writer와 drain도 연결했다.
같은 thread의 중첩 공개 호출은 내부 소유자로 승인하지 않는다. 구형 writer와 이 모듈 집합의
혼합 설치는 지원하지 않는다. 대상 파일을 지우거나 lock을 교체해 반환시키지 않는다.

`test_xenova_lifecycle_ownership.py`와 `test_xenova_cost_progress.py`는 다음을 직접 확인한다.

- 실제 lock의 Debian/Ubuntu ID·version·source로 생성한 A의 fsck를 계속 막아둔 동안
  두 poller의 정상 get 6회와 B의 실제 stop·control journal completed가 끝난다.
  그 뒤 A도 같은 원 요청으로 성공하며 불변 root fsck는 1회다.
- 미수락 검사 중 경쟁 요청은 원 restore journal을 만들지 않는다. 수락 뒤 같은 key는
  thread 및 새 Python process에서 같은 operation/RUNNING을 회수하며 fsck/이동을 복제하지 않는다.
- gate 닫기·재열기는 검사 결과를 무효화한다. 원 기록은 새 허가로 바꾸지 않는다.
  root 변경은 필요한 재검사 또는 게시 전 거절, 최초 fsck 오류는 미수락이다.
- purge unlink barrier 중 B 실제 stop 완료, 실제 unlink 후 오류는 CLEANUP_INCOMPLETE,
  같은 원 key/inventory로 남은 항목을 확인해 정상 완료한다.
- 상속 FD와 실제 소유 부모 SIGKILL·자식 잔존에서 새 실행/drain을 막고, 자식 종료 뒤 정상
  보호 반환·다음 restore를 확인한다. 운영 PID/자산을 사용한 시험이 아니다.

과거 `P55-T06-remaining-restore-lock.json`은 첫 P55 폴더에 그대로 남는다. 후속의
`assertions-regression-final7/F-T01-{debian,ubuntu}.json`은 A 해제 전 실제 완료 증거다.
조회 성공/stop 의도 수락만으로 통과시키지 않았다. 단일 게시·필수 저장 syscall의 지연,
기존 물리 제어 자체의 공통 직렬화·모든 스케줄의 공정성은 해결/보장 범위가 아니다.

옮기지 않은 연관 경로도 소스에서 구분했다. 아래 비용 의심을 필요한 배타성으로 확정하지 않는다.

| 남긴 구간 | 현재 판정·근거 | 다음 확인 |
|---|---|---|
| 자원 변경 `xenova_resources.guards/mutate` | 정지·의도/구성 guard 후 작은 instance.json/원 결과 게시. 기존 usage 전역 열람은 공통 안에 남음. 게시 배타성은 필요, usage 전체 보유는 비용 의심 | 플랫폼: 큰 proc/FD 입력의 실제 대기 재현과 별도 대상 소유/수락 경계를 확인해야 추가 분리 가능. 이번 독립 진행 수락은 lifecycle 대상 |
| firmware transition/legacy replace | `bundle_info/copy_revision/validate_instance`의 검증·복사와 DHCP가 공통 안에 남음. 새 lifecycle lease와 같은 자산 충돌은 거절하지만 firmware 자신의 긴 실행 분리는 미구현 | 플랫폼: 실제 bundle 크기/사용 열람/DHCP 단계별 계수·barrier 필요. 공통 저장 순서가 복사 전체 배타성의 증거는 아님 |
| templates/probe/recovery-operation | 기존 공통 metadata/관측/원 기록 경로. 새 자동 재개·숨은 I/O queue 없음 | 원장/카탈로그 규모와 느린 helper 반례는 기존 비용 색인으로 유지. 이번에 전 경로 무대기를 선언하지 않음 |
| CPU set/host prepare | 모든 관리 guest 정지 유지보수 창에서 정책/anchor/receipt·dom0 적용 정산을 직렬화 | 현 계약의 필수 직렬화 유지. 실행 중 정책 완화나 live repin으로 해결하지 않음 |

### C1: CPU 지원 범위 변경과 호환

`xenova_cpu.validate_spec/placements/policy_candidate/context/set_policy/prepare`를 대조했다.
OS와 A/B 개수 조건을 제거하고, 기본 공유 mask·UUID별 shared/exclusive 동일-vCPU mask를
지원한다. shared 지정은 기본 공유 집합의 부분집합, exclusive는 그 집합과 다른 전용 mask에
비중복이다. 해제는 정식 전체 정책 교체에서 해당 UUID를 제거하는 요청이다.
다중 vCPU Linux를 1-vCPU로 줄이지 않고 scalar cfg와 실제 모든 vCPU를 검증한다.
Zephyr의 고정 firmware 1-vCPU 제약은 유지한다.

새 policy/journal은 2.0 placements다. 기존 exclusive 입력도 받지만 새 publication은 2.0이다.
1.0 policy·완료/미완료 journal은 원래 표현/hash로 읽고 같은 key 재개도 1.0을 유지한다.
새 정책 이후 과거 key replay는 과거 결과와 current_policy를 구분한다. anchor/host receipt는
기존 1.0이며 hash는 저장 policy 전체 기준이다. CPU 전용 reader만 버전을 확장했다.
정확한 공개 입력·조회 확장은 [정식 계약](../platform/cli-manager-spec.md#cpu-contract)을 따른다.

`test_xenova_cpu_pinning.py`는 양 Linux 실제 선택의 4-vCPU shared pinning→reboot/recover→
정지→해제→기본 mask 실행, multi-vCPU exclusive {1,2}, 마지막 vCPU drift와 stop,
겹치는 공유 pinning, 3개 전용 예약과 추가 공유 Zephyr, retired/purged 예약의 Linux 이전,
Zephyr 없는 관리/host 준비, factory 과거 ticket의 현재 정책 무변경, old/new journal 회수를 확인한다.
기대 mask는 렌더러와 독립된 상수로 실제 cfg·전체 관측과 비교한다.
실행 중 변경·scheduler/pool 이동·미등록 대상 신규 지정은 거절한다. 전체 관리 guest 정지
유지보수 창은 보존했으며 일반 start/get이 dom0/실행 guest를 몰래 repin하지 않는다.

### E1: OS별 관리 대응과 근거 수준

아래 공통 근거는 현재 소스/최종 회귀에 연결되며 실제 OS 부팅/장치 수락을 뜻하지 않는다.
이름만 바꾼 세 벌의 공통 시험으로 직접 검증을 가장하지 않는다.

| 기능 | Debian·Ubuntu | Zephyr | 실제 경계·남은 수락 |
|---|---|---|---|
| 선택/생성·실패 종결 | linux_template_paths의 고정 두 OS 입력·root copy/prehash, S5 양 OS 실패→수습→새 생성 | network_zephyr의 실제 카탈로그 신규 등록·별도 bundle, legacy seed/lifecycle | mount/chroot·Xen/Store는 대역. 고정 대용량/실부팅은 7 |
| list/get/기본 정보 | read_progress 두 OS의 현재 관측·원 결과·부분 실패, 실제 selected Linux start/pinning | network/CPU mixed 등록·stopped 조회 | 등록/실행·설정 자원/실제 affinity·예약 IP/Agent IP 분리. 상위 조합은 6 |
| start/autostart | C-T01 두 OS start/새 incarnation 직접, batch/중복 실행/부팅 의도는 공통 boot/CPU 회귀 | CPU의 autostart·협조 reboot 직접 및 batch 공통 보호 | 두 Linux의 template별 autostart 실부팅은 아님. 성공 VM 보존·미확인 앱 상태 유지 |
| stop/reboot/destroy | F-T01 두 OS stop·CPU 양 OS toolstack reboot/recover, 명시 force·응답 유실 공통 회귀 | network_zephyr 협조 stop/reboot·legacy 명시 force | Agent 부재와 guest 협조 실패는 다름. 자동 force 없음 |
| retire/restore/purge | 양 OS restore barrier 직접, 소유 unlink/오류 재개 및 기존 Linux tomb/inode 시험 | CPU/기존 lifecycle에서 retire/restore/purge·예약 보존 | 운영 삭제 미수행. 현재 미사용/부분 효과를 원 journal로 확인 |
| 자원·CPU | 두 OS 4-vCPU pin/unpin 직접, 자원 변경 정지/hash·CPU 문맥 및 전체 vCPU 공통 보호 | 1-vCPU pin/unpin 공통 policy/context·정식 release, 추가 shared VM | 실제 Xen affinity 효과/부하 격리는 7. OS별 SMP 일반화 아님 |
| 조건부 복구 | 양 OS 선택 C-T01 recover_once 직접, attempt/원 기한/의도·실행 공통 회귀 | network/CPU의 새 실행·정책/협조 경로 | Worker 건강/반복 정책·재합류·출력 허가는 별도 |
| Agent 부재 | 선택 fixture에 Agent 프로세스/표본 없이 Xen 기반 start/get/stop/reboot/recover 진행 | Agent 없는 firmware 프로필 관리 | guest 기능 건강은 미확인. Agent 내부 collector/서비스 정책은 담당자 |
| backend·저장·네트워크 | 실제 cfg·scope/CLI/boot source와 소유 자식·FD 로컬 회귀 | block 없는/네트워크 프로필·Store/grant 종료 로컬 회귀 | 실제 qemu/backend/vif 수명·API restart는 미수락. Agent 책임으로 넘기지 않음 |
| 관리/host 종료 | lifecycle 검사/수락/상속 FD drain, gate 세대 무효화와 기존 boot 보호 | 같은 host drain·guest 협조 종료/잔존 보호 | API cgroup/backend는 기존 HTTP 담당 공동 수락. 실제 poweroff/unmount는 7 |
| 실행 부하 | 정상 polling/관리 간섭·원장 규모·임시 메모리 | 실제 native worker 9개 + host runtime/Telemetry/콘솔 | CPU/memory/disk/network 공통 보드 간섭·주기 전체 비용/IRQ/SD는 미측정 |

### F2/E2: 유지한 비용·추가 비용·검증과 포장

동일 fixture의 후속 entry→최종 계수다. 최초 P55의 505→101 등의 개선과 혼합하지 않는다.

| 입력/관측 | entry → 최종 | 해석 |
|---|---|---|
| 성공 이력 101개 create plan | 원장 read 101→101, parse 112→112 | 첫 국소 최적화 유지 |
| 수습 complete 이력 101개 create plan | read 202→202, parse 209→209, fsync 202→202 | 다른 writer/새 호출의 내구 확인 유지 |
| 같은 불변 root restore | fsck 1→1, fsync 17→17 | payload 해시 추가 0. 소유/재검증의 추가 metadata 비용은 별도 |
| 같은 restore의 추가 확인 | text read 37→88, parse 34→51, 원장 read 14→16, Xen/helper 10→12 | unlock 전후 대상 tomb/의도/gate/원 기록·최신 자원·제출 직전 실행 guard. 전체 payload 재처리 아님. 대상 파일 open/flock/stat 전부의 계측을 뜻하지 않음 |
| create plan 100 extra success/settled 계측 peak | 2,429,969→2,430,223 / 2,506,417→2,506,706 bytes | 계측 fixture 1표본, 운영 메모리 여유 판단 아님 |
| 동시 원 작업 조회 1/2/4, 이력 101개·각 3회 | peak 최대 1,573,888 / 3,086,425 / 5,967,318 bytes, 반환 뒤 history 객체 참조 전부 0 | 첫 complete history를 barrier로 함께 유지한 상한 입력. 정상 조회 전부 성공. 실패/정상 plan의 참조 반환은 별도 weakref 회귀 |

`MEM-COST-{ENTRY,FINAL7}.json`, `MEM-LIFETIME-FINAL7.json`은 원 표본을 보존한다.
동시 create 계획은 기존 공통 수락 구간으로 직렬화되며 이를 동시 4건 성공 성능으로 포장하지 않는다.
Gateway 0/32/128의 dumps 1·실제 dump 1·fsync 2와 오류/독립 사본 회귀도 유지한다.

고정 Zephyr `684c9e8f32e4373a21098559f748f06915f950c9`와 API 10개를 확인하고 기존 도구의
실제 경로로 native_sim/native/64 worker fixture를 빌드했다. `NATIVE-WORKERS-combined1*.json/log`:
9개 실제 kernel 시나리오 전부 rc=0. queue 교체·지연·독립 진행·부분 시작·잔존/join·재시작
실패 후 회수 assertion이다. host elapsed/CPU/RSS만 추가 측정했으며 전체 주기의 wakeup/queue
분포·장기 idle·IRQ·SD 비용이나 실제 pinning 효과는 측정하지 않았다. kernel/guest source는 최종
후보와 바이트 대조해 재사용한다. 새 패키지 설치·환경 수리는 하지 않았다.

관리 전체·Gateway 집중·dom0-manager·runtime/소비자 suite의 실제 argv/rc/log는 최종
manifest에 연결한다. 집중 회귀를 포함한 suite 숫자는 합산하지 않는다.

| 최종 실행 | 결과 | 근거 범위 |
|---|---|---|
| `regression final7` | 721 passed, 236.38초, rc=0 | 양 Linux·Zephyr·생성/수습·자원/CPU·lifecycle 소유·부팅/복구·기존 비용 회귀 포함 |
| `focus final7` | 220 passed, 88.45초, rc=0 | lifecycle 직접 반례/CPU pinning·기존 CPU/Gateway 집중. 위 회귀와 겹치는 시험을 합산하지 않음 |
| `manager final7` | 576 passed, rc=0 | 기존 Starlette/httpx 경고 1. 타 담당자 제품 변경 없음 |
| `coverage final7` | 134 passed, 2 skipped, rc=0 | runtime/Store/Telemetry/콘솔/binding/네트워크·직접 설치 경로 import |
| native kernel worker | 9개 시나리오 rc=0 | 고정 소스/API·기존 실행기를 사용한 실제 native_sim kernel; 보드 성능 아님 |

중간 `focus-f1b`의 purge 확인 UUID 누락 fixture 1건, `regression-f1c`의 종료 gate 오류 코드
호환 3건, `focus-c1a`의 CPU 2.0을 일반 1.0 실행 reader로 읽던 실패 30건은 원 log/JSON에
보존했다. fixture를 정식 입력으로 고치고 기존 종료 의미/CPU 전용 reader로 해결했다.
실패 assertion을 제거하거나 시간값을 올려 통과시키지 않았다. final7 결과와 합산하지 않는다.

최종 Markdown 링크/앵커 649개, 실제 정의 줄 11개와 기준선 기호 79개를 검사했다.
개념 문서 0~11장 문장·14개 흐름 코드 블록·19개 표는 보존하고 적용 상태 장만 갱신했다.
platform 920파일은 final7 시험 snapshot과 일치한다. 이 중 후속 변경 14개 파일과 기존 P55
작업을 구분했고, 출발 P55 manifest의 14개 경로도 entry와 일치했다. `git diff --check` 통과,
index/HEAD/branch는 불변이다. 정규 recipe/서비스·실데이터·운영 정책을 바꾸지 않았다.

실제 root mount와 pinned Xen PTY source 미지정 시험은 여전히 미수행이다. 필요한 제공물은
격리 root mount 환경과 고정 Xen PTY source/동일 revision이며 해당 OS/backend 담당과 7번에서
실제 해제/원문 전달 assertion을 수락해야 한다.

recipe Python 33개+비활성 creation example 1개의 최종 바이트/모드·11개 설치 경로 import를
확인했다. `SUBSET_PACKAGE-FINAL7.json`, tar SHA256
`405185a5ff49462bfd5bdd6443e8a0c00f3abc1f7ad9e0c8b1be163fe3fe49b8`.
recipe/RDEPENDS는 불변이며 필요한 모듈은 이미 모두 포함돼 있다. Gateway의 이전 소스-layout
묶음과 현 source 동일성을 확인해 재사용하며 정규 recipe/unit 설치로 승격하지 않는다.

후속 인계는 기존 [기준선 표](platform-management-baseline.md#followups)와
[HTTP D02/D08](../requests/20261029-request-http-spring-ui.md),
[Worker D01/D03](../requests/20261029-request-recovery-worker.md),
[Linux/Agent](../requests/20261029-request-linux-agent.md),
[Gateway](../requests/20261029-request-gateway.md)를 유지한다. CPU 버전/입력/조회 소비,
운영 create 정책 공급·권한·API backend 귀속·정규 Gateway 포장과 동일 후보의 실제 Xen/SD/
설치/HTTP/화면·장치 부하 수락은 미완료다. 그 사실을 5.5 후속의 로컬 정상 진행과 혼동하지 않는다.
6·7번은 실행하지 않았고 commit/push·보드·운영 정책·서비스·실데이터를 변경하지 않았다.

판정은 **5.5 후속의 하위 구현·로컬 수락 마감**이다. 핵심 F1의 실제 독립 stop과 C1의
OS 공통 정적 pinning/해제가 정상 완료되고, 같은 원 기록/신원/기한·실패/반환 보호를
확인했다. 위 미수행과 상위/실설치·전체 주기/실보드 비용까지 완료로 확대하지 않는다.

<a id="stage55-cross-boundaries"></a>
## 5.5 좁은 교차 보완: 남은 공통 대기·CPU 연쇄·추가 비용

### 후보와 범위

착수는 `feat/platform/recovery-scenario-closure` / `4033702cd156ee576202e85c5784068d17386160`,
tracked/untracked 모두 깨끗한 작업트리였다. 앞선 final7의 제품·시험은 7개 작업단위 커밋에
반영돼 있으며, 이번에는 commit/push하지 않는다. Linux pinning이나 restore 독립 stop을
다시 미지원/미해결로 판정하지 않는다. 변경은 재현된 firmware/usage/DHCP 결합과 같은 CPU
검증 단계의 중복 처리다. 공개 입력/응답·schema·시간값·운영 정책은 바꾸지 않았다.

PC 원자료는 `C:/Users/SSAFY/Documents/Codex/2026-10-31/xenova-stage55-cross-boundaries/`에 둔다.
`BASELINE_SOURCE.json`, 중간 `CANDIDATE*_SOURCE.json`, `FINAL_SOURCE.json`은 실행 입력이며,
마지막 `FINAL_CANDIDATE.json`은 실제 최종 diff/새 파일·시험·부분 포장·문서 검증을 연결한다.
서로 다른 입력의 통과 기록을 섞지 않는다. 보드/서비스/실데이터를 사용하지 않는 기존 WSL
격리 fixture이며, 현재 고정 의존성만 사용했다.

### 남은 대기의 재현과 분리

착수 `locks-baseline`의 5개 positive assertion은 실패했다. B get/원 작업 조회와 stop 수락은
성공했지만 물리 종료가 pending이었다. DHCP 메서드 입구의 지연만으로 외부 호출을 확인했다고
하지 않고, `cross-before-baseline`에서 실제 systemctl reload와 legacy CLI copy도 추가 재현했다
(3개 실패 원자료 보존). 모두 admission은 사용 가능했다. 실제 DHCP 외부 호출은 이미 admission
밖이었으며, 남은 원인은 공통 operation 잠금이었다.

| A만 지연시킨 경계 | 최종 직접 assertion | 유지한 소유/공유 구간 |
|---|---|---|
| firmware-transition bundle_info / copy_revision | A 대기 중 B get/원 조회·stop completed, A 해제 뒤 원 transition 완료 | 수락 전 대상 lease, 수락 뒤 원 key/spec/세대 결합. 같은 자산 start와 같은 key의 재검사/중복 게시 차단 |
| update-resources usage_check | B의 정상 조회·실제 stop/원 결과가 A보다 먼저 완료 | 대상 configuration/의도/원 기록·실행 부재 재확인, 짧은 admission 아래 instance.json 게시 |
| retire/restore DHCP start와 reload 각각 | 네 barrier 모두 B 실제 stop completed, admission 사용 가능, A도 정상 완료 | 서비스 확인/대기는 공통 밖. 최신 registry의 dhcp-hosts atomic_write와 원 journal/세대 확정은 공통 안 |
| legacy firmware-replace CLI copy_revision | 실제 독립 CLI가 멈춘 동안 B stop completed | 동일 프로필 준비와 대상 lease·기존 슬롯. 기존 비-journal CLI 출력/교체 계약 유지 |
| DHCP start 대기 중 별도 VM 신규 등록 | A 복귀 뒤에도 B의 새 MAC/IP 예약이 파일에 남고 retired A 예약은 제거됨 | 잠금 밖에서 얻은 옛 목록으로 예약 파일을 덮지 않음 |

정확한 시험은 [CrossBoundaryTest](../../platform/tests/test_xenova_cross_boundaries.py)의
`test_firmware_*`, `test_resource_usage_*`, `test_*dhcp*`, `test_legacy_firmware_replace_*`다.
9개 직접 시험이며 JSON은 `assertions-cross-final/X-DWAIT-*.json`에 저장한다. 원 조회·stop
수락과 control-operation completed를 따로 확인하고, A는 아직 살아 있고 release=false인
상태여야 한다. Xen 전체를 막거나 polling을 끄거나 자동 force로 통과시키지 않는다.

획득 순서는 기존 **대상 lease → lifecycle I/O 슬롯 → 공통 operation → 짧은 admission**이다.
공통/admission 아래 lease·자식 종료를 기다리지 않는다. resource/firmware도 기존 한 I/O 슬롯을
사용하므로 무제한 복사를 추가하지 않았다. 일반 조회/다른 VM stop은 이 슬롯을 사용하지 않는다.
복귀 시 tombstone·intent·원 journal, 구성 변경 경로의 실제 instance.json을 대조한다.
firmware의 주소/MAC 예약은 usage 뒤 현재 원장/registry로 다시 판정한 뒤 수락한다.

DHCP는 서비스 확인 → 최신 예약 파일 게시 → reload로 나눈다. 서비스/네트워크 확인과 reload는
소유 경로에서만 잠금 밖이다. 새 registry는 서비스 확인 뒤 한 번 읽고, reload 뒤에는 더 소비할
registry를 만들지 않는다. 파일 게시·필수 저장의 지연은 여전히 공유 저장소의 한계다.
직접 Python replace_firmware의 기존 호출자는 공통 잠금 보유 계약을 유지한다. create·제어·복구의
다른 DHCP 경로, templates/probe/recovery-operation 등까지 무조건 병렬화한 결과가 아니다.

### CPU 정상 연쇄와 오류 뒤 탈출

[CPUCrossTest](../../platform/tests/test_xenova_cpu_cross.py)는 다음 직접 assertion을 추가한다.
앞선 양 OS 실제 템플릿 선택·4-vCPU pin/unpin·reboot/recover 시험은 그대로 회귀한다.
새 resize/수습 교차는 Ubuntu 계열의 작은 Linux fixture에서 공통 Manager/CPU/원장을 실행하며,
이 시험 하나를 Debian 실제 설치·부팅 수락으로 확대하지 않는다.

| 교차 조건 | 실제 확인 |
|---|---|
| pinned Linux 정지 2→4→2 → 해제 | 매 시작의 렌더링 입력과 현재 vCPU 전체가 literal mask `[3]`, 해제 뒤 두 vCPU 모두 `[0,3]`. 제거된 vCPU 요구·새 vCPU 기본 mask 잔존 없음 |
| 정책/receipt 손상, receipt applying, 정책/receipt read EIO | start/recover는 CPU_POLICY_INVALID 또는 CPU_NOT_READY로 차단. 동일한 신원으로 Manager stop 완료. 원 파일/정책 회복·prepare 뒤 정상 start. 몰래 repin/중복 create 없음 |
| 빈 placements + 새 host + 과거 factory ticket | 현재 정책 바이트·완료 이력 보존, 새 host receipt, 예약 자동 부활 0, 기본 공유 mask로 정상 start |
| 생성 실패 → 명시 수습 → 실제 CPU writer → 정상 VM start | 수습 전 writer 차단, 내구 반환 뒤 같은 policy key로 성공. 원 create status/error는 그대로이며 holds=false. 원장 삭제/새 UUID 우회 없음 |
| 원 journal 전 restore 검사 lease | 검사 중 원 journal이 실제로 없더라도 CPU writer 차단. 검사 종료/restore 성공 뒤 같은 policy key·prepare·정상 start 완료 |
| CPU 관측 중 prepared journal만 새로 출현 | 정책 값이 그대로여도 관측 뒤 전체 이력에서 pending 검출, 준비 성공으로 읽지 않음 |

CPU의 저장/관측 오류가 정확한 기존 실행의 stop을 막는 결함은 이번 시험에서 재현되지 않았다.
이 부분은 제품 수정 없이 근거를 보충했다. CPU 2.0의 기존 공개 의미·구형 hash/원 key replay·정지
유지보수 조건·모든 OS의 live repin/scheduler/pool 미지원은 유지한다.

### 추가 비용의 목적과 실제 중복 감소

`restore-cost-{baseline,candidate5,candidate6}-counts.json`은 동일 fixture의 실제 read/parse/fsync와
각 primitive의 호출 위치 사슬을 보존한다. 첫 P55나 실보드 시간과 섞지 않는다.

| restore 단계 | 읽는 근거와 무효화 경계 |
|---|---|
| 잠금 해제 전 | 대상 tomb/intent, 수락 전 또는 수락된 원 journal, host/barrier·실제 대상 FD 소유 |
| 긴 usage/fsck 뒤 | 같은 target·intent·원 기록인지 대조, 다른 VM이 바뀔 수 있는 주소/용량용 registry 갱신 |
| 수락 이후/게시 직전 | root revision·metadata·정지/현재 실행·gate 검사. 원장 저장/checkpoint·외부 I/O를 넘은 옛 근거로 게시하지 않음 |
| DHCP 서비스·reload 전후 | 대상/원 결과 수명은 유지, 다른 VM의 현재 예약 목록은 파일 게시 전에 새로 확보. 실제 호출 횟수는 그대로 |
| 완료 | 세대/원 결과와 필수 file/parent 동기화. 이력·논리 예약과 실행 FD 반환은 별개 |

착수 restore는 text read 88·parse 51·원장 read 16·helper 12·fsck 1·fsync 17이었다.
DHCP 분리 직후 candidate5는 119/66/20/12/1/17이었다. 호출 위치로 확인한 같은 guard의
Execution/boot 중복 생성과 DHCP의 같은 단계 registry 재읽기만 줄여 candidate6은
**93/64/20/12/1/17**이다. 새 unlock 경계의 원장/의도 확인은 남겼다. 전체 payload hash나
추가 fsck는 없다. host/gate 검사는 각 guard에서 새로 수행하며 요청 간 cache가 아니다.
DHCP를 걸친 aggregate span은 제거하고 해당 경로는 실제 lock hold/released와 개별 command로
계측하여, 부모 lock span을 끝낼 때 중첩 aggregate가 잘못 닫히지 않게 했다.

CPU는 [benchmark_cpu_cost.py](../../platform/tests/benchmark_cpu_cost.py)의 같은 시작 fixture로
5경로 × 1/4-vCPU × 기본/명시 shared pinning, 20조건을 계수했다.
전은 **이번 착수의 CPU 2.0**이며 CPU 1.0 대비 기능 확장 비용/SD 시간 개선율이 아니다.
각 행의 네 조합에서 정책 전체 처리 횟수는 같았다. vCPU별 관측 행 수는 필요한 만큼 늘어난다.

| 요청 | policy/anchor parse 각각 전→후 | journal 전체 scan/parse 전→후 | receipt parse | CPU.observe / vcpu-list (전후 동일) |
|---|---|---|---|---|
| start | 20→18 | 24→18 | 2 | 2 / 4 |
| get | 0→0 | 0→0 | 0 | 0 / 0 |
| Linux reboot | 19→17 | 23→17 | 2 | 2 / 4 |
| recover_once | 23→20 | 29→20 | 3 | 3 / 6 |
| host prepare | 6→6 | 7→7 | 1 | 2 / 4 |

한 require의 동일 검증 단계는 전체 이력 scan 5→2다. entry와 외부 관측 후의 완전 검증을
유지하고, 마지막 검증의 이력으로 pending도 판정한다. 순수 context 조립은 같은 정책을 사용한다.
get은 실행 신원/현재 존재 조회이며 CPU 준수 검사를 새로 붙이지 않는다. host prepare는 실제
dom0 vCPU별 pin 제출 사이의 현재 정책/신원 guard가 남으므로 횟수를 줄이지 않았다.
효과 제출·새 incarnation·정책 교체를 넘는 결과 재사용이나 전역 cache는 없다.

성공 이력 101개의 create plan은 read101/parse112, 수습 complete 이력 101개는
read202/parse209/fsync202로 그대로다. 기존 임시 메모리 반환 assertion은 통합 회귀에 유지하고
별도 규모 계측은 반복하지 않았다. native worker 9개는 미변경 guest 소스의 해시 대응으로 재사용한다.
계측은 논리 작업량이며 물리 I/O/보드 latency/공정성 상한을 측정한 결과가 아니다.

### 회귀·부분 포장과 후속 인계

최종 명령·rc/log는 `cross-final`, `regression-final` 및 `FINAL_CANDIDATE.json`에 연결한다.
최종 교차 18 passed(38.26초), 통합 회귀 761 passed(262.48초), 모두 rc=0이다.
18개는 761개에 포함되므로 합산하지 않는다. 중간 candidate5의 759개 통과도 별도 이력이다.
candidate2/3의 수습 fixture 실패는 loop JSON 대역 누락, 원 실패 status를 FAILED로 잘못
기대한 assertion이었다. 실제 RECOVERY_REQUIRED/error를 보존하도록 시험을 정정했다.
비용 실행의 첫 reboot 대역 설정 누락과 helper import 누락도 원 log로 보존했다.
실패를 없애려고 제품의 원 상태·보호 assertion·기한을 완화하지 않았다.

부분 포장은 recipe Python 33개+비활성 예시 1개다. 바이트/모드·설치 경로 import 11개와
recipe/RDEPENDS 불변을 확인한다. 새 module·runtime 의존성·활성 정책을 추가하지 않았다.
실제 BitBake/RPM/이미지·실설치 검증이 아니다. 게스트/kernel/네이티브 helper 소스는 미변경이다.
`SUBSET_PACKAGE-FINAL.json`의 tar SHA256은
`630140c58ddbafe10b633527498d5365c2d62b1dae2f0706115f8c813c84784d`다.
Markdown 링크/앵커 663개와 실제 정의 줄/기호를 확인하고, 개념 0~11장 문장·14 text 블록·
19개 표를 보존했다. `git diff --check` 통과, index·branch·HEAD는 착수와 같다.

| 후속 | 필요한 제공물/수락 |
|---|---|
| 6: 모듈 집합/소비 계약 | CPU policy/journal 2.0과 기존 1.0 replay를 이해하는 같은 후보의 Python 33개 집합, 설치 manifest/hash. Manager·preparation·resources·zephyr/transition·vm·CPU를 구형 writer와 혼합하지 않음. 기존 API/Worker/CLI 실행자가 이전 import를 붙잡은 채 쓰지 않도록 배포 담당자가 수락 중지·기존 실행/helper drain·프로세스 교체·새 import 경로 확인 순서를 계획. 이번에 서비스 재시작하지 않음 |
| 6: 상위 소비 | 기존 HTTP D02/D08·Worker/Agent/Gateway 요청 유지. CPU 입력/표시·원 작업 pending/replay·운영 create 정책 공급은 실제 담당 구현/공동 수락 |
| 7: 같은 후보 실물 | 새 host와 empty/explicit 정책, 모든 vCPU 실제 affinity, Linux CPU/memory/I/O 부하와 dom0/B 관측·stop 지연, 단계별 syscall/SD 비용. 실제 backend/vif/PTY·root mount·API restart, Gateway 정규 recipe/unit·장치 출력은 기존 담당과 수락 |

이번 마감은 **요청한 세 묶음의 하위 구현·로컬 교차 수락**이다. 모든 물리 조치의 병렬성이나
templates/probe 등 미측정 경로의 대기 제거로 확대하지 않는다. 기존 final7 성과/원자료를
보존했고, 큰 설계·공개 schema·운영 정책·타 담당자 제품·보드·배포·commit/push·6/7은 바꾸거나
실행하지 않았다.

<a id="defaults-errors-review"></a>
## 2026-11-01 입력 기본값·재전달과 오류·진단 비용

이 절은 당시 후보의 이력이다. 아래의 최초 입력 형태 일치/동일 명시값 충돌 선택은 이후
사용자 결정과 [create 호환 보완](#create-compatibility)으로 대체됐다. 과거 시험 결과와
측정은 보존하되 현재 입력 지시로 사용하지 않는다. 나머지 진단·내구성 보호는 유지한다.

### 후보·판정 기준과 실제 사용

착수는 `feat/platform/recovery-scenario-closure` / **23858bc19753904c69ed5ce60463206ea3238a3c**,
tracked diff/untracked 모두 없는 상태다. 앞 절의 P55 교차 보완을 보존했다. 제품은 하위
Manager/creation/CLI/trace와 기존 소유 Gateway만 변경했다. 타 담당자 제품, 운영 정책·
자산·서비스·CPU affinity·보드·commit/push·6/7번은 변경/실행하지 않았다.

이번 완료 기준은 **정상 생략이 실제 생성으로 이어지고, 수락한 원 결정은 replay에서
유지되며, 불필요한 계산/출력 대기는 줄어도 오류의 원인·코드·사유·수습 구분과 필수
저장이 보존되는가**다. 오류 코드 수나 로그 건수 감소를 개선 지표로 삼지 않는다.

```bash
# ID와 버전은 실제 게시 목록에서 선택하고, UUID는 최초 요청 때 한 번 발급·보관한다.
xenova-vm create --vm-id "$VM_ID" --name example \
  --template-id "$TEMPLATE_ID" --template-version "$VERSION" --execute --json
# 응답을 잃었다면 위 최초 제출 그대로 재전달한다. 출력의 확정값으로 body를 다시 만들지 않는다.
```

vCPU·메모리를 각각 생략하면 **기존** K.describe의 default_resources가 원천이다.
현재 지원 프로필은 Debian/Ubuntu 1-vCPU·512MiB, Zephyr 고정 1-vCPU·16MiB다.
새 운영 숫자·minimum→recommended 추정·latest 버전·create pinning 필드를 추가하지 않았다.
전체 명시 요청은 계속 지원한다. 생략→명시 동일 숫자로 최초 입력 형태를 바꾸면
IDEMPOTENCY_CONFLICT다. 정확한 호출·출력은 [정식 create](../platform/cli-manager-spec.md#cmd-create)를 따른다.

### B: 무엇을 어떻게 바꿨는가 — 입력과 수락

| 항목·선택 종류 | 생략/명시 검사와 원천 | 확정·저장·replay·표시 |
|---|---|---|
| vcpus/memory_mib: 사용자 선택·프로필 기본값 | M._normalize는 key 존재를 유지. 명시값은 정확한 int>0, bool/null/0/음수/문자열/NaN/Inf 거절. 신규만 M._selection→_resolve_create로 기존 selected.default_resources·limits 대조 | op.request는 최초 정규화 입력의 key 존재, initial_spec/spec_hash는 확정 자원과 기존 guest 설정, op.template은 당시 작은 원천. 완료 replay는 현재 catalog를 읽거나 다시 복사하지 않음 |
| IPv4: 사용자 선택/자동 할당 | dhcp profile에서 생략은 기존 자동 예약, 명시는 플랫폼 주소 범위 검사. null/빈 문자열은 자동 할당으로 바꾸지 않고 metadata/복사 전 INVALID_SPEC. dhcp capability 없는 Zephyr profile의 IP 미지원 유지 | assigned의 원 MAC/IP와 신원·원 기한 보존. 현재 Agent IP와 구별 |
| template ID/version | 정확한 ID/명시 version 필수, 정식 catalog 선택. 기본 버전/latest 계약을 새로 만들지 않음 | 저장된 op.template 출처로 원 입력/확정값 관계 확인. 현재 원천 부재도 완료 이력 회수를 막지 않음 |
| CPU 개별 배치 | create에 override 필드를 추가하지 않음. 미지정은 현재 플랫폼 정책 상속, CPU 2.0의 정지 변경/OS 공통 배치 유지 | create 자원 결정과 다음 실행의 현재 mask는 별개. 새 incarnation·효과 guard는 재확인, live repin 없음 |
| UUID/key·권한 | 기존 정규 UUID와 `create:<UUID>` 규약 유지. 이름/대상/명시값·존재 형태 변경은 충돌 | 새 UUID/key/기한을 replay에서 발급하지 않음. force/purge/수습 실행 승인 확대 없음 |
| 호출/조회 예산·운영 시간 정책 | 기존 생략 상한과 원 deadline 구분. 운영 create 정책 부재에 임의 fallback 숫자 추가 없음 | 원 수락 host/deadline과 이번 호출 잔여 예산 유지 |
| Wi-Fi·부팅/설치 | 기존 parser의 wlan0/KR 등 별도 선택 계약, 비밀 입력·기존 설치 identity plan 유지 | 이번 create 공통화를 다른 명령에 복제/확대하지 않음 |

**변경 이유와 경계:** CLI는 default=None인 두 resource를 늘 dict에 넣어 null로 전달했으며,
Manager는 둘을 필수로 요구했다. parser 숫자 기본값을 추가하지 않고 실제 입력된 key만
전달하도록 고쳤다. 공통 resolver는 I/O 없는 작은 계산이며, 처음 수락하기 전 선택/슬롯
대기 후 재확인 때만 현재 metadata를 쓴다. 수락된 key 검색·유일성은 이 해석보다 먼저다.
슬롯 대기 동안 기본값이 바뀌면 아직 미수락인 요청만 새 값을 선택한다. 패자는 원 진행/
미수락 경합을 확인하며 원 값을 덮거나 두 번째 복사를 시작하지 않는다.

계획은 `data.request`(제출 선택)와 `data.spec`(이번 계획 해석값)을 구분하고 저장/예약하지
않는다. 계획을 확인한 숫자로 실행하려면 **새 실행의 첫 제출부터 plan.spec을 완전 명시**한다.
계획 뒤 생략 입력으로 실행하면 그때 신규 선택이며 앞 계획이 예약된 것으로 해석하지 않는다.
수락 뒤에는 첫 body를 유지한다. 원 initial_spec, update-resources 이후 configuration,
Xen 관측의 domid/state 및 CPU-layout의 모든 vCPU affinity는 서로 바꾸어 쓰지 않는다.
현재 메모리 실측은 별도 Xen stats 소비 경로의 관측이며 lower 구성값으로 가짜 실측을
만들지 않았다. Agent 미보고는 여전히 미확인이다.

**호환:** 자원을 하나라도 생략한 create만 journal **1.2**다. 원 request·당시 template·
initial_spec의 관계를 매 완전 원장 검사에서 대조한다. mismatch는 RECOVERY_REQUIRED이며
현재 기본값으로 복구하지 않는다. 저장 template/capabilities가 잘못된 타입인 경우도
creation.validate에서 사용 전에 RECOVERY_REQUIRED로 거절한다. 일반 AttributeError로
빠지거나 예약 반환으로 해석하지 않는다. Manager에 같은 타입 검사를 중복하지 않았다.
완전 명시 Linux 1.1/Zephyr 1.0 및 그 hash/완료 replay는
그대로다. Linux timing/수습 구조와 schema_version은 함께 검증하고, 1.2를 다른 action에
허용하지 않는다. 새 writer와 구형 optional-input 미지원 reader/writer의 혼합 설치는
지원하지 않는다. 기존 원장을 일괄 변환하지 않았다.

### C: 오류·출력별 실제 경로와 판정

| 경로·빈도·문맥 | 확인한 사실과 판정 | 수정/보존 기준과 종료 책임 |
|---|---|---|
| Manager 입력/저장/효과 실패: 관리 요청 | 전용 ManagementError와 envelope가 입력·관측·저장 오류를 구별. 이미 충족 | if→전용 예외→호출 경계를 유지. 내부 TypeError를 INVALID_SPEC으로 합치지 않음. 최초 원 오류·원 작업/의도/수습 저장·finally 유지 |
| NeedLinuxSlot·ReadChanged: 준비 경합/조회 변화 | 슬롯 획득은 잠금 밖, read 회차 최대 2. 이미 의도된 제어 | 같은 함수명이 예외라는 이유로 Result 체계로 전환하지 않음. writer/실행 교체의 필요한 재확인 유지 |
| Queue.Empty·idle/pending: loop/수신 | 표준 queue 대기/일시 부재, 원래 full stack 없음. 이미 충족 | empty() 선검사로 바꾸어 producer 경쟁을 만들거나 polling 주기를 낮추지 않음 |
| Trace.event/span: 요청별 상세 512개(최대 4096) 포화 뒤 반복 | 폐기할 event도 _fields/context/시각을 생성했고 span도 버릴 row를 만들었음. 확인된 낭비 | event는 포화 판정 후 dropped만 증가. span은 summary·command_summary/기한용 업무는 유지하고 상세 row만 생략. 필수 저장 경로 변경 없음 |
| Gateway MEASURE: actor/전환·출력 | INFO 비활성에도 필드 join/f-string 실행. 확인된 낭비 | isEnabledFor 뒤에만 형식화. INFO 수준·MEASURE 형식/도구 소비·주기는 유지 |
| SerialSink.write 실패: 출력 actor | log.error의 동기 handler가 SinkError 반환 전에 기다려 같은 turn의 STOP을 붙잡음. 실제 결함 | 첫 cause_type/errno·원 SinkError from cause 보존 후 즉시 반환. actor의 fence·실제 STOP 제출이 먼저. 각 제출의 failure_cause는 현재 type/errno를 보존하고 메시지/희귀 내부 스택은 STOP 시도 뒤 구성. EIO→STOP ENOSPC도 두 원인을 구분. 진입점 fatal은 소유 종료 뒤 full cause/stack 출력 |
| Gateway 진입점 종료 오류: 희귀 종료 | client.stop 예외가 순차 finally의 sink/lease/새 진단 worker 정리를 건너뛰고 먼저 잡힌 실행 오류도 가릴 수 있었음. 실제 코드/주입으로 확인한 결함 | 개별 종료를 끝까지 시도하고 최초 실행 오류와 각 정리 오류를 별도 full cause/stack으로 종료 뒤 보고. 전파 중인 내부 결함은 정리 오류를 note로 연결하고 그대로 전파. client ENOSPC 주입으로 나머지 close와 첫 ValueError 보존 확인 |
| Gateway 부가 warning/INFO: MQTT callback/actor | root StreamHandler의 write/flush가 생산자에 동기 결합. 느린 sink 반례 확인 | 기존 stdlib QueueHandler 기반 Gateway 전용 128개 사본 worker. 메타데이터/할당·prepare는 생산자 비용이며 포화 전 검사로 불필요한 prepare 생략. 권위/receipt/명령/필수 사건 저장은 이 queue와 분리 |
| invalid/queue overflow·aux offline: 반복 가능한 수신/단절 | raw topic 경고·오프라인마다 인위적 ConnectionError 생성. 개선 | 누적 거절/drop과 1,2,4… 경고. offline은 예외 없이 별도 aux_offline. 일반 warning에 raw body/topic/credential 없음. 정상 연결 회복과 출력 보호 유지 |
| aux 실제 publish 예외/내부 결함 | 기존은 action type warning만 남겨 원 내부 원인이 부족했음. 진단 보완 | type/errno/event/message가 다른 transport는 별도 private 사본, 내부 결함은 첫 full stack+최근 16개. 같은 type/message/위치의 연속 반복만 재형식화 생략, nested cause는 매번 보존. 오류를 offline으로 합치지 않음 |
| CLI/helper stdout·RequestOutput | JSON/marker/콘솔 증거의 소비자와 요청별 64KiB 제한 존재. 이미 충족 | print→logger 일괄 치환 없음. exit code/JSON/비밀 입력/원 stdout 유지, 전역 FD redirect 없음. 느린 opt-in trace_sink는 자기 반환을 늦출 수 있으나 공통 잠금 밖, 다른 실제 stop 진행 확인 |
| Zephyr drive/runtime/Store/netfront/Telemetry·native helper | 설정 원문은 LOG_MODE_DEFERRED=y, PRINTK/CONSOLE=y. XD_READY/XDIAG_BIND·shell·종료 요약/JSON은 소비되는 프로토콜/희귀 진단. source/기존 시험 근거 | C/ABI/주기/formatter·config 변경 없음. printk를 deferred logging과 같다고 단정하지 않음. MQTT decoder의 반복 LOG_WRN은 고정 backend/worker 총비용 미측정으로 분류하고 임의로 삭제하지 않음 |
| 콘솔 archive·원자료 | 기존 bounded async 사본과 포화/PTY 보호 시험 존재. 이미 충족, 이번 source 불변 | 원 PTY/실행 binding·gap/손실을 보존. 부가 사본 폐기를 원 콘솔 폐기로 확대하지 않음 |
| HTTP/Java/Agent | 입력 필수 DTO/상위 재조립, 검증 body 읽기·동기 warning, Reporter 반복 stack 위치를 읽기로 확인. 담당 인계 | 외부 제품은 미수정. 구체 수정 위치·측정·정상/실패 수락은 기존 요청에 누적. Java/Python/C 비용을 동일 배수로 가정하지 않음 |

**queue의 교환관계:** 실행 진입점에 daemon thread 하나와 유한 queue가 추가된다. 일반
메시지는 4096문자에서 자른 뒤 제어문자를 escape하여 최대 16384문자이고 mutable 인자/
traceback frame을 나중에 참조하지 않는다. 포화/실패/잘림·첫/최근 errno·worker_alive를
snapshot으로 구분한다. ERROR/CRITICAL·exc_info는 포화/drop/자르기 대상이 아니고 직접
전달하며 최근 critical 원문도 보존한다. 이 희귀 full stack과 private 내부 원인 사본의
바이트를 임의로 잘라 원인을 지우지 않았으므로 모든 오류의 메모리 상한은 주장하지 않는다.
close는 0.25초 join, 막힌 worker는 미확정으로 표시한다. sink 회복 뒤 실제 종료 확인,
같은 실패 sink로 재귀 출력하지 않음. 임의 callback·shared logger·OS I/O·프로세스 종료 전체
대기까지 제거한 보장은 아니다. [Gateway 상세](../../platform/services/gateway/README.md#수신출력-진단).

### 같은 조건의 비용과 남긴 비용

원자료 폴더는 `C:/Users/SSAFY/Documents/Codex/2026-11-01/xenova-defaults-errors/`다.
`review_cost.py`, `review-cost3-entry-counts.json`, `review-cost-release-counts.json`은 실제
선택 모듈 경로/Python 3.12.3까지 남긴다. 같은 소형 template/Manager/real file·fsync와
fake Xen·guest fixture, 같은 logging level/계수 hook의 논리 작업량 비교다. 물리 SD I/O나
운영 지연 감소율이 아니다. 단일 계수 실행을 p99/운영 상한으로 해석하지 않는다.

| 고정 입력·primitive | entry→final | 보존/추가 비용 |
|---|---|---|
| 완전 명시 plan text read/parse/describe | 8/7/2→8/7/2 | 새 복사/예약 0 |
| 완전 명시 accept read/parse/describe/fsync/copy | 33/23/4/23/1→동일 | 기존 수락·필수 저장·작업 소유 유지 |
| 완료 replay read/parse/describe/copy | 11/8/0/0→동일 | 원 완료 회수·현재 원장 검증 유지 |
| 새 생략 plan/accept/replay | final의 위 완전 명시 계수와 동일 | resolver는 저장/현재 metadata에서 순수 계산. 옛 후보의 미지원 입력을 속도 비교로 만들지 않음 |
| cap=0 event 1000회 _fields/clock | 1000/1001→0/1 | drop=1000 유지, 생성자 clock 1 유지 |
| cap=0 span 100회 상세 row append | 100→0 | summary/command calls 각각 100, drop=100 유지 |
| INFO off MEASURE 100회 fields.items | 100→0 | 활성 MEASURE 문법/의미 보존 |
| offline aux 100회 ConnectionError | 100→0 | 실패 총계 100, 내부 결함/transport 원인은 별도 |
| 첫 내부 TypeError와 동일 위치 100회 | final full stack 형식화 1·반복 99 | 옛 코드는 full stack이 0이었음. **100→1 개선으로 꾸미지 않음**. 첫 원인 보존을 위해 새 진단 비용 추가 |
| 느린 sink 1001 warning | release prepare 129 / queue max128 / drop872 | 계측 peak303693 bytes, 종료 때 worker 미확정→sink 회복 후 false, FD4→4. 계측/Mock 보관 비용 포함, dom0 용량 보장 아님 |
| 실제 serial 첫 EIO→STOP | entry는 handler 해제 전 STOP 없음→final은 해제 전 두 번째 실제 write·STOP 제출 성공 | 출력 fence·원 errno·원 cause 유지, 장치 ACK 아님 |

시리얼의 서로 다른 현재 원인은 기존 8192개 private submissions ring에도 보존한다.
failure_reason/출력 차단 의미를 바꾸지 않고 failure_cause의 type/errno/message를 추가했다.
예상된 OSError·부분 쓰기 SinkError는 full stack을 만들지 않는다. 내부 TypeError 등은
STOP 뒤 failure_traceback 문자열을 보존한다. 이 드문 원인 사본의 메모리/형식화는 추가한
진단 비용이며 없앤 비용으로 세지 않는다. live traceback/frame이나 새 영구 원장은 없다.
메시지/스택은 private 진단이고 새로운 MQTT/HTTP 응답 필드가 아니다.

Gateway Authority serialize/필수 fsync, create history 101개·restore fsck1, 현재 CPU
guard와 실제 vCPU 검증은 기존 비용/교차 시험을 같은 제품 후보에서 회귀했다. 이번 변경은
새 root 해시/fsck·로그용 Xen/Store 재관측·원장 재스캔을 추가하지 않는다. 새 optional journal
1.2의 저장된 입력/확정값 관계 검사는 순수 계산으로 추가되어 원장 수에 비례한다. request 간
cache나 default DB가 없고, 원천/작업 소유/정책·실행이 바뀌는 경계의 재확인은 남긴다.

### 수락 대응과 검증의 한계

아래 추적 ID는 새 공개 enum/티켓이 아니다. 신규 두 하위 시험과 Gateway 진단 시험을
기존 S3/S4/S5/P55/CPU/주행 시험에 연결한다. 같은 시험을 다른 이름으로 합산하지 않는다.

| ID | 이번 직접 assertion·기존 근거 |
|---|---|
| DE-T01/02 | `test_xenova_input_defaults.py`: 최소 Manager/CLI 존재 정보, 각 resource 독립 생략, 실제 Debian Manager/Ubuntu CLI 검증 복사와 Zephyr CLI bundle. invalid/null/False/IP는 catalog·예약·복사 전 거절, 원 unknown/범위 검증은 기존 ManagementTest |
| DE-T03/04/06 | defaults 시험의 partial/legacy replay·보존 bytes, cold process+template 부재+catalog 조회 실패 hook, 생략→명시 충돌, 저장 입력/확정값 mismatch RECOVERY_REQUIRED. 원 hash·신원·원 deadline 재발급 없음 |
| DE-T05 | 원 수락 write 뒤 EIO는 false, 같은 요청 정상 회수·copy1. 수락 전 slot barrier에서 defaults revision 변경+같은 key 경쟁: journal0→원 수락1, 패자 미수락, winner 최신 선택. 저장 미확정을 성공으로 치환하지 않음 |
| DE-T07 | plan/request/spec 무저장, 계획 숫자 명시 실행 vs 새 생략 신규 선택. 현재 구성/CPU 상속은 기존 CPU cross의 pin/resource/unpin·새 host·정책 회수 시험 재사용 |
| DE-T08/12 | lower `test_xenova_diagnostic_cost.py`: 내부 TypeError≠INVALID_SPEC, required write ENOSPC≠optional diagnostic warning. 기존 trace clock/collector 실패·atomic fsync/dir fsync, create child timeout/수습/상속 FD/lease·원 오류 보호 재사용 |
| DE-T09 | cap=0 event/span 상세 구성 0·summary 유지, INFO off Expensive.items 실패 hook이 실행되지 않음. 로그용 업무 관측 추가 없음 |
| DE-T10/11 | Gateway `test_diagnostic_cost.py`: 실제 bounded queue 포화·snapshot 불변·회복/worker 종료, real StreamHandler ENOSPC50의 별도 errno·재귀 stderr0, custom sink 실패·worker start 실패. offline100/internal100/서로 다른 errno·TypeError·KeyError·nested cause 구별, 희귀 full stack 보존 |
| DE-T13 | 실제 CLI JSON 파싱/exit·제어 stdout 격리, Gateway raw topic/body 제거·기존 MEASURE 소비 형식. 기존 TraceTest의 payload/argv/path 제외와 console 원 PTY·gap 시험 재사용. private 결함 stack을 제품 응답에 추가하지 않음 |
| DE-T14 | `test_same_key_default_change_error_burst_and_slow_sink_preserve_real_other_stop`: Debian copy barrier+default 변경+64 invalid+정지한 부가 sink, 같은 원 active replay 값 보존·원장1·B 실제 stop/결과 완료가 A 해제 전. 다른 긴 lifecycle/수습·두 Linux polling/CPU 교차는 기존 시험 회귀 |
| DE-T15 | C/Zephyr/native source/config 미변경으로 새 ARM/ELF 재빌드 해당 없음. 고정 SDK native worker9의 선행 근거는 source 불변으로 연결, 이번 coverage의 host core/PTY/계측 시험과 실보드 주기 총비용을 구분 |

하위 제품/시험은 `SEALED2_SOURCE.json`으로 `run_checks.py regression sealed2`를 실행했다.
시리얼 원인 보완 뒤 `RELEASE_SOURCE.json`으로 `gateway release`, `coverage release`,
`diagnostics release`를 실행했다. 두 후보 사이 변경은 Gateway verified_runner와 그 시험뿐이며
하위 모듈/시험·직접 의존 Gateway diagnostics/authority 등 바이트 일치를 확인했다. 최종 rc·실행
argv/시간·raw log와 합산하지 않은 범위는 `FINAL_CANDIDATE.json`에 모았다. 문서만 후속
편집한 내용은 최종 작업트리 manifest로 다시 대조했다. 수행하지 않은 보드/전원/실제
broker/서비스 조작은 fixture 시험으로 대체
완료한 것으로 표시하지 않는다.

하위 최종 회귀는 **790 passed**, Gateway 전체는 **171 passed**, diagnostics는 **18 passed**,
나머지 소유 영역 coverage는 **163 passed, 2 skipped**다. 겹치는 시험은 합산하지 않는다.
coverage의 root 전용 mountinfo 시험은 root Python에 pytest를 설치하지 않고 기존 unittest
한 건만 실제 root/소유 TemporaryDirectory에서 실행해 **1 passed**로 보완했다.
나머지 skip은 pinned Xen console source 미지정으로 실제 patched PTY 결합이 미수행인 것이다.
해당 제공물/담당·대체할 수 없는 assertion은 기존 7번 콘솔 수락에 남긴다. 실제 mount나
운영 broker를 조작하지 않았다.

중간 FINAL_SOURCE/FINAL2_SOURCE/SEALED2_SOURCE의 결과도 보존한다. 진입점 정리 예외·
optional 원장 원천 검사를 보완한 뒤, 마지막에 actor가 서로 다른 시리얼 실패를 공통 사유만
남기던 경계를 추가 보완했다. 첫 SerialSink 정보만 남기거나 느린 로그로 돌아가지 않고 각
제출 원인을 보존했다. 서로 다른 errno·내부 결함·부분 쓰기의 네 조합과 최초 STOP 순서를 같은 시험으로
확인한다. diagnostics의 Gateway 사례는 전체 171개에 포함된다. 이전 suite 시간/개수를
합산하지 않으며 변경 없는 하위 790개는 source 바이트 일치로 재사용한다.

**중간 실패도 보존:** old defaults-before의 두 positive assertion 실패는 옛 필수 입력/
CLI null의 재현이다. 첫 진단 before 실행은 Gateway pyproject의 pythonpath로 작업트리
소스가 섞여 해당 Gateway 결과를 전후 근거에서 제외했다. 독립 config/rootdir로 old
사본을 선택한 `diagnostics-before-isolated2-entry`는 네 중심 assertion이 예상대로 실패한다.
중간 slow-handler fixture가 Handler.release를 Event로 가린 결함을 resume으로 바로잡고
반례를 재실행했다. 처음 cost trace callback의 return 누락/activate 오타는 도구 결함이며
정정된 review-cost3만 사용한다. raw 기록을 덮어쓰지 않았다.

통합 중 예산 시험 하나는 entry에서도 실패했다. list의 관측 전후 registry2회는
ReadContext.validate의 대상 집합 보호였으므로 제품은 바꾸지 않았다.
`test_registry_cost_consumes_get_and_list_original_fifteen_second_budget`는 get1/list2회와
그 모든 비용이 최초 15초에 포함됨을 검사하도록 오래된 호출 횟수 기대만 정정했다.
공개 오류/기한·정상 진행 assertion을 완화하지 않았다. optional 원천 손상 시험은
`defaults-source-before-final2`에서 AttributeError가 재현됐다. 첫 sealed 후보에서는 Manager
검사보다 앞선 creation.validate의 metadata 접근이 남아 있어 같은 실패가 났다. 타입 검사를
공통 creation 검증의 접근 전으로 옮기고 Manager 중복 검사는 제거했다. final의 잘못된
원 입력 관계/원천 null/capabilities null은 모두 RECOVERY_REQUIRED이며 현재 catalog 조회 0이다.

### 계약·부분 포장·담당 인계와 미완료

하위 recipe Python33+비활성 예시1의 실제 설치 경로/모드/바이트·독립 import11을 확인했다.
`SUBSET_PACKAGE-SEALED2.json` tar SHA256은
`74b2c45c74f27b90ad75c07e7e6c8fda247f9318ed3ba0e2c2c9dfb06ac1f2b0`다.
recipe/RDEPENDS·원활성정책·고정 guest/native helper는 바꾸지 않았다.
Gateway는 16 Python+README/pyproject의 **소스 묶음**이며 새 diagnostics.py가 자동 package
검색 범위에 포함된다. 새 진단 모듈은 실제 stdlib import 통과. 완전 entrypoint 단독 import는
시험 host Python에 기존 선언 의존성 paho가 없어 미수행 범위로 남겼다(GATEWAY_IMPORT_FINAL2).
Gateway 시험은 기존 명시 dependency fixture를 사용한다. 새 패키지를 설치하거나 의존성을
추가하지 않았다. Gateway 소스 tar SHA256은
`eef2ae723af10b1113f03b40989c4ed02d687167e90541b5d62e0a86246e20bb`다
(`GATEWAY_SOURCE_PACKAGE_RELEASE.json`, 시리얼 원인·진입점 정리 보완과 현재 README 포함).
BitBake/RPM/wheel·정규 Gateway recipe/unit·이미지·실설치 완료가 아니다.

| 후속·담당 | 현재 제공물·구체 수락 |
|---|---|
| HTTP/Spring/UI D02·D08 | [현재 요청](../requests/20261029-request-http-spring-ui.md#defaults-errors): 필드 존재 보존, nullable/primitive DTO의 생략 손실 방지, 최초 body 저장, 계획/확정/현재/실측 표시. 상위는 현재 resource 필수이며 하위 생략 종단 제공 전. invalid body 원문 읽기/동기 warning·Java 중복 포장은 실제 비용/민감 정보/다른 stop 진행으로 담당 수락 |
| Worker D01/D03 | [현재 소비 요청](../requests/20261029-request-recovery-worker.md): optional journal1.2/원 request·initial_spec 구분, 실패 수습→예약 반환→현재 attempt/CPU guard 정상 진행. 상태/표본 결측에 create 기본값 적용 금지 |
| Linux/Agent D01-L01~L05 | [현재 요청](../requests/20261029-request-linux-agent.md): Reporter 반복 stack·sink 비용을 collector/transport/내부 결함별 계수. 최초 cause·표본 unavailable·필수 종료/회복 보존, 실측 없이 최적화 완료 선언 금지 |
| 배포·6/7 | Manager/creation/CLI/기존 writer 전체 같은 후보와 journal1.2·CPU2.0 호환을 함께 배포. 기존 실행자/helper drain·프로세스 교체·import/hash 대조. Gateway diagnostics.py의 logging.handlers/copy/queue/threading 등 고정 target stdlib closure, 기존 paho/serial 의존성·정규 recipe/unit·실제 backend/API restart는 공동 수락 |
| 운영/실보드 | create 시간 정책의 승인 값, 실제 Xen affinity·Linux 부하·SD/IRQ/콘솔/장치 출력·물리 정지는 기존 7번. 이번에 숫자나 정책을 결정/적용하지 않음 |

하위 로컬 제공은 생략 입력/원 결정 보존과 확인된 진단 낭비 수정까지다. 모든 예외/출력을
제거하거나 원인 분류를 축소한 작업이 아니다. 필요한 metadata 재확인·필수 fsync·희귀
cause/stack 비용은 남기며, 위 상위 소비/설치·실물 수락을 같은 완료로 세지 않는다.

<a id="create-compatibility"></a>
## 2026-11-01 create 호환 보완 — 원 확정값의 의미 비교

### 기존 호출과 새로 허용하는 교차

기존 완전 명시 사용법은 그대로다. UUID/이름/정확한 템플릿 ID·버전과 명시값을 제출하면
같은 생성 경로를 사용한다. 다음은 지원 Linux 프로필에서 같은 VM/key·나머지 명세라는 전제다.

```bash
xenova-vm create --vm-id "$VM_ID" --name example \
  --template-id "$TEMPLATE_ID" --template-version "$VERSION" \
  --vcpus 2 --memory-mib 768 --execute --json
# 응답 유실 뒤 아래처럼 두 자원을 생략해도 원 2/768로 같은 작업을 확인한다.
xenova-vm create --vm-id "$VM_ID" --name example \
  --template-id "$TEMPLATE_ID" --template-version "$VERSION" --execute --json
```

반대로 생략 수락→원 확정값 명시도 허용한다. 최초 명시 768이 당시 기본값 512와 달라도
원 768을 유지한다. 원 512에 768을 명시하면 여전히 충돌이고 null/bool/0/음수/문자열 등은
원 값으로 보충하지 않는다. 원 create key는 기존처럼 정규 UUID에서 정한다. UUID 변경은
별도 key/새 요청이지 임의 전달 key로 과거 VM과 합치는 행위가 아니다. IP·이름·템플릿 등은
기존 비교 의미를 유지한다. 응답 전체를 create 입력에 복사하지 않는다.

### 수정 전 반례와 최소 변경

착수 branch/HEAD는 `feat/platform/recovery-scenario-closure` /
**23858bc19753904c69ed5ce60463206ea3238a3c**다. 직전 작업의 미커밋 22파일을 보존했다.
원자료는 `C:/Users/SSAFY/Documents/Codex/2026-11-01/xenova-create-compatibility/`에 있다.
`ENTRY_SOURCE.json`은 그 작업트리이며 이전 작업의 FINAL_CANDIDATE 해시와 대조한다.
HEAD만으로 새 제품 후보를 식별하지 않고 READY_SOURCE + 현재 문서 해시를 사용한다.

수정 전 `compatibility-before-entry`의 정상 요청 assertion은 IDEMPOTENCY_CONFLICT로
실패했다. `_mutate`가 key의 유일한 원 기록을 얻은 뒤 `previous.request != spec`으로
존재 형태를 비교하던 결과다. 이 반례와 과거 형식 충돌 시험 결과는 지우지 않았다.

이번 제품 변경은 **xenova_management.py 한 파일**이다.

| 위치 | 바꾼 일·기준 | 유지한 경계 |
|---|---|---|
| `_same_create_request` | 이미 읽고 검증한 특정 원 operation의 initial_spec에서 두 자원만 가져옴. incoming의 생략만 보충하고 명시값은 원 값과 비교. 다른 필드는 원 request 그대로 비교 | 원/입력 dict를 변경하지 않는 작은 사본 두 개. 현재 catalog/config/관측 조회 0. 원 자원이나 지원 legacy request가 자기 모순이면 RECOVERY_REQUIRED |
| `_mutate`의 previous 분기 | create만 위 의미 비교로 바꾸고 action/다른 mutation 비교는 그대로. LinuxSlot 전후 재진입도 같은 분기를 사용 | 기존 완전 검색/중복 key 거절·짧은 수락 경계·owner/상속 FD·원 deadline·실패/수습/완료 경로 |
| `_operations`/creation/CLI/진단 | **제품 코드 미변경**. 원 1.2 request→당시 template→initial_spec 자기 검증과 explicit 입력 strict 검사 유지 | 새 body의 형태와 저장 원본의 무결성을 분리. source 손상을 새 기본값으로 채우지 않음. 새 실행기·저장 형식·전체 재검색·전역 잠금·fsync·payload 검증 추가 없음 |

동등성 판정은 실행 허가가 아니다. 활성 작업은 원 진행을 반환하며 partial 실패와 수습
완료는 실패 원인을 유지한다. 수습 완료의 내구 예약 반환도 기존 경로다. 완료 replay는
원 결과/현재 구성을 구분하고 현재 update-resources 값을 되돌리지 않는다.

### 저장·호출 호환을 구분한 수락

| 조합 | 제공/제한과 직접 확인 |
|---|---|
| 기존 완전 명시 CLI/Manager → 새 하위 | 새 옵션/버전 선택 없음. 실제 CLI 2-vCPU/768 최초 수락→생략 CLI로 같은 operation/원 값 회수. 기존 Manager/CLI 회귀 재실행 |
| 기존 완전 명시 HTTP 생산자 → 새 하위 | 변경 없는 CreateVmRequest/서비스/Driver/Provisioner/SerializedManager/HTTP app을 기존 FakeXen fixture로 실행. 새 필수 header/mode/endpoint 없음. 기존 integration 파일도 entry/ready 각각 13 passed |
| 생략 입력 → 새 하위 | 각 자원/둘 모두 독립 선택. 신규는 기존 정식 기본값, 수락 뒤는 원 값. 상위 DTO/화면의 optional 전송은 여전히 별도 확장 |
| 새 reader/writer → 지원 1.0/1.1/1.2 혼재 | 실제 Zephyr 완전 명시 1.0, Linux 완전 명시 1.1/생략 1.2를 한 저장소에서 만들고 모두 교차 replay. 원 byte/hash/출처 유지, 변환 0 |
| 옛 내부 writer → 모르는 새 형식 | 별도 설치 호환 문제. 같은 후보 모듈 집합·기존 실행자 drain/import 교체를 배포 담당자가 수락. 이를 완전 명시 HTTP caller의 부호환으로 설명하지 않음 |

일반 caller는 raw journal의 버전 분기/직접 해석/변환을 하지 않는다. 기존 최초 body 저장은
계속 사용할 수 있지만 그것만을 유일한 replay 방식으로 강요하지 않는다. 확인한 원 값 또는
생략을 전송하며 현재 화면 숫자/최신 기본값을 임의로 다시 넣지 않는다.

### 정상·충돌·중단·진행성 근거

`test_xenova_create_compatibility.py`와 기존 `test_xenova_input_defaults.py`의 합계는
23개다. 공통 시험을 OS 이름만 바꾸어 세 벌 만들지 않았다.

| 조건 | 실제 assertion |
|---|---|
| 자원 존재 4×4 교차 | vCPU/메모리 각각·둘 모두·부분 명시의 origin 4형태×replay 4형태. key 순서 반전도 같은 operation, 원 byte 동일·copy1·replay catalog0. 비기본 2/768 포함 |
| 실제 차이/무효값 | 다른 유효 resource·name·template ID/version은 IDEMPOTENCY_CONFLICT. IP key 제거도 기존 충돌 유지. null/True/False/0/음수/string/list/dict는 INVALID_SPEC·원 기록 불변 |
| 현재 구성 변경 | 원 1/512 create→정지 자원 변경 2/768→생략 또는 원 명시 replay. original initial_spec512와 current configuration768 분리, 원장 불변·현재값 rollback0. 현재768을 원 값으로 제출하면 충돌 |
| cold/원천 부재 | 실제 별도 Python process에서 생략→동일 명시 및 비기본 완전 명시→생략. template directory를 소유 fixture에서 옮기고 catalog 실패 hook을 둬도 원 결과·ID·hash·기한·bytes 유지 |
| 활성/후속 진행 | Ubuntu의 비기본 full 원 copy를 막은 중 omitted replay는 같은 RUNNING/원 값/기한. Debian의 omitted 원 copy+default 변경+invalid 폭주+느린 sink 중 full 동등 replay와 B 실제 stop 완료가 A 해제 전. 원 대상 journal1 |
| 수락 경쟁 | omitted A가 slot 획득 전 멈춘 동안 full 비기본 B가 먼저 수락/완료. A 재진입은 임시 default 대신 B의 원 값을 사용, 원 작업/예약/prepare1·추가 복사0. slot 보유·미수락 경쟁도 기존 의미 유지 |
| 실패/수습 | 실제 검증 복사 EIO→omitted 동등 replay는 원 실패. 정식 discard-private 뒤도 원 실패/동일 op/원 bytes를 유지하고 새 prepare0. 거짓 성공·자동 재생성0 |
| HTTP 응답 유실 교차 | 일부 생략 원 수락 파일 write 뒤 EIO, prepare0. 기존 HTTP가 만든 full1/256 요청은 같은 op로 등록/시작201, prepare1, 원 request/hash/creation/할당 유지. 이미 실행 중 같은 POST는 기존409, 응답은 기존 네 필드 |
| 저장 자기 검증/기존 보호 | 1.2 request/initial mismatch·source/capabilities null 거절, 기존 저장 EIO/child·owner/lease·기한·수습·양 Linux prehash0/root 검증 복사1을 관련 회귀에 연결 |

기존 입력 시험의 “동일 명시도 충돌”만 승인된 성공 assertion으로 바꿨고, 실제 다른 값의
충돌을 유지했다. 이 작업의 비교 함수 자체를 mock하지 않았다. HTTP 제품/테스트 소스도
바꾸지 않았다. 외부 Xen/guest/이미지 크기/실물 I/O는 기존 fixture 범위다.

기본 host Python만으로는 FastAPI가 없었지만 기존 `test-tools` 경로에 프로젝트 요구 범위의
실제 FastAPI/Pydantic/httpx가 있었다. 새 의존성을 설치하지 않았다. pytest importlib 모드가
저장소 `platform` namespace로 stdlib를 가려 Rich/HTTP import가 실패한 이력은 보존했다.
실제 stdlib platform 파일과 기본 pytest import 모드를 사용해 해결했으며 제품은 바꾸지 않았다.
Starlette/httpx deprecation warning은 남겼고 이를 없애려고 httpx2를 설치하지 않았다.
초기 fixture의 c.data 대신 실제 c.m.data, A journal 개수는 B를 포함한 전체2 대신 대상1로
정정했다. 보호/실행 assertion을 삭제하지 않았다.

### 작업량·부분 포장과 최종 후보

`review-cost-locks-entry-counts.json`/`review-cost-locks-ready-counts.json`은 같은 작은 실제 file/Manager/
fsync, fake Xen·preparer, 같은 계수 hook·Python 3.12.3에서 비교했다. explicit와 omitted를
각각 새 동등 fixture로 실행했으며 아래 계수는 전후/두 입력 모두 같다.

| 정상 경로 | text read / JSON parse / catalog describe / fsync / prepare |
|---|---|
| plan | 8 / 7 / 2 / 0 / 0 |
| 최초 수락·완료 | 33 / 23 / 4 / 23 / 1 |
| 완료 replay | 11 / 8 / 0 / 3 / 0 |

같은 trace에서 lock.wait/lock.hold 회차도 plan1/1·수락2/2·replay1/1로 전후 동일했다.
잠금 대기의 운영 시간 상한이나 미세 CPU 증가율을 이 단일 계수 실행으로 주장하지 않는다.

새 비교는 두 작은 dict의 일시 사본과 두 정수의 순수 비교 비용을 추가한다. 요청 밖 cache나
추가 보존 객체는 없다. 원장 스캔/잠금 회차·payload read/hash/copy·helper·필수 저장은 추가하지
않았다. 같은 성공 결과의 primitive를 비교했으며, 옛 형태 충돌의 빠른 실패와 새 정상 성공을
개선율로 비교하지 않는다. 물리 I/O·SD latency·운영 상한은 미측정이다. 기존 trace/MEASURE/
offline/시리얼 원인 보존은 source 불변과 동일 후보의 진단/Gateway 시험으로 확인한다.

recipe Python33+비활성 예시1, 설치 경로/모드/바이트·독립 import11을 대조했다.
`SUBSET_PACKAGE-READY.json` tar SHA256:
`8899bb3a5f3866bf0f44e695ef08527d61f487f439da5f1ed7b016c7fda480c7`.
새 모듈/runtime 의존성·recipe 변경·설치 버전 선택을 추가하지 않았다. BitBake/RPM/전체 이미지·
운영 설치가 아니다. READY_SOURCE의 제품/시험과 최종 문서 해시·작업별 delta·명령/rc·전후 비용은
`FINAL_CANDIDATE.json`/`TASK_DELTA.diff`에 모은다. 새 코드가 없는 Gateway 묶음을 재포장하거나
C/Zephyr/ARM을 형식적으로 재빌드하지 않는다.

최종 같은 후보의 통합 회귀는 **799 passed**, 중심 23개는 여기에 포함된다. 변경 없는
HTTP integration은 entry/ready 각각 **13 passed**, 진단 **18 passed**, Gateway 전체
**171 passed**다. 서로 겹치는 시험을 합산하지 않는다. 각 실행 argv/시간/rc와 원 log는
FINAL_CANDIDATE에 연결했다. 원본 오류 분류/진단 파일과 타 담당자 제품의 바이트 불변도
대조한다. 문서의 사라진 VM 가이드 링크는 실제 CLI·Manager 계약 경로로 정정했고,
표시 이름을 임의 생성한다고 적힌 과거 문장도 실제 unchanged HTTP가 전달하는 name에 맞췄다.

### 현재 담당 지시와 실제 미수행

[CLI·Manager](../platform/cli-manager-spec.md#cmd-create), [VM API](../contracts/vm-api-contract-v1.md#lower-create-compatibility),
[HTTP/Spring/UI](../requests/20261029-request-http-spring-ui.md#defaults-errors),
[관리 연동](../platform/management-integration-request.md), [Worker](../requests/20261029-request-recovery-worker.md)의
현재 본문을 개정했다. 이전 원 body의 존재 형태를 유일한 허용 방식으로 요구하지 않는다.
기존 D01/D02/D03/D08·CPU/예산·pending/표본 의미와 다른 미완료는 유지한다.

상위의 **선택적 생략 UI/DTO/serializer**, 실제 Spring 종단, 구형 내부 writer와 새 저장의 혼합
방지·모듈/process 교체, 운영 create 정책·실보드/동일 설치본 수락은 각각 미완료다.
하위 원 공개 응답을 읽는 Worker/HTTP에 raw journal parser를 새로 요구하지 않는다.
보드·운영 VM/CPU·실데이터·서비스·배포·commit/push·6/7번은 변경/실행하지 않았다.


<a id="stage6"></a>
## 2026-11-01 6번 — 현재 담당 지시와 플랫폼 배포 준비

### 후보와 직접 마감 범위

착수 branch는 `feat/platform/recovery-scenario-closure`, HEAD는 `8e3232a43045966d5e29bee31ae29c2a716704bb`다.
착수 작업트리는 clean이었다. `2e48552` 진단, `f9a5720` 기본값/의미 비교, `8e3232a` 보고/인계의 바이트를 보존했다.
이번 후보는 이 HEAD + 포장·문서·새 시험 diff와 `FINAL_CANDIDATE.json`의 파일 hash로 식별한다.
운영 자산·서비스·설정·데이터·보드·배포·commit/push·7번은 변경/실행하지 않았다.

**6-A 인계 준비 완료**, **6-B 소유 포장 보완·고정 환경 로컬 배포 준비 완료**로 판정한다.
이는 미정 계약 결정·담당 제품 구현·전체 이미지/실제 설치·종단 수락을 포함하지 않는다.
운영 create 정책·차량 설정·Agent 제공물은 해당 기능의 운영 준비 대기로 남는다.

### 6-A — 무엇을 어떤 기준으로 바꿨는가

| 원문·변경 위치 | 확인한 문제와 실제 개정 | 유지한 근거·완료 범위 |
|---|---|---|
| [HTTP·Spring·화면](../requests/20261029-request-http-spring-ui.md) | 기본 관리/원 결과/stop·동시성/optional 입력/CPU/진단의 수정 위치·이유·정상/실패 수락을 현재 본문에 모았다. 차량 입력/표시는 별도 절, 현재 선행조건과 담당 회신을 명시 | key stop·원 key 조회는 기존 제공으로 보존. 실제 라우터는 `/vms/{vm_id}/control-operations?request_key=...`. full HTTP 201·이미 실행 중 POST 409와 하위 동등 replay는 별개 |
| [Worker](../requests/20261029-request-recovery-worker.md) | D01-W01~W05·D03-W01~W04별 observe/Engine/store/rejoin의 실제 기호, 관측 오류·원 attempt 회수·안정 구간/예산·제공물과 정책 대기를 연결 | warm-drive 최소 소비의 로컬 완료는 보존. raw journal parser·자동 수습/새 key/기한·SQLite/영속 이관·Worker–Gateway 원격 API를 만들지 않음 |
| [Linux·Agent](../requests/20261029-request-linux-agent.md) | L01~L05의 계측·실제 앱 진행·전달·로컬 감독·설치 제공물을 나눴다. 실제 reporter 예외 경계·측정/회복 assertion과 후보/hash/entrypoint/의존성/unit/권한/일반 생성·seed별 smoke 회신을 요청 | host VM/CPU/backend는 플랫폼 책임. Agent 설정 기본값을 계측 결측에 적용하지 않으며 제품 코드는 미변경 |
| [Gateway](../requests/20261029-request-gateway.md) | 직접 소유와 입력/Worker/장치 공동 연결을 구별하고 기존 DEC/ARCH·D03-G ID, 포장·전송 결정·수락/회신을 현재화 | stateless 준비/권위와 stateful 전송/물리 적용은 별개. 미측정 ERROR/CRITICAL·Zephyr 비용을 새 완료로 바꾸지 않음 |
| [관리 연동 상세](../platform/management-integration-request.md), [상세 Worker](../requests/2026-10-27-recovery-health-ctrl.md), [상세 Linux](../requests/2026-10-27-linux-health-agent.md), [색인](../requests/README.md) | 현재 지시의 시작점을 하나로 연결했다. 구식 HTTP 미구현·형태 동일성·과거 대기는 현재 지시로 읽히지 않게 분리 | 기존 원문/ID/실패/측정은 이력으로 보존. 현재에 옮긴 anchor는 유지하고 중복 역사 anchor만 history 접두사로 구분 |

기존 full 호출은 그대로 지원하고 optional DTO/serializer는 별도 확장이다.
두 자원 생략↔원 확정값 명시를 양방향 허용하며 최초 비기본 명시도 같다. null/무효값·실제 차이는 여전히 거절한다.
원 initial_spec과 현재 구성/카탈로그를 바꿔 쓰지 않고 원 기록/hash/신원/기한을 재작성하지 않는다.
일반 호출자의 journal 버전 선택과 raw 해석 요구는 없다. 내부 writer cohort의 교체와 외부 호출 호환은 분리했다.

요청한 `im not ai` 스킬은 로컬 skill catalog·파일 검색에서 찾지 못했다.
대체 [humanize-korean SKILL.md](C:/Users/SSAFY/.codex/skills/humanize-korean/SKILL.md)를 네 현재 본문에 적용했다.
기술 내용 개정 뒤 윤문을 별도로 수행했으며, 자동 low/light 판정·독립 monolith·공통 gate를 거쳤다.
HTTP/Worker/Linux/Gateway 변경률은 각각 **0.03%/0.03%/0.02%/0%**, 모두 gate rc=0·자체검증 6/6·등급 B다.
확인된 연결어미 뒤 쉼표만 삭제했고 이미 양호한 본문에는 추가 수사를 넣지 않았다. 과거 이력은 윤문 대상에서 제외했다.
게이트 원본·입력/출력·summary와 경로는 원자료에 보존한다.

### 6-B — 포장 변경과 판단 기준

기존 배포 상세 원본이 부팅 설명·요청서·부분 tar 근거에 흩어져 있어
[배포 준비](../platform/deployment-readiness.md)를 한 곳에 만들고 안내·부팅 문서에서 연결했다.
여기에 source→recipe→설치 경로→로드 프로세스, 의존성, 설정 공급/권한, unit/scope,
교체/drain/복구·공동 결정·7번 입력표를 모았다. 별도 운영 updater나 runtime 검증 프레임워크는 추가하지 않았다.

| 실제 변경 | 이유·채택 기준 | 검증·남은 조건 |
|---|---|---|
| `recipes-core/xenova-gateway/xenova-gateway_0.1.0.bb` | 정규 Gateway recipe 부재를 확인. 기존 entrypoint와 runtime Python만 site-packages에 설치, 공용 MQTT·하위 플랫폼·실제 고정 Paho/pyserial·표준 분할 의존성 선언 | 실제 RPM 생성·package QA·pkgdata·payload/target import 확인. 개발 PYTHONPATH·시험 fixture를 제품 의존성으로 쓰지 않음 |
| `files/xenova-gateway.service` | 기존 책임을 포장에 연결. root/UMask=0077·필수 env·기존 `python3 -m`·Restart=no·기본 disable | mount Requires, first-boot/broker/autostart After, admission Before. 초기 초안의 admission 뒤+shutdown 앞 순환 가능성을 발견해 수정했고 최종 loader로 정상/주입 순환을 검증 |
| `files/gateway.env.example` | 선택 profile·실제 UUID·broker credential·device·차량 한계 공급 위치를 비활성 예시로 제시 | `/usr/share`에만 설치. 활성 `/etc/xenova/gateway.env`·Authority·재무장·차량 시간/한계는 생성하지 않음 |
| bringup image의 `IMAGE_INSTALL` | 정규 패키지 포함을 실제 이미지 선택 경로에 연결 | 패키지 포함과 enable/출력 허가를 분리. 전체 이미지/rootfs 빌드·SD 적용은 미실행 |
| Gateway README·pyproject 설명 | 옛 `/opt` 수동 경로와 새 정규 site-packages, 배포판 serial 명칭과 Yocto `python3-pyserial`, 아직 남은 설치 수락을 구분 | runtime 의존성 버전을 올리지 않았고 Gateway 제품 `.py`는 불변 |
| `test_xenova_gateway_packaging.py` | 실제 recipe do_install을 fixture에서 실행해 파일·모드/활성 설정 부재·초기화 금지·기존 unit과의 ordering 회귀를 확인 | 새 3개 assertion 묶음 통과. source 문자열 검색만으로 package 성공을 대신하지 않음 |

기존 xenova-platform·xenova-mqtt-client recipe는 필요한 최신 하위 파일을 포함해 불필요한 분해를 하지 않았다.
실제 private Python은 **32개**, 확장자 없는 Python verifier/CLI·wrapper/native는 별도 경로다.
과거 부분 묶음의 “33 Python”은 verifier를 포함한 수치여서 현재 private 모듈 개수의 목표로 사용하지 않았다.
실제 RPM private 32개와 Gateway/MQTT 소스의 payload 바이트·0644 모드를 대조했다.
creation-policy의 null example은 여전히 비활성이고 운영 승인값을 공급하지 않았다.
기존 UUID/execution scope·최종 guard·host drain·상속 FD 보호를 그대로 사용하며 API unit의 KillMode를 완화하지 않았다.

### 고정 환경의 실행 결과와 한계

원자료 위치는 `C:/Users/SSAFY/Documents/Codex/2026-11-01/xenova-stage6/`다.
착수 `ENTRY_SOURCE.json`, 최종 `FINAL_CANDIDATE.json`, `TASK_DIFF.patch`,
`PACKAGES.json`, `LOCAL_CHECKS.json`, `UNIT_CHECKS.json`, `HUMANIZE_GATES.json`에 후보·명령·rc·원 로그를 연결한다.
WSL 후보는 `/home/ssafy/xenova-stage6-20261101/source`, 기존 layer·cache를 사용한 별도 build다.
6개 layer commit은 현재 layers.lock과 일치한다. 네트워크를 막은 `BB_NO_NETWORK=1`로 실행했다.

- `bitbake xenova-platform xenova-mqtt-client xenova-gateway`의 설치·RPM·QA가 통과했고, 최종 unit을 반영한 runtime provider 부분 빌드도 통과했다. 최종 명령·대상 목록은 `build-runtime.sh`/`runtime-providers.json`, 원 로그는 `bitbake-final.log`다. 전체 이미지 빌드가 아니다.
- 실제 target은 Python **3.14.7**, Paho **2.1.0**, pyserial **3.5**다. pkgdata에서 `python3-logging`의 handlers/pickle/io와 `python3-threading`의 queue, ctypes/libffi 등 의존성을 대조했다.
- **39 RPM의 Python 의존성/플랫폼 payload**를 별도 root에 추출하고 기존 QEMU로 실제 ARM Python을 실행했다. Gateway/MQTT 하위 모듈 25개와 private 하위 32개, 합계 **57 모듈 import** 통과. TCP connect/bind audit 차단을 두었고 broker/serial·Authority 동작은 실행하지 않았다. 전체 이미지·Xen 도구·kernel 서비스 실행 성공을 뜻하지 않는다.
- 기존 고정 ARM **systemd 259.5** loader에서 실제 unit을 읽는 정적 검증은 정상 rc=0, admission 순환 주입은 rc=1·cycle 검출이었다. 기반 unit/실행 파일 일부는 기존 unit fixture이며 서비스나 장치를 실행하지 않았다.
- 같은 현재 소스의 포장/부팅·배포 payload/기본값/의미 비교 영향 회귀는 **60 passed**. 새 3개와 create 23개가 여기에 포함된다. 기존 799/171 결과를 다시 합산하지 않는다. 실제 unchanged HTTP 어댑터 assertion도 이 60개에 포함된다.
- 기존 하위·Gateway 제품 소스와 타 담당자 제품 바이트가 착수 manifest와 일치한다. 그래서 무관한 전체 C/Zephyr/ARM 앱 빌드·회귀는 반복하지 않았다. 새 recipe는 실제 ARM target 의존성/import로 검증했다.

초기 검증 실패도 구분해 보존한다. Windows에서 만든 검증용 셸의 CRLF 때문에 `set -e`/경로가 깨져 잘못된 작업 디렉터리에서 BitBake socket 생성이 실패했다.
시험 셸을 LF로 고쳤고 그 시도에서 생긴 lock/log는 근거 폴더로 옮겼다. 기존 사용자 lock이나 운영 원장은 건드리지 않았다.
초기 raw cpio 추출은 고정 RPM 형식과 맞지 않아 기존 libarchive 도구로 바꿨고 native sysroot 라이브러리를 지정했다.
시험 socket 대역이 SSL 상속을 훼손한 경우는 connect/bind audit hook으로 고쳤다.
Python closure의 ctypes/libffi 누락은 실제 하위 RDEPENDS를 fixture에 포함해 해결했다. 이 네 가지는 제품 입력/예외 결함으로 보고하지 않는다.
WSL VHDX·host I/O accounting 경고와 기존 Starlette/httpx deprecation은 보존했고 이를 없애려고 도구/의존성을 업그레이드하지 않았다.

### 공동 결정과 다음 수락

[공동 결정표](../platform/deployment-readiness.md#decisions)는 Worker–Gateway 전송·Agent 서비스 wire·운영 생성 시간값·명시 수습 HTTP·실제 차량 한계/장치 피드백의 담당과 대기 기능을 분리한다.
새 topic/route·운영 숫자를 만들지 않았다. 담당자 제품 코드는 미변경이며 요청서를 실제 전달/회신받거나 구현 완료로 표시하지 않는다.

[7번 최소 입력표](../platform/deployment-readiness.md#stage7)에 CLI/보드 FastAPI와 EC2/Spring/화면의 시작점,
OS별 정상 관리·입력 호환·동시 stop·원 결과/수습·CPU·Worker/Agent/Gateway·실제 교체·선택 차량의 입력/관측·실패·정리 owner를 적었다.
실제 API backend/vif 수명·서비스 교체와 같은 후보 설치·전체 rootfs·실보드/장치·optional UI/DTO/제품 종단은 미수락이다.
소스 원복과 저장 데이터 복구, 구형 writer downgrade도 같은 행동으로 취급하지 않는다.


<a id="stage6-documents"></a>
### 6번에서 수정한 문서 전체

| 용도 | 문서 |
|---|---|
| 현재 담당 작업 | [HTTP·Spring·화면](../requests/20261029-request-http-spring-ui.md), [Worker](../requests/20261029-request-recovery-worker.md), [Linux·Agent](../requests/20261029-request-linux-agent.md), [Gateway](../requests/20261029-request-gateway.md) |
| 기존 상세·진입 색인 | [관리 연동 상세](../platform/management-integration-request.md), [상세 Worker](../requests/2026-10-27-recovery-health-ctrl.md), [상세 Linux](../requests/2026-10-27-linux-health-agent.md), [요청 색인·회신 양식](../requests/README.md) |
| 배포 원본·안내 | 새 [배포 준비](../platform/deployment-readiness.md), [플랫폼 안내](../platform/README.md), [부팅·종료](../platform/management-boot-boundaries.md), [Gateway README](../../platform/services/gateway/README.md) |
| 근거·적용 상태 | 이 보고서의 6번 절, [기능별 기준선](platform-management-baseline.md), [개념 문서 마지막 적용 장](../platform/conceptual-architecture.md#s12) |

문서 15개(신규 배포 원본 1개 포함)를 수정했다. 정식 create 의미·오류/wire schema는 바꾸지 않았다.
별도 코드/메타데이터 변경은 Gateway recipe/unit/example, 이미지 포함 연결, pyproject 설명, 포장 시험에 한정했다.

<a id="gateway-provider"></a>
## 2026-11-01 — Gateway 제공자 연결 G1/G2/G3

### 실제 제공 상태와 후보

착수는 `8e3232a43045966d5e29bee31ae29c2a716704bb` + 보존한 6번 diff/새 파일이다.
최종 후보는 HEAD에 `FINAL_CANDIDATE.json`의 파일 hash·`CANDIDATE.diff`·새 파일을 합쳐 식별한다.
원자료는 `C:/Users/SSAFY/Documents/Codex/2026-11-01/xenova-gateway-provider/`, 격리 source/build는
`/home/ssafy/xenova-gateway-provider-20261101/`에 있다. 최초 파일 hash는 `ENTRY_SOURCE.json`이다.
6번 결과를 되돌리지 않았고 commit/push·운영 배포·보드·서비스·실데이터·7번은 실행하지 않았다.

| 구분 | 확인한 것 / 이번 제공 / 남은 수락 |
|---|---|
| 이미 제공 | warm actor·planning·단일 Authority·원 전환·내부 submit_transition, 출력 guard·진단 queue·6번 recipe/unit |
| 이번 G1 | 별도 process용 실제 UDS/client/CLI, submit/get/commit/cancel/finish, 원 결과·지속 exclusion·정상 종결 |
| 이번 G2 | 완전 ManeuverIntent Python/C codec·구체 적용 adapter·기존 준비/전환 연결, 실제 MQTT의 테스트 stateful adapter |
| 이번 G3 | 명시 feedback-1·실제 serial RX/TX/codec·원 명령/session/만료·가상 ESP32의 실제 PTY 왕복 |
| 상대 제품 | Worker Engine/rejoin의 소비, 실제 planner 상태 생산·기하/동역학 검증·계산, ESP32 firmware·센서 생산 |
| 실물/결정 | 승인 ACK 기한·차량 한계, 실제 물리 정지·watchdog/E-stop, Worker 설치본·보드/차량 연속성 |

### 이제 사용하는 방법

`from xenova_gateway.client import GatewayClient, GatewayRejected, GatewayUnavailable`로 import한다.
기본 endpoint는 `/run/xenova-gateway/control.sock`, 통신 budget은 1초다. 원 전환 기한과 다르다.
같은 client를 사용하는 CLI는 `python3 -m xenova_gateway.client`다.
설치 예제 `python3 /usr/share/xenova/gateway-examples/provider_roundtrip.py`는 별도 Gateway 자식과 실제 UDS/Authority로
예비 B 보호→동등 replay→실제 intent 차이 충돌→원 조회→취소를 실행하고 소유 임시 자산을 정리한다.
Xen·앱·출력만 fixture다. 실제 Worker/VM 복구를 수행한 예제가 아니다.
정확한 함수·wire·제한·오류·결과 읽기는 [제공자 계약](../contracts/gateway-provider-v1.md)을 따른다.

### 무엇을 어떤 기준으로 바꿨는가

| 위치 | 수정 전·이유 | 구현·채택 기준 | 유지한 보호·추가 비용 |
|---|---|---|---|
| ipc_contract.py·ipc.py·client.py | 별도 Worker가 내부 queue를 부를 수 없었음 | bounded framing·selector·실제 peer/DAC·partial I/O·총 budget | Authority는 actor만 변경. 메시지8192 bytes·연결16·mailbox4/4/8. 연결·decode/encode 추가 |
| provider.py·Authority/verified | 조회 직후 재선택 경합과 원 보호 종결 경로 필요 | 기존 Authority에 보호 최대16개, 실행 exclusion→새 binding finish/원 실행 cancel | 새 journal/DB/daemon 없음. 원 key/spec/기한·현재 host/대상·승인 유지. 수락/종결 각각 save1 |
| planning.py·warm.py·state_transfer.py | stateless 준비만 전환에 연결됐음 | 수락 완전 사본·현재 대상 적용/lifecycle을 기존 준비에 채택. rebuilt는 stop_then_resume | 정상 A 유지, 적용≠계산≠출력. 사본8·적용4·수신4, 표본별 fsync0 |
| 공용 handoff.py·xm_state_*·C adapter | 구체적인 생산/적용 codec·훅 필요 | merge segment/current context 확인 뒤 불변 preparation에 연결 | planner 구현 없음. 기본 C stateless 유지. copy568 bytes·ARM 적용 훅 직접 stack608 bytes |
| feedback.py·output_owner.py·runner | write 성공만으로 장치 단계를 알 수 없었음 | 명시 profile·session/u64 sequence·legacy generation/counter 대응·센서 capability/null | write-only bytes 동일. serial owner1·pending32/events64/RX4096. 승인 기한 필수, RX/codec·참조 RAM 추가 |
| runner 원 결과 view | 현재 관측과 원 작업 당시 단계 구별 필요 | 원 기한 안의 정확한 대상 앱/장치 단계만 원 기록에 최초 채택 | 현재/original 분리. 단계별 추가 save 필요, lookup에는 없음. 원 generation 재발급0 |
| main 종료 | owner가 살아 있는데 lease 반환 시 다른 Gateway가 열 수 있음 | owner/IPC thread 종료 미확정이면 lease 유지, 실제 종료 뒤 후속 실행 정상 획득 | daemon thread를 성공 종료로 보지 않음. 실제 blocked write는 기반 한계 |
| strict 수신 | 크기 한도 안의 깊은 JSON도 RecursionError 가능 | IPC/MQTT 개별 nesting 오류 거절과 다음 정상 입력 진행 | 내부 결함 전부를 입력 오류로 변환하지 않음. 원인/코드·stack 보존 |

활성 보호는 RAM 출력 철회→STOP 시도→필수 저장이다. 예비 대상은 정상 Active의 STOP·새 generation 없이 배제한다.
장치 만료는 같은 단일 device owner가 처리하므로 actor fsync/진단 대기 중에도 STOP을 시도한다.
실제 write가 멈춘 장치의 물리 정지를 보장하지 않으며 live owner를 다른 writer로 교체하지 않는다.
held·현재 host/target이 맞아도 Manager의 현재 intent/execution/소유·force guard는 계속 필요하다.
lookup/replay는 새 효과·기한·승인 발급이 아니다.

### 로컬 수락과 대역 범위

실행 도구는 원자료의 `run_gateway.py LABEL [pytest paths]`, `c_checks.py`, `arm_compare.py`, `final_build.py`,
`verify_packages.py`, `client_package.py`, `target_socket.py`, `peer_credentials.py`, `unit_check.py`다.
실제 argv·환경·stdout/stderr·후보 hash는 각 JSON/log에 남겼다. 새 의존성 설치·운영 broker 사용은 없다.

| 수락 | 직접 assertion·실행 경계 |
|---|---|
| C01~C05 | 별도 Gateway/CLI process·UDS/actor/Authority, 정상 A 유지·B 인계/준비 실패, active/standby 보호, 동등 생략/명시 동시 원 기록1, 실제 실행/intent 차이 충돌, 응답 유실 뒤 get |
| C06~C08 | 실제 UDS 수락/커밋 뒤 소유 자식 SIGKILL·stale socket 회수·cold replay, 교체 전/후 EIO 미확정, cancel/commit 경쟁·rollback0, 새 binding finish→같은 B 정상 인계, stale 완료 거절 |
| C09~C10 | partial/붙은/과대/중복 key/NaN/deep JSON, 응답 비소비·포화 뒤 회복, high-water<=16·FD/thread 반환, 실제 root 서버의 UID65534 거절 뒤 root 회복, 다른 파일/활성 lease·미종료 owner lease 보존 |
| C11~C12 | StateReceiver+Gateway·C 실제 적용, 고정 ARM Mosquitto2.1.2/Paho loopback MQTT, 당시 수락 사본 유지·늦은 갱신/부분/혼합/retained/새 instance 거절, 재구축/연속 승계 분리 |
| C13~C14 | 실제 pyserial/OutputOwner/PTY·가상 ESP32, CRC/overflow 재동기, 수락≠적용≠stationary·센서 부재null, 옛 ACK/session·reboot·u8 wrap/u64 sequence·만료 뒤 정상 STOP, 느린 critical handler/필수 저장 중 만료 STOP |
| C15 | RPM/QA·설치 bytes/0644·inactive example/preset, client-only ARM import, ARM client→설치 Gateway Python UDS 왕복, 고정 systemd259.5 정상/주입 cycle 거절 |
| C16 | create 의미 비교·기본값/원 replay·변경 없는 HTTP full adapter·CPU pin/unpin/교차·lifecycle/firmware/usage/DHCP 진행성과 포장 영향124개, 공용 MQTT149개, native3개 |

Gateway 최종 전체220개는 `gateway-final-all.json`, 중간 결과218개·focused29개는 각각 `gateway-final.json`·`ipc-sigkill.json`이다. focused를 전체에 중복 합산하지 않는다.
FastAPI/httpx 경고는 기존 테스트 도구의 deprecation이며 Spring/화면 optional DTO·실제 Worker·보드는 미수행이다.
G2의 MQTT·binding guard·Gateway/Authority는 제품 코드, binding/Xen과 상태 계산기는 fixture다.
C helper는 실제 preparation/적용을 실행하지만 실제 planner·kernel stateful worker 완성 시험이 아니다.
G3의 가상 stationary=true도 contract 입력이며 실제 모터·제동 관측이 아니다.

### 비용·자원과 유지한 저장

원 보호 신규 수락의 실제 atomic_json은1회다. 동등 replay8회+lookup40회에 추가 save0이며
조회 때문에 Xen/Store·전체 디스크·원장 재검색을 호출하지 않는다. 40개 wall/actor 시간·tracemalloc 원 표본은 `IPC_COST.json`에 있다.
hook·tracemalloc·Lab selector 대기가 포함된 로컬 값이다. 운영 상한/p99나 개선율로 사용하지 않는다.
원 app/device 단계는 최초 확인 때만 추가 저장한다. file/parent 동기화·저장 미확정 차단은 유지한다.
IPC가 없던 기준선과 신규 IPC의 지연을 성능 개선율로 비교하지 않는다. warm 실행에 I/O thread1·device owner1이 추가됐다.
입력/응답 각각8192 bytes·연결16·mailbox16이며 포화의 무거절을 보장하지 않는다. 24개 slow/partial/nonreading 연결 뒤 정상 회복과 FD/thread 반환을 확인했다.
G2 상태 type만 중복 key/nonfinite 검증을 위해 작은 JSON을 추가 해석한다. drive/heartbeat의 파싱 횟수는 유지한다.
PUBACK/수신으로 progress를 올리지 않으며 G3 ACK 표본마다 fsync하지 않는다.

고정 Zephyr `684c9e8f32e4373a21098559f748f06915f950c9`/SDK로 warm-operations의 착수/최종 source를 따로 빌드했다.
착수 C 네 파일은 Git blob과 ENTRY_SOURCE hash 일치를 먼저 확인했다.

| ELF 후보 | text | data | bss |
|---|---:|---:|---:|
| 착수 | 414902 | 5880 | 659446 |
| 최종 | 415075 | 5704 | 659446 |

기본 stateless 구성의 정적 크기이며 stateful worker 동적 비용·차량 성능은 아니다.
추가 적용 훅의 ARM object는 text2686/data0/bss0, 직접 stack608 bytes다. native 구조체는 snapshot248/application192/copy568 bytes다.
시험 main stack7360 bytes를 제품 worker 요구량으로 바꾸어 쓰지 않는다. 실제 adapter 연결 때 호출 stack·불변 사본 수명은 해당 worker에서 수락해야 한다.
ELF/map/설정/stack·firmware hash는 `ARM_COMPARISON.json`과 각 build receipt에 보존했다.

### 포장·설치 수준

고정 Yocto6.0.4/레이어·로컬 sstate·BB_NO_NETWORK에서 Gateway/MQTT/platform의 install/QA/RPM을 생성했다.
기존 recipe의 client subpackage와 main/common 의존성을 사용한다. 새 중계 daemon·라이브러리 버전을 도입하지 않았다.
RPM40개 의존성 root에서 ARM Python3.14.7의65개 import와 lower32개 모듈 bytes/모드를 대조했다.
client와 선언 표준 런타임만 추출한 root에서도 서버/Paho/xenova_mqtt 부재 상태의 import가 통과했다.
ARM client→설치 Gateway 코드(호스트 Python)의 실제 WSL UDS로 submit/get/cancel을 확인했다. ARM 보드 서버·실제 service 구동 수락과 다르다.
unit은 RuntimeDirectory0700·root/UMask0077·disable이며 활성 env·운영 기한·Authority 초기화·재무장·설치는 수행하지 않았다.

### 실패 기록과 다음 수락

최초 pytest/cmake 탐색, glibc RPM 미복원, cold child PYTHONPATH/HTTP 경로 누락·systemd255 선택은 harness/environment 오류다.
기존 고정 도구·pkgdata·경로를 공급해 수정했고 잘못 지정한 테스트 파일명까지 원 로그에 남겼다.
실제 broker 예제의 callback 자기 PUBACK 대기는 비대기 publish_tracked로, 예제의 keyword-only cancel 호출은 실행 시험으로 찾아 수정했다.
legacy Sink Mock의 가짜 thread 속성은 close-only spec으로 맞췄고 최초 cause·정리 순서 assertion은 보존했다.
기존 stateful 일괄 미지원 assertion은 이번 승인된 기능에 맞춰 준비 가능·완전 적용 근거 없는 commit 불가로 교체했다.
새 lease/중첩 JSON 결함은 제품 방어와 정상 회복 시험으로 닫았다. 오류 종류·희귀 cause/stack·필수 기록을 축소하지 않았다.

Worker 현재 본문에 실제 client/import·원 요청·실패 처리와 Engine _task_attempt/_task_replay/_on_result,
RejoinCoordinator.completed/pump·finish/cancel 연결을 지정했다. 과거 전송 미정/내부 queue 문장은 이력이다.
Worker 장애 판단·attempt 예산·사용자 stop·Manager guard·재합류 정책은 제품 담당이며 제품 소스는 미변경이다.
Gateway README/직접 요청·배포 원본과 기존 ESP32 요청, MQTT drive-state-1·serial 고정 vector를 함께 갱신했다. 중복 요청서는 만들지 않았다.
Worker 새 본문은 humanize-korean light 게이트·자체검증6/6·변경률0.02%를 통과했다. 기술 내용은 별도 소스/시험으로 확인했다.

G1은 실제 호출·원 회수·지속 보호/정상 종결·권한·포화·재시작·client 포장의 하위 로컬 제공이다.
G2/G3는 구체 codec/적용/parser/I/O와 실제 통로의 제공자 로컬 수락이며 실제 planner·ESP32·물리 정지 완료는 아니다.
다음은 Worker 소비→실제 앱 adapter→ESP32 생산/capability/vector·승인 차량값→같은 설치본의7번 공동 수락이다.
D03-G02 백업 최신성, Agent L05/템플릿 후보, 상위 API/화면·운영 create 시간 정책의 기존 미완료는 그대로다.

<a id="history-driving-app"></a>
## Gateway 이력 수명과 실제 주행앱 — 2026-11-01 후속

착수 후보는 platform `cdcb61977ceca630dd9862b7cecd3bf04bf29e1c`다.
MR !314 병합과 원격 feature 삭제를 확인한 뒤 이전 로컬 feature만 정리하고
`feat/platform/gateway-history-driving-app`을 생성했다. 이후 commit/push·운영 상태는 변경하지 않는다.
첨부 구현 지시 SHA-256은 `24a1e5ca61e0d6031e94a35d17aa7c435bae54ecb3815108c95768527bb13c1a`다.
원자료는 PC `C:/Users/SSAFY/Documents/Codex/2026-11-01/xenova-history-drive`에 둔다.

| 설계/수락 | 착수 실제 경로·상태 | 이번 구현/확인 | 외부 경계 |
|---|---|---|---|
| A/H01~11 | `provider.py`·`authority.py`: 보호16/전환32·64KiB, 종결 정리 없음 | 시간 포함 key·고정 보존·내구 수락 시 종결 수거·증거 공간 예약 | 운영 retry/retention 승인 |
| B0/ARCH-T01~09·36 | 기존 v1/v2·G2 codec/runtime 있음. WorldSnapshot protobuf·Pi 공통 odom은 미연결 | 정식 schema·고정 decoder/pool·좌표/시간/odom | Jetson mapper·MCU 센서 생산 |
| B1/ARCH-T13~15·37 | `adapter.c:automatic`→`auto_step`: 차선편차/전방거리 계산, lattice/RPP 아님 | 실제 bounded primitive 탐색·차체/최종 명령 검사·tracker | 승인 차량 제원/실측 |
| B2/ARCH-T10~12·16~23·31~35·38 | 실제 독립 worker·join/취소·manual-session·G2 준비/상태 통로 있음. 실제 path 상태는 착수 때 없음 | 실제 firmware 두 개의 path 생산/적용·MANUAL 독립·양방향 재합류를 아래 최종 후보로 확인 | Worker 제품/신뢰 binding·실장치 |
| C/ARCH-T28~30·34 | 고정 ARM/Yocto/client package 경로 있음 | 최종 ARM 링크·정규 RPM/QA·격리 설치·실제 socket 대조. 상세 범위는 최신 종결표 | 운영 설치·7번·차량 |

### A — 채택한 수명 모델과 변경 기준

사용자가 시간 포함 key와 기존 요청 호환을 승인했다. `ipc_contract.new_request_id`는
host/단조 발급시각/UUID를 결합한다. 임의 UUID만으로 무기한 정확한 중복 판별을 약속하지 않는다.
기존 원 기록 자기 검증·동등 spec 비교·원 신원/기한은 유지하고 신규 수락에만 현재 기간/fence를 적용한다.

`history.py:candidate/admission/stamp/room`은 정책·종결/참조·원 기한·고정 보존과 최대 증거 공간을 확인한다.
긴 별도 정리나 daemon 대신 기존 actor의 단일 수락 저장에 수거/fence를 결합했다.
활성 exclusion·준비·pending 전환 참조와 저장 미확정은 수거하지 않는다.
file/parent fsync 성공 전 RAM 교체·공간 반환은 없다. 같은 generation의 예비 VM 보호/취소 반복은 새 generation을 만들지 않는다.
정책 변경 뒤 원 key 재활성화는 저장 fence로 차단하고 host 변경은 시각 교차 비교 전에 거절한다.
원 결과 보존과 현재 복구/출력 허가는 별도이며 과거 결과를 성공으로 재구성하지 않는다.

`tests/test_history.py`는 실제 client/UDS/actor/Authority에서 100회 반복·cold 재시작,
활성 보호·pending 참조·동등 재전달/실제 충돌·EIO·용량 예약/회복·정책/host 변경·과거 UUID 호환을 검사한다.
첫 국소 실행 `history-first.json/.log`: 39 passed. 대역은 기존 lab의 Xen/app/MQTT/device 경계이며
이력 구현·socket·파일 저장은 제품 코드다. 180/30ms 등은 로컬 수명 시험값으로 운영 기본값이 아니다.
전체 회귀와 이후 단계의 최종 후보/제공물은 아래에 이어 기록한다.

A의 전체 Gateway 회귀 `history-regression.json/.log`는 236 passed다. 이후 B0/B2 변경을 포함한
최종 전체 회귀를 대신하지 않는다. `test_provider_ipc.py`의 기존 저장 중단 시험은 replace 전/후
미확정과 cold 회수를 직접 검사하므로 같은 시험을 새 ID로 복제하지 않았다.

### B0/B1 — 실제 입력·계산과 변경 판단 근거

`docs/contracts/drive-world/world.proto/.options`를 공통 원본으로 두고 protoc 3.20.3·nanopb 0.4.9.1로
Python/C를 생성했다. 고정 도구는 작업 전용 디렉터리에만 공급했고 SDK·전역 pip는 바꾸지 않았다.
`world/dependencies.lock.json`은 실제 사용한 runtime 원문과 도구 archive hash를 보존한다.
전체 map은 uncompressed 단일 PUBLISH·최대 64×64와 32KiB wire, 별도 3-slot pool로 수신한다.
보관 셀의 원 age는 유지한다. 출처·epoch·배열·유한 좌표·원 시각/명시 clock interval을 확인하며,
현재 pose는 지도가 지정한 정확한 odom 표본과 최신 표본의 상대 변환으로 계산한다. 최신 표본으로 anchor를 대체하지 않는다.
reader lease와 generation을 분리해 이중 반환/ABA가 다른 독자의 참조를 해제하지 않게 했다.

`odometry.py`와 opt-in `feedback-2`는 기존 single serial owner에서 누적 좌우 encoder를 적분한다.
별도 장치 시계에서 Pi 시간을 빼지 않고 probe send/receive 구간으로 측정 시각 오차를 제한한다.
첫 표본에서 속도를 추측해 발행하지 않으며, 회전으로 추정한 steering과 측정 steering을 구별한다.
기존 write-only/feedback-1에 새 프레임을 자동 전송하지 않는다. `odometry-first`: 13 passed로
실제 PTY/partial frame·wrap·reset·불확실 clock 거절과 정상 회복을 확인했다. 실제 ESP32 생산/보정 수락은 아니다.

`planner/lattice.c`는 현재 heading/steering에서 곡률 변화율을 지킨 Ackermann primitive를 실제 탐색한다.
고정 정답 경로를 생성물에 넣지 않는다. root는 9개 연결 제어, 내부는 5개 제어를 쓰며 32 heading bin,
map 해상도의 위치 bin으로 중복을 제한한다. sparse node 8192·heap decrease-key·hash 16384와 최대 probe,
expansion/충돌/원 deadline/cancel을 각각 제한한다. 이 양자화·가중 heuristic을 완전성·최적성 보장이라고 부르지 않는다.
`lattice-lab-1.json`은 명시적 test profile이며 차량 운영값이 아니다.

초기 Euclidean 탐색은 정상 우회에서 충돌 연산 한도에 도달했다. 단순 한도 상향 대신 map당
64-row occupied bit index와 4096-cell 장애물 heuristic을 한 번 구성하고 재사용했다.
계수의 `cells`는 row probe와 실제 occupied SAT를 세며 전체 bbox 셀 읽기 수인 것처럼 비교하지 않는다.
heuristic 구성량·메모리는 별도 비용이다. root의 실제 비명목 steering 연결은 9개로 유지하고
내부의 근접 중복 분기는 5개로 제한했다. 생성 manifest에 두 제어 집합을 모두 기록한다.

초기 tracker는 장애물 corner를 지나지 못하거나 종단 방향을 만족하지 못했다. planner가 tracker의
명시 편차 여유를 예약하고 실제 충돌 검사한 직선 종단 연결을 추가했다. 경로 목표를 완화하거나
차량 위치를 경로점으로 옮겨 시험을 통과시키지 않았다. 최종 명령의 steering/rate/accel/brake 제한 뒤
반응·제동 궤적을 다시 계산하고 차체·표본 사이 간격·위치/방향 불확실성을 검사한다.
lookahead 상수 대신 실제 target 거리 제곱으로 curvature를 계산한다. cost를 물리 clearance로 환산하지 않는다.

`tracker-uncertainty`: 11 passed, `lattice-frames`: 15 passed다. 후자는 곡선 reference/도로,
회전 원점, 좌/우 우회·복귀, 비명목 초기 heading/steering, 연산/노드/deadline/cancel 거절 후 정상 새 작업,
unknown·좁은 통로·새 장애물의 최종 STOP, 실제 tracker 명령을 독립 bicycle 모델에 적분한 진행을 포함한다.
`engine-first`: world·lattice·engine 28 passed. 실제 C 경로를 전송 상태로 만들고 수신 쪽이
전체 경로·현재 geometry·tracker 계산을 통과한 경우만 채택하며 잘못된 사본은 기존 유효 경로를 덮지 않는다.
이 시점의 core 시험은 Zephyr firmware·Gateway가 제출한 최종 명령의 전체 통합 수락을 대신하지 않는다.
실패한 탐색/시험 harness 실행도 원자료에서 보존하며 이후 변경의 최종 회귀를 아래에 이어 기록한다.

### B2 연결 중 확인한 경계와 보완 기록

`lattice_input.c`의 전용 map/state 수신자와 기존 Pi 제어 수신자를 연결하고,
`lattice_app.c`에서 실제 planner·tracker·완전 경로 적용을 독립 worker에 연결했다.
`DriveLinkIO/DriveLinkProvider`는 실제 binary MQTT와 기존 actor/G2를 잇는다.
full path encode/decode는 별도 I/O 소유자가 수행하며 actor가 또 planner를 구현하지 않는다.
현재 적용/전달 중 사본을 고정하고, 관측 실패에는 bytes만 보존하며 새 허가는 부여하지 않는다.
새 binding/app 신원이나 확인된 종료 뒤에만 이전 참조를 반환한다.

호스트 core의 `world-pending-map`은 32 passed다. 이 중 새 교차 시험은 지도→odom 역순에서
기존 유효 map/명령 보존, 정확한 anchor 도착 뒤 실제 장애물 채택·제한, 정상 새 입력 뒤 진행 회복을
제품 C 코드로 확인한다. 새 후보는 기존 3-slot pool의 한 writer slot만 사용한다.
재수신·재파싱 없이 원 관측 나이와 source revision을 유지하고, 교체/만료/종료 때 소유자가 반환한다.
정상 준비를 위해 무효 anchor를 최신 표본으로 대체하거나 READY 유지 시간을 줄이지 않았다.

초기 통합 실패는 숨기지 않는다. `pair-input`~`pair-tap` 원자료에서 다음을 구분했다.

| 확인 사실 | 수정/판단 | 보존한 조건 |
|---|---|---|
| 작은 메시지용 기본 네트워크 예산에서 13KiB map 수신 `-ETIMEDOUT` | 신규 시험 프로필에 두 bounded 큰 본문과 작은 제어용 TCP/window·packet/fragment reserve 명시 | 기존 250ms I/O 기한 상향 없음, 크기 초과는 해당 연결에서 차단 |
| 고정 native 기본 tick 100Hz, 실제 고정 ARM 10,000Hz | native overlay를 ARM과 같은 해상도로 설정 | 기존 20ms worker/clock 예산 유지 |
| 원 probe/reply trace의 40~50ms 지연. 고정 SDK `eth_native_tap.c:eth_rx` 기본 RX poll 50ms 확인 | native 전용 TAP poll 1ms. Xen IRQ/실차 수락 근거로 확대하지 않음 | 시계 구간 검증·원 시각·만료 유지 |
| Paho 2.1에 TCP_NODELAY 기본 설정 없음, header/body가 작은 write로 나뉨 | 선택한 binary 연결과 신규 firmware 프로필에서만 명시 설정 | 구형 firmware 프로필·원 기한·오류 종류 유지 |
| 새 map이 다른 broker의 정확한 odom보다 먼저 도착해 현재 map을 잠시 쓸 수 없음 | 위의 bounded writer 대기 후 게시 | 이전 map의 원 유효기간까지만 사용, 새 장애물은 기준 표본 뒤 실제 채택 |
| MQTT 내부 abort가 먼저 socket을 닫으면 기존 마지막 오류 기록을 놓침 | disconnect/input의 원 오류를 보존 | 상세 오류 분류 축소 없음, 종료 뒤 유한 진단만 출력 |

`native-pair-*`는 같은 제품 CMake/main/runtime을 고정 Zephyr로 빌드한 개별 후보다.
첫 ARM `arm-lattice-picolibc` 성공은 뒤의 위 변경을 포함한 최종 ARM 증거가 아니다.
minimal libc에 없는 수학 함수를 고정 SDK의 실제 picolibc로 연결했다. 최초 NEWLIB 선택 오류와
정확한 `ZSOCK_TCP_NODELAY` 심볼 수정 전 빌드 실패도 보존한다. SDK/라이브러리 버전은 올리지 않았다.

현재 전체 통합 수락은 계속 진행 중이다. `pair-anchor`는 두 실제 firmware의 초기
READY/QUALIFIED를 동시에 확인한 뒤 명시 resume·실제 출력·인계 단계로 진행한다.
아직 그 이후의 정상 주행/반복 운영 전체 성공으로 보고하지 않는다.

패키지 준비 시 `xenova-gateway` recipe에 실제 `xenova_drive_world` payload와 고정
`python3-protobuf` 의존성을 연결했다. 별도 Yocto build에서 정규 package 생성을 시도했지만
고정 `jsoncpp 1.9.7` 소스가 download cache에 없어 `BB_NO_NETWORK=1`의 do_fetch가 실패했다.
로그는 `/home/ssafy/xenova-history-drive-package-20261101/build/tmp/work/cortexa72-poky-linux/jsoncpp/1.9.7/temp/log.do_fetch.8799`다.
이것을 source tar/import 또는 RPM 성공으로 바꾸어 기록하지 않는다. 최종 payload 대조·가능한
install/QA와 동일 후보의 ARM 재빌드 결과는 후속 기록으로 연결한다.

### 2026-11-02 통합 단계의 확인과 남은 반례

`world-scope-validation`은 최신 문자열 NUL/ASCII·중첩 차량 표본 검사를 포함한 제품 C
시험 32 passed다. `native-validated`의 실제 Zephyr binary SHA-256은
`47ef8cddf2a290a16c7b86a143cff4d9fb07e5acfef7dd358a1572ed831a5df9`다.
`pair-posix-validated`는 그 동일 펌웨어 두 개·broker J/P·제품 Gateway actor/Authority·
별도 프로세스 G1 client·실제 serial codec/PTY를 연결했다. 실제 시리얼 제출 명령만으로
가상 차량이 이동하고, A→B 적용/새 계산·장애물 우회·reference 복귀·B→A 적용이 진행했다.
이 회차의 복귀 조건은 위치 편차였으므로 후속에서는 종단 방향도 검사한다.
정답 path로 차량 pose를 덮지 않았으며 mapper·encoder/차량·Xen/Store binding만 대역이다.

PTY 포화 시험은 `write_timeout=0`인 pyserial 3.5의 EAGAIN 반복 대기를 드러냈다.
그 시도는 폐기하고 POSIX fd에서 단일 nonblocking `os.write`로 정확히 수락한 바이트를
반환하도록 고쳤다. EAGAIN/부분 쓰기는 원 실패로 남고 소유자가 종료한 뒤 새 owner의 정상
쓰기까지 시험한다. fd 없는 URL backend의 기존 유한 쓰기 계약은 별도로 유지한다.
임의 재시도·기한 연장·시리얼 writer 추가로 해결하지 않았다.

신규 binary 프로필의 계획 인계는 이미 제출한 원 명령의 남은 유효시간만 연속 구간으로
사용한다. 새 target의 실제 계산·현재 binding/신선도는 여전히 필요하며 원 command expiry와
독립 OutputOwner의 STOP은 연장하지 않는다. 네트워크에서 붙어 도착한 목표는 실제 차량
목표 변화율을 어기며 제출하지 않고 정확한 원 목표 하나만 보류한다. 새 입력·STOP·원 나이·
실제 제출 guard로 대체/폐기하며 범용 큐나 보간 명령을 만들지 않는다.

`manual-hold-posix` 45 passed, `manual-stop-guard` 52 passed는 각각의 당시 후보 결과다.
G1 선택 `manual_hold=true`는 정상 MANUAL 선택을 내구 보존하고 AUTO 회복만으로 복귀하지
않는다. 명시 resume/countdown으로만 AUTO로 돌아간다. 새 MANUAL 제어는 같은 Pi/device
session·generation의 실제 STOP/APPLIED·stationary 보고를 요구한다. 타이머·write 성공을
물리 정지로 바꾸지 않으며 같은 운전자 MANUAL A/B의 정당한 연속은 별도로 취급한다.

확장 통합 시험의 실패도 보존한다. `pair-modes-idle`의 첫 MANUAL 진행 실패는 준비된 예비가
없는 시점의 SAFE_STOP→disarmed MANUAL에 시험 호출자가 재무장하지 않았기 때문이다.
`pair-manual-session`은 현재 generation의 준비 유지 구간 전에 resume를 보낸 시험 오류였다.
`pair-manual-qualified`는 이를 맞춰 실제 지도 단절→정지 확인→명시 재무장→중립/새 행동→
MANUAL 주행→현재 키의 MANUAL B 승계까지 진행했다. 이후 AUTO instance 재합류는 아직
통과하지 않았다. 해당 회차의 `OUTPUT_PATH_FAILED` 최초 cause는 기존 10ms 제출 결과
미확정이며 원자료에 남긴다. 정상 C/native 단위 통과만으로 이 미완료를 닫지 않는다.

종료 후 자원 계수 후보 `native-resources`의 binary는
`2fdf4f98c8fbb9b46780528996c08e44f70926b7068d915e0cec17b72fd36f0d`다.
그 회차의 engine 644,664 bytes 중 map 81,208·search 475,872·path 31,560·odom 55,320은
포함 항목이며 다시 합산하지 않는다. 두 guest의 종료 보고에서 path lease/writer와 map
reader/writer는 모두 0이었다. ELF/BSS·network/stack·임시 encode 자원은 별도 집계 대상이다.

패키지 빌드는 미뤄 둔 결과로 마감하지 않고 재개했다. 고정 recipe의 jsoncpp 1.9.7
`3455302847cf1e4671f1d8f5fa953fd46a7b1404`, abseil-cpp 20260107.1 archive SHA-256
`4314e2a7cbac89cac25a2f2322870f343d81579756ceff7f431803c2c9090195`, protobuf 6.33.6
`6e1998413a5bca7c058b85999667893f167434bc`를 작업 전용 download cache에 공급했다.
Python protobuf sdist도 recipe checksum으로 공급 중이다. 네트워크는 지정 source fetch에만
허용하고 실제 build는 `BB_NO_NETWORK=1`로 수행한다. 버전·의존성·고정 layer를 바꾸지 않았고
다른 진행 build나 운영 rootfs를 교체하지 않았다. 아직 최신 정규 RPM 완료 증거는 아니다.

### 실제 재합류 계산 결함과 고정 target 후속

`pair-rejoin-heading`은 위치와 방향의 원 reference 복귀, 지도 단절→현재 세션의 명시
재무장→중립/새 행동→실제 MANUAL 출력→같은 키의 B MANUAL 출력까지 진행했다.
그 뒤 A의 새 AUTO instance는 준비되지 않았고, 이번 회차에는 Gateway 출력/내구 실패가
없었다. 실제 pose `(4.5282, 1.5311, 0.0051)`를 제품 C 시험으로 옮겨 국소 재현했다.

두 원인을 나눠 고쳤다. 기존 합류 목표가 가까워도 종단 직선 0.64m를 새로 확보하도록
요구하던 조건은, 이미 그 구간 안에서 재구축하는 root에 한해 짧은 정방향 연결을 허용한다.
각 점·sweep·steering/heading·실제 tracker 검사는 유지한다. 이미 도착한 root는 현재
차체와 실제 정지 투영을 확인한 stationary 경로로 표현하며 타이머 READY를 만들지 않는다.
다른 장거리 탐색 노드까지 도착 조건을 풀었던 첫 시도는 기존 closed-loop 회귀에서
실패해 채택하지 않았다. 같은 정상 우회·실제 종단 도달 assertion을 유지한 채 범위를 좁혔다.

도로 끝 goal은 전면 길이만 빼면 회전 차체 corner와 마지막 반 표본 sweep가 도로 밖으로
나갔다. `engine.c:de_plan_start`가 해당 margin/guard/sweep를 포함한 차체 corner 반경을
예약하도록 수정했다. 충돌 검사·goal 거리/방향 허용량·노드/충돌 한도는 완화하지 않았다.
`short-connection-before`의 원 실패와 중간 두 회귀 실패는 보존한다.
최종 국소 `terminal-corner-fixed`는 world/lattice/engine 36 passed다. 독립 fixture 기하로
예상한 endpoint와 실제 제품 goal, 양의 정상 tracker 명령을 함께 검사한다.

ARM `arm-resources-v2`에서는 `.tbss.errno` orphan 경고를 확인했다. 고정 cross toolchain의
TLS 지원이 Zephyr에 전달되지 않은 상태였으므로 ARM builder가 정식
`CONFIG_TOOLCHAIN_CROSS_COMPILE_SUPPORTS_THREAD_LOCAL_STORAGE=y`를 전달하고 최종
`CONFIG_THREAD_LOCAL_STORAGE=y`를 필수 확인한다. `arm-tls`는 경고 없이 빌드·포장했다.
native host toolchain에는 그 cross 전용 Kconfig symbol이 없으므로 공통 프로필에 넣지 않는다.
native configure의 중간 실패도 보존한다. 이 ARM 후보는 위 종단 수정 전이므로 최종 재빌드가 필요하다.

Yocto `resume-package.log`의 오프라인 정규 빌드는 2,937 task 완료, Gateway와
Python protobuf의 package QA·RPM 생성을 통과했다. 기존 kernel I/O-accounting 경고는
환경 제약으로 남겼다. 이후 수정분은 `package-provider-current`에서 별도로 반영한다.
source·RPM bytes/mode·target import와 최소 client 설치 검증은 최종 후보에 다시 연결한다.

<a id="history-drive-final"></a>
### 2026-11-02 최종 후보 — 실제 변경과 수락 기준

위 소제목들은 실패와 수정 과정의 이력이다. `jsoncpp` fetch 부재·planner 재합류 미수락·
ARM 재빌드 필요라는 당시 판정을 현재 미완료로 읽지 않는다. 착수 HEAD `cdcb61977ceca630dd9862b7cecd3bf04bf29e1c`와
브랜치는 유지했고 commit/push는 하지 않았다. 최종 변경 파일·해시·diff와 제공 archive는
PC 원자료 디렉터리의 `delivery-final/CANDIDATE.json`을 기준으로 한다.

| 변경 위치 | 왜 바꿨는가·채택 기준 | 유지한 경계·실제 근거 |
|---|---|---|
| `history.py`, `authority.py`, `provider.py`, client | 유한 이력을 삭제/초기화 없이 반복 사용한다. 원 key 발급 시각과 원 deadline에 retry/retention 약속을 고정하고 신규 내구 수락에 종결 수거를 결합 | 활성 exclusion·미확정·PREPARING/PREPARED·pending 참조는 보존. 원 deadline/hash/신원 불변, host/floor fence로 정리한 key의 deadline 변경 재실행 차단. 별도 DB/tombstone 없음 |
| `world.c`, `lattice_input.c`, 공통 Bootstrap | 실제 정식 binary 입력·exact odom anchor·출처/시계/좌표·고정 pool을 연결. map이 먼저 도착하면 기존 writer slot에서 anchor를 기다림 | 이전 유효 map과 원 age 유지. 잘못된 출처·unknown/부분 배열·NUL·과거 epoch/표본을 정상값으로 합성하지 않음. `test_world.py`·`test_engine.py` |
| `lattice.c`, `engine.c`, 생성 primitive | 현재 pose/steering에서 실제 탐색·연속 primitive·swept footprint·도로/cell 검사·RPP·최종 반응/제동 경로 검사 | 정답 path·상수 명령·타이머 progress 없음. node/index/heap/연산/기한/취소 상한과 실패 후 새 정상 작업을 검사. 무제한 탐색이나 한도 상향으로 해결하지 않음 |
| `lattice_app.c`, workers/runtime | 선택된 실제 계산의 채택·준비·progress·상태 생산을 연결하고 AUTO instance 교체에서 실제 cancel/join/참조 반환 | MANUAL·I/O는 AUTO join을 기다리지 않음. 대기 자격·적용·새 계산·출력 허가는 별개. 동일 두 firmware의 양방향 인계·새 실행 합류 |
| `drive_link.py`, `state_transfer.py` | 현재 owner의 완전 path 사본과 target의 실제 적용/계산을 기존 G2에 연결. 큰 body decode/encode는 별도 I/O owner에서 수행 | full copy 4·metadata 8·target별 inflight 1, 적용/전달 중 참조 고정. 이전 유효 사본과 권한 종료 뒤 늦은 갱신 구분. PUBACK으로 적용 판정하지 않음 |
| `sink.py`, `output_guard.py`, `verified_runner.py` | 실제 nonblocking 단일 write·원 목표 변화율 대기·MANUAL 유지/정지 관측을 연결 | EAGAIN/부분 write는 실패. command 만료·10ms 제출 확인은 그대로. 원 나이를 늘리지 않는 보류 한 칸, STOP은 즉시 폐기/보호. 희귀 최초 cause 보존 |

#### 마지막에 확인한 시간 판정 결함

guest→Pi clock은 점 값이 아니라 구간이다. 구간을 좁히면 현재 시각의 상한 추정이
이전 map 수신 시각의 상한보다 조금 낮아질 수 있다. 기존 `now < received_us` 판정은
이 경우에도 `OBSERVATION_EXPIRED`로 만들었다. `clock-upper-original`은 실제 engine에서
원 관측 Pi 하한 1,100,000us·원 만료 1,400,000us인 map을 1,220,000us에 수신한 뒤,
유효한 현재 상한 1,215,000us로 추종하면 DE_EXPIRED가 되는 반례를 재현했다.
그 이전 `clock-upper-before`는 시험 odom 순서부터 잘못돼 거절됐으므로 결함 재현 근거로 쓰지 않는다.

`dw_snapshot_time`이 원 관측 하한과 원 publication 하한으로 절대 만료를 계산하도록 바꾸고
engine 입력·qualifier·명령 만료가 같은 판정을 사용하게 했다. 수신 나이 clamp·새 TTL·기한 연장은 없다.
1,215,000us에서는 실제 양의 tracker 명령, 1,400,001us에서는 기존 만료 거절을 직접 검사한다.
원 수신/관측 값 불변·미래 publication·trust 만료도 검사한다. `clock-bound-final`은 제품
world/lattice/engine **40 passed**다. 고정 생성기 3.20.3의 deprecation 경고는 도구 이력이며 런타임 결함으로 바꾸지 않는다.

#### 같은 후보의 실제 통합과 자원 수명

`pair-clock-final`은 고정 `native-clock-bound-final` binary
`c2bdd792bc615f764bd078ae5b1d6f4767f5618ab725b5bf030ca3b4ad00ff45` 두 개를 실행했다.
운영과 분리한 새 network namespace의 broker J/P·제품 Paho/MQTT·Gateway actor/Authority·
별도 process G1 client·single-owner serial/PTY를 사용했다. mapper·rear-axle 물리/encoder·
root 신뢰 binding/Xen/Store만 fixture다. planner/tracker/충돌/상태 적용은 제품 C다.

최대 64×64 입력에서 실제 양의 진행→장애물 우회→위치·방향 복귀→A/B stateful 승계,
지도 단절→실제 STOP/APPLIED·정지 표본→명시 재무장·중립/새 행동→MANUAL 진행,
같은 세션의 B MANUAL 진행→AUTO instance cancel/join·새 계산→사용자 명시 AUTO 복귀,
양방향 실행 SIGKILL→현재 새 binding·실제 Standby 재합류를 수행했다.
그 뒤 명시적으로 유지한 중립 MANUAL에서 최대 map을 계속 수신하며 같은 Authority에
40개의 protect→동등 replay→cancel→보존 만료/수거를 진행했다. 생성마다 빈 Authority를 만들지 않았다.
마지막에 정리된 key 조회의 `RESULT_NOT_RETAINED_OR_UNKNOWN`, deadline 변경 submit의
`REQUEST_WINDOW_EXPIRED`, 새 정상 요청/Standby를 확인했다. MANUAL 40회의 중립도 실제 계산이며 타이머 progress가 아니다.

이 실행은 rc0·error 없음, 시리얼 제출 **4,688건 모두 수락**·실패0이었다.
독립 물리 OBB/SAT 대조는 9,000 표본에서 도로 이탈/장애물 충돌 없이 최소 분리 투영
0.09669m를 확인했다. 이는 정확한 Euclidean 최소 거리나 실차 여유 보장이 아니다.
정상 guest는 원 join에서 rc0, map reader/writer·path lease/writer0으로 끝났다.
의도한 두 SIGKILL의 -9는 정상 cleanup 성공으로 세지 않는다. 그 다음의 새 guest가 실제로 진행했다.
FD는 scenario 객체 내부에서 5→9였지만 모든 client/callback 객체 수명 종료 뒤 **5→5**,
잔존 thread·정리 오류·예상 밖 자식 종료는 모두0이다. Paho 재사용 wakeup FD가 살아 있는
client 객체에 남는 시점과 객체 해제 후를 분리했으며 live FD를 강제로 닫아 통과시키지 않았다.

별도 `pair-support-armed-final`은 같은 firmware·Gateway·최대 map에서 실제 양의 DRIVE를
확인한 다음, 원 보호 수락의 `atomic_json`과 CRITICAL 진단 handler를 함께 막았다.
두 호출자가 아직 대기 중일 때 독립 owner의 watchdog STOP1과 실제 PTY의 STOP 수신을
확인하고 나서 sink를 해제했다. 원 보호는 PROTECTED로 내구 수락됐고 정당한 cancel 뒤
현재 출력이 다시 진행했다. 희귀 진단의 원 문구도 남았다. rc0·제출917건 모두 수락,
독립 기하 표본1,865개·FD5→5·잔존 thread0·네 자식 정상 종료다.
이는 STOP 제출/가상 장치의 목표 철회 근거이며 실제 물리 정지나 모든 fatal 경로의 무대기를
의미하지 않는다. 첫 `pair-support-final`은 인계 직후 아직 DRIVE가 아닌 STOP 상태에서
watchdog 증가를 기다린 시험 선행조건 오류였다. 실패를 보존하고 실제 DRIVE/원 expiry를
확인한 뒤 지연시키도록 고쳤으며 watchdog 기한은 바꾸지 않았다.

이전 `pair-maximum-final`·`pair-maximum-cleanup`은 기능 진행 뒤 자원 계측 위치 때문에
전체 rc1이었으므로 최종 성공에 합산하지 않는다. `pair-final-scope`의 10ms 제출 미확정은
실제 `OUTPUT_PATH_FAILED`로 남겼다. `pair-final-scope-2`·`pair-final-resource`·
`pair-final-manual-history`의 관측 만료/전환 중단도 보존한다. 일부는 위 시각 판정 반례와
같은 잘못된 만료 조건의 영향을 받았으며 모든 지연을 그 결함 하나로 단정하지 않는다.
오프라인 물리 대조는 owner 종료 뒤 실행한다. 대조 자체의 Python GIL 비용을 살아 있는
10ms 제출 경로에 주입했던 이전 계측과 제품 지연을 구분한다.

#### 실제 비용·자원과 계측 범위

| 경로 | 실제 계수/상한 | 해석 |
|---|---|---|
| 종결 수거+새 보호 수락 | `history-cost-final`: 필수 atomic save1·file/parent fsync2·1,096 bytes | 같은 수락 저장에 수거 포함. 5회씩 get/replay/history의 추가 save/fsync0. 필수 저장을 best effort로 바꾸지 않음 |
| 원 보호 get/replay | `IPC_COST.json`: 40 get·8 replay, 추가 save0 | 당시 전체 Gateway 회귀의 실제 UDS/actor/store. lookup actor 44.8~71.9us, 호출 10.83~13.66ms는 fixture 표본이며 운영 상한 아님 |
| engine 저장 | 677,488 bytes | map81,208·search508,640·path31,560·odom55,320은 포함 항목. 여기에 다시 더하지 않음 |
| 최대 입력 실제 우회 탐색 | 확장1,395·primitive6,974·footprint118,523·cell/row/SAT886,620·취소 확인353,966 | `pair-support-armed-final`의 한 firmware/한 실제 탐색. node high-water2,194·open1,359·index probe5. A/B 값을 중복 합산하지 않음 |
| 종단 연결 보완 | node별 실제 최대 lateral offset 32KiB 추가 | 단순 near-reference 연결과 실제 우회 뒤 settling을 구분. 연산/goal/collision 한도는 유지. 임시 메모리를 숨긴 비용 감소 주장 없음 |
| 별도 I/O/보관 | map3·path3·odom128, wire32KiB, map/state MQTT 분리, 네트워크 fragment512×256B | decoder/encode·상태 사본·thread stack은 engine 외부. 전체 정적 배치는 ARM ELF/BSS로 대조 |
| 진단 | 첫 cause·8개의 최근 서로 다른 decode 사유·누락 계수 | invalid마다 stack/출력하지 않되 오류 종류·희귀 내부 결함을 합치지 않음. 기존 slow CRITICAL/저장 대기 중 독립 명령 만료 STOP 회귀 유지 |

신규 planner에 과거의 같은 알고리즘 기준선은 없다. 기존 단순 AUTO 대비 속도 개선률을 만들지 않는다.
취소 확인·node/open/index high-water는 epoch별 실제 카운터다. SIGKILL한 첫 epoch의 종료
카운터를 새 실행의 값으로 채우지 않는다. compiler direct frame과 실제 전체 call-chain
stack peak, host 관측 최대와 Xen WCET도 구분한다. 실제 lock wait 분포·Xen wakeup/IRQ·
SD/USB·실차 부하의 상한은 이번 자료로 수락하지 않는다.

#### 소스·ARM·정규 패키지 제공 수준

고정 Zephyr `684c9e8f32e4373a21098559f748f06915f950c9`·SDK GCC14.3.0/picolibc의
`arm-clock-bound-final`은 rc0, source digest
`f02eb94420424b088162cbcc4b7dd64310058dfb411fae3bb6084357d7555b6f`, firmware SHA-256
`8b79b56428126601cd1e0dd5c3788d7107ba0ee1427a207386a8a34961459631`이다.
ELF/map에서 `lp_advance/lp_track/lp_footprint/de_track/dw_snapshot_time/drive_lattice_start`의
실제 링크, TLS/stack sentinel·16MiB, Zephyr+nanopb license와 receipt/source bytes를 확인했다.
사용 RAM은 **2,176KiB/16MiB**다. 전체 RAM을 늘려 planner를 수락하지 않았다.
`BUILD_RECEIPT.json`의 direct stack frame audit는 호출 사슬 전체 peak 측정을 대신하지 않는다.
이 ARM 바이너리를 native 또는 qemu-user로 실행했다고 쓰지 않는다.

`package-history-drive-final`의 실제 정규 빌드/QA·`RUNTIME_PACKAGE_BUILD`가 성공했다.
`PACKAGES.json`의 **46개 runtime RPM closure**, 하위32개 payload와 Gateway/MQTT/world
Python 바이트·0644 모드, disabled unit/preset·활성 설정 미생성을 대조했다.
실제 ARM Linux Python3.14.7/QEMU에서 설치 모듈68개 import와 socket side effect 부재를 확인했다.
`TARGET_CODEC.json`은 source PYTHONPATH 없이 설치된 protobuf6.33.6/upb가 독립 protoc golden
623 bytes와 최대 25,305 bytes/64×64를 decode/encode하고 중복·부분·oversize를 거절한 결과다.
`CLIENT_PACKAGE`는 서버/Paho/protobuf 없는 최소 client root의 ARM import,
`TARGET_SOCKET`은 설치 ARM client→실제 UDS→설치된 Gateway 코드의 submit/get/cancel이다.
후자는 Gateway 전체 ARM 실행·실제 앱/Xen/장치 시험은 아니다.

Gateway 전체 `gateway-current-final`: **254 passed**, 추가 저장 계수 시험 `history-cost-final`: **1 passed**.
같은 시험을 중복 합산한 총 회귀 수로 표시하지 않는다. 공용 C `COMMON_C_BUILD`: ASan/UBSan
6 suite·120,000 동시 payload parse 포함 통과. `platform-current-final`: **68 passed**로
기존 full/optional create 의미 비교·변경 없는 HTTP 어댑터·기본값·CPU 교차·lifecycle 소유·binding·진단을 확인했다.
bootstrap 별도6개 결과는 중복 가능성이 있어 합산하지 않는다. 고정 FastAPI 테스트 도구의
httpx 안내 경고와 protobuf 생성기 경고 때문에 runtime 의존성을 올리지는 않았다.

재현 도구는 `run_lattice_pair.py --help`, core 시험 `pytest -o addopts= -q platform/apps/drive/tests/test_{world,lattice,engine}.py`,
Gateway `pytest -o addopts= -q platform/services/gateway/tests`다. 정확한 도구 경로·환경·명령·rc와
원 로그는 위 PC 자료의 JSON/log 및 통합 source snapshot에 있다. Linux의 새 root network
namespace에서만 실행하는 통합 도구이며 운영 broker/VM에 연결하는 예제가 아니다.

#### 현재 제공물과 담당자에게 남기는 일

현재 원본은 [world 계약/codec/golden](../contracts/drive-world/README.md),
[Gateway key·보존·MANUAL·결과 계약](../contracts/gateway-provider-v1.md),
[앱 빌드/실행](../../platform/apps/drive/README.md),
[설치 단위](../platform/deployment-readiness.md#history-drive-install)다.
[주행 설계 22절](../platform/drive-pipeline-architecture.md#s22)에 Nav2 Jazzy 원 revision
`d5d768b7b38a05e7cfd2b6c693b786b402161bed`·CMU/추종 원리→제품 함수→시험을 연결했다.
ROS/Nav2 전체 이식·원 구현의 성능/최적성 보장은 하지 않았다.

Worker는 제공 client의 시간 key를 원 attempt에 한 번 보존하고 실제 Engine의 보호→현재
Manager guard→새 Bootstrap/binding→finish/cancel에 연결한다. Jetson은 제공 encoder/schema와
원 odom/clock 계약으로 실제 sensor→mapper를 생산한다. ESP32는 feedback-2의 session/counter·
누적 encoder·원 시각/품질을 실제 firmware에서 생산하고 watchdog/물리 정지를 수락한다.
HTTP/Spring/화면은 기존 관리 호출을 유지하고 MANUAL 유지·명시 복귀·관측 단계를 실제 route에
연결한다. 이 UDS가 외부 HTTP API라고 안내하지 않는다. Linux L05 새 Agent 후보를 받은
다음 일반 생성/factory seed의 template receipt와 실제 guest smoke를 갱신한다.
각 기존 요청서의 현재 본문을 수정했고 타 담당자 제품 코드는 대신 바꾸지 않았다.

본인 소유의 planner/tracker·공통 codec·Gateway 제공자 경로를 외부 담당 대기로 넘기지 않았다.
다만 선택 프로필은 **합성 시험 차량**이다. 실제 센서·차량 제원/보정·mapper clock/trust·
운영 retry/retention·승인 create 시간값은 실제 제공/승인을 받아야 한다.
운영 이미지·새 Agent 템플릿·실제 Xen/USB/SD·물리 정지·연속 승계·7번은 미수행이다.
D03-G02의 손상 backup 최신성 복원은 이번 이력 수거로 닫지 않았다.

최종 제공 디렉터리는 PC 원자료의 `delivery-final`이다. `source-changes.tar.gz`·`tracked.diff`는
위 base에 적용할 변경, `drive-firmware.tar.gz`는 ARM 번들/receipt와 재현용 native binary,
`runtime-rpms.tar.gz`는 검증한46개 runtime closure, `local-evidence.tar.gz`는 정상/실패
결과와 재현 명령이다. private fixture credential·Authority 파일은 evidence archive에서 제외했다.
각 archive SHA-256·변경 파일·정확한 패키지 목록은 `CANDIDATE.json`에 모았다.
현재 수정 문서는 이 보고서, 주행 설계·배포 준비, Gateway/주행앱 README, Gateway/world 계약·
벡터, 기존 Gateway·Worker·Jetson·ESP32·HTTP/Spring·Linux 요청서와 요청 색인이다.

### 2026-11-02 커밋·MR 전 소스와 런타임 대조

후속 사용자 요청에 따라 위 작업을 작업 단위로 커밋하고 현재 feature에서 platform으로
MR을 준비한다. 앞선 절의 commit/push 미수행은 당시 작업 범위이며 이 후속 요청과 구분한다.
새 기능·운영 변경이나 7번 실행을 추가하지 않았다.

제공 manifest의110개 소스 파일은 대조 시작 때 모두 일치했다. 고정 ARM receipt의307개
빌드 입력과 실제 링크·TLS/stack sentinel/license, 설치 ARM codec의623/25,305-byte
정상 roundtrip·중복/부분/oversize 거절을 다시 확인했다. 제품 C의 `pre-mr-drive`40개,
Gateway 전체 `pre-mr-gateway`255개가 모두 통과했다. 시간 상한 갱신의 원 만료 보존,
내구 수락/수거·동등 replay·상태 적용·장치 세션·출력/진단 보호를 포함한다.
ARM firmware hash와 검증된 runtime payload는 위 후보 그대로다. 이번 문서의 공개 준비
기록 추가는 기존 firmware/패키지 재생성이나 앞선 제공 archive의 제자리 변경이 아니다.

`git fetch origin --prune` 후 `origin/platform`은 착수 HEAD `cdcb619`와 같았다.
커밋 직전 상대 변경이 없으므로 충돌 해결용 merge/rebase는 필요하지 않았다.
공개 후 후보는 실제 Git commit과 MR diff로 식별하고, 소스 archive·빌드 receipt의
당시 후보 식별자를 새 HEAD로 고쳐 쓰지 않는다. 최종 push/원격 ref·MR 생성 결과는
PC `2026-11-02/gateway-history-mr`에 남긴다. squash·자동 merge는 사용하지 않는다.

새 파일을 stage한 뒤 확인된 nanopb 원문·라이선스의 trailing space와 고정 생성 C의
마지막 빈 줄은 원문/hash 재현성을 위해 보존했다. whitespace 검사는 이 두 디렉터리만
제외하고 수행하며, 제외한 vendor 파일은 `dependencies.lock.json`의 원문 해시로 대조한다.
검사를 맞추기 위해 vendored/generated 소스를 임의 윤문하거나 firmware를 바꾸지 않았다.

<a id="cli-query-consistency"></a>
## 2026-11-02 — OS 공통 경량 의도 조회·CLI 경로 정합화

### 후보와 0단계 재사용

착수 branch는 `feat/platform/gateway-history-driving-app`, HEAD는
`f8a866389e19b6f688459ff26775e5c55fd7668f`이며 당시 worktree는 깨끗했다.
최종 후보는 이 HEAD + 이번 diff/새 파일이다. [소스·시험·비용 manifest](evidence/cli-query-consistency-20261102.json)와
PC `C:/Users/SSAFY/Documents/Codex/2026-11-02/cli-query-consistency`의 원 로그/재현 script로 식별한다.
WSL 작업본은 `/home/ssafy/xenova-cli-query-20261102/candidate-final2`다. commit/push는 하지 않았다.

필수 0단계는 [생성·factory·신호/공급 조사](vm-registration-signal-mapping-20261102.md)와
[당시 소스 manifest](evidence/vm-registration-signal-mapping-20261102.json)를 재사용했다.
조사 후보 `206361b` 이후 착수까지의 차이는 보고서 커밋뿐이었다. 이번에는 그 표를 다시
작성하지 않고 구현에 필요한 등록/intent reader·조회 호출과 소비자의 현 위치만 대조했다.

| 앞선 조사에서 채택한 근거 | 이번 사용과 제외 |
|---|---|
| 일반 Linux·Zephyr create의 등록과 초기 stopped 의도 | `Manager._read_states`와 Execution의 저장 intent 검증을 재사용. 새 기본 의도를 저장하지 않음 |
| factory 빌드/seed와 `FirstBoot.register`의 보드별 신원·등록·running 의도 | registry와 tombstone을 등록 원본으로 사용. 일반 create journal이 없는 factory도 포함 |
| autostart 설정·명시 의도·과거 lifecycle intent | 별도 필드로 반환. 누락은 unknown/null, 과거 세대는 적용 불가로 표시하고 자동 정정하지 않음 |
| template 선언·다음 기동 자산·현재 binding/신호 수신의 서로 다른 수명 | intents에는 저장 template 식별만 제공. 현재 firmware 적용·Agent/앱 건강·수신을 추측하지 않음 |
| 자격 확보/export·Linux private 설정·Zephyr 실행별 provisioning/receipt | 조사 표의 재사용 지점 유지. MQTT 자동 공급·Spring 통지·실제 firmware/신호 신규 연결은 미구현/이번 범위 밖 |

### 확인한 문제와 실제 수정

제품 수정은 기존 `xenova-platform` recipe의 여섯 Python 모듈에 한정했다.
Gateway·planner·guest·타 담당 제품과 기존 데이터 형식은 변경하지 않았다.

| 현상·분류 | 원인 → 수정 또는 유지 이유 | 직접 근거 |
|---|---|---|
| `--json`이 실행 경로를 고름 / 인터페이스 불일치 | `xenova_vm.main`의 raw argv 분기를 제거. 파싱한 action/명시 인자로 한 handler를 호출하고 결과만 렌더링 | 기본/JSON/short의 동일 action·관측 결과, literal `--json` key, `--`·equals 옵션 시험 |
| status의 두 의미 / 명시적인 호환 변경 | status는 모든 형식에서 list. 옛 자산/hash/시각 종합 진단은 `diagnose --details`로 보존 | 두 형식에서 같은 handler, 명시 진단의 기존 instances/domains/time 출력 |
| 옵션 수락 후 유실 / 확인된 결함 | argparse SUPPRESS와 action별 허용표. timeout/budget/lifecycle 등을 실제 spec에 전달. 무관한 force/execute/restore·초과 인자는 효과 전에 거절 | 실제 CLI→Manager 전달·거절, 다른 UUID/반복 옵션 충돌과 동일 대상 허용 |
| 원 key CLI 누락 / 미연결 | control-operation의 ID/key 중 정확히 하나를 선택자로 전달. 선택적 UUID 대조, 선행 live get 없음 | 한글/공백 key·retired 대상·원 deadline/operation 불변 회수 |
| probe/context·templates/recovery 조회가 긴 공통 잠금 대기 / 확인된 결합 | 현재 관측은 get과 같은 ReadContext·관측 슬롯으로 이동. catalog/원 attempt는 작은 원본을 두 회차 대조 | 공통 잠금 보유 중 정상 조회, restore fsck barrier 이전 B 실제 stop·원 완료 |
| 현재 host 읽기 실패가 유효 원 제어 기록까지 차단 / 확인된 결합 | Execution 생성 없이 `read_requests`·`read_attempt`로 원 무결성 확인. 현재 host/기한 비교만 unknown과 제한된 원인 경고로 분리 | 수정 전 boot reader OSError의 ARTIFACT_INVALID 재현. 수정 후 OSError/잘못된 host ID에서 원 결과 회수·원 기록 불변 |
| 이름 해석이 예산 밖, 두 대상 중 하나를 조용히 선택 / 확인된 결함 | CLI 진입부터 소모한 기간 차감. UUID는 바로 사용, 이름은 한 metadata 검색. 두 표현은 같은 UUID여야 함 | 작은 예산 거절 후 정상 회복, 같은 이름/UUID 성공·다른 대상 거절 |
| Python create helper가 생략을 None으로 전달 / 확인된 결함 | 존재하는 자원 속성만 공통 create spec에 전달. 명시 None은 계속 거절 | 생략 계획 정상값과 명시 null 오류, 기존 full/optional 의미 비교 회귀 |
| spec JSON 오류를 저장 오류로 혼합 / 오류 경계 정정 | 잘못된 UTF-8/JSON·중복 필드·비유한 수는 INVALID_SPEC, 파일 I/O는 STORAGE_UNAVAILABLE. 내부 결함은 입력 오류로 숨기지 않음 | 잘못된 JSON/파일 부재·내부 TypeError·필수 저장 오류의 기존 분류 시험 |
| `get --json -- UUID` 거절 / 추가 발견 | 표준 argparse의 intermixed 파싱으로 옵션 뒤 위치 인자도 처리 | 공개 entrypoint의 separator/equals·key 문자열 시험. 별도 CLI 프레임워크 없음 |

`read_record/read_intent`는 기존 `Execution.read/intent`의 파일·schema·신원 검증을 분리한 순수 reader다.
제어 writer는 기존 메서드와 주입 reader를 그대로 사용한다. `read_requests`의 완전 검색·유일성,
원 hash·UUID·파일 경로·예산 검사는 유지했다. `read_attempt`도 기존 원 시도 무결성 검사를 유지한다.
저장 검증과 현재 host 관측 분리는 현재 제어/복구 guard를 생략하는 허가가 아니다.

### 명령·근거·보호 선택표

상세 인자/오류와 실제 사용 예제의 원본은 [정식 CLI 명세](../platform/cli-manager-spec.md#usage)다.
전체 parser의 action/명시 옵션 허용표는 `xenova_management_cli.OPTIONS`이며 새 runtime schema가 아니다.

| 명령·canonical action | 읽는 근거·부작용 | 보호/예산·현재 판정 |
|---|---|---|
| intents 단건/목록 | registry+tombstone+저장 intent. 일반 등록의 게시 완료 guard만 읽음 | 공통 잠금·관측 슬롯·Xen·host ID·hash·쓰기 0. 최대 두 metadata 회차, timeout/budget |
| list/status/get/probe/execution-context | 현재 Xen+등록/실행/의도 결합. cache 게시 | 공통 mutation lock 없음. 기존 제한 관측 슬롯·게시 전 guard 유지. probe observation, 나머지 기존 VMView |
| snapshot | 과거 관측 cache와 나이 | UUID는 registry/Xen/새 게시 없음. 내부 이름은 별도 등록부 해석. max-age는 관측 예산과 다름 |
| operation/control-operation/recovery-operation | 자산/control/복구의 각 원본, 필요한 원 owner 근거 | 원 key 완전 검색·두 회차 검증 유지. 내구 재확정/reconcile/재실행 없음. timeout/budget |
| templates | catalog descriptor·작은 manifest full=False | registry/공통 잠금 제거, 완전 결과 재대조. root payload 전체 hash 없음. budget |
| create, retire/delete, restore, purge, update-resources, firmware-transition | 원 명세·자산·lifecycle·현재 사용/예약 | 기존 계획/execute·대상 lease/원 owner·효과 직전 guard 유지. 생성 단일 검증 복사 유지 |
| start/stop/reboot/destroy | 원 intent/control·현재 신원·CPU/host guard | UUID/이름·형식과 관계없이 같은 Manager. 즉시 동작, destroy의 명시 force 유지. CLI 이름 해석도 호출 budget 차감 |
| autostart-set/board-shutdown-set/config/host-resume | 저장 정책/런타임 cfg/gate | 즉시 변경이라는 기존 의미. config는 단순 조회가 아님. 무의미한 execute 거절 |
| cpu-layout/cpu-policy-set/cpu-host-prepare | 정책/anchor/receipt와 실제 affinity | 기존 CPU 보호·정지 변경 조건 유지. 조회의 live repin/자동 정지 없음 |
| recover-once/recover_once, operation-reconcile/recovery-reconcile | 원 attempt·원 수습 대상과 현재 guard | 기존 권한/원 기한·진단/execute 구분 유지. 조회와 수습을 합치지 않음 |
| diagnose, diagnose --details | 시각/boot 인자 또는 옛 status의 자산·시각 종합 진단 | details의 기존 공통 잠금·자산 해시 비용 유지. 반환 결과 렌더링은 잠금 밖 |
| check-xen/check-network/dhcp-ready, host-init/host-quiesce, autostart/shutdown-all/poweroff/reboot-board, console/firmware-replace | 기존 부팅·종료·PTY·동일 프로필 운영 handler | 기존 효과/성공 출력·대상 보호 유지. JSON 미지원은 실행 전 거절. firmware-replace는 계획/execute |
| xenova-boot-status, xenova-wifi status, xenova-broker list/check | systemd+runtime 사본 / wpa_cli+ip / 보호된 계정 세대 | 별도 도구의 의도된 계약. boot --watch만 안내 사본 게시, Wi-Fi 조회는 연결 변경 안 함, broker 목록은 비밀 비공개. 새 기능/자동 공급 미구현 |

설치 script/unit·first-install에서 `xenova-vm status`의 옛 종합 JSON을 파싱하는 호출은 찾지 못했다.
기존 운영 문서의 상태 확인 예제는 새 list 의미로 유효하다. 외부 미제공 자동화는 미확인이다.
소스 조사만 한 Wi-Fi/broker/boot 도구를 이번에 실서비스에서 실행했다고 하지 않는다.

최소 사용 예제(운영에서 이번에 실행한 명령이 아님):

```sh
xenova-vm list --short
xenova-vm intents --lifecycle all --json
xenova-vm intents UUID --timeout-ms 1000 --json
xenova-vm execution-context UUID --json
xenova-vm snapshot UUID --max-age-ms 1000 --json
xenova-vm control-operation --idempotency-key '원 key 한글' --vm-id UUID --json
xenova-vm diagnose --details --json
```

intents의 최소 반환은 VM UUID/name/guest kind/template 식별, autostart, lifecycle/generation,
desired_state/intent_revision/intent_lifecycle_generation/source/applicable다. 신호·실제 실행·CPU
관측 필드를 채워 넣지 않는다. 누락 의도는 unknown/null/false이며 source도 추측하지 않는다.
목록은 기본 REGISTERED, lifecycle all/RETIRED/PURGED filter와 단건 UUID를 지원한다.
단건과 lifecycle filter의 동시 지정은 거절한다. 등록/intent 오류를 빈 목록으로 바꾸지 않는다.
같은 이름이 다른 UUID로 재등록되면 과거 의도를 승계하지 않는다.

### 호환과 소비자 전환

| 사용 | 현재 의미와 전환 |
|---|---|
| 기존 full create, 생략↔원 값 명시, Manager/HTTP DTO | 기존 의미 유지. 원 UUID/key/spec/hash/기한·저장 형식 불변. 외부 API/header 변경 없음 |
| get/list/probe/execution-context | 기존 반환 shape·관측 cache 유지. probe/context는 더 강한 읽기 전후 정합성 대조를 공유 |
| status --json / 기본 status | 모두 list. 기본 status의 옛 종합 결과 소비만 diagnose --details로 전환 |
| no-JSON VM 제어/설정 | Manager envelope와 원 operation 참조를 일관되게 출력. 예전 raw 결과/무출력에 의존한 소비자는 envelope 처리 필요 |
| 명시했지만 무시되던 옵션·임의 create CLI key | 조용한 성공 대신 INVALID_SPEC. create key는 생략 또는 정확한 create:UUID. Manager/HTTP 기존 full 호출의 사용법은 변경하지 않음 |
| 기존 부팅/PTY 운영 명령 | 정상 출력/동작 유지. parser·실행 실패는 공통 오류 envelope/2·3·4 종료 대응으로 정리. 기존 stderr/1만 가정한 자동화는 조정 필요 |
| 일반 호출자·내부 설치 | journal 선택/raw 파일 해석 불필요. 내부 reader 공유 모듈·실제 import는 같은 설치 후보로 교체 |

Worker의 `PlatformObserver._refresh_intents`는 여전히 live list에서 의도를 취득한다.
[기존 D01-W01/W02 현재 본문](../requests/20261029-request-recovery-worker.md)에 실제 intents
호출/필드/오류·별도 Xen 확인·recover_once guard·회복 수락을 적었다. 실패 시 이전 cache를
현재 복구 허가로 쓰지 않아야 한다. 제품 코드는 대신 수정하지 않았다.
[HTTP D02/D08](../requests/20261029-request-http-spring-ui.md)에는 SerializedManager의 독립 fork,
_BUDGET_ACTIONS/call_with_budget의 실제 action·잔여 예산 연결과 상위 경합 수락을 남겼다.
관리 연동 문서·기준선·배포 원본도 현재 조회 범위만 갱신했다.

### 비용: 줄인 관측·대기와 추가 검증

`benchmark_query_cost.py`를 entry와 candidate-final2에 같은 fixture로 실행했다.
1/10/100은 **등록 metadata 규모**이며 동시 VM 실행 수가 아니다. Linux 한 개와 유효한
Zephyr 등록 metadata를 사용하고 intent는 한 개만 명시돼 있다. kernel/root 파일은 작은
fixture다. 각 점은 한 호출의 primitive 계수·로컬 시간이며 운영 성능/최악시간 표본이 아니다.
원 표본에는 common lock/observation slot 시도 수·실제 대기/보유 시간도 포함한다.

| 같은 질문·조건 | entry → 현재 실제 계수 | 보존/증가 이유 |
|---|---|---|
| get/list | 1개 get read/parse7, registry2, helper2, fsync3로 동일. 100개 list registry300/helper2/fsync201로 동일 | 기존 관측 정합성·사본 게시 비용 유지 |
| probe/context, 단건 | helper4→2, registry1→2, read4→7, parse5→7, host read4→9, journal0→1. fsync3 유지 | 공통 잠금1→0·관측 슬롯0→1. get과 같은 원 게시/현재 intent·incarnation/게시 guard를 공유하는 비용 증가 |
| templates, metadata100 | registry100→0, read/parse103→6, manifest1→2. helper/hash/fsync0 | 긴 잠금 대신 작은 catalog 전체 결과를 두 번 대조. metadata1에서는 read4→6 증가 |
| recovery-operation, metadata100 | registry100→0, read/parse101→2, 원 attempt1→2, host read2→1 | 단일 원 기록 변경 대조. 현재 host context 생성은 불필요. 원 hash 검증 유지 |
| control-operation | 원 journal2·read/parse2 유지, host read4→2 | 완전 검색/유일성·두 회차 유지. 읽기 전용 원 결과와 현재 host 관측 분리 |
| intents, 신설 | metadata1/100: registry3/300·read/parse9/306, intent2, 게시 성공 guard journal1. helper/hash/host read/fsync·공통 잠금·관측 슬롯 모두0 | 전체 등록 완전성·세대 대조 비용은 O(N)으로 남음. 이력 전체 검색이 아니라 해당 등록의 원 게시 guard |
| status | 옛 진단 helper7→새 list2. metadata1 read6→9, metadata100 read303→306 | **질문 자체의 명시적 변경**이다. 같은 진단을 빨리 했다는 성과로 계산하지 않음. 진단은 details에 보존 |

intents는 live list를 빠르게 위장하는 cache가 아니다. 필요한 Xen 관측을 요청하면 기존 비용을
지불한다. catalog 두 회차·probe/context의 guard 증가를 숨기지 않았고 대용량 해시·fsck·새
fsync·전역 잠금·등록 mirror·TTL cache를 추가하지 않았다. 원 helper/관측 슬롯과 자원 종료는
기존 소유 경계다. 로컬 비용표의0은 그 경로에서 계측한0이며 파일 I/O 전체가 없다는 뜻은 아니다.

### 수락·원 로그·실패와 재실행

native Python3.12.3, WSL Ubuntu24.04의 task 사본에서 다음을 실행했다.
`regression.sh candidate-final2`의 PYTHONPATH는 시험/first-install/guest 공통·플랫폼·기존
Gateway/MQTT/dom0-manager 소스를 명시한다. 제품 설치 검증에서는 이 source PYTHONPATH를 쓰지 않았다.

- 관련 회귀 `unittest -q`: 296개 실행, 1개 skip, 나머지 통과. 생성/기본값·의미 비교·CPU·lifecycle owner·조회/예산·진단·종료/부팅 회귀를 포함한다.
- 최종 집중 `test_xenova_query_consistency`: 22개 통과. 위 회귀와 대부분 겹치며 합산하지 않는다. 마지막 동일 이름 재등록 assertion은 별도 집중 실행에 추가됐다.
- Q04는 `test_public_cli_queries_and_key_lookup_finish_before_other_restore_barrier`: 실제 CLI→Manager→reader, A fsck 해제 전에 B 조회와 stop 원 phase completed. 이후 A도 성공.
- Q13은 느린 renderer를 멈춘 동안 다른 요청의 실제 shutdown 완료와 깨진 pipe 후 원 완료 replay를 함께 확인했다. Python Manager 출력/trace 격리는 기존 회귀를 재사용했다.

| Q 수락 | 연결한 실제 assertion |
|---|---|
| Q01·Q02 | FactoryQueryTest 실제 grow/factory/register의 두 Linux, 일반 Zephyr bundle/create, 저장 의도 누락/손상, Xen/host/자산 validator 호출 실패 hook·전후 파일 불변 |
| Q03 | intent 변경 유한 재회차, retire 중 목록 변경, 동일 내부 이름 새 UUID 등록에서 과거 intent 미승계. 계속 변경되면 TARGET_CHANGED, 이후 정상 조회 |
| Q04·Q10 | 위 lifecycle CLI barrier와 공통 lock 보유 조회, 기존 read-progress의 느린 get/list·다른 VM stop·슬롯 포화 후 회복 |
| Q05·Q06·Q07 | 기본/compact/short 동일 결과·handler, 명시 details, 옵션 전달/금지·같은/다른 대상·반복 옵션·separator/equals/literal key |
| Q08·Q09 | 원 key/ID 선택자, retired 회수·원 기한 불변, ManagementBudgetTest의 중복/부분 열람/손상/다른 대상/부재/host/예산. 현재 host 실패도 원 결과 회수 |
| Q11·Q12 | snapshot UUID의 registry/Xen 금지·나이 불변과 기존 관측 역전/옛 execution guard. 이름 해석·목록/원장 순회·슬롯 예산 만료/잔존 helper 회수 후 정상 재시도 |
| Q13·Q14 | 느린/깨진 출력에서 다른 stop 진행·원 결과 유지, 요청별 fork 예산/trace, Manager 생성 금지 hook의 help·인자 오류 |
| Q15 | 기존 create compatibility의 양방향 자원 조합·비기본 원 값·기본값/현재 구성 변경·지원 journal 혼재·실패/수습/활성 replay |
| Q16 | 아래 정규 RPM·설치 ARM Python·실제 native wrapper의 help/intents/key |

현재 native 환경에 FastAPI/Pydantic/httpx가 없어 변경 없는 HTTP adapter 시험 1개는 skip이다.
과거 create-compatibility의 실제 HTTP 근거를 보존하되 이번 후보의 재실행이라고 하지 않는다.
현재 full CLI/Manager는 직접 회귀했고 HTTP 제품 소스/DTO는 바꾸지 않았다.

초기 실패도 보존했다. Windows mount에서 권한 시험이 성립하지 않아 ext4 사본으로 옮겼고,
child import 환경에 기존 Gateway/MQTT 경로를 보충했다. 변경 없는 systemd unit 전체 시험은
고정259 loader 지정 없이는 실행되지 않아 이번 회귀에서 해당 PackagingAndUnitsTest를 제외했다.
unit 미변경이며 과거6번 근거를 이번 재실행으로 세지 않는다.
reader 분리 후 예전 Execution.requests와 세 번째 host read에 지연을 넣던 두 시험은 실패했다.
주입점을 현재 순수 reader/두 번째 host read로 옮기고 deadline·오류·정상 회복 assertion을 유지했다.
separator 반례는 제품 parser를 고쳐 재실행했고, 새 이름 재등록 시험의 잘못된 fixture key는
fixture를 수정했다. 초기 로그·실패 사본은 PC 자료에 남겼다.

### 부분 포장·설치 수준과 종료 판정

기존 고정 Yocto 설정·cache를 복사한 task 전용 build에서 `BB_NO_NETWORK=1 bitbake xenova-platform`을 실행했다.
3863 task 중3845는 재사용, 영향 recipe의 install/package/QA/RPM 모두 성공했다.
WSLv2 VHDX 권고와 host kernel CONFIG_TASK_IO_ACCOUNTING 부재 경고 두 개는 남겼다.
신규 의존성·recipe/unit 변경은 필요하지 않았다. 전체 이미지·guest/ARM planner 재빌드는 하지 않았다.

변경 여섯 모듈과 기존 wrapper의 source→do_install→실제 RPM payload 바이트·0644/0755 모드가 일치했다.
고정 native RPM 도구의 shared-library 경로 누락, gzip tar 출력에 cpio를 적용한 검증 script 오류,
`/dev/null` 절대 symlink에 대한 안전 extraction 거절은 각각 도구 경로/형식 확인·필요한 일반 파일만
추출하는 방식으로 정정했다. 빌드 결과를 바꾸거나 설치 symlink를 없애서 통과시키지 않았다.

ARM Linux Python3.14.7/QEMU8.2.2는 실제 설치 모듈의 help/intents/key와 원 기록 불변을 확인했다.
이 smoke의 storage mount guard만 fixture로 대체했고, Xen 명령은0이었다.
별도 native private mount namespace/chroot에서는 실제 설치 `/usr/sbin/xenova-vm`을 빈 env·source
PYTHONPATH 없이 실행했다. task 소유 ext4 fixture를 private `/srv/xenova`·`/run/xenova`에 bind해
실제 mount 확인도 통과했다. host의 실제 /srv/run·서비스·VM은 변경하지 않았다.

마지막으로 변경 Python12개 AST, 새로 연결한 로컬 문서 링크/anchor20개와 diff whitespace를 확인했다.
기존 CRLF 정규화 안내는 남기고 무관한 generated/vendor 파일을 고치지 않았다. HEAD·stage 상태를
대조해 commit/push 미수행과 이번 소유 파일 범위를 확인했다.

현재 하위 로컬 구현·관련 회귀·부분 RPM/설치 경로 수락은 마쳤다. Worker/API의 실제 소비 연결,
optional HTTP/UI·Spring, 운영 정책 공급·전체 설치본·실제 Xen/보드/SD·7번 수락은 미수행이다.
MQTT 자동 공급·Spring 등록 통지·실제 firmware/신호 선택의 신규 연결은 앞선 조사 위치에 남긴다.
이번 조회 완료를 그 기능의 완료나 모든 외부 장애에서의 제어 기한 보장으로 확대하지 않는다.


<a id="vm-provisioning-integration"></a>
## 2026-11-02 OS 공통 VM 자격 공급·등록 통지·신호/배포 연결

이 절은 앞선 연결 조사·CLI 정합화 뒤 승인받은 A~D 제공자 구현이다. 시작 후보는
`feat/platform/gateway-history-driving-app`, HEAD `8a23b8a700ebf7093e35e1c07dbd8f1864d16260`이며
착수 diff/untracked는 없었다. HEAD를 바꾸지 않고 소유 파일을 수정했다. 실제 소스·시험·산출물을 근거로
아래 상태를 정리하며 지시문 자체를 완료 증거로 사용하지 않았다. 최종 source/hash·원자료·명령·package는
[evidence JSON](evidence/vm-provisioning-integration-20261102.json)에 모은다.

### 이제 가능한 정상 사용과 원본

기존 full/optional create와 생략↔원 확정값 명시의 동등 replay는 그대로다.
신규 Debian/Ubuntu는 private 검증 복사 안에서 실제 Agent env를 받는다. factory는 공용 seed에 비밀을
넣지 않고 host UUID 계획 뒤 같은 공급부를 호출한다. Zephyr는 같은 UUID 암호를 준비하고 실제 start의
새 execution에 기존 Store/receipt를 적용한다. create 완료 replay는 이 일을 다시 실행하지 않는다.

```sh
xenova-vm provisioning 850e8400-e29b-41d4-a716-446655440001 --json
xenova-vm provision 850e8400-e29b-41d4-a716-446655440001 --execute --timeout-ms 10000 --json
xenova-vm registration-notify 850e8400-e29b-41d4-a716-446655440001
# 아래만 명시적인 HTTP 1회 송신이다. 설정·등록 대상·권한이 필요하다.
xenova-vm registration-notify 850e8400-e29b-41d4-a716-446655440001 --execute --json
```

기존 full HTTP adapter는 제품 코드를 바꾸지 않고 201/응답 필드·동일 POST 409 회귀를 확인했다.
새 registration-only 경로는 현재 Spring 제품에 없으므로 제공자 계약안·실제 sender·HTTP fixture로
구현했다. **Spring 합의·수신 구현·DB 종단 수락과 하위 로컬 제공 완료는 다르다.**
정확한 입력·응답·private 파일·설치/API 예제는 [VM 공급 계약](../contracts/vm-provisioning-v1.md)이 원본이다.

### 무엇을 어떤 기준으로 수정했는가

| 묶음·확인한 문제/누락 | 실제 변경과 기준 | 보존한 보호/비용 |
|---|---|---|
| A / 계정 helper가 생성/factory에 연결되지 않음 | `xenova_provisioning.ensure/apply_linux`를 Linux private root와 FirstBoot UUID 계획 뒤에 연결. Zephyr는 default preparer의 private firmware·기존 대상 lease 아래 공급한 뒤 등록 | broker가 암호/해시 원본. 공용 template·seed·firmware에 비밀 없음. root 0700/0600 adapter 사본, 같은 UUID 재사용 |
| A / 네트워크 능력·역할·현재 신호 혼동 위험 | `xenova_guest_profile.from_manifest/validate`의 한 원천. 검증 Agent receipt와 source/config로 채널·기대 신호·state model 해석. 명시 false/미선언 null/무효 형식과 지원 adapter 불일치를 나눔 | OS 이름으로 권한 확대 없음. Agent/Publisher 생존과 앱 진행/READY 분리. 미선언 stateless 승인 없음 |
| A / interrupted hash 입력을 hash로 오인 | 기존 `xenova_broker.hash_new`가 원 plaintext staging과 완료된 실제 Mosquitto hash를 구별해 같은 암호로 재개. generation 게시/원 기록·부모 fsync는 유지 | 평문/해시 값을 출력하지 않음. 혼합·손상/원문 소실을 정상 부재나 새 암호로 보정하지 않음 |
| A / 명시 제거와 단순 부재의 구별 | 현재 manifest의 revoked fence와 `CredentialRevoked`. 자동 ensure는 `CREDENTIAL_REVOKED`, 다른 원본 오류는 안전한 원인/chain을 가진 `CREDENTIAL_SOURCE_UNAVAILABLE` | 회전/복원은 기존 명시 broker 절차. 삭제한 자격 자동 부활 없음. 기존 오류 종류/코드를 합치지 않음 |
| A / 실제 송신과 ACL 불일치 | Agent의 기존 UUID client가 쓰는 자기 VM v2와 drive/link out·승인 Gateway in을 정책에 추가. 기존 generator로 비밀 없는 개발 ACL 재생성 | VM의 vehicle.control/다른 VM 신호 쓰기 추가 없음. 실제 두 broker의 자기 v1/v2 정상 송신과 타 VM 사칭 거절 |
| A / 실행별 신원의 이중 발급 위험 | `Platform.start` binding 뒤 기존 Bootstrap·`/srv/xenova/drive-rejoin/UUID/execution` receipt/lock 공유. `credential_file/receipt_directory` 제공. 변경 없는 Worker `ProductionBootstrap.refresh`로 원 신원 소비 | 영구 암호 유지. 새 incarnation만 새 boot/app/publisher. Store applied는 guest 소비/건강 증명이 아님. 옛/uncertain receipt 자동 덮기 없음 |
| A / private Zephyr 공급 실패 뒤 진행 막힘 | 완성된 원 private firmware만 대상 lease를 다시 잡고 원 key/명세/자산을 검증해 공급/게시 재개. 명세는 공급 전과 현재 원장 확인 뒤 동일 규칙으로 대조 | 원 operation/첫 오류/예약 유지. 복사 1회·같은 원 암호, 손상/불완전 파일 삭제·재복사 없음. Linux 명시 실패 수습은 별개 |
| B / 기존 Spring create는 UUID 발급+Pi create/start | 별도 등록-only 계약안, 실제 codec/sender·같은 수동 CLI·HTTP 수신 fixture 구현. 기존 `/api/v1/vms`를 역호출하지 않음 | 기존 HTTP full 호출/201/409 유지. token은 root private 설정, body에는 공개 등록 구성만 포함 |
| B / 응답 유실·재부팅의 자동 중복 위험 | create 완료 journal/factory complete progress에 자동 기회를 먼저 소비. 이후 독립 유한 프로세스에 공개 body 전달 | 자동 최대 1회. 기록 직후 중단하면 0회 가능. replay/boot/restore/복구로 보충하지 않음. redirect/retry/후속 GET 없음 |
| B / HTTP·DNS·부가 저장의 원 응답 간섭 | 공통/대상 FD를 넘기지 않는 짧은 PIPE_BUF 이하 body 전달. optional 결과 fsync도 자식 소유. slot 4개, 실제 resolver 정체를 감독자가 기한에 종료·회수 | 새 daemon/outbox/무제한 thread 없음. 느린 sink 중 B 실제 stop·원 결과 완료. 필수 등록 journal fsync는 그대로 |
| C / 다음 선택과 살아 있는 이전 실행 혼동 | `Execution.prepare_launch`가 검증 loader의 `signal_profile`을 캡처. 공개 provisioning은 초기 template 참조/다음 profile/현재 capture·supply를 나눔 | 순수 intents에는 Xen/Store/발급 추가 없음. 새 source를 옛 실행에 붙이지 않고 capture 부재는 unknown |
| C / 실제 planner가 factory 선택에 없음 | 실제 고정 ARM warm-lattice-lab build→`package-xenova-drive-template.py`→catalog lock/defaults/assembly/FirstBoot strict 지원 프로필 연결 | Starter의 과거 입력/일반 호출 보존. 가짜 hash/Starter/시험 adapter로 제품 firmware를 대체하지 않음. 차량 값은 명시 lab |
| C / 코드 생성의 Windows/LF 해시 불일치 재현 | generator UTF-8/LF를 고정하고 같은 도구로 재생성. 원 protoc golden은 보존하고 새 primitive와의 configuration 거절/현재 참조 정상 수락을 짝지음 | planner/tracker/충돌 계산은 바꾸지 않음. 새 asset hash와 실제 ARM/C 테스트를 연결하고 옛 사본을 현재 구성으로 승인하지 않음 |
| D / 설치 모듈·정책 의존/검증 누락 | 플랫폼 recipe에 새 모듈·helper·비활성 설정 포함. broker library subpackage로 기존 bringup 의존 순환 회피. assembly의 설치 바이트 검증 목록도 확장 | 별도 중계/범용 서비스 없음. 같은 내부 writer cohort 교체와 외부 HTTP 호환을 분리. 운영 설정/서비스는 미변경 |

### 지원 프로필과 전달/소비 대응

| 입력·자산 | 일반 생성 / factory | 실행·소비와 기대의 한계 |
|---|---|---|
| Debian·Ubuntu의 실제 Agent 설치 receipt | 같은 supplier→private `/etc/xenova/agent.env`; factory는 host UUID를 `platform-identity.json`으로 주고 새 guest identity helper/unit 뒤 Agent 시작 | 변경 없는 AgentSettings/build_client/TelemetryPublisher 사용. Agent heartbeat/v2 지원이며 앱 heartbeat/업무 정상은 미확인 |
| actual drive warm-lattice-lab | 작은 firmware private 준비·원 UUID 자격 / 같은 실제 ARM template A/B의 서로 다른 UUID·계정 | start/restart/recover의 실제 binding→기존 Bootstrap codec·Store. AUTO stateful·MANUAL stateless·Standby progress idle |
| 알려진 Starter / 미선언 자산 | 검증한 선언만 해석. MQTT 선택이 없으면 공급 skip | Starter app heartbeat false, 미선언 null, malformed는 오류. 관리 가능한 VM을 Agent/Gateway 부재 때문에 전역 차단하지 않음 |
| 별도 world broker | root private world 입력→실제 codec 검사→기존 drive 설정에 world만 설치 | Pi 암호를 Jetson에 전달/재사용하지 않음. 원 broker/mapper 설치·ACL 승인은 Jetson 담당. world 부재는 AUTO 입력 unavailable이며 MANUAL/VM 관리와 독립 |

### 28개 수락 묶음과 실제 경계

제품 test entrypoint는 `platform/tests/` 기준이며 표의 기존 회귀는 이름만 바꿔 복제하지 않았다.
Store·mount/guest OS·Spring DB는 외부 fixture다. 네이티브 hash·broker/socket/process·Agent/Worker
소비 parser·C decoder·실제 ARM 컴파일/패키지는 각기 실제 코드를 실행했다. 설치/실보드 수락으로 확대하지 않는다.

| ID | 재사용/추가 assertion과 정상 회복 |
|---|---|
| A01 | `ProvisioningHooksTest.test_both_linux_create_hooks_use_real_preparation_supplier_and_agent_parser`, Zephyr create hash barrier. 두 Linux 분기·private root·원 replay |
| A02 | 실제 `FirstBoot.register` UUID 계획 뒤 Linux 공급·완료 boot skip, 실제 ARM template의 A/B 독립 복사/등록/공급. Linux mount/OS는 명시 fixture |
| A03 | 기존 `CreateCompatibilityTest` 양방향 자원 표현/동시 key/응답 유실 + 완료 replay 공급/새 hash 없음 |
| A04 | 실제 Platform start→stop/start→recover_once의 세 실행·원 암호 유지·새 receipt; 기존 reboot/autostart/host 변경 guard 회귀 |
| A05 | unknown/false/invalid·guest adapter 불일치와 원 role ACL. 실제 Agent v1/v2 정상 송신·다른 VM 쓰기 거절 |
| A06 | hash launch EIO·hash 후 ENOSPC·기존 generation rename/process kill·post-pointer fsync 실패. 같은 plaintext/기존 hash로 지원 재개 |
| A07 | broker 기존 권한/symlink/손상/원본 소실 차단, revoked 명시 복원 뒤 정상 공급. 새 암호 fallback 없음 |
| A08 | reload/offline은 unconfirmed·next_start, 저장 오류와 분리. 격리 실제 broker 회복·다음 정상 인증 |
| A09 | 같은 VM supply의 새 generation/hash/reload 0, public status의 한 read/parse·쓰기 0. 기존 내구 재확인 fsync 1 유지 |
| A10 | 실제 private env→AgentSettings→Agent client/v2, Bootstrap→제품 native Zephyr C decoder. Store는 외부 대역이며 실제 guest 수락 아님 |
| A11 | 격리 Pi/world 두 broker의 독립 인증 영역·동시 다른 Client ID. world import는 실제 codec, Jetson 제품 설치는 미수행 |
| B01 | 실제 일반 create 완료/factory complete hook→별도 sender process→실제 HTTP fixture, 동일 등록 UUID |
| B02 | slow response·실제 resolver LD_PRELOAD 정체→supervisor의 자식 종료/회수. 이후 정상 명시 송신 |
| B03 | 서버 저장 뒤 EOF→delivery-unknown, 자동 후속 0, 명시 동일 UUID 재송신→중복 행 0의 수신 계약 fixture |
| B04 | 완료 journal 소비 뒤 launch 미수행→replay/완료 boot가 보충하지 않음. 원 등록 유지 |
| B05 | 4 slot 포화/다섯 번째 skip·회복 뒤 정상 manual, 두 factory 등록·일부 실패 분리 |
| B06 | 등록-only route만 실제 POST. 기존 Spring create 소스는 UUID 발급/Pi 호출임을 대조. 실제 Spring DB transaction 교차는 담당 제품 미수락 |
| B07 | config 권한·부재, 403/redirect·duplicate JSON·oversize·접수 응답을 확정으로 처리하지 않음. 운영 TLS/token은 미공급 |
| B08 | 실제 human/JSON CLI·무관한 force 옵션 거절·retired 대상 거절·same sender. VM/암호/디스크 생성 없음 |
| C01 | Starter false·Agent true/없음 false·미선언 null·malformed 오류, app_health_asserted=false의 실제 Agent v2 |
| C02 | verified loader capture와 다음 profile을 다르게 읽고 원 실행 불변. transition/실패/새 실행 기존 회귀 |
| C03 | actual warm AUTO stateful/MANUAL stateless·Standby idle 선언과 이전 planner/Gateway 준비/계산 보호 재사용. 선언만으로 READY 없음 |
| C04 | 실제 ARM source/BUILD_RECEIPT→새 template envelope→lock→factory strict validator·A/B seed→등록/공급 |
| X01 | A hasher barrier 유지 중 B intents/원 operation과 물리 stop·원 결과 완료. 기존 restore/firmware/usage/DHCP barrier 재사용 |
| X02 | A 등록 통지 HTTP 대기 중 원 create 응답·B 실제 stop/원 결과 완료. caller optional fsync 금지 hook·slow resolver/자식 회수 |
| X03 | 기존 CLI·Manager·기본값/동등 요청·CPU/lifecycle·실패 수습 회귀와 변경 없는 실제 HTTP adapter 201/409 |
| X04 | 고정 recipe QA/RPM→설치 바이트/모드·독립 target 모듈→ARM Python/QEMU의 실제 HTTP fixture 송신. 전체 이미지/실설치는 별도 |
| X05 | source/제공 archive의 비밀 배제, 실제 실패 뒤 정상 회복. 테스트 임시 credential/Authority/환경 파일은 제공 archive에 넣지 않음 |

### 비용·검증과 포장 수준

최종 검증 사본은 WSL `/home/ssafy/xenova-provisioning-20261102/release3`다. 아래 결과는
중복 합산하지 않으며 `release2`의 drive/C 입력은 최종 사본과 같은 소스임을 지문으로 대조했다.

| 실제 실행 | 결과·수락 범위 |
|---|---|
| `test-a.sh release3` | 149개 통과, skip 없음. 실제 발급/해시·일반 준비/factory hook·두 격리 broker·Agent parser·새 실행 Bootstrap/C decoder·HTTP/CLI·중단/회복 |
| `regression.sh release3` | 297개 실행·실패 0·skip 1. CLI/의미 비교·CPU·lifecycle·실제 stop·비용/진단/수습 보호. native FastAPI 환경이 없는 이 실행의 HTTP skip은 아래 별도 실행으로 확인 |
| `http-compatibility.sh release3` | 변경 없는 실제 HTTP adapter 9개 통과. 기존 full 호출의 201·동일 POST 409, 새 하위 의미 비교와 저장 혼재 |
| `gateway.sh release3` | 252개 통과·3개 skip·기존 protobuf 경고 1. Gateway 소스는 이번에 미변경. 이력·IPC·출력 보호·취소·상태/feedback 회귀이며 skip은 이번 통과에 포함하지 않음 |
| `test-drive.sh release2` | 실제 planner/tracker/world 40개 통과·기존 protobuf 경고 1, native CTest 6개 통과. 알고리즘 대역·고정 정답으로 교체하지 않음 |
| `verify-firmware.py release3` | 현재 입력 308개의 ARM BUILD_RECEIPT 지문 일치. 실제 firmware `11f4a75c…e29e87`, 선택 manifest `893b3e6f…bb5d87f`, 전체 hash는 evidence |
| 정규 BitBake·payload 검증 | `bitbake xenova-platform xenova-mqtt-broker`: 3990 task 성공. 정규 RPM 3종과 설치 파일 36개의 바이트·모드 일치. 캐시가 생략한 broker install image는 실제 `-c install`로 만든 뒤 대조 |
| target ARM/QEMU | 제품 source PYTHONPATH 없이 Python 3.14.7/OpenSSL 3.5.8 import·provisioning/통지 plan·실제 HTTP 1회. 격리 chroot의 설치 Python entry/help 확인. `xenova-vm` shell wrapper는 바이트/모드·고정 전달 argv를 대조하고 그 Python entry를 실행 |

BitBake 경고는 WSL VHDX 안내와 host kernel의 process I/O accounting 미설정 두 가지다.
경고를 숨기거나 라이브러리를 올리지 않았다. 동일한 Gateway 전체 시험의 앞선 병행 실행에서는 짧은
이력 창을 쓰는 두 assertion이 실패했으며 원 로그를 보존한다. 다른 빌드/회귀가 끝난 뒤 같은 Gateway
소스·timeout·시험으로 위 252/3 결과를 얻었다. 이를 모든 저장장치/부하에서의 지연 보장으로 해석하지 않는다.

신규 공급 기능의 비용을 과거 무공급 경로의 속도와 나누지 않았다. 실제 native Mosquitto hash 도구와
같은 tmp fixture를 사용한 한 표본에서 최초 공급은 text read 13·JSON parse 6·hash batch 1·generation 1·
reload 요청 1·필수 fsync 32였다. 같은 VM 재확인은 read 15·parse 7·hash/generation/reload 0·fsync 1,
공개 status는 read 1·parse 1·쓰기/hash/reload 0이었다. repeat의 fsync 1은 기존 writer의 원 게시
내구 재확인이라 유지했다. 시간 표본은 evidence에 남기며 실제 SD/운영 상한으로 쓰지 않는다.
기존 조회 benchmark의 1/10/100 metadata × 9개 action, 총 27조건은 착수 HEAD 사본과 최종 사본의
read/parse/외부 호출/fsync/잠금 계수가 모두 같았다. JSON 출력이나 이번 공개 profile 조회를 이유로
기존 조회에 새로운 자격 발급·전체 payload 검증·Xen 호출을 붙이지 않았다.

등록 통지의 기회 표시는 기존 완료 저장과 합쳐 별도 outbox를 만들지 않았다. 공개 body는 한 번의
4096-byte 이하 pipe로 보내고 optional 결과 저장·HTTP는 자식 소유다. 실제 resolver 정체·slow reader와
slot 포화 뒤 정상 요청 진행을 검사했다. 모든 커널/공유 저장장치 장애에 대한 hard deadline 보장은 아니다.
원 create의 prehash 0·검증 root copy 1, 원 결과 replay의 새 물리 효과 0과 오류·최초 원인/수습 보호는
기존 비용/기능 회귀에 연결했다. 오류 코드/로그 개수 감소를 개선 지표로 사용하지 않았다.

ARM firmware는 고정 Zephyr 4.4.0·SDK/toolchain과 실제 warm-lattice-lab을 새로 빌드했다.
공개 선택은 `zephyr-drive-lattice-lab-arm64`, 배포 버전 `1.0.0-lab.1`이다. 실제 binary/manifest/source hash는
lock·evidence·BUILD_RECEIPT에 연결한다. 아직 없는 image/template를 가짜 hash로 채우지 않았다.
Linux 선택 1.0.2의 immutable hash는 유지하고 다음 template builder의 profile/seed helper 입력만 연결했다.

부분 포장은 `xenova-platform`, `xenova-mqtt-broker`, `xenova-mqtt-broker-provider` 정규 recipe를 고정 환경에서
빌드·QA·RPM 생성해 source→image→RPM payload/모드를 대조한다. `/usr/lib/xenova`의 제공 모듈과 wrapper,
공통 정책·비활성 config 예시를 실제 target root에 설치해 ARM Python 3.14.7/QEMU로 조회/계획·HTTP 송신을
실행한다. 개발 제품 source PYTHONPATH를 주입하지 않는다. target의 실제 서비스/하드웨어·전체 이미지와 다르다.

### 실패 기록과 보완 기준

초기 native fixture의 plaintext/hash 판정 실패·완료 hash 재입력 방지, private Zephyr를 너무 일찍 게시해
intents가 실패한 반례, original workspace 재개 차단을 보존했다. 완성 private 검증 뒤 공급/게시, 같은 원
암호/작업·복사 1회로 보완했다. 한도 증가/파일 삭제/새 key로 시험을 통과시키지 않았다.
CLI 새 action의 budget allowlist 누락과 새 분기에서 control response_deadline을 inf로 덮은 회귀도
기존 budget assertion으로 발견해 수정했다. 기본값/기한 보호 시험을 완화하지 않았다.
최종 동시 빌드 중에는 잘못된 통지 설정을 0.1초 자식 대기로 다시 판정하던 경로가
`configuration-invalid`를 `delivery-unknown`으로 바꾸는 반례를 발견했다. supervisor가 확인한
설정 오류는 송신 자식을 만들지 않고 그대로 기록하도록 고쳤다. 실제 수동 호출과 fork 금지 assertion을
함께 통과시켰으며 오류 종류를 줄이거나 대기 시간을 늘려 통과시키지 않았다.

ARM 첫 검증은 CRLF로 인한 primitive manifest 불일치를 실제 재현해 실패했고 생성기를 고쳤다.
Yocto의 미존재 python3-ssl 의존성과 broker wrapper의 직접 python3-core QA 실패는 고정 manifest/pkgdata에
맞춰 수정했다. host CMake/pytest 경로 누락은 기존 고정 도구 경로로 해결했으며 새 설치/pip 업그레이드는 없었다.
이전 후보·실패 로그는 원자료에 보존하며 이후 통과 수치와 중복 합산하지 않는다.

### 인계·미수행·다음 수락

- Worker: 현재 요청서 D01/D03의 `ProductionBootstrap.__call__/refresh`와 VM별 credential 경로·같은 receipt_dir,
  기대 신호 소비를 연결한다. 원 attempt·Manager guard·Gateway 보호·재합류 정책은 기존 책임이다.
- Spring/HTTP: 등록-only route/DTO/auth·DB 중복/늦은 요청 계약을 합의/구현한다. 운영 endpoint/token을 공급하고
  원 Spring 생성 transaction 교차를 실제 제품으로 수락한다. 기존 full 호출은 새 버전/header 없이 유지한다.
- Linux Agent: 실제 두 OS guest에서 identity/env/unit→v1/v2·collector/앱 차이를 수락한다. Agent 내부 제품은 미변경이다.
- Jetson: 별도 broker 계정/ACL·private world 입력을 제공하고 실제 mapper/두 연결의 장애·회복을 수락한다.
- 플랫폼 설치: 같은 내부 provider/Manager/Bootstrap/guest helper cohort를 drain/import 대조해 설치한다.
  새 정책과 기존 broker store hash가 다르면 기존 initialize 계획/명시 승인으로 정책만 반영한다. 자동 암호 회전/이관은 없다.

현재 담당자 요청서·공통 계약·CLI/부팅/배포/빌드 문서를 개정했다. 원 상세 ID/이력은 보존했고 상대 제품 코드는
대신 수정하지 않았다. 실제 Spring 수신/Worker 소비/Jetson 계정·새 대형 Linux rootfs·전체 이미지·보드/VM/EC2·
운영 broker/DB/서비스·실차/재무장·7번 수락·commit/push는 수행하지 않았다.

공개 제공물·재현 스크립트·정규 RPM·실제 drive template·빌드 receipt·원자료 manifest는 PC
`C:/Users/SSAFY/Documents/Codex/2026-11-02/vm-provisioning/delivery-release3/`에 모았다.
임시 VM root·credential/token·Authority·raw bootstrap 파일은 이 묶음에서 제외했다.
source changes는 위 HEAD에 적용하는 변경 묶음이며 전체 이미지나 운영 설치 완료본이 아니다.

## 2026-11-02 — Red Bull Ring 도로 우선과 자동 종단 보완

기존 합성 짧은 지도 대신 저장소 simulator의 Red Bull Ring geometry를 84.336m 시험 축척으로
사용했다. 이전 13개 장애물 완주는 접촉/바깥 경계 보호만 확인했으며, S4/S5의 빈 코너에서
도로를 벗어나 지름길을 택하는 품질 결함은 별개였다. 이번 수락은 **도로 안 우선·필요 우회·복귀**다.
시험 도로 폭 0.72m·64×64/0.1m rolling grid·차량/시계/odom·bicycle plant는 fixture이며 운영값이 아니다.

### 무엇을 어떤 기준으로 바꿨는가

- 원 boundary는 hard 통행 경계로 유지하고 `required_features` bit0와 기존 cell_flags bit3으로
  선호 도로를 구분했다. `mark_road`는 실제 생산자가 준 도로 geometry만 표시하며 occupancy·age·
  원 시각을 바꾸지 않는다. Python/C strict reader와 생성 schema, path/인계 `world_features`를
  연결해 서로 다른 도로 의미의 path 채택을 막았다. feature=0은 과거 호환이며 새 품질 수락은 아니다.
- 같은 heap/node pool에서 차체의 도로 밖 사용 거리를 길이/곡률보다 우선했다. 기존 4096-cell
  유도값 공간을 재사용하고 차체 inscribed disk를 반영했다. shoulder 색인은 고정 512B 추가,
  node는 48B 이하 유지다. analytic 종단이 도로 밖을 가르면 실제 primitive 탐색을 계속한다.
- 새 정책의 실제 주행 중 42.66m에서 자동 목표가 회전 grid 밖에 놓이는 기존 결함을 확인했다.
  기존 끝 여유와 4.5m horizon 안의 실제 reference 지점으로 제한했다. 꼭짓점의 0길이 구간은
  건너뛰며 새 점 외삽이나 grid/예산 확대는 하지 않는다.
- 다음 67.73m 정지에서는 목표 허용 범위의 3,969 자세 표본이 모두 실제 footprint 충돌이었다.
  `lp_start_local`은 원 collision index·cell 한도·deadline으로 자동 종단을 확인하고, 막히면
  같은 horizon에서 resolution 간격으로 더 가까운 reference 지점을 고른다. 다음 rolling map은
  장애물 너머 목표를 선택할 수 있다. 명시 `lp_start` 목표와 원 `merge_goal`·통과 근거는 보존한다.
  안전한 전방 후보가 없으면 거절하고 실제 새 정상 지도로 회복한다.

42.66m·67.73m 실패 원자료/바이너리와 수정 전후 시험은 PC
`C:/Users/SSAFY/Documents/Codex/2026-11-02/circuit-road-priority/`에 각각 보존했다.
중간 실패를 완주나 회귀 통과 수치로 합산하지 않는다.

### 같은 13개 배치의 최종 결과와 비용

| 항목 | 먼 쪽 배치: 이전 → 최종 | 우회 공간 쪽 배치: 이전 → 최종 |
|---|---|---|
| 기준선 진행 | 84.24 → 84.24 / 84.34m | 84.24 → 84.24 / 84.34m |
| 실제 모의 이동 / 모의 시간 | 79.64m/199.30s → 84.72m/212.00s | 81.14m/203.06s → 84.72m/212.00s |
| S4/S5 57~62m 기준선 최대 편차 | 82.2 → 8.8cm | 60.2 → 8.8cm |
| 같은 구간 차체+여유의 도로 밖 표본 | 492 → 0 | 561 → 0 |
| 새 탐색 수 / 채택 path | 548/106 → 266/131 | 634/127 → 266/131 |
| 새 탐색 평균 / p95, host ms | 228.8/530.6 → 122.6/365.7 | 211.0/482.4 → 125.2/367.3 |
| tracker 평균, host ms | 0.181 → 0.154 | 0.167 → 0.154 |
| host 전체, s | 132.51 → 41.14 | 140.53 → 41.86 |

두 최종 배치에서 actual trace와 채택 path는 같았다. 새 도로 우선 경로에서는 여섯 바깥 장애물이
출력을 바꾸지 않았다는 이 입력의 결과이며 일반적인 무영향 보장은 아니다. 각 10,600 표본에서
장애물 접촉·hard 경계/grid 이탈·tracker 오류 0, 차체+관측 여유의 최소 장애물 간격 0.167m다.
도로 밖 차체 사용은 V1~V7 주변의 7개 구간이며 모두 복귀했다. 빈 코너 2~13/57~62/78~83m의
차체 도로 이탈은 각각 0이다. 마지막 우회는 73.24m에서 정상 종결했고 최종 활성 intent도 0이다.
장애물 근처라는 이유만으로 모든 경로의 수학적 최소 이탈·전역 최적성을 주장하지 않는다.

266개 탐색 중 68개는 WORK_BUDGET, 67개는 NOT_FOUND였다. 새 실패 경로를 게시하지 않고
현재 유효한 path·명령 보호를 유지한 결과 모든 tracker 표본이 진행했다. 실패를 성공으로 합치거나
원 deadline을 늘리지 않았다. 노드 high-water 4,308/4,331은 원 8,192 이하이며 확장 4,096·
collision cell 1,500,000·2s 시험 planner budget·원 차량 속도/조향 한도도 유지했다.

시간은 WSL host에서 새/기존 실제 C 호출을 구분한 각 배치 1회 표본이다. 도로 의미와 실제 궤적이
달라진 기능 변경 전후이며 순수 미세 최적화 배수·ARM/20ms 실시간/실차 보장은 아니다. road mask
생산은 별도 평균 0.441/0.444ms로 계수했다. 원 계수·원 trace·스크립트·범위는 `comparison.json`과
`report.md`, 실패 회귀·후속 실행 명령은 같은 폴더의 로그에 있다.

### 최종 소스·인계·부분 포장 수준

관련 world/lattice/engine 회귀 **59건**, 별도 격리 broker의 실제 Gateway binary 사본/적용 회귀
**7건**을 통과했다. 고정 SDK로 최신 native_sim과 Xen ARM firmware를 부분 빌드/포장했다.
ARM source SHA-256은 `cd07f1501e3a3f78090e0e2c059b452a1e6bb80bed84316dacf1db50cb11fdf3`,
`zephyr.bin`은 `7d6e4073aef3929d29485e98153061b0368449f35e621807916215dfe82a663f`다.
계산 worker stack 8,192B와 기존 sentinel을 유지했다. direct frame 검사는 전체 호출 peak나 보드
stack high-water를 대신하지 않는다. `build-source-check.json`이 시험/빌드/현재 소스 일치를 확인한다.

기존 Gateway recipe가 `xenova_drive_world/*.py`를 0644로 설치하므로 새 `road.py`도 포함한다.
개발 소스 경로 대신 별도 설치한 모듈을 고정 target Python 3.14.7/protobuf 6.33.6의 ARM/QEMU에서
실제 import·road 표시·새 State 필드로 확인했다. 새 정규 RPM/전체 이미지·운영 설치 완료는 아니다.
`20261029-request-jetson.md` 현재 본문에 실제 import/생산 위치·실패 수락을 연결했다.
실제 mapper는 observed 도로/바깥 통행 영역을 공급하고 새 지원 후보를 설치해야 한다.

최종 두 완주만 `overview.png`에 그렸다. 개별 지도 조각이나 이전 정지 지점은 표시하지 않았다.
이전 코드/실패·기존 planner/Gateway 변경을 보존했고 타 담당 제품·운영 설정·서비스·보드·실차·
재무장·7번 수락·commit/push는 수행하지 않았다.


<a id="board-recovery-20261104"></a>

## 2026-11-04 실보드 복구 시나리오 재시험

작업 기준은 `feat/platform/guest-clock-sync`의 `983fa12`와 기존 1~5번 수정, 이번 diff다.
Pi의 설치본과 시험 후보를 분리했다. 설치본 API는 8081에 유지하고, 후보 HTTP와 Gateway는
loopback과 시험용 저장 경로에서 실행했다. 기존 실행은 Debian domid 6, Zephyr A/B 2·3이다.
Xen 장애 주입과 firmware 교체 대상은 이번에 등록한 시험 VM으로 한정했다.

### 재현과 수정

| 확인 지점 | 수정과 검증 |
| --- | --- |
| Zephyr start 뒤 자격 공급 실패 | 실제 start에 성공한 Xen 실행에서 `-b---- → ------ → -b----`가 관측됐다. 자격 공급의 첫 live 관측만 기존 예산 안에서 기다린다. 같은 부트스트랩의 이후 검사에서 빈 상태를 받으면 직전 live 확인과 host·execution ID·domid·Xen 고유 신원이 일치하는지 다시 확인한다. paused·shutdown·crash, 신원 변경, 의도 변경은 기존처럼 거절한다. 일반 binding 조회에는 완화를 적용하지 않는다. |
| 설치된 lab.2의 협조 종료 미확정 | 시험 VM의 `xl reboot`와 이후 `xl shutdown`이 각각 기존 15초 반환 한도를 넘겼다. XenStore에 `poweroff`가 남아 있었고 설치 ELF에는 `4dee6b3`의 `xsr_read_status` 심볼이 없었다. 현재 소스로 ARM 번들을 빌드해 시험 VM에 정식 firmware-transition을 적용했다. 새 번들에서 domid 11→12, 같은 reboot 키의 재전달에서 12 유지, 정상 stop 뒤 부재, 원 reboot·stop 기록의 completed를 확인했다. 브로커에서 v1 `app.lifecycle` 214건, v1 drive link 출력 23건을 받았다. |
| Linux 복사 뒤 게시 잠금 경합 | 첫 시험은 약 297초 후 공통 잠금 경합으로 원 작업이 preparing에 남았다. `266ae7a0-f894-41a1-adea-adc3d6c029ff`에 정식 `discard-private`를 적용해 원 기록을 FAILED로 종결했다. 준비가 끝난 소유자에게만 원 실행·호출 기한 중 남은 시간으로 기존 잠금을 기다리게 했다. 새 시험은 복사 직후 실제 flock을 1초 잡은 상태에서 264.74초에 등록을 마쳤다. 원 작업 `2e06856a-2709-4de1-a009-f6e8b1b28bba`를 명시값을 채운 같은 키로 회수했고, domid 13으로 기동했다. Agent heartbeat가 새 UUID에서 실제 수신됐다. `recover_once` `7233da39-53c6-4963-9f7a-4a4f3388bd9d`로 34.44초 뒤 domid 14가 됐다. Agent는 두 boot ID에서 총 6건이 들어왔다. 같은 attempt는 원 결과를 반환했고, 새 attempt의 옛 실행은 TARGET_CHANGED, 사용자 stop 이후의 attempt는 INTENT_MISMATCH였다. 이 두 시간은 서로 다른 경합 표본이다. |
| 다음 이미지의 ARM 선택 | 시험과 동일한 `zephyr.bin`을 새 불변 1.0.3으로 포장했다. binary SHA-256은 `c27cdc5944b17016dbb434c25ceb525acc562ba16e71afac6d32ff445e6738b9`, 공개 manifest는 `7f4df6201d5df2dca128bcd072228a365c57c6d96341b522d1d6c2e1034d8b18`다. 기본 lock, factory defaults와 조립 예시를 같은 버전으로 맞췄다. 설치 중인 A/B는 기존 자산으로 계속 실행했다. |

### 시나리오별 근거와 남은 수락

| 단계와 ID | 이번 증거 | 남은 실보드 수락 |
| --- | --- | --- |
| ① A1·E1·A4 | 기존 factory VM 실행과 신규 Zephyr·Debian의 별도 실행 신원을 보드에서 확인했다. 새 Zephyr에서 v1 앱 이벤트, 신규 Debian의 두 실행에서 Agent heartbeat를 받았다. Worker 회귀로 최초 미수신과 유예, 새 실행의 기준 초기화를 확인했다. | 새 SD 최초 등록, 첫 미수신부터 운영 Worker 상태 파일과 회복까지. |
| ② A2·A3·A5·AO1–AO4 | 실제 v1 수신과 기존 설치의 v2 생존을 구분했다. Worker/Gateway 회귀에서 미수신·무효·지연·중복·비인가 신호와 관측 불가를 시험했다. | 기존 A/B의 v1 재수락, 현장 ACL 부분 장애와 실제 장치 시계 대응. |
| ② B1–B4·BO1–BO3 | 제품 C 계산·지도·tracker 시험과 Worker/Gateway의 늦은 계산·모드 분리·출력 한도 회귀를 실행했다. | 현재 차량 입력, 실보드 마감 지연, 실제 변화율 및 장기 운용 수락. |
| ②·③ C1·C2·CO1–CO3 | allocator·진단 제한·건강 분리의 로컬 근거와 Linux 준비 자원 보호를 구분했다. | 실제 메모리 오염·스택 고갈의 출력 차단 지연과 보드 자원 측정. |
| ②·③ D1·D2·D3·D4·DO1·DO3 | Worker 복구 중복 제거, 공통 연결 실패 보류, 두 실행의 분리 회귀를 실행했다. 실제 시험 VM 처리 중 기존 세 VM의 domid가 유지됐다. | 운영 Agent/App의 로컬 복구 승격, 공유 backend 장애 주입, VM별 대역폭과 동시 손실 정책. |
| ④ F1·F2·F5·FO1·DO2 | Zephyr 같은 reboot 키 재전달과 원 stop 조회, 새 Debian 게시 경합과 원 실패 수습을 보드에서 확인했다. 하위 stop/복구 경합과 원 기한 회귀를 실행했다. | 운영 Spring의 원 key 회수, 실제 복구 중 동시 사용자 stop, 기간 단위 전역 복구 상한. |
| ⑤ E4·F4·G1 | 로컬 Worker·Gateway의 실행 결과와 건강 회복 분리, 저장 실패·중복 사건 경계를 확인했다. | 연속 장애의 안정 구간과 episode 예산 정책, 운영 저장 장애·회복 수락. |
| ⑥ H1·H2·H3·B4 | 로컬 warm 모드·승계·장치 codec 및 원장 회귀를 실행했다. 보드의 별도 Gateway 프로세스는 최초 기동과 재시작 모두 SAFE_STOP·DISARMED와 출력 미허가를 반환했고 중복 실행을 거절했다. 실제 ARM의 PTY·warm·보호 순서 fixture 20개가 순차 재실행에서 통과했다. | Jetson 실제 입력, 장치 적용·물리 정지, 전체 A→B→A 연속 승계. |
| ⑦ A4·AO3·F2·G2·GO1·H3 | 원 결과 조회, 로컬 손상 원장 및 재시작·사건 중복 보호 회귀를 실행했다. 실제 보드의 시험 Gateway는 SD의 시험 전용 Authority로 재시작했다. 손상된 원본을 거절했으며 자동 재무장·세대 초기화가 없었다. | 운영 host 재부팅, 운영 Authority 손상 복원, 운영 Spring 재시작과 장치 세션 재결합. |
| ⑧ E2·F2 | 시험 Zephyr와 Debian의 사용자 stop과 실제 Xen 부재, retire/purge를 확인했다. Debian의 기존 복구 요청은 stop 이후 현재 의도 대조에서 거절됐다. 원 stop 키 조회는 두 게스트 모두 completed였다. | 운영 UI에서 복구와 사용자 stop을 경합시키는 종단 수락. |

### 설치와 관측

- 설치본 부팅 journal에서 첫 설치 서비스 완료는 15.08초, 두 텔레메트리의 첫 실행은 약 10.07초였다.
  후보의 systemd 연결과 SD 조립 게이트는 별도 회귀로 검증한다.
- 후보 설정 생성으로 기존 계정을 사용한 Worker env 0600과 실제 A/B UUID를 확인했다. 운영 Worker를
  다시 시작하거나 새 env를 운영 경로에 쓰지 않았다. 설치본의 상태 파일 및 기존 A/B v1 수신은 별도 수락이다.
- 보드의 실제 journal을 시험 SQLite로 수집했다. 조회 200건과 커서 저장을 확인했고,
  후보 FastAPI의 인증된 `/api/v1/logs`에서 2건을 읽었다. CPU 조회는 200, 인증 없는 CPU 조회는 401,
  없는 UUID의 reboot는 404였다. 설치본 8081의 OpenAPI에는 CPU·reboot 경로가 없었다.
- `xl vcpu-list`에서 A/B hard affinity는 각각 1과 2, Debian과 dom0의 공유 마스크는 0,3이었다.
  실제 pin 변경, 호스트 재부팅, 시리얼 차량 연결과 Spring/화면 종단은 별도 수락이다.

### 검증 환경과 정리

- 최종 하위 Python 후보는 Linux native 파일시스템에서 복구·정지·부팅·등록·포장 회귀 204개를 통과했다.
  Windows 공유 마운트에서 실행한 두 fixture는 정책 파일이 group/world writable로 보였으며, 같은 권한
  검증을 유지한 Linux native 실행에서 통과했다. Worker와 Gateway 로컬 회귀도 각각 종료코드 0이었다.
- XenStore·netfront·진단 일반/ASan·UBSan 회귀 32개, C controller·health·state·codec 6개,
  lattice·world 44개를 통과했다. 실보드에서 큰 fixture를 병렬로 실행한 첫 시도에는 자식 시간 초과,
  긴 Unix socket 경로, 누락된 시험 전용 자료가 있었다. 경로·자료와 동시 부하를 분리해 위의 ARM
  Gateway 20개를 다시 실행했다. 병렬 PTY에서 관측한 10ms 제출 미확정은 별도 성능 관찰로 남긴다.
- 실보드에서 만든 시험 VM은 정식 stop/retire/purge로, 미게시 Debian 자산은 원 operation으로 정리했다.
  발급한 시험 계정 12개를 공식 revoke로 회수했고 기존 계정의 비밀번호와 기존 세 domid를 대조했다.
  원 작업 기록은 조회할 수 있게 보존했다. 회수한 Debian 시험 자격의 브로커 인증이 실제 거절됐다. 이후 12초 구독에서 기존 Debian heartbeat 2건, 기존 A/B v1 앱 이벤트 0건이었다.
- 1.0.3 전달 tar의 SHA-256은 5dfccc645ddc8842cc6de566c7ff59aead7e16df48310386e3f537d56e5f5086이다.
  Linux의 template release root에 풀면 `zephyr/xenova-zephyr-drive-lattice-lab-1.0.0/1.0.3`를
  현재 lock으로 읽을 수 있다. 새 전체 이미지 빌드·설치와 실제 장치 출력 수락은 남았다.



<a id="board-xenstore-v1-resume-20261104"></a>

## 2026-11-04 실보드 v1 발행 고착 진단

983fa12와 기존 복구·배포 작업트리를 기준으로 같은 Pi에서 재현했다.
Factory A/B의 netfront는 연결됐고 패킷 잠금 시간 초과는 0회였다.
Store 자격과 부트 신원은 현재 브로커와 실행 receipt에 맞았지만,
직접 브로커 구독에서 Debian heartbeat만 받고 기존 A/B의 v1 앱 이벤트는 받지 못했다.

- 계측 ARM의 v1 이벤트는 시험 VM에서 1,207건, 동료 시험 VM에서 564건을 송신한 뒤
  멈췄다. 이후 Store 문맥 조회가 연속으로 실패했고 마지막 오류는 -EBUSY였다.
- 늦은 시작 응답이나 종료 미확정 거래가 있으면 xs_transaction_start가 새 요청을
  거절하면서 늦은 응답이나 남은 거래를 정리하지 않았다. 새 호출은 자기 기한 안에서
  늦은 응답을 회수하고 고아 거래만 abort한다. 원 commit은 재전송하지 않는다.
  동일 거래의 ENOENT는 종료된 거래를 정리한 것으로만 처리한다.
  [Xen의 거래 조회·종료 구현](https://raw.githubusercontent.com/xen-project/xen/master/tools/xenstored/transaction.c)
- XenStore 수정 ARM의 zephyr.bin SHA-256은
  6739415b17699ba4861b46f54c713e8da6cc8df807c5d3768074dfa6323c9c41이다.
  XD_REPORT와 XD_CONTEXT는 기존 콘솔 요청에서만 큐 수락·실제 제출·최초 오류와 현재
  문맥을 보여준다. 늦은 시작과 미전송·늦은 종료는 native 및 ASan/UBSan에서 확인했다.
- 바인딩은 두 UUID/name/domid 목록, 고유 실행 ID 두 번, 현재 의도·admission을
  계속 대조한다. 전체 디스크 설정 목록을 제거한 비교에서 24건씩 모두 확인했고
  중앙값은 660.3→405.3ms였다. 순간 무표시 상태는 같은 기한 안에서 한 번만 재확인한다.
- Gateway의 CRC-16/CCITT-FALSE를 Python 표준 라이브러리의 C 구현으로 바꿨다. ARM의 동일
  63바이트 프레임 인코딩 CPU 시간은 1,267.0→222.2µs였고 바이트가 일치했다.
  Debian 검증 복사와 Xen 조회를 병행한 PTY 표본에서는 각각 50회 중 미확정 5→2건이었다.
  기존 10ms 한도와 STOP 보호를 유지했다.
  [Python crc_hqx](https://docs.python.org/3/library/binascii.html#binascii.crc_hqx)

실보드 PTY는 ESP32 대신 가상 장치를 썼고, Gateway 보호 시험은 mock 출력을 사용했다.

두 시험 VM에 같은 ARM을 적용한 뒤 135초 동안 15초 간격으로 실제 MQTT 수신을
확인했다. 두 VM 모두 구간마다 v1 앱 이벤트를 보냈고, 최종 콘솔은 누적 송신
5,115건과 4,151건, Store 문맥 정상 및 마지막 오류 0을 기록했다.
Factory A/B에는 순서대로 정상 종료, 정식 firmware-transition, 기동을 적용했다.
A domid는 2→9, B는 3→10이었다. 두 VM의 UUID·MAC·IPv4는 동일했다.
각각 45초 동안 새 boot ID의 AUTO/MANUAL v1 이벤트를 A 449/449건,
B 449/447건 받았다. 두 앱 모두 WAITING/NOT_READY였다.

현재 패키저, 기본 template lock, factory defaults와 첫 설치 예제는 1.0.4를 선택한다.
공개 manifest SHA-256은 21f6526e57c93866fcf009cf6ad21f8fe9f4499715854211545bc683a67104ea다.
정식 패키저에서 같은 manifest와 바이너리를 재생성했고 template workflow 10개가 통과했다.
별도 전체 SD 이미지 조립이나 운영 서비스 재설치는 수행하지 않았다.

임시 Worker와 mock Gateway를 분리 프로세스로 연결했다. 두 시험 VM을 동시에
검증하는 동안 새 ARM은 브로커에 계속 v1 이벤트를 보냈으나 실행 바인딩은 간헐적으로
만료됐다. 설치본은 Python 신원 helper를 사용하고 있어 기존 레시피의 ARM native
helper를 시험 PATH에만 넣어 대조했다. 같은 2초 관측 기한 안에서 후반 xl list -v와
고유 실행 ID 명령이 여전히 시간 초과했다.

동료 시험 VM을 제거한 뒤 기존 B의 정상 신호와 남은 시험 VM의 v1 이벤트를 실제
Worker가 현재 신원과 대조해 받았다. 시험 VM의 이전 boot ID만 Worker 수신 경계에서
차단했다. Gateway 보호 레코드 및 recover_once 제출은 0건이었고, 관측 오류가 반복된
뒤 시험 Worker가 중도 종료했다. 커널 로그에는 OOM이 없었다. 종료 원인과 자동 복구
종단은 미확정이다. 이번 수락은 기존 A/B v1 복귀와 시험용 원 보호 호출을 포함하며
실제 자동 재시작이나 차량 적용 수락을 포함하지 않는다.

시험 VM 두 대는 정상 stop·retire·purge로, 두 VM에 발급한 MQTT 계정 네 개는
공식 revoke로 정리했다. 기존 브로커 자격 15개의 값은 그대로였다.

정리 후 Factory A/B만 대상으로 정책 none의 Worker와 mock Gateway를 다시 띄웠다.
복사한 기존 applied receipt를 읽고 현재 Xen 실행을 검증했다. Worker가 A의
AUTO/MANUAL 273/271건, B의 300/300건을 수락했고 두 VM의 감시가 활성화됐다.
A 바인딩 오류가 한 차례 더 관측됐다. Gateway context의 시작과 마지막 표본은
SAFE_STOP/FAILED였고 출력 권한은 끝까지 false였다.


### 2026-11-04 Gateway STOP 제출 대기 계측과 MANUAL 재확인

이번 변경은 STOP의 소유·완료 전달 경계에 한정한다. 기준 HEAD는 983fa12이며,
기존 guest-clock-sync 작업트리의 미커밋 변경을 보존했다. 앞선 vehicle/state.pb
ACL 추가는 해당 보드에 적용된 상태로 시험했다.

| 확인한 구간 | 실측과 수정 기준 |
|---|---|
| 큐 접수 → 소유 스레드 기상 | 첫 표본 0.28ms, 별도 표본 약 21.1ms. 호출 스레드의 대기 만료와 실제 쓰기 실패를 분리 |
| 기상 뒤 수신 처리 | 첫 표본에서 STOP보다 앞서 3.44ms 소비. 대기 중인 출력부터 처리하고 쓰기 뒤에 RX·odom 진행 |
| 논블로킹 쓰기 호출 전후 | 첫 표본 벽시계 10.88ms, 해당 스레드 CPU 약 0.36ms. 커널·GIL·호스트 스케줄링 각각의 비중은 미분리 |
| 동기 호출의 10ms 만료 | 기존 코드가 계속 소유 중인 STOP을 OUTPUT_PATH_FAILED로 확정. 원 제출의 완료 참조를 보존하고 같은 결과 회수 |
| 새 명령 직후 ACK view | 이전 명령의 applied가 잠시 남는 경합을 전체 회귀에서 발견. 완료 알림 전에 최신 명령 참조를 게시 |

output_owner.py는 한 장치 스레드와 기존 한 칸 mailbox를 유지한다.
기동·주기 STOP은 StopSubmission을 반환하며 정상 pending마다 예외를 던지지 않는다.
verified_runner.py는 원 제출 기록 하나를 pending → accepted/failed로 갱신한다.
원 submitted_monotonic을 보존하며 serial_completed_monotonic은 actor의 회수 시각이다.
미확정 STOP이 있는 동안 새 STOP·DRIVE 제출을 보류한다. 직접 write 호출의
10ms 한도와 DRIVE 미확정 뒤 필수 STOP, 원 명령 만료 및 feedback 기한 검사는 유지한다.
필수 보호 저장 직전의 직접 STOP 제출은 기존 동기 경계를 사용한다.
실제 쓰기 실패·장치 소유자 종료 및 드문 내부 오류의 원인·traceback도 보존한다.

회귀는 기존 test_feedback.py와 test_provider_ipc.py에 국소 추가했다.
대기에서 깨어난 STOP의 RX 선행 방지, 원 STOP 단일 제출, 완료 전 DRIVE 차단,
실제 쓰기 성공 회수, OSError 실패 및 Authority 원본 보존을 검사한다.
Linux/WSL에서 저장소의 pytest 설정과 기존 시험 패키지, 보드에서 가져온 Python
의존성 소스를 격리 사용했다. 실제 보드의 protobuf backend는 upb이며,
WSL 회귀의 protobuf는 Python backend를 사용했다.

실보드 시험은 기존 A/B의 실행을 유지하고 실제 USB 장치가 없는 상태에서
명시한 /dev/pts 경로 하나에만 Gateway feedback-2를 연결했다.
가상 ESP32는 기존 run_lattice_pair.py의 byte-stream 물리 fixture를 재사용했다.
수동 입력 송신과 가상 ESP32를 독립 프로세스로 분리했으며 차량 수치는
기존 rc-test-1 / encoder-lab-1 시험값만 사용했다.

기존 Worker RejoinCoordinator와 실제 하위 Xen 조회를 사용하는 후보에서는
현재 binding의 원래 2초 관측 기한 초과가 반복됐다. DEADLINE_EXCEEDED에는
observation publication deadline expired, short lock deadline expired가 포함됐다.
시험 종료 후 admission 잠금의 보유 PID는 없었고, A 단독 관측 두 번은
411.0ms와 580.0ms에 성공했다. 잠금 고착이나 단일 helper 결함으로 확정하지 않는다.
마지막 앱 분리 시험은 실제 Xen 신원과 기존 applied receipt를 전후 대조하는
임시 binding 생산자로 Worker 경계를 대체했다. 변경·부재·관측 실패에서는
binding을 발행하지 않고 기존 3초 유효기간을 유지했다. 이를 실제 Worker
종단 수락으로 계산하지 않는다.

관측한 개선 후보에서는 출력 실패 0을 유지하며 원 STOP 결과를 회수했다.
하지만 MANUAL의 POSE_UNAVAILABLE·INPUT_STALE가 계속 관측됐고,
A의 MANUAL heartbeat는 대시보드에서 미수신이었다. 실제 발행 boot/app instance는
기존 applied receipt와 일치하고 진행 번호는 0이었다.
MANUAL 진입·계산 완료·heartbeat 화면 수락은 미완료로 남긴다.

A는 domid 11 / execution 18897f8f-896d-4e05-a393-08f524f3348b,
B는 domid 10 / execution 0d268d88-1d3f-4a14-b66e-71f1cf448e21을 시험 전후 유지했다.
Authority는 시험 전용 경로를 사용했고, 운영 Gateway 서비스 교체 대신
임시 후보를 실행했다. 실제 장치·물리 정지 수락에 해당하지 않는다.

최종 Gateway 회귀는 255 passed, 5 skipped, 88.50초였다. 최종 보드 후보는
STOP 60건을 회수했고 쓰기 실패 0, 종료 후 pending 0이었다. 이 회차는 A binding
확보 단계에서 끝나 MANUAL 입력 송신 단계에 도달하지 못했다. 앞선 입력 송신 회차의
대시보드도 MANUAL heartbeat 미수신이었다. 계측·시험 대역 원문과 소스 SHA-256은
로컬 2026-11-04/stop-profile/evidence.tar 및 summary.json에 보존했다.

## 2026-11-04 앱 메모리 계측·TCP 스택·정지 수습 후속

### 현재 제공 상태와 사용법

최종 펌웨어는 **1.0.7**이다. CLI/VM의 기존 입력과 MQTT v2.1의 토픽·봉투는 유지했다.
`xenova/v2/vms/{vm_id}/telemetry/metrics`에서 `family="app_memory"`와 `memory.app.*`를 읽으면 된다.
`labels.region`은 `drive_maps`, `drive_paths`, `drive_search_nodes`, `drive_odometry`다.
값은 bytes/count의 십진 문자열이며, 유효한 0과 null·경합·미지원·실패를 구분한다.
스택은 공식 콘솔의 `xenova telemetry threads`로 명시 요청한다. 자동 스택 스캔은 추가하지 않았다.

| 범위 | 최종 확인 | 남은 것 |
|---|---|---|
| 소스·실제 owner→Diagnostics→codec | 지도·경로·탐색·odom 점유/용량/최고 점유/반환·실패·경합, RAM 포함 관계, 명시 스택 | 운영 입력의 필요 용량·수치는 별도 측정 |
| native·ARM/QEMU·부분 포장 | 실제 owner/codec/MQTT·고부하·thread 교체·ARM ELF·불변 bundle 검증 | 전체 이미지/RPM/설치본 종단과 동일시하지 않음 |
| 실보드 B | 1.0.7 설치·정상 stop/start, 네 앱 지표와 스택 실제 수신, 원 stop 결과 회수·시험 설정 복원 | 합성 지도는 큐 제출 뒤 실제 앱 채택/계산까지 통과하지 못함 |
| Spring·화면 | 아래 제공 계약/원문/시험을 연결할 수 있음 | 소비 제품 연결은 서브 작업. 스택 화면은 사용자 결정으로 보류, EC2 배포는 별도 승인 |

### 무엇을 어떤 기준으로 바꿨는가

| 실제 위치 | 변경과 이유 | 보존한 조건 |
|---|---|---|
| `platform/apps/drive/world/world.[ch]` | 실제 map 슬롯·odom ring의 점유·boot 중 최고 점유·확보 실패/경합을 owner가 갱신. 읽기 위해 payload를 다시 세거나 복사하지 않음 | 유한 슬롯/ring·원 데이터 수명·반환·odom reset 이후 최고 점유 |
| `platform/apps/drive/planner/engine.[ch]` | path 슬롯 및 완료된 탐색 slice의 node 점유를 관측. used→peak 읽기 순서를 고정해 자기 모순을 방지 | 실제 planner/tracker/충돌 검사·노드 상한. 상수 used나 타이머 progress 없음 |
| `platform/apps/drive/zephyr/src/lattice_memory.[ch]`, `platform/guests/zephyr/diagnostics/src/resources.c` | 등록은 한 번만 CAS 0→1→2로 게시. 주기 cache는 owner try-lock만 사용하고 busy는 원 rc/null로 보냄 | 무대기 owner 읽기·기존 200ms collector. 새 전역 잠금/전체 배열 scan 없음 |
| `diagnostics`, `platform/guests/zephyr/telemetry/src/model.c·worker.c` | RAM 선언·mapped/static·page allocator와 앱 풀을 구분. CPU의 기존 frozen capture를 공유해 새 10,448B observation 복사를 제거 | 원 sample/window·실제 관측 시각, heap/pool/stack의 중첩 범위 |
| `platform/guests/zephyr/telemetry/src/worker.c` | CPU/앱의 첫 예산 시도를 교대하고, 만료 후에도 미전달 page/region 순서를 새 표본에서 이어감. encode 실패로 wire가 0인 경우 토큰 예약을 환불 | 16KiB/s·8KiB burst·원 2초 TTL. 오래된 값을 새 시각으로 바꾸지 않음. CPU 부분 window를 합쳐 완전으로 만들지 않음 |
| `diagnostics`·snapshot codec | ARM fill scan의 최대 사용량/최소 여유를 보냄. native pthread의 가짜 Zephyr stack 배열은 unsupported/null | 명시 요청·행수 상한·snapshot-local row ID. CPU lifetime ID와 결합 금지 |
| `platform/apps/drive/zephyr/prj.conf`·build script | 실제 1.0.4의 TCP workqueue 1,024B를 2,048B로 보완하고 resolved 설정 guard/별도 audit receipt 추가 | RX/TX 4,096B, sentinel, direct netfront send frame 48B. timeout을 늘리지 않음 |
| `xenova_execution.py`·`test_xenova_stop_acceptance.py` | 완료된 직접 다음 revision의 명시 force와 현재 부재가 확인될 때만 옛 uncertain graceful stop을 원 실패로 종결 | 원 key/hash/신원/기한/최초 cause·내구 저장·소유 프로세스 확인. 추가 물리 명령/전체 request scan 0 |

앱 `reserved_bytes`는 정적 구조체 예약이고, `capacity_bytes`는 그 안의 슬롯/노드/ring 사용 한도다.
`used_bytes`가 줄어도 Xen에 VM RAM을 반환했다는 뜻은 아니다.
map/path/search/odom와 heap·stack은 firmware 영역 안에 있으므로 더해서 VM 전체 사용량으로 만들지 않는다.

### 최종 후보와 로컬 수락

기준은 `feat/platform/guest-clock-sync` / HEAD `983fa12`에 보존된 미커밋 변경이다.
HEAD 하나가 모든 변경을 뜻하지 않는다. 펌웨어 subset의 실제 고정 식별은 다음과 같다.

| 항목 | 최종 값 |
|---|---|
| ARM build | `/home/ssafy/xenova-app-memory-20261104/arm-release107-final` |
| source SHA256 | `abba8b121028864e84ff40289852430c3475c60af8bc67eb58bc9f2878121463` |
| zephyr.bin SHA256 / bytes | `a7e2c9fa249ab15cfbd2cd9f26e69f3e60832c288eea50c0dc35d5fcfa36fc8e` / 523,328 |
| release directory | `/home/ssafy/xenova-app-memory-20261104/release-1.0.7` |
| manifest revision | `2127ee188c3648b25d2f4d4e6d772f84a5892af174269f8f219a8f6956d06c94` |
| package 전송 archive SHA256 | `8df91f2ea558a3b54d653750965c9231e1b432ad8ff174d5feff6a1b1615adc3` |
| 별도 실행 provider SHA256 | `9b4cb13aa257ae1079abfd2aa4c88020b8f17df0c3242fb06dfe278721f37e79` |

고정 SDK/Zephyr와 새 불변 output을 사용했다. package가 현재 source fingerprint와 receipt·payload 9개 파일의
hash/크기/읽기 전용 mode를 대조한다. 공개된 옛 bundle을 제자리 수정하거나 운영 기본 catalog를 자동 승격하지 않았다.

- codec/source/실제 engine/world 관련 62개 회귀: 32.02초 통과.
- 최종 worker/계약/TCP 영향의 telemetry/source/Zephyr lifecycle 31개: 28.89초 통과.
  두 묶음은 일부 중복되므로 93개의 독립 시험으로 합산하지 않는다.
- 실제 kernel owner→Diagnostics→제품 codec의 native 2개와 ARM QEMU 2개 통과.
  native pthread stack과 실제 ARM stack의 지원 차이를 그대로 검증했다.
- 기존 172-message MQTT 수락은 중간 후보 근거다. 최종 worker는 아래의 실제 25초 부하 수락으로 별도 확인했다.
- stop 수습 신규 11개+10 subtests, 관련 stop/execution/reboot/recovery 125개+15 subtests 통과.
  3개의 환경 실패는 수정 전 모듈에서도 동일 재현했다:
  force-policy fixture mode 거절 2개, systemd259 analyzer loader 미공급 1개.
- `git diff --check` 통과. 실제 pytest 명령은 telemetry/source 두 파일과 engine/world, 이후
  `test_xenova_zephyr_lifecycle.py`를 영향별로 실행했다. 상세 argv/SDK/명령 결과는 아래 원자료에 남겼다.

### 전송 비용과 고부하 반례의 마감

실보드 1.0.6에서 CPU 앞 페이지가 예산을 먼저 사용하고 앱 순서가 매 cycle 0으로 돌아가면서
odom이 전혀 전달되지 않는 반례를 확인했다. PC 회수 checkpoint의 앱 전달은 map/path/search/odom
`12/12/2/0`이었다. 개별 메시지의 schema가 맞다는 사실로 전체 제공을 통과시키지 않았다.

실제 publisher 25초·양 VM 각 실제 28 threads·동일 설정의 before/final 비교는 다음과 같다.
이는 harness 전체 벽시계 시간이 아니라 publisher 실행 시간이다.

| 계수 | 변경 전 | 최종 |
|---|---:|---:|
| 앱 map/path/search/odom 전달 | 14/11/2/0 | 15/15/15/14 |
| 전체 수신 메시지 | 392 | 385 |
| 활성 CPU 완전/부분 window | 17/7 | 3/21 |
| 최종 실제 모든 28 lifetime thread·마지막 CPU page | 일부 앞 순서 우세 | 모두 회전 수신 |
| 최종 원 schema 오류 | — | 0 |

개선의 근거는 로그나 메시지 수 감소가 아니라 **누락됐던 region/CPU tail의 실제 전달**이다.
고정 예산의 부담도 명시한다: 과부하에서는 CPU 동시 완전 window가 줄었다. 부분 page를 다른
window와 합치지 않았고, 전체 thread의 같은 순간 관측·hard deadline을 보장한다고 쓰지 않는다.
관측한 앱 최대 수신 간격은 5.872초, source age 최대 1,980ms였다. 전송률과 TTL은 올리지 않았다.

실제 thread 수를 `28→16→28`로 바꾼 별도 25초 시험도 382-message 수락을 통과했다.
양 VM 각각 재생성한 12개 thread의 lifetime ID가 새 값이고, 모든 40개 lifetime ID를 관측했다.
페이지 수 축소/확대 후 modulo와 부분 window 구분, map 점유→반환 0·peak 보존도 확인했다.

실제 1.0.4 대비 1.0.7의 오프라인 ELF 차이는 RAM 선언 +0, mapped +12,288B,
static data +8,192B, 명명 stack +1,024B(TCP만), engine +64B, kernel heap capacity +0이다.
중복 `app_memory_frozen`은 없다. callback의 알려진 직접 frame 합 160B는 전체 최악 stack 상한이 아니다.

1.0.4의 실행 가능한 retransmit→ARP C-frame 하한은 1,152B로 TCP 예약 1,024B보다 컸다.
기존 PC/LR은 sentinel 검출 지점이지 최초 덮어쓰기 명령의 증거가 아니다.
1.0.6에서 22개 원/계측 객체 기계어·relocation 일치를 확인했고, 1.0.7에서 그 22개 분석을 다시 했다고
확대하지 않는다. 최종 실제 B snapshot은 TCP 예약 2,048B / 최대 사용 436B / 최소 여유 1,612B였다.
이 표본이 모든 ARP/재전송 상황의 최악 상한은 아니다.

### 실보드 시험과 실제 MQTT 값

접속은 사용자 지정 `root@100.108.226.65`로 전환했다. 운영 Gateway/실제 USB 장치는 사용하지 않고
임시 Authority·PTY·별도 world broker로 시험했다. A는 이 작업에서 재부팅하지 않았다.

1.0.4 B는 새 firmware 적용 전 `tcp_work` overflow로 멈춰 있었다. 원 graceful timeout/uncertain을 보존하고,
사용자 승인 후 당시의 정확한 B 실행만 force로 수습했다. 이어서 원 key
`4ecdab40-1ab1-4de4-a2ad-3166e2fa97ac`를 임시 제공자로 재호출해
`failed/XEN_EXECUTION_FAILED`로 닫았다. 실제 명령은 조회뿐이었고, 이후 보드 재부팅 뒤에도
원 `operation_id=140a5611-7345-47e0-941c-81ede29eb18c`·hash·기한·원 host의 결과를 회수했다.
정상 graceful 성공으로 바꾸거나 새 실행 권한으로 사용하지 않았다.

중간 1.0.6의 1,755-message raw는 사용자 확인의 사고 재부팅으로 /run에서 PC 회수 전에 소실됐다.
도구 출력의 596B TCP high-water와 전체 raw 보존을 구분했다. 이후 중간 자료는 PC에도 회수했다.
새 host `55e67259-5483-478e-a611-396e44506802`에서 최종 1.0.7을 설치했다.

| 보존 capture | 원 v2 메시지 / schema 오류 | 앱 네 region 수신 | 의미 |
|---|---:|---|---|
| run3 | 934 / 0 | 50/50/49/49 | 현재 B firmware/실행 신원 일치, 사용·peak 모두0 |
| run4 | 927 / 0 | 56/55/55/56 | 원 source age 최대1,998ms, 사용·peak 모두0 |
| capture6 | 430 / 0 | 24/25/24/24 | 동일 실행의 최종 snapshot 5parts·19rows 정합 |

capture6의 stack 수집 비용은 631µs였지만 각 part 전송은 원 collection 끝에서 3,972~26,360ms 뒤였다.
수집 비용과 전송 대기를 구분하고 최신 주기 관측으로 바꾸지 않는다. row ID는 snapshot-local이다.
이 capture는 정리 전 `e8d9b94d-cbfd-47ac-97bb-d41db2dd7118` 실행이며 아래 복원 후 실행의 수신 근거가 아니다.

실제 원 메시지의 값은 다음과 같다. 숫자는 bytes이며 kernel/page allocator/앱 영역을 합산하지 않는다.

| 원 metric 범위 | 실제 값 |
|---|---:|
| firmware mapped / static data | 2,252,800 / 1,732,608 |
| page allocator free | 14,524,416 |
| kernel heap used / free | 0 / 65,536 |
| net RX used / free | 0 / 131,072 |
| net TX used / free | 0 / 8,192 |
| maps reserved / capacity / used | 81,224 / 80,472 / 0 |
| paths reserved / capacity / used | 31,608 / 31,536 / 0 |
| search reserved / capacity / used | 509,152 / 393,216 / 0 |
| odom reserved / capacity / used | 55,328 / 55,296 / 0 |

별도 실제 200ms CPU window는 system 12.379%, idle 87.621%, main 7.034%,
drive-auto 0%, planner 0.218%, tcp_work 0.007%였다. 이는 guest-accounted UP 비율이며 pCPU 점유율이 아니다.
실제 원 body 네 개는 `board-run4-wire-examples`에, topic/QoS/retain/수신 시각/source seq/원 SHA는
그 `manifest.json`에 보존했다. 예제 파일은 편집한 가상 JSON이 아니다.

### 지도 주입의 미완료와 정상 복원

가상 장치/mapper만 대역 처리했고 B 앱·planner·codec·Gateway는 실제 코드를 사용했다.
지도 enqueue가 곧 앱 수락/실제 계산이라는 주장은 하지 않는다.

| 회차 | 조건 | 결과 |
|---|---|---|
| 초기 | Gateway 비활성, retained control 없음 | maps0. fresh 시험 Authority의 실제 SAFE_STOP bootstrap으로 시험 전제 보완 |
| run1 | launcher의 잘못된 공용 MQTT 경로 | 설치본 fallback을 발견. 원 실패 보존 후 실제 업로드 경로/hash로 고정 |
| run2 | 올바른 MQTT, 같은-process 200ms binding stress | map queue0. actual vehicle freshness 미충족 |
| run3 | 현재 후보 identity-only observer | map queue8, allocation0, CONTROL_APPLY_UNCONFIRMED |
| run4 | 실제 Worker처럼 조회 완료 후 1초 대기 | map queue12, allocation0, CONTROL_APPLY_UNCONFIRMED |
| run5 | Worker 관측과 Gateway를 별도 process로 분리, 제품 BindingFile 사용 | map queue7, allocation0, CONTROL_PUBLISH_UNCONFIRMED |

정리 전 SAFE_STOP은 같은 actor의 출력 철회로 실제 발행·수신 및 DISARMED를 확인했다.
binding 부재를 과거 복구 허가로 바꾸지 않았고, 매 회 실제 출력은 0이었다.

단독 binding profile은 B389ms/A468ms, 두 VM 병렬760/831ms로 성공했다.
같은 process/Gateway 부하에서는 원 2초 예산의 관측 실패와 차량 표본 만료가 발생했다.
process 분리 후 binding 오류는 줄었지만 actor p95는 run4 65.9ms→run5 91.5ms였으므로 GIL 하나를
확정 원인으로 쓰지 않는다. idle 지도 생성 0.7~1.9ms / encode 12.3~15.1ms 세 표본도 남겼다.
원 1초 control 적용·차량 freshness·clock 한도를 올려서 통과시키지 않았다.

**실보드 합성 지도 채택→실제 path/탐색 점유 증가→입력 만료 후 반환은 미완료**다.
다음 플랫폼/Gateway 작업은 `verified_runner.run_once/_delivery`, `DriveLinkProvider.receive/tick`,
guest `lattice_input.c:link_received/vehicle_received`와 장치 feedback 단계의 지연/거절을 원 clock probe에
대응하는 것이다. 이 절의 자료만으로 특정 구간의 최초 원인을 확정하지 않는다. 실제 Jetson/ESP32/실차 수락도 없다.

최종 정리에서는 B 1.0.6→1.0.7 및 1.0.7 시험 종료를 모두 **정상 stop**으로 확인했다.
임시 world 필드만 제거하고 기존 Pi 자격 값은 같음을 대조했다. 사고 재부팅으로 원 /run backup을
잃었으므로 원 파일의 byte-exact 복원을 주장하지 않고, 나머지 credential 값의 동일성과 world 부재를 확인했다.
복원 start의 최초 admission busy는 미수락이었고 같은 key 재호출로 기동했다.
최종 B는 `execution=45824862-0d33-452a-b510-0ead09d6ba47` / domid5 / observed이며 A 실행은 동일했다.
Worker active, 운영 Gateway inactive를 복구했고, 소유 시험 process 0·broker 종료를 확인한 뒤
정확한 /run 시험 디렉터리를 제거했다. 보드 재부팅·실제 출력·비밀번호 재발급·commit/push는 이 작업에서 하지 않았다.

원자료의 공통 디렉터리는 `C:/Users/SSAFY/Documents/Codex/2026-11-04/app-memory`다.
`final107-artifact-evidence.json`, `final107-ram-diff-summary.json`,
`native-fairness-final-assertions.json`, `board-final-independent-summary.json`,
`board-final-public-manifest.json` 및 `board-final-public.tar.gz`를 함께 본다.
최종 archive는 105개 공개 시험 파일이며 private 디렉터리/credential 값 필드를 제외했다.
archive SHA256은 `81cc25bebe4498cfbcf6c26e98c92fccdcf65fbac4bfefcdb0560dacc5f4eb1a`다.

[정식 계약](../../platform/guests/zephyr/telemetry/contract/CONTRACT.md#95-앱-메모리와-펌웨어-예약-범위),
[소비 인계](../../platform/guests/zephyr/telemetry/contract/docs/CONSUMER_HANDOFF.md#앱-메모리와-스택-연결),
[기존 HTTP/Spring/화면 요청서](../requests/20261029-request-http-spring-ui.md)의 현재 본문을 사용한다.
Spring 저장·조회와 앱 풀 화면은 후속 소비 연결이며, 스택 화면은 보류·EC2 배포는 사용자 지시 대기다.


<a id="manual-buffer-managed-ram-20261104"></a>
## 2026-11-04 수동 버퍼 계측과 관리 RAM 요약

이번 후속은 앞 절의 앱 풀·커널 힙·스택 발행을 유지하면서 MANUAL의 입력·대기·실행·결과 보관을 연결했다.
상단은 기존 물리 페이지 원천으로 **Zephyr 관리 RAM 확보량**을 계산하도록 확정했다.
소유 플랫폼의 구현·로컬 수락·부분 firmware 제공을 마쳤다. Spring/프론트·운영 설치는 이 완료에 포함하지 않는다.

### 사용 위치와 실제 바뀐 동작

상세 발행은 계속 MQTT 2.1의 `xenova/v2/vms/<vm_id>/telemetry/metrics`, QoS0·retain=false다.
새 토픽·서비스·VM 관리 API를 만들지 않았다.

| 화면 대상 | 실제 계약 위치 |
|---|---|
| 자동 지도·경로·탐색 | `app_memory` / `drive_maps, drive_paths, drive_search_nodes` |
| 공통 차량 관측 | `app_memory` / `drive_odometry` |
| 수동 입력·대기·실행·보관 결과 | `app_memory` / `manual_input, manual_pending, manual_running, manual_result` |
| 공통 RX/TX | `memory_regions` / `net_rx_data, net_tx_data`. MQTT 전용 사용량이 아닌 공통 네트워크 풀 |
| 커널 힙 | `memory` / `memory.kernel_heap.*` |
| 스택 | 기존 `telemetry.snapshot`의 threads 행. 발행은 유지하며 화면 적용은 보류 |

앱 영역은 기존 여덟 `memory.app.*` 지표를 유지하고 수동 네 영역에만 `payload.buffer`를 추가한다.
현재 점유·예약량·내용 길이는 다른 값이다. 이번 저장소 범위는 다음처럼 한정했다.

| region / 소유 API | 포함한 실제 저장소와 ARM 크기 | 점유·유효성·수명 기준 |
|---|---|---|
| `manual_input` / `xd_io_memory_read` | `io.c:shared.input` 하나. 예약1200B·payload 용량1024B | 보관 길이가 used. 미수신·분류 전·무효·만료·문맥 변경을 metadata로 구분하며 만료만으로 보관 bytes를 없애지 않음 |
| `manual_pending` / `drive_workers_memory_read` | MANUAL `task.pending`, 2360B | 실제 pending 소유 중에만 슬롯 전체 점유. 최신 대기 교체가 running 사본을 덮지 않음 |
| `manual_running` / 같은 API | 별도 `task.running`, 2360B | callback이 실제 반환할 때까지 점유. 취소·RESIDUAL만으로 해제하지 않음 |
| `manual_result` / 같은 API | `task.result`, 2592B. 내부 source job도 이 크기에 포함 | 완료 사본 보관 중 점유. 읽기는 복사이며 해제가 아님. 다음 완료·join 후 restart의 실제 정리와 구분 |

`working`, runtime/main의 입력 사본·staging job·main의 완료 사본·자동 지역변수·전송 슬롯은 위 표의 범위 밖이다.
같은 저장소를 다른 이름으로 더하지 않았고, 이 네 영역의 합을 수동 앱 전체나 VM 전체 used라고 부르지 않는다.
공유 worker 경합 누계는 세 슬롯에 같은 값을 제공하므로 합산하지 않는다. 진단 getter의 BUSY는 업무 누계에서 제외한다.
입력 경합은 기존 업무 snapshot의 32bit 원천이다. u64 문자열 봉투가 원천 폭을 늘리지 않으며 감소 시 불연속으로 처리한다.

`buffer`는 phase·content_bytes·원 sequence/source_uptime_ms·worker lifetime·원 context/connection revision,
input_status·원 source_rc·result의 performed/last_read_sequence·revoked를 제공한다.
원 seq/time0은 유효하며, 음수 원 시각은 null로 보존한다. 새 control·재조회·reconnect가 옛 입력의 원 시각/revision을 갱신하지 않는다.
performed는 callback 수행 사실, accepted는 입력 분류 사실이다. READY·현재 출력 허가·장치 적용을 뜻하지 않는다.
최고 점유는 실제 owner 전이에서 갱신해 짧은 계산도 남기고, worker restart로 boot 최고값·누계를 지우지 않는다.

실제 native MQTT 원문 예제의 result는 `sequence="200"`, `last_read_sequence="199"`, `phase="retained"`,
`content_bytes="2592"`, `source_uptime_ms="3980"`, `performed=true`였다.
보관된 새 완료와 이전 읽기까지 구분되지만, 이것만으로 현재 계산 중이라고 표시하지 않는다.
[원문·신원·해시](C:/Users/SSAFY/Documents/Codex/2026-11-04/manual-memory/wire-examples/manifest.json)는 개별 메시지 예제다.
서로 다른 VM/표본의 예제를 한 snapshot으로 합치지 않는다.

### 상단 RAM: 같은 원천의 전체·확보·미할당

새 wire 지표를 중복해서 만들지 않았다. 기존 `family="memory"`의 다음 값과 원 표본 쌍을 사용한다.

- total: `memory.firmware.ram_region_bytes`.
- free: `memory.page_allocator.free_bytes`.
- committed: `total - free`.
- percent: `committed * 100 / total`, Decimal ROUND_HALF_UP 소수3자리.
- 단위는 bytes의 정확한 십진 문자열이다. 화면 MiB는 1024²로 변환한다.

고정 SDK `684c9e8f…`의 물리 frame 관리 범위는 CONFIG_SRAM_BASE_ADDRESS/SIZE다.
현 지원 Zephyr는 catalog·instance 검증·Xen cfg 생성·firmware DT/CONFIG가 16MiB로 맞는다.
페이지 free-list도 같은 관리 범위를 사용하며 현 profile은 demand paging off·page 정렬이다.
정적 image·heap/pool/stack 예약과 동적 page 확보는 비free에 포함된다.
외부 grant-table window·외부 PFN bare-map은 이 local RAM free-list와 구분한다.
현재 등록 memory_mib는 구성값이며 별도 Xen 실측이 아니다. ARM 부트에 실제 할당 nr_pages를 관측하는 제공물도 없다.
따라서 이름은 **Zephyr 관리 RAM 확보량**, free는 **미할당 물리 RAM**으로 한정한다.
현재 payload 점유나 Linux MemTotal−MemAvailable과 같은 뜻으로 표시하지 않는다. 물리 free가 모든 풀·가상주소 할당 성공을 보장하지도 않는다.

같은 VM/boot/publisher/실행 문맥의 같은 memory 봉투·source_sample_seq/observed_uptime_ms만 조합한다.
total0·free>total·mapped>total·static>mapped·mapped+free>total, null·실패·미지원·만료에는 비율을 만들지 않는다.
same-source의 새 heap 메시지는 RAM lease를 갱신하지 않는다. 같은 표본이 오류/null로 바뀌면 원 실패를 보존하고 옛 정상 비율을 숨긴다.
새 source의 정상 수집에서 회복한다. 당시 참조 view는 외부 현재 실행 확인을 필수로 요구했다. 이 표시 조건은 아래 [최근 관측 후속](#ram-recent-observation-20261104)의 사용자 결정으로 대체되었으며, 당시 시험 결과와 불변 제공물은 이력으로 보존한다.

실행 가능한 참조와 정상·실패 예제는 [정식 계약](../../platform/guests/zephyr/telemetry/contract/CONTRACT.md),
[catalog/schema](../../platform/guests/zephyr/telemetry/contract/schemas/metric-catalog.json),
[소비 인계](../../platform/guests/zephyr/telemetry/contract/docs/CONSUMER_HANDOFF.md)에 둔다.

```sh
cd platform/guests/zephyr/telemetry/contract
python3 tools/validate_contract.py --self-test
python3 tools/validate_contract.py --ram-summary fixtures-2.1/ram-committed.json
```

두 번째 명령의 합성 예제는 total16384/committed4096/free12288 bytes, percent `"25.000"`다. 운영 기본값이 아니다.
과거 보드1.0.7 원문은 2252800/16777216=13.428%라는 역사적 계산만 확인했다. 새 후보 수신·현재 상태로 재명명하지 않았다.

### 구현 위치와 비용 판단

입력은 `drive_io.h/io.c/manual_input.h`에서 기존 parser1회의 결과와 작은 owner shadow를 재사용한다.
workers.c/h는 별도 pending/running/result 소유 metadata를 기존 교환 경계에서 관리한다.
`lattice_memory.c`는 기존 단일 Diagnostics provider에 AUTO4+실제 MANUAL4를 합성한다.
미연결 standalone provider는4만 제공하며 미관측 영역을0으로 채우지 않는다.
`resources.h/resources.c`, `telemetry/model.c/worker.c`는 실제 count와 작은 metadata를 기존 cache/frozen/codec 경로에 전달한다.
진단은 기존 lock의 K_NO_WAIT만 사용한다. payload 재파싱·전체 scan·새 전역 잠금·별도 전체 frozen 사본·매 수집 Xen/Store 호출을 추가하지 않았다.

| 비용 / 비교1.0.7→최종1.0.8 | 실제 근거와 한계 |
|---|---|
| 선언 RAM·기존 pool/input/job/completion·설정·스택 예약 | 동일. writable stack 예약119584B 유지 |
| image mapped / static 범위 | +12288B / +8192B. 서로 겹치는 범위이며 합산하지 않음 |
| resources snapshot / task | 2904→3384B(+480) / 11536→12048B(+512). snapshot의4region×56+4detail×64가480B |
| 직접 C frame | memory callback96→112B, worker run2768→2784B. getter208B. 부분 호출합은 실제 stack 최고값이 아님 |
| 전송 | 기존16KiB/s·burst8KiB·원 표본TTL2초·4096B 한도 유지. 실제 수동 payload 최대2579B, U64 경계 fixture2933B |
| 정상 / 실제 스레드 부하 | 같은 native 후보·32초 두 Publisher. 정상484건, 부하490건. 양쪽 모두8region과 각10/30개 thread ID 수신, 현재 oracle 위반0 |
| 부분 표본 / freshness | 정상 CPU완전27·부분5, 부하 완전2·부분29. 일부 window를 완전 표본으로 합치지 않음. 실제 region 수신 최대간격4.699/8.481초, 송신 원 표본 age 최대1980ms |
| 시간 수치 | native tick 해상도에서 수집/직렬화 누계0으로 찍힌 값은 비용0 근거가 아님. 이 표본으로 µs 단위 개선·실보드 기한을 주장하지 않음 |

짧은 수동 계산은 200ms 관측 사이에 끝나 현재 pending/running이 모두0으로 수신될 수 있다.
실제 MQTT에서도 현재0·최고2360B가 함께 나왔고 result는2592B 보관됐다.
그래프를 움직이려고 계산을 지연하거나 가짜 할당을 만들지 않았다.
부하의8.481초 수신 간격은 소비 lease6초를 넘을 수 있다. 그 구간은 stale로 표시하며 모든 영역 상시 fresh라고 하지 않는다.
974건은 원문으로 다시 검증했고 native/ARM/현재 작업트리의 공통 제품 C/H114개도 byte 일치했다.

### 실제 수락과 실패 이력

| 실행 경로 | 확인 결과 |
|---|---|
| 입력 원천 native10 | 실제 io accept/refresh/getter·parser·mutex. 외부 MQTT latest delivery만 fixture. seq0·원 age/revision 보존·무효/만료 보관·mode/instance/reconnect·BUSY→회복 |
| 수신→수동 계산 chain1 | encoded envelope/core decode→실제 io snapshot→job→worker thread→기존 stateless manual adapter/manual_step→보관/읽기. v_milli1000·read_sequence1 확인. lattice의 실제 차량/clock callback 수락과 구분 |
| worker native11 / ARM QEMU | immutable running+최신 pending 교체·동시점유·AUTO독립진행·실제 callback 잔존·취소/join/restart·peak 보존. 동일 ARM ELF3회 연속PASS |
| RAM native3 / ARM QEMU3 | 기존 pool/stack2개 보존. 실제 k_malloc512와 map slot점유/반환은 physical free 불변. 실제2page map/쓰기에서8192B감소, unmap후 원복 |
| ARM RAM 직접 수치 | 시험용128MiB: total134217728, free132980736→132972544→132980736, committed1236992→1245184→1236992. Xen16MiB 보드 측정으로 확대하지 않음 |
| 실제 codec·기존 C | native13/ARM15 원문 oracle 통과. 기존 ASan/UBSan C6개 통과. native/ARM ENOTSUP 원 errno(-95/-134)를 같은 숫자로 강제하지 않음 |
| 최종 관련 Python |111개·10subtests 통과. 정상+ASan/UBSan codec, source, 기존 AUTO world/engine·실행 신원·collection·포장 포함 |
| 계약 | 기존2.0 30정상/30거절·11sequences/28assertions, 기존2.1 및 새 MANUAL 정상17/거절24, RAM23벡터·현재성23assertions. 아래 개별 evidence로 대역·중복 범위를 확인 |

최종 Python 명령은 `pytest -o addopts= -q`에
`test_xenova_telemetry.py, test_xenova_telemetry_sources.py, test_engine.py, test_world.py,
test_xenova_drive_lifecycle_bootstrap.py, test_xenova_drive_collection.py,
test_xenova_drive_integration.py, test_xenova_template_workflow.py`를 지정했다.
실제 전체 argv/SDK·빌드 설정·return code는 아래 command JSON과 native/ARM evidence에 보존했다.

ARM 시험에서는 새 시험 함수의 다중 completion 값 반환이 main8192B를 넘겼다.
시험을 out-pointer/정적 보관으로 고쳐 직접 frame13376→416B, 대기 helper224B로 줄였으며 제품 stack/sentinel·기한은 유지했다.
정상 업무 경합까지 증가할 수 있는 누계의 옛 고정 baseline assertion도 각 강제 주입 직전 기준으로 고쳤다.
완료 후 main 재개가300ms 경계를 넘는 관측 경합은 원 finished_ms<=원 절대end 검증과 마지막 nonblocking read1회로 다뤘다.
원 기한 뒤 완료를 성공시키지 않는다. 최초 별도 RUNNING timeout의 개별 원인은 확정하지 않았고 일부 rolling 원로그는 command transcript만 남은 한계를 evidence에 명시했다.
최종3회 통과를 모든 host 부하·실보드의 시간 상한으로 확대하지 않는다.
시험 종료 대기32초/40초의 정합화·Python import 경로 보완·picolibc errno 분석 수정도 시험 환경 이력이며 제품 timeout을 올린 것이 아니다.

### 최종 후보·부분 포장·인계

현재 브랜치 `feat/platform/guest-clock-sync`, HEAD `983fa121dbdb7a208ad61cd9d2738394c80d23c3`에
기존 미커밋 작업과 이번 후속이 함께 있다. HEAD만 전체 후보라고 하지 않는다.

```text
version: 1.0.8
source: 4add6d53d8c7264472e9381a771334ec983ab7eeefda408b6308b1b44a12b96f
zephyr.bin: 05274f0b41c79f88f743051e7bbe5ff7a0465835c530f30cd239b61063fd424e
firmware manifest: 47c8eaedb7032c93bbfb58e003d7bde4fd07b3de8506ccbed76912912b61649a
contract SHA256SUMS: 7d01a7f5bb82f7658167f42a38fb91731be6ffad4186d7ce5143eb2f9c842c3f
contract manifest: 0e28e382d4d2295db627de1946d368f80034984394376d4b96661f5a766e528c
```

빌드/부분 템플릿은 WSL `/home/ssafy/xenova-manual-memory-20261104/arm-release108-ram-final`,
`release-1.0.8-ram-final`에 있다. package가 현재 fingerprint와 receipt·9개 payload를 다시 검증했다.
중간 pre-LF/E8 후보와 이전1.0.7은 보존한다. 새 source 때문에 과거 산출물을 제자리 수정하지 않았다.
새 C 코드는 기존 firmware에 링크되며 별도 target daemon·unit·라이브러리는 없다.
정규 전체 이미지/RPM 재조립·보드 설치·default template 선택 변경은 실행하지 않았다.

주요 실행:
```sh
python3 -B platform/scripts/build-xenova-drive-app.py --app drive --profile warm-lattice-lab \
  --zephyr-source /home/ssafy/perseus-zephyr-template-20261016-153845/source/zephyr \
  --tools /home/ssafy/perseus-zephyr-template-20261016-153845/host-tools \
  --output /home/ssafy/xenova-manual-memory-20261104/arm-release108-ram-final --execute
python3 -B platform/scripts/package-xenova-drive-template.py \
  --build-root /home/ssafy/xenova-manual-memory-20261104/arm-release108-ram-final \
  --output /home/ssafy/xenova-manual-memory-20261104/release-1.0.8-ram-final --version 1.0.8
```

근거 위치:
- `C:/Users/SSAFY/Documents/Codex/2026-11-04/manual-memory/`: 최종 MQTT 원문/해시·정상/부하 집계·source 동등성·전달 묶음.
- `C:/Users/SSAFY/Documents/Codex/2026-11-04/app-memory/`: `input-memory-verification.json`,
  `worker-memory-acceptance/summary.json`, `worker-stack-arm/audit.json`,
  `managed-ram-sdk-analysis.json`, `managed-ram-codec-evidence.json`,
  `final108-arm-memory-evidence.json`, `final108-ram-diff-summary.json`,
  `manual-contract-final-checksums.json`, `ram-contract-final-checksums.json`,
  `ram-reference-cli-and-historical-evidence.json`.
- WSL `/home/ssafy/xenova-manual-memory-20261104/`: 실제 command/log JSON·현재 후보 ELF와 부분 package.
  입력·RAM 추가 시험의 별도 command/log는 `/home/ssafy/xenova-app-memory-20261104/`에도 보존했다.

[기존 D02·D08 현재 요청서](../requests/20261029-request-http-spring-ui.md)에 설치·소비 위치와 정상/실패 수락을 개정했다.
서브 작업용은 이 제공물의 buffer·원 표본/context·RAM summary를 Spring 저장/조회와 화면에 연결한다.
스택 화면은 계속 보류한다. 운영 보드·실차·EC2·서비스·실데이터·타 담당 제품·commit/push는 이번에 변경하지 않았다.

최종 전달 묶음은 [manual-ram-provider-1.0.8.tar.gz](C:/Users/SSAFY/Documents/Codex/2026-11-04/manual-memory/manual-ram-provider-1.0.8.tar.gz)다.
SHA256 `ae11c1ea45d778a543b733b450168ec517d55d5af200fb92978030074b0caa23`,
2971639B·201개 항목이며 firmware·계약·원문·검증 요약의 내부 SHA를 모두 확인했다.
인계 bundle은 운영 설치가 아니며 private broker fixture·비밀번호를 포함하지 않는다.


<a id="ram-recent-observation-20261104"></a>
## 2026-11-04 RAM 최근 관측 계약 마감

### 현재 사용 기준과 실제 예제

상단은 **Zephyr 관리 RAM 확보량의 최근 유효 관측**이다. 사용자는 마지막 유효 메모리 표본·원 관측 시각·만료를 표시하고 새 부팅의 그래프를 이어 붙이지 않는 기준을 확정했다. 이에 따라 외부 현재 실행 확인을 필수로 두었던 이전 절의 표시 조건을 대체했다. 같은 memory 봉투의 total/mapped/static/page-free 검증, 정확한 uint64·HALF_UP 소수3자리 계산은 유지했다. MQTT 버전·topic·metric·단위·봉투 필드는 바꾸지 않았다.

기본 참조 소비는 외부 bind/expected_context 없이 동작한다. 정식 기존 계약 시험 환경에서 contract 디렉터리의 예제는 다음과 같다. ReferenceConsumer는 시험 oracle이며 제품 SDK·새 서버가 아니다.

```python
import sys
from pathlib import Path
sys.path.insert(0, "tools")
from validate_contract import load_message, validate, ReferenceConsumer

m = load_message(Path("fixtures-2.1/ram-committed.json"))
validate(m, f"xenova/v2/vms/{m['vm_id']}/telemetry/metrics", 0, False)
c = ReferenceConsumer()
c.ingest(m, received_ms=1000)
assert c.ram_view(m["vm_id"], now_ms=1000)["percent"] == "25.000"
assert c.ram_view(m["vm_id"], now_ms=7001)["reason"] == "RAM_RECEIPT_EXPIRED"
```

위 숫자는 total16384/free12288 bytes의 합성 계산 fixture다. 최근 수신은 물리적으로 현재 실행인 VM·앱 건강·복구 허가·출력 권한·장치 적용을 증명하지 않는다. 독립 문맥을 가진 소비자가 명시한 bind/expected_context의 기존 strict 비교는 유지하며, RAM 이외 oracle stream의 기존 bind 의미도 바꾸지 않는다. 네트워크 자체의 운송 지연 상한은 확인하지 않았다.

### 확인한 문제와 국소 수정

| 수정 전 반례·이유 | 최종 처리·보존한 보호 |
|---|---|
| 외부 bind가 없으면 ingest부터 거절하고 expected_context가 없으면 정상 RAM도 UNKNOWN | RAM 최근 관측만 기본 ingest→cache→view를 연결. 다른 소비 목적의 실행 확인·명시 필터는 유지 |
| 같은 원 source가 오류−75가 된 뒤 새 envelope로 옛 정상값이 오면 정상 비율로 복원될 수 있었음 | 원 lease 안에서도 최초 실패·원 rc를 유지. 새 유효 source에서만 회복. 이전23assertion은 만료 이후 회복만 보아 이 결함을 놓쳤고, 원 실패와 중간42assertion 후보를 보존 |
| 같은 boot/publisher/source에서 receipt만 바뀌면 receiver 최초시각1000→6000으로 갱신되어8000에도 정상 | 원 source 동일성과 context 대조를 분리. RAM_CONTEXT_CHANGED로 원 payload/lease를 유지하며 보류 |
| 같은 message_id의 RAM 내용 상충은 ID_REUSE_CONFLICT를 반환하면서도 기존 정상 비율이 남음 | 해당 현재 source만 같은 원 사유로 보류. 과거 다른 source의 ID 충돌로 최신 표본을 무효화하지 않음 |
| source A→B→새 envelope의 옛A에서 A를 다시 채택하고 lease 재발급 | 현재 캐시의 원 순번·시각으로 역전을 거절. 같은 순번의 다른 시각은 양방향 상충. 새 순번·비감소 시각에서 정상 회복 |
| boot A/p1→B/p1→지연A/처음 보는p2가 쌍 단위 퇴역을 우회 | 기존 순번 map에 알려진 퇴역 boot 표식을 재사용. A의 어느 publisher도 재선택하지 않고, 현재 B의 publisher 정상 교체·다음 새 boot C는 허용 |
| 실패·만료 view가 원 source 쌍·시각·status를 비움 | 검증한 whole payload의 원 identity/source/status/rc를 보존하고 파생 bytes/percent만 null |

오류 종류를 합치거나 최초 원인을 제거하지 않았다. 동일 원 source의 정상 재전달·다른 family·새 heap collection은 6초 원 lease를 연장하지 않는다. source/수신 단조 시계를 섞지 않고 수신 후 경과에 원 송신 당시 source age를 더한다. 실제0·미수신·미지원·경합·오류·만료를 구분한다.

세션이 바뀌면 캐시와 그래프 구간을 나눈다. 같은 boot/publisher라도 원 host/execution/receipt가 달라진 새 source는 그래프를 이어 붙이지 않는다. 이는 원 관측 문맥 보존이며 추가 외부 조회를 필수로 한다는 뜻이 아니다. 알려진 퇴역 출처 차단은 관측·보존 범위 안의 보호이고, 아직 보지 못한 출처의 물리적 최신성이나 무기한 모든 과거 세션 기억은 보장하지 않는다. 새 원장·daemon·runtime 전역 잠금·매주기 추가 조회/복사/스캔은 없다.

### 최종 후보·실행·제공 수준

기준 HEAD `983fa121dbdb7a208ad61cd9d2738394c80d23c3`와 기존 미커밋 작업을 보존했다. 최종 `validate_contract.py` SHA는 `5115f026d064306fa89aa85c981afae57b2ebd764cbbdd3ec722f2d1a6e809bc`다. 이번 수정은 정식 계약·소비 인계·참조 도구와 현재 요청서·빌드/배포 설명·본 보고서다. 카탈로그·schema·C/H·wire는 기존 제공물을 재사용했다.

| 실제 실행·범위 | 결과·한계 |
|---|---|
| `python3 tools/validate_contract.py --self-test` | 2.0 정상30/거절30·sequence11개28assertions, 2.1 정상27/거절24, RAM 계산23벡터, RAM view140assertions PASS. 오프라인 계약 oracle이며 실제 보드/제품 화면 시험 아님 |
| 독립 driver `review_recent_ram.py --assert-final` | 동결 원문과 실행 전후 동일 SHA, 11/11 PASS. 원 lease·sticky·context·ABA·퇴역 boot/새 publisher·시각 상충 반례 재실행 |
| 기존 환경의 `python3 -B -m pytest -o addopts= -q platform/tests/test_xenova_telemetry.py --tb=short` | 15PASS, pytest6.68초/전체 명령10.09초. 실제 C codec·ASan/UBSan 출력 및 기존 공급 fixture 포함. Xen/운영 broker/보드는 대역·미실행 |
| 별도 계약 묶음 압축 해제→해시→독립 import→self-test→위 기본 예제 | top175/contract166 checksum PASS, 정상25.000→6초 초과 만료 및 원 source metadata 보존. 제품 설치본이나 target rootfs 수락이 아님 |
| 불변1.0.8 BUILD_RECEIPT와 현재 소스 대조 | C/H130개 포함 소스·설정163개 불일치0. bin `05274f0b…63fd424e`, manifest `47c8eaed…61649a` 그대로. 이번에 ARM/QEMU 재빌드하지 않음 |

검증 근거는 `Documents/Codex/2026-11-04/app-memory/recent-observation/`에 모았다. `before-result.json`·`before-ABA-result.json`과 중간 동결 결과를 보존하고, `final-result.json`·`final-codec-regression.json/log`·`archive-acceptance.json`·`firmware-unchanged.json`으로 최종 후보를 구분한다. 이 값은 로컬 시험 결과이며 처리 기한·운영 성능 보장이 아니다. 이번 변경은 wire 크기·주기·고정 전송 예산을 늘리지 않는다.

별도 host 계약 제공물 `ram-recent-observation-contract-2.1-20261104.tar.gz`는 **176 members / 149988 bytes**, SHA `bdd2f3d0536975137738a3f2561390b1e9ae1cb005550d0af0f19be65c3b93cb`다. 현재 contract SHA256SUMS의 SHA는 `b774a589ac06ed85d715795aa35ac912f1c4a3a976a098d3adc7026f5e3a6e54`다. 이전 불변1.0.8 firmware와 `manual-ram-provider-1.0.8.tar.gz`(SHA `ae11c1ea…0caa23`)를 덮어쓰거나 새 계약 검증을 이미 포함한 것으로 재표기하지 않았다.

### 인계와 미수행

- 정식 원문은 `telemetry/contract/CONTRACT.md` 6절/9.6절, 소비 절차는 `docs/CONSUMER_HANDOFF.md`, 담당 지시는 기존 HTTP·Spring·화면 요청서의 현재 본문이다. 빌드/배포 원본도 별도 host 계약 묶음과 기존 firmware의 수준을 구분한다.
- 독립 실행 확인을 공급하려고 추가했던 하위 `telemetry_context` projection은 최신 결정으로 철회했다. 본인이 추가한 부분만 제거해 Manager/provisioning 두 파일이 작업 전 미커밋 원문과 바이트 일치함을 확인했다. 철회 후보·시험은 이력이고 현재 제공 기능이 아니다. FastAPI 응답·endpoint·권한은 변경하지 않았다.
- 서브 작업용은 확정 계약으로 Spring 원문 저장·조회와 프론트의 최근 관측/원 시각/만료/그래프 구간을 연결한다. 실제 소비 제품 구현·배포 수락은 그 후보의 결과로 판단하며 이 보고서로 대신하지 않는다.
- 스택 발행 계약은 유지하고 화면은 보류한다. 보드·운영 서비스·실데이터·실차·EC2·배포·commit/push는 이번 후속에서 실행하지 않았다.


<a id="image-default108-20261105"></a>
## 2026-11-05 다음 이미지의 주행 A/B 1.0.8 선택

사용자가 다음 이미지에 1.0.8을 선택하도록 요청했다. 이전 기본1.0.4에서 실제 검증한 불변 ARM1.0.8로 Zephyr 선택만 맞췄다. Ubuntu·Debian의 기존1.0.4 선택·UUID·자격·자원·autostart 정책은 그대로다. MQTT schema2.1과 v2 namespace, 최신 RAM 최근 관측 계약·스택 화면 보류도 유지한다.

| 실제 변경 위치 | 적용 기준 |
|---|---|
| `platform/guests/default-templates.lock.json` | Zephyr version/directory/실제 manifest SHA를1.0.8로 함께 변경. Linux 두 entry는 바이트 의미 그대로 보존 |
| `platform/yocto/meta-xenova/recipes-core/xenova-platform/files/defaults.json` | factory A/B 공통 template_version1.0.8. 영구 VM 신원이나 설치된 기록은 변경하지 않음 |
| `platform/first-install/example.config.json` | RTOS release 경로와 manifest hash를 같은1.0.8로 선택 |
| `platform/tests/test_xenova_template_workflow.py` | lock·factory A/B·조립 예시의 version/path/hash 일치와 기존 경로 검증을 연결 |
| 기존 build-guide/deployment-readiness | Linux1.0.4/주행1.0.8 구분, 부분 제공물과 실제 이미지·보드 설치 수준을 구분 |

선택한 manifest SHA256은 `47c8eaedb7032c93bbfb58e003d7bde4fd07b3de8506ccbed76912912b61649a`, 실제 bin SHA256은 `05274f0b41c79f88f743051e7bbe5ff7a0465835c530f30cd239b61063fd424e`다. 기존1.0.8 manifest·bin을 제자리 수정하거나 hash 검증을 우회하지 않았다. `/home/ssafy/xenova-manual-memory-20261104/release-1.0.8-ram-final`의 실제10파일을 검증해 사용했다.

기존 제품 함수 `assemble.rtos_info → copy_rtos_templates → copy_rtos_seeds → copy_management_catalog`를 격리 데이터 경로에서 실행했다. A/B 두 seed 모두 template/active_firmware version1.0.8과 동일 bin hash를 확인했다. identity_state는 pending이며 실제 VM 등록·시작·MQTT 계정 발급은 하지 않았다. 잘못된 manifest hash는 정상적으로 거절했다. 처음 단독 fixture는 전체 조립이 미리 만드는 instances 디렉터리를 빠뜨려 실패했고, 그 불완전 fixture를 보존한 뒤 정상 전제를 갖춘 새 fixture로 통과했다. 제품 보호를 완화하지 않았다.

- 템플릿 workflow 회귀: `python3 -B -m pytest -o addopts= -q platform/tests/test_xenova_template_workflow.py --tb=short` → **10PASS·5subtests**,1.71초.
- 배포 payload까지 확장한 묶음: **23PASS·21subtests/1환경 오류**,28.38초. 실패는 기존 `systemd >=259` loader 부재이며 `XENOVA_DOM0_SYSTEMD_ANALYZE`가 필요한 시험이다. unit이나 의존성을 바꿔 통과시키지 않았고 target package QA 완료라고 주장하지 않는다.
- 정규 release 상대 경로로 포장한 archive를 독립 경로에 풀고 실제 `xenova_zephyr.bundle_info`로10개 읽기 전용 파일·manifest/bin을 다시 검증했다.
- 별도 archive 검사의 첫 실행은 도구의 기존 first-install Python 경로 누락으로 import하지 못했다. 정상 기존 모듈 경로를 공급해 같은 archive가 통과했다. 제품의 모듈·의존성 변경은 없다.

빌더 제공물은 `Documents/Codex/2026-11-05/image-default108/zephyr-drive-template-1.0.8.tar.gz`다. **2,739,063bytes**, SHA256 `806d60aad2358c1e14799ba9f6713838ccc5dcb233a1df17fa50d6f165ae3f9a`이며 내부 경로는 `zephyr/xenova-zephyr-drive-lattice-lab-1.0.0/1.0.8/`다. 기존 release root의 새1.0.8 경로로만 공급하고 기존 게시본을 덮지 않는다. 같은 폴더의 `actual-template-seed-acceptance.json`·`template-archive.json`·`archive-acceptance.json`·`tests.json`에 원 결과와 대역/환경 범위를 기록했다.

다음 이미지 빌더는 이 template을 실제 RELEASE_ROOT에 공급하고, 변경된 defaults를 포함한 플랫폼 패키지/rootfs와 조립 config를 함께 사용해야 한다. 캐시된 옛 rootfs나 이전 조립 config가 새 선택을 이미 포함한다고 간주하지 않는다. 이번 작업은 소스 기본 선택·실제 템플릿과 seed 경로의 로컬 수락이며, 전체 dom0 이미지/RPM 재빌드·SD 기록·기존 A/B 전환·운영 서비스/실데이터·EC2·실차·commit/push는 실행하지 않았다.


<a id="branch-commit-closure-20261105"></a>
## 2026-11-05 문서 현재화와 작업 단위 커밋

사용자 요청에 따라 에이전트 없이 문서 정합성을 보완하고, 기존3커밋(710054e/59db419/983fa12)을 보존한 채
나머지를10개 작업 단위로 나눴다. 제품/계약/빌드/선택9커밋 뒤 본 문서 정리를 별도 커밋으로 남긴다.
브랜치명 변경·push·배포·보드/운영 서비스·실차 작업은 수행하지 않는다.

### 무엇을 어떤 근거로 정리했는가

- §cli-manager-spec.md#time-budgets§의 '현재 FastAPI가 하위 예산을 전달하지 않는다', '원 key를 보존하지 않는다'는
  구식 설명을 실제 §main.py:_install_request_budget§·§budget.py§·§drivers/platform.py:call_with_budget§·
  §service.py:request_stop§·§api/vms.py§와 대조해 교체했다.
- 선택 헤더가 있을 때만 도착 시점부터 Q/S·하위 잔여 예산을 공유한다. 헤더 생략의 기존 동작,
  read_fork의 기본false·기능 설정·하위 fork 지원 조건, 구형 하위의 공유 직렬화 fallback을 유지한다.
  create/retire의 budget_ms 미전달과 필수 저장·실제 설치본의 시간 한계는 미완료 범위로 남긴다.
- key stop의200/202·원 key 조회와 keyless204를 분리했다. 현재 CPU GET의 TARGET_CHANGED503·
  구형 하위 INVALID_SPEC501, 아직 미등록인 CPU 오류의500과 권장 매핑을 구분했다. 오류 종류·원 사유를 합치지 않았다.
- 보고서 앞에 현재 작업/확인 수준을 모아 기존 단계별 이력과 구분했다. Linux 기본1.0.4, 주행 기본·로컬1.0.8,
  마지막 보드 메모리1.0.7을 한 가지 완료로 합치지 않는다.
- HTTP/Spring 요청서에서 RAM 문맥 분리와 stack row identity 설명이 섞인 문장을 분리했다.
  기존 time-budgets·sources 및 과거 하위 소절의 anchor를 보존하고 관련12문서의 로컬 링크/anchor가 모두 해석되는지 확인했다.
- 최종 MQTT 계약·166파일 checksum은 바꾸지 않았다. schema2.1과 최근 관측/원 lease·최초 오류·퇴역 boot 보호,
  스택 화면 보류·타 담당 제품 경계는 기존 확정 계약을 따른다.

### 작업 커밋 대응

| 커밋 | 작업 단위 |
|---|---|
| §7e0bca6a§ | reboot 결합·원 stop 실패 수습 |
| §5d76fbaa§ | create 게시·binding 진행 |
| §9278accc§ | Worker/Gateway 자격·factory 포장 |
| §44fa2ca4§ | XenStore 고아 거래 정리 |
| §05497677§ | Gateway STOP 원 결과·출력 우선 |
| §b74f4d89§ | MQTT 앱 버퍼·최근 RAM 계약 |
| §d6f74912§ | 실제 owner 메모리 계측·공정 발행 |
| §234c24e3§ | TCP 스택·불변 버전 빌드 검사 |
| §6268d79b§ | 실제 주행1.0.8 기본 선택 |

Git의 기존 LF 규칙에 따라 일부 시험 입력의 CRLF가 index에서 정규화됐다. 기존 불변1.0.8 firmware/manifest를
수정하지 않았다. 현재 구현 커밋의 **비시험 C/H·빌드 설정132개**가 원 ARM receipt와 일치함을 Git blob으로 확인했다.
기존 ARM source digest는 원 snapshot의 값이며, host 계약 후속·정규화된 시험 입력까지 현재 저장소 전체와 같은 digest라고 주장하지 않는다.

### 이번 실제 로컬 확인

Windows 작업 파일은 원문·해시로 백업했다. 실행은 버전 관리되는 소스만 Linux 임시 경로에 복사한 fixture에서 수행했으며
큰 이미지·운영 자산은 복사하지 않았다. 테스트한 플랫폼 소스1184파일이 원 작업 파일과 바이트 일치했다.

| 확인 범위·기존 시험 | 최종 결과·한계 |
|---|---|
| reboot/stop/binding/provisioning/preparation/packaging/template/XenStore/Telemetry 하위 묶음 | 일반 사용자139PASS·21SKIP·21subtests. root 소유 조건의21건은 아래 두 회차로 모두 따로 확인 |
| root-owned private 공급·factory 설정 | 기존 Mosquitto2.0.18 도구 경로를 공급해20PASS·5subtests. 실제 운영 계정/브로커를 쓰지 않음 |
| 실제 ARM1.0.8 factory 시험 | XENOVA_ACTUAL_DRIVE_TEMPLATE로 불변 번들을 공급해1PASS. 실제 조립/등록/VM별 공급 함수, Xen/서비스는 소유 fixture |
| key stop/HTTP 통합/CPU/OpenAPI 계약 |166PASS. 기존 HTTP·서비스 예산·fork·원 결과 의미 확인, 라이브러리 deprecation warning1건은 의존성 변경 없이 보존 |
| Gateway IPC/feedback/PTY |41PASS. 실제 로컬 IPC·원 STOP 수습·바이트 경로, 실제 장치/물리 정지 수락 아님 |
| planner/world/Telemetry 원천/lifecycle bootstrap |53PASS. 실제 제품 계산/owner 코드와 원 관측·독립성 비회귀 |
| MQTT 정식 self-test |2.0 정상30/거절30·11sequence28assertions,2.1 정상27/거절24,RAM계산23·view140assertions PASS |
| 배포 payload |13PASS·16subtests,1환경 실패. systemd>=259 loader 미공급의 기존 제한이며 경고/검증을 제거하지 않음 |
| 문서·index·파일 범위 |12문서 로컬 링크/anchor 이상0, 전체 whitespace 검사 통과, 계약166파일의 실제 staged blob checksum 일치 |

첫 실행의 환경/입력 실패도 보존했다. WSL Git이 Windows gitdir를 읽지 못해 host Git inventory로 소스를 공급했고,
first-install/common 경로 누락·변경 없는 Spring DTO 누락·Mosquitto PATH 누락은 기존 도구/입력을 공급해 같은 시험이 통과했다.
별도 metadata 검사기는 bitbake -e 로그 인자 없이 호출해 사전 오류가 났으며, 정규 BitBake metadata/package QA를 새로 수행한 근거로 세지 않는다.
실패를 숨기려고 제품·권한·timeout·필수 검증을 완화하거나 의존성을 설치하지 않았다.

정확한 명령/전체 로그/해시·커밋 계획은 `Documents/Codex/2026-11-05/commit-closure/`에 있다.
`platform-core-paths-fixed`, `private-root-fixtures-tools-fixed`, `actual-arm-factory`, `http-contract-inputs-fixed`,
`gateway`, `planner-telemetry`, `mqtt-contract`, `deployment-payload`의 json/log를 확인한다.
최초 오류·중간 결과·원문 backup, `docs-check.json`·`tested-source-equivalence.json`·
`committed-arm-source-equivalence.json`·`commits.json`도 보존한다.

systemd259를 갖춘 고정 target/정규 패키지·전체 이미지 검증, 실보드 지도 채택/실제 계산·MANUAL heartbeat 화면,
운영 Worker/Gateway 및 장치 종단은 각 기존 수락의 미완료 범위다. 이번 커밋과 로컬 성공 횟수로 완료를 대신하지 않는다.

<a id="worker-status-closure-20261105"></a>
## 2026-11-05 Worker 반복 운영·상태 발행·boot 문맥 국소 보완

후보는 `feat/platform/integration-followup`의 HEAD `5d05101efd39c457cb78a05b473952bc38e7a317`와
이 절에 연결한 미커밋 diff다. 기존 검토 원자료는 `Documents/Codex/2026-11-05/platform-review/`에,
이번 source manifest·명령·전체 로그·해시는 같은 날짜의 `worker-status-fix/`에 둔다.
`verification.json`에 최종 파일/시험 사본의 바이트 대조와 각 실행 명령·종료 코드·로그 hash를 모았고,
`final-candidate.json`과 `candidate.patch`로 HEAD 이후 실제 변경을 식별한다.
실보드·운영 서비스·실데이터·배포·commit/push는 수행하지 않았다.

### 변경과 유지한 책임

| 변경 묶음 | 확인한 문제 → 수정 | 유지한 보호·추가 상태 |
|---|---|---|
| 판단 사본·중복 로그 ID | 기본 무제한 `judgments` 큐는 사용하지 않아도 쌓였다. 기본은 누계만 세고 Engine에 직접 전달한다. 명시한 유한 진단 큐만 `put_nowait`하며 포화 손실을 센다. `policy=none`의 `_skipped` ID는 기존 닫힌 record 정리 때 같이 해제한다 | `run_once()`의 이번 판단 목록, Engine의 필수 수락·저장, pending event·살아 있는 보호의 보존 조건과 `KEEP_CLOSED=20` 유지. 새 복구 큐·DB 없음 |
| 부가 상태 발행 | 감시 스레드의 상태 파일 쓰기·상태 로그·Gateway 진단 조회를 전용 실행자에 넘겼다. Watcher/Engine metadata는 owner에서 작은 표본으로 만들며 실행자에서 재조회하지 않는다 | in-flight 1개+최신 pending 1개, 실행자 1개. 원 표본 시각/lease 유지. 교체·실패·잔존 계수와 최초/마지막 예외·최초 내부 결함을 유한하게 보존. 필수 저장을 best effort로 바꾸지 않음 |
| boot 문맥 | 단조 나이만으로는 다른 부팅의 비슷한 uptime을 구분하지 못했다. 상태 파일 버전2에 `host_boot_id`를 넣고 reader가 현재 boot 확인 뒤에만 나이를 계산한다 | 다른 boot는 STALE, 과거 버전1·손상·boot 확인 불가는 UNAVAILABLE. 다음 정상 표본으로 자연 회복. 원 journal/hash/기한이나 운영 기록을 변환하지 않음 |

상태 파일2는 MQTT2.1·firmware 버전·복구 journal 버전과 별개다. 일반 호출자가 버전을 선택하지 않는다.
`StatusReporter.report()`는 실행자 시작 전 동기 단건 진단에 남기고, 실제 Worker는 `submit()`을 쓴다.
`sink_snapshot()`의 계수는 프로세스 내 부가 진단이며 새 필수 원장이 아니다. 원 출력·credential·stack을
상태 파일에 복제하지 않는다. `close()`는 최대0.25초만 기다리고 False이면 실행자가 남았음을 나타낸다.
커널 I/O를 강제 취소하거나 성공 drain으로 꾸미지 않는다. 기존 일반 로그의 공유 handler와 모든 CPU/I/O
간섭까지 제거했다는 보장은 아니다. HTTP 상태 route는 이번에 추가하지 않았다.

### 실패와 회복 assertion

이전 검토의 실제 50회 표본은 닫힌 Engine 기록20개와 별개로 원문 큐50개(maxsize0)·`_skipped`50개가 남았다.
원자료는 `platform-review/retention-probes-confirmed.log`다. 아래 새 회귀는60회로, 서로 다른 표본 수의
지연 배수나 운영 상한을 비교하는 시험은 아니다.

- 실제 Worker/Watcher/Engine으로 crash→명시 stopped를60회 반복했다. 필수 닫힌 기록은20개,
  `_skipped`도20개이며 기본 진단 원문 큐는 없다. 명시한 용량1 사본 큐는1개·손실59개로 제한되고,
  Engine에는60개 판단이 전달된다. 그 뒤 신선한 정상 신호가 HEALTHY로 처리된다.
- 실제 상태 파일 교체, Gateway 진단 조회, 상태 logger를 각각 막았다. 막힌 동안26 tick이 진행하며
  in-flight1/pending1·교체24로 제한된다. 원 객체를 바꿔도 제출 표본은 바뀌지 않는다.
  해제 후 기록이 끝나도 원 표본이20초 지났으면 STALE이며, 새 정상 표본에서 OPERABLE로 돌아온다.
- ENOSPC와 내부 TypeError를 따로 주입해 최초 원 cause·오류 종류·한 번의 상세 기록을 보존하고 정상 쓰기로 회복했다.
  ENOSPC 뒤 다른 TypeError·KeyError도 각각 원 cause와 stack을 남기며, 정상 발행 뒤 같은 결함이 다시 발생하면
  다시 기록한다. 최초 I/O 실패가 후속 내부 결함을 숨기지 않고 연속된 같은 내부 결함의 중복 출력만 억제한다.
  부가 sink 실패와 필수 RecoveryStore 실패를 함께 주입하면 실제 복구 호출은0이다.
  필수 저장을 복구하고 기존5초 재시도 대기를 지킨 뒤에만 정상 호출1회가 진행한다.
- 다른 boot의 uptime이 기존 표본보다1초 큰 경우도 STALE이며, 서로 다른 단조 시각의 나이를 만들지 않는다.
  새 writer의 정상 표본, 버전1 뒤 정상 표본, boot 읽기 오류 뒤 회복을 확인했다.
  유효하지 않은 host UUID·null·bool은 정상 근거로 쓰지 않는다.
- 느린 sink에서 `close()==False`와 실행자 잔존을 확인하고, sink 해제 뒤 실제 종료·추가 제출 거절을 확인했다.

### 검증·포장 수준

| 같은 제품 후보의 확인 | 결과·대체 경계 |
|---|---|
| Worker/상태/Engine 집중 회귀 |174PASS. 첫 회차172PASS/1FAIL은 새 회복 시험이 기존5초 backoff 전에 성공을 기대한 오류였다. 시험 시계만 진행시켜 수정했고 제품 예산/보호는 완화하지 않았다 |
| dom0-manager 전체 |1022PASS·9subtests,207.89초(집중174 포함, 포장 회귀와 병렬 실행). 원 key·HTTP·소유/저장·Gateway 보호·재합류 회귀 포함. 기존 라이브러리 deprecation warning1건 유지 |
| 하위 Recovery/stop·배포 payload/Gateway 포장 |70PASS·35subtests,85.49초(Manager 회귀와 병렬 실행). 고정 ARM systemd259 loader를 QEMU user-mode로 실행해 정상 graph와 cycle 음성 대조 확인. 실제 PID1/service 기동이나 운영 지연 상한은 아님 |
| 실제 ARM Python/QEMU |고정 CPython3.14.7/AArch64로 현재 수정 모듈을 실행. sink 정지 중26 tick·pending1·교체24, 정상 쓰기2회·실행자 종료, boot ID 주입 변경 뒤 정상 발행 PASS. socket connect/bind를 audit hook으로 금지 |
| 설치·의존성 |현재 recipe의 기존 Python RDEPENDS closure75개를 캐시 RPM에서 별도 임시 root에 추출. 새 패키지 의존성/모듈/설치 경로를 추가하지 않았다. 현재 recipe의 기존 `.py` 설치 규칙과 unit 대조. 새 RPM/전체 이미지 빌드는 아님 |

ARM 시험은 처음에 Gateway용 과거 설치 root만 사용해 `zoneinfo` 경로, 이어 불완전한 의존성 closure의
`concurrent`가 누락됐다. 현재 Manager recipe의 Python 의존성 전체와 실제 stdlib/site-packages 경로를
공급한 뒤 통과했다. 제품 import를 대역으로 바꾸거나 새 버전을 설치하지 않았다. 원 오류는 first/second ARM 로그에 보존했다.

QEMU의 확인 수준은 구분한다. 앞선 검토의 QEMU는 ARM systemd loader 검사였고,
이번에는 실제 변경 Python 모듈의 ARM user-mode 실행을 추가했다. Zephyr 전체 게스트 부팅,
Gateway 고정 broker/QEMU 전송5개, 새로운 firmware/운영 설치·물리 정지 수락까지 수행한 것은 아니다.
Spring stop 원 key 연결과 episode 간 안정 구간/누적 복구 예산은 앞선 검토의 별도 미연결 범위로 남긴다.

<a id="worker-budget-closure-20261106"></a>
## 2026-11-06 E4 안정 구간·VM별 누적 복구 예산 국소 구현

후보는 `feat/platform/integration-followup` / HEAD `5d05101efd39c457cb78a05b473952bc38e7a317`와
현재 diff·새 `recovery/budget.py`, `tests/test_recovery_budget.py`다. 전날 Worker 사본·상태 sink·boot 보완을
보존했다. 착수 전 diff/해시는 `Documents/Codex/2026-11-06/e4-budget/before.patch`·`before.json`에,
이번 명령·로그·소스 대조는 같은 폴더에 둔다. 프롬프트 자체를 완료 증거로 쓰지 않는다.
이 로컬 구현 단계에서는 실보드·운영 env/unit·서비스·원장·commit/push를 변경하지 않았다.
이후 승인된 소유 fixture의 보드 실행과 Worker 일시 정지는 [보드 후속](#budget-board-observations-20261106)에 분리한다.

### 실제 달라진 계약과 구현 기준

| 변경 | 위치·기준 | 보존한 것·실제 제공 범위 |
|---|---|---|
| 선택적 명시 프로필 | `RecoveryPolicy.stable_s/vm_max_attempts`, 대응 env 두 개를 함께 검증. 운영 기본값 없음. 상한은 기존 episode 상한 이상 | 미설정은 기존 동작. 숫자는 시험 파일의 명시 PROFILE에서만 공급. 기존 backoff·기한·force 승인 유지 |
| VM별 누적 | `RecoveryEngine._task_attempt`, `RecoveryStore.vm_budget_used`. 새 논리 attempt와 계수를 같은 원자 저장으로 확인 후 호출 | 원 replay/조회/응답 유실/Worker 재시작의 추가 차감0. 새 episode·실행·사유·사용자 stop은 계수를 초기화하지 않음. 실제 재부팅 횟수와 구분 |
| 연속 안정 | `Watcher.stability_samples` → `StableRun` → `Engine._update_stability`. 이번 관측의 의도·domid/실행·앱 binding·원 수신 시각만 사용 | 캐시 재조회는 진행0. source 만료·건강 lease 밖 공백·관측 실패·문맥 변경은 연결을 끊음. Worker 재시작 때 누계는 복원하고 안정 시간은 다시 확인 |
| 정상 대기·회복 | warm `WAITING/QUALIFIED`의 두 앱 감독 진행으로 확인. 실제 계산은 실행 중일 때 기존 Watcher 기준으로 확인 | 가짜 heartbeat/계산0. 준비·입력 부재를 VM 재시작 사유로 만들지 않음. Xen-only starter는 Xen 관측 범위. 원 COMPLETED·현재 건강·안정·재합류·출력 허가 분리 |
| 소진·저장 실패 | 새 시도만 보류하고 원 오류와 별도로 UNKNOWN/EXHAUSTED/CONTEXT_UNAVAILABLE을 표시. 안정 갱신 저장 실패는 계수를 되돌림 | 원 결과·pending 사건·Gateway 보호/정산 유지. 시간만으로 자동 재개0. 충분한 실제 안정과 저장 확인 뒤 새 장애의 정상 복구 진행 |
| 유한 상태·비용 | VM당 계수 하나, 필요한 VM당 진행 중 안정 표본 하나. 기존 저장에 결합. 연속 건강26회에서 갱신 fsync1을 계수 | 매 tick 추가 fsync·전체 원장 재검색·대용량 복사·새 전역 잠금/daemon 없음. 닫힌 기록20 정리와 별개. 명시 등록 부재/영구 제거 응답에서만 계수 정리 |

동작값은 Worker 공통 정책이고 사용량/보류는 VM별이다. `engine.budget_status(vm_id)`는 소유 프로세스·시험의
내부 조회이며 새 HTTP/MQTT endpoint가 아니다. 상태 파일의 세 보류 사유와 전이 로그에 원 recovery·VM·사용량/상한을
연결한다. 기존 오류 code/사유·원 예외를 합치거나 없애지 않았다.

새 attempt의 계수는 이미 있던 필수 저장에 합치므로 별도 fsync를 추가하지 않는다. 안정 예산 갱신은 새로운 필수 전이여서
동기 저장1회를 추가한다. 기존 Engine 잠금/필수 파일 I/O 경계는 유지했으며, 필수 저장장치가 멈춰도 감시 지연이 없다는
보장은 아니다. 느린 부가 status sink와 필수 정책 저장을 구분한다. 이 대기와 실제 부하의 시간 예산은 다음 보드 시험에서 측정한다.

마감 정적 점검에서는 재발행 주기와 실제 건강 lease를 구분해 정상 스케줄링 지터를 수락했고, 판정 직전 confirm에서 바뀐
의도/실행 관측을 그 health와 같은 owner 표본으로 보존했다. 이전 관측의 문맥을 안정 근거로 붙이지 않는 회귀를 추가했다.

### 저장 호환과 재시작

기존 내부 저장1은 계속 읽는다. E4 추적을 시작한 저장2는 `vm_budget`을 기존 recoveries/pending과 함께 쓴다.
과거1의 남은 닫힌 기록20개만 세어 전체 사용량을 추측하지 않는다. 그 상태는 UNKNOWN으로 새 자동 시도만 보류하고,
유효한 원 attempt는 동일 spec/ID/기한으로 회수한다. 충분한 정상 관측을 얻으면 기준0을 내구 기록한다.
저장2를 읽은 새 코드가 제한을 비활성화해도 기존 계수를 버리거나1로 downgrade하지 않는다.
일반 호출자에게 저장 버전 선택/변환을 요구하지 않으며, Engine/state/policy/budget/Watcher/Worker의 내부 설치본은
같은 후보로 교체해야 한다. 구형 writer의 저장2 호환은 주장하지 않는다. 손상/중복 JSON key/형식 오류는 기존 history-lost 보호로 차단한다.

기본 `/run`은 같은 host 부팅의 Worker 재시작 범위다. 보드 재부팅까지 보존하는 운영 경로 변경은 하지 않았다.
별도 영속 fixture에서는 boot가 바뀌어도 시간 없는 계수는 보존하고, 안정 시간은 새 부팅/관측에서 다시 확인했다.
Worker 중단 구간·다른 host의 단조 시각을 안정 시간에 더하지 않는다. 새 기록 부재와 과거1의 정보 부족은 다르게 처리한다.

### 로컬 정상·실패·회복 수락

제품 경로는 실제 Worker/Watcher/Engine/RecoveryStore/EventLog다. 외부 Xen/Manager 통신 결과만 기존 fixture로 대체했다.
원 논리 attempt의 수락 저장과 실제 호출 사이에서 저장 파일의 계수/원 spec을 확인한다.

- episode 세 개 뒤 소진·단순100초 경과의 보류·다른 VM 정상 복구·연속 안정 뒤 새 요청 진행을 확인한다. 값은 시험 프로필이다.
- 응답 유실·동일 attempt 조회·Worker 재시작 뒤 원 ID/spec/deadline/사용량 유지, 별도 Python 프로세스에서 실제 Worker를 기동해 계수 복원을 확인한다.
- 닫힌 기록20개를 넘어선24회 사용량을 기록 정리/재시작 뒤에도 유지한다. 복구 중·미확정인 원 시도에는 안정 환급을 하지 않는다.
- 캐시 heartbeat·관측 실패·의도/실행/host 변경·상태 sink 지연 중에도 제한/정상 회복을 확인한다.
- 새 attempt 저장 실패의 실제 호출0·차감0, 안정 갱신 저장 실패의 거짓 회복0, 정상 저장 뒤 다음 복구 진행을 확인한다.
- 과거1의 원 결과 회수와 미확인 예산·충분한 안정 뒤 회복, 저장2 손상 원본 보존·자동 실행 차단을 확인한다.
- 정상 warm Standby 두 앱의 작업 progress0·실제 앱 heartbeat 미생성 상태에서 감독 진행만으로 안정 회복한다. 기존 AUTO/MANUAL 독립성과 Gateway 보호는 기존 회귀에 연결한다.

최종 실행 명령/종료 코드/로그 hash·제품 파일 대조는 이 폴더의 `verification.json`에 모은다.
ARM/QEMU는 기존 고정 CPython3.14.7·캐시 의존성에서 실제 변경 모듈을 실행하는 수준이며 전체 Zephyr 부팅·새 RPM/이미지·운영 배포가 아니다.

| 최종 후보 검증 | 실제 결과·범위 |
|---|---|
| 집중 회귀 |259PASS,14.22초. E4 신규40건과 기존 Store/Engine/Worker/상태/warm 회귀 포함 |
| Manager 전체 |1062PASS·9subtests,371.98초. 집중259 포함. 기존 라이브러리 deprecation warning1건이며 의존성 변경 없음 |
| Recovery/stop·recipe payload·Gateway 포장 |70PASS·35subtests,114.51초. 전체 회귀와 병렬 실행. 고정 ARM systemd259 loader의 정상/cycle 음성 대조 포함. 실제 PID1/service 기동은 아님 |
| ARM/QEMU 실제 모듈 |AArch64 CPython3.14.7에서 실제 Worker/Watcher/Engine/RecoveryStore 실행. 응답 유실·재시작 원 spec 유지·상한3에서 추가 호출0·안정 회복 후 다음 호출1 확인. 값5초/3회/episode2회는 명시 fixture뿐이며 Xen/통신 port는 대역 |
| 부분 설치·의존성 |실제 recipe의 `.py` 설치 규칙으로 새 `dom0_manager/recovery/budget.py` 포함 바이트/0644 모드를 임시 root에서 대조. 기존 target Python 의존성 closure75개를 캐시 RPM으로 공급해 ARM 실행. 개발 source 경로 import이며 새 정규 RPM/전체 이미지 QA·운영 설치로 확대하지 않음 |

모든 시험은 소유 임시 fixture에서 수행했다. 최종 소스·시험 사본을 파일별 SHA256으로 대조하며,
새 파일까지 담은 `candidate-with-new-files.patch`와 `changes.tar`는 검토용 원자료이고 정규 패키지가 아니다.
관측·배포 환경이 다른 로컬 실행 시간을 운영 deadline이나 상대 성능 배수로 해석하지 않는다.

<a id="budget-board-followup-20261106"></a>
### 실보드 예산 조정 입력표와 진행 근거

사용자 결정: 이번 운영 수치는 확정하지 않고, 구현 마감 뒤 실보드에서 기존 미측정 예산과 함께 조정한다.
아래 표는 시험 시작 전의 입력표다. 2026-11-06에 실제 시험을 시작했으며, 확인한 결과는 아래 후속 절에 분리한다.
이전 보드 성공·실패는 당시 후보의 근거로 보존하고,
이미 고친 증상을 미해결로 다시 등록하지 않는다. 기간 단위 전역 복구 상한(DO2)은 이번 VM별 E4와 별도다.

| 묶음·원본 | 다음에 실제로 측정할 것 | 유지할 수락 기준·미완료 구분 |
|---|---|---|
| E4 / `recovery/policy.py`, `budget.py`, `engine.py` | 원 신호 주기/지연·안정 후 재발 분포·재시작/재합류 비용, 짧은 정상과 지속 정상. 같은 키/Worker 재시작/부하 병행 | 안정 시간과 VM 상한은 측정·운영 승인으로 선정. 원 시도/예산 보존, 소진 후 정상 회복, B 독립 진행. `/run`의 host 재부팅 보존 범위 별도 |
| Worker 신호·유예 / `recovery/config.py`, `watcher.py` | cold/warm 부팅의 첫 유효 Agent/App·현재 binding, 감시 지터·손실/동료 신선도 창·공통 경로 단절/회복 | 기존 최초 미수신/공통 경로 보류 유지. 입력/Standby 대기를 장애로 만들지 않음. 현재 기본값이 실보드 승인값이라는 주장은 하지 않음 |
| 복구 호출 / `recovery/policy.py`, `protection.py` | graceful 종료·강제 수습·새 실행 확인·재관측·Gateway 보호의 각 구간과 원 기한 소모 | 같은 attempt 회수·실행 신원·현재 intent·force 승인 유지. 정상 종료 지연을 즉시 실패/force로 합치지 않음. 숫자는 정상/실패와 병렬 표본으로 검토 |
| Linux 생성 / `creation-policy.json`, `xenova_creation.py`, 배포 준비 문서 | 지원 템플릿과 실제 SD의 준비/검증 복사/게시 구간·호출 예산 | 예제 null을 활성 정책으로 쓰지 않음. create 승인 예산만 확정하며 원 완료 조회·stop에 전역 gate를 추가하지 않음 |
| Gateway / `verified_runner.py:_submit`, `sink.py`, feedback 계약 | 접수→actor→writer→시리얼 제출/feedback 각 구간, 정상·병렬 부하·느린 로그/장치의 지연 | 기존 초기 STOP 미확정 진단/수정 근거 보존. 원 제출 회수·명령 만료·STOP 보호 유지. PTY 계약 증거와 실제 장치 적용/물리 정지는 구분 |
| Zephyr 네트워크 / `xen-runtime/src/netfront.c`·ring 시험 | 일시 backend 조회 실패·packet 경합·부족 버퍼·복귀 중 TX/RX/MQTT 진행과 최대 대기 | 이미 보완한 일시 실패/종료 보호를 재사용. 신원·ring/grant·실제 backend 종료는 완화하지 않고 현재 설치 펌웨어와 부하에서 재수락 |
| 주행/계측 / `apps/drive/zephyr/src/workers.c`, Diagnostics·Telemetry2.1, 앞선 보드 보고 | 지도 채택→실제 계산/완료·pool 점유/반환, deadline/cancel·감독 신선도·수집/직렬화/발행 비용. AUTO/MANUAL·통신 동시 부하 | 현재 작은 tick 측정의0을 비용0으로 해석하지 않음. native/합성 지도 성공을 실제 보드 입력 채택으로 대체하지 않음. 운영 자원·기한은 승인 프로필에서 확인 |

시험 전에 설치 파일/firmware·현재 적용 프로필·신호/정책 원천을 후보와 대조하고, 대상 VM·필수 원 기록·설정 백업/복원·
실제 출력 차단/장치 대역 경계와 정리 책임을 정한다. 아래 시험은 새 소유 VM·private 정책·별도 broker·가상 장치를 사용하며
운영 정책 확정·기존 VM 교체·재무장·차량 종단 수락과 구분한다.

<a id="budget-board-observations-20261106"></a>
### 2026-11-06 보드 후속 — 등록 경합 수정과 예산 시험 중간 근거

기준은 `5d05101efd39c457cb78a05b473952bc38e7a317`와 현재 미커밋 변경이다. 소스 수정·시험은 계속 진행 중이며
아래 결과로 일곱 묶음 전체 마감이나 운영 정책 승인을 주장하지 않는다. 비밀 없는 로컬 원자료는
`C:/Users/SSAFY/Documents/Codex/2026-11-06/board-budget`, 로컬 회귀는 같은 날짜의 `e4-budget`에 보존한다.
보드 시험 소스는 `/run/xenova-budget-20261106-227jfxlm/source`에만 공급했다. 운영 설치 모듈은 교체하지 않았다.

#### Zephyr 등록의 초기 intent 경합

확인된 실패는 firmware 준비·DHCP 반영 뒤 `Execution.initialize()`가 admission 잠금을 즉시 얻지 못해
원 create가 `RECOVERY_REQUIRED`로 남는 것이었다. VM 실행 전이라는 사실은 준비 자산도 없다는 뜻이 아니다.
첫 원 요청은 기존 replay로 완료됐지만, 두 번째 요청에서는 같은 경합이 반복됐다.

`xenova_management.py:_finish/_complete_registration`을 국소 수정했다. 기존 no-wait 초기화가 admission 경합을
반환하면 `PendingRegistration`으로 현재 원 작업과 등록 상태를 전달한다. 호출자의 기존 응답 기한 안에서
**공통 잠금 밖**에서 기다린 후 재진입하고, 원 op·host·tombstone·instance·기존 대상 lease를 재확인한다.
현재 guard가 달라지면 거절한다. 기다리는 동안 원 요청·UUID·명세·생성 기한을 새로 발급하거나 복사하지 않는다.
`xenova_execution.py`의 API·원장 형식과 no-wait admission 원칙 자체는 변경하지 않았다.

| 검증 | 직접 확인한 assertion·결과 | 한계 |
|---|---|---|
| 실제 보드의 같은 Zephyr 원 key | 독립 프로세스가 초기 intent 직전에 실제 admission flock을 보유. 같은 operation `e2e04697-48a0-4ea2-910b-ca75b02f025a`가 `RECOVERY_REQUIRED → SUCCEEDED`; 원 신원·명세 보존; helper가 대기 중 공통 잠금 획득 성공. 호출 4.5666초, 계측한 admission 대기 17.764ms | 운영 설치 교체 없음. 이 표본의 wall time은 운영 상한이 아님 |
| 로컬 등록·회복 기반 | `local_checks.py registration-admission-r4 platform/tests/test_xenova_management.py platform/tests/test_xenova_recovery_foundation.py`: 116 passed | 고정 systemd259 검사 환경 재사용 |
| 생성·동시성·수습·조회·기본값·기존 HTTP 호환 | `registration-guard-final-r2`: 158 passed. 명시된 여섯 파일은 동명 JSON/log에 보존 | 실제 HTTP 서버·Spring 종단을 실행한 결과가 아님 |
| 중단/문맥 변경 | 원 기한 내 실패 후 같은 key 재개, 검증 복사1; host/op/lifecycle/config 변경은 거절; 원 오류 유지 | publish·`_save_tomb` 등 다른 admission 경계를 전부 대기로 바꾼 것은 아님 |

최종 Manager 바이트 SHA256은 `0501b25d97964bf9bf8f4c73c64b806c255f59215e687dc615fd8ece2d57a3b8`다.
`registration-fixed.out/json`에 호출·trace 원자료가 있다. 앞선 `published` checkpoint 주입은 `_save_tomb`에서
먼저 거절되어 초기 intent 시험에 도달하지 못했다. 실제 `_save_tomb` 완료 뒤 주입하도록 시험을 고쳤고,
이 시험 배치 오류와 제품 결함의 수정 결과를 구별한다.

#### 기존 binding 관측·발행의 중복 작업

실제 보드에서 기존 실행의 `observe → receipt refresh → observe`가 Xen 관측을 두 번 수행하는 것을 확인했다.
`rejoin.py:_work`는 이미 결합된 receipt를 먼저 읽고 새 관측 한 번으로 같은 실행인지 확인한다.
새 실행 공급은 기존 전후 관측을 유지한다. receipt 오류도 현재 실행을 확인한 뒤 원 오류로 전달하며
다른 실행·새 intent·중단 신호를 옛 receipt로 덮지 않는다.

`pump`의 100ms idle tick은 같은 binding 파일을 다시 쓰지 않는다. 실제 future 완료 또는 행 변경 때 게시한다.
같은 `stopped/absent`라도 **새 관측 완료**가 있어야 lease를 갱신하고, 새 관측 없이 3.1초가 지나면 만료한다.
관련 `test_drive_rejoin.py`, warm liveness·하위 binding 회귀 25건이 통과했다.
변경 전후 보드 소표본에서는 기존 실행 관측이 2회→1회, 30초 binding 쓰기가 160→6회였다.
negative-row 갱신 보완 전의 측정이므로 최종 후보의 전체 성능 수락으로 합치지 않는다.
동일 부하에서 binding 만료도 여전히 관측됐다. TTL 연장이나 guard 생략으로 덮지 않았으며 남은 지연을 별도로 진단한다.

#### 일곱 예산 묶음의 중간 결과

| 묶음 | 확인한 결과 | 아직 완료로 볼 수 없는 범위 |
|---|---|---|
| E4 | 새 Debian 소유 fixture의 실제 Xen/Manager/Agent MQTT/Worker/Store 사용. 응답 유실·새 Worker 프로세스에서도 같은 attempt와 누계1 보존. 세 원 attempt 뒤 네 번째 장애는 새 attempt 없이 누계3으로 보류. 원 Agent의 연속 진행 뒤 3→0 내구 갱신. 갱신 후 실패도 차감에 포함하고 명시 수습 뒤 다음 정상 복구 완료 | `e4-status14.out`: passed=true, 장애5, 원 attempt5, used2/limit3, outstanding=false. `stable_s=60`, `vm_max_attempts=3`은 시험값. 새 시도의 기존 상한120초 비교에서117초 성공으로 여유 약3초뿐이며 운영값 확정 근거가 아님. 실패한 원110초 기한은 불변 |
| 신호·관측 | 75초 실제 수신에서 기존 Linux Agent 약5초, Zephyr heartbeat 약2초. 입력 없는 AUTO/MANUAL은 실제 lifecycle 대기·progress0을 유지 | 수신 빈도와 현재 binding 적격성은 다름. 관측 경합이 있어 전달 판정까지 완료로 표시하지 않음 |
| 원 제어 수습 | 새 시험 B의 start가 관측 잠금 만료로 미확정. 원 key 재호출로 같은 operation·domid·execution을 유지하며 완료 | 후속 provisioning과 앱 적용은 별도. 재시작으로 우회하지 않음 |
| Linux create | 첫 private 600초 프로필은 검증 복사 중 만료, 원 실패 보존 후 `discard-private`로 종결·예약 반환. 다음 private 900초 프로필은 575.755초에 생성 완료 | 표본/부하가 달라 예산 확대 효과나 운영 승인값으로 해석하지 않음. 운영 creation-policy 파일 생성 안 함 |
| Gateway | 실제 AArch64 OutputOwner/serial codec/PTY에서 STOP 제출·미응답·장치 reboot 검사. 별도 process의 UDS→actor→Authority에서 동등 replay·취소·partial client/병렬 조회 정상 진행 | PTY·가상 feedback은 물리 정지 증거가 아님. 운영 Gateway는 mock/DISARMED 유지 |
| 네트워크 | 새 소유 B의 실제 shell에서 netfront connected/stopping=false, Store unavailable28/recoveries27, packet lock timeout0·최고 hold1322µs·최고 poll gap20331µs. 실제 MQTT v2 계속 수신. native·ASan/UBSan의 실제 ring/grant/종료/Store 회귀 3 passed | 보드 표본의 모든 실패 조건을 의도적으로 주입한 것은 아님. packet 경합·버퍼 고갈의 보드 수락은 별도. 계수 reset은 해당 guest boot |
| 지도·계측 | 별도 인증 broker·실제 B firmware·제품 Gateway·PTY 사용. 연결 슬롯 수정 뒤 v1 AUTO/MANUAL 이벤트와 여덟 app_memory region 실제 수신. 정상0과 collection_failed/null 구분 | `world-r6/r8`에서 병렬 부하의 binding·장치 적용/odom 현재성 보호로 지도 공급이 시작되지 못함. 실제 planner·이동·점유 증가/반환은 미통과. 아래 실패/부하 진단에 원자료 연결 |

Gateway PTY 표본의 serial 확인은 4.09~37.31ms, feedback 관측은 18~70ms였다. 비동기 ticket 제출을 재사용했고
10ms 한도를 올리지 않았다. 이 수치는 polling·스케줄링을 포함하며 write 자체의 실행 시간이나 실차 보장이 아니다.
UDS 표본은 partial header3개·병렬 조회12개에서 정상 조회 138.7~265.9ms, 동시 연결 최고6,
만료3, 종료0을 기록했다. `gateway-pty.*`, `gateway-ipc-r2.*`에 경계·입력·결과를 남겼다.

#### 만료한 미결합 Linux launch의 명시 종결

안정 갱신 뒤 원 attempt `cb42ab93-eea0-44d7-b7df-b5d57cd13c5c`는 110초 중 남은 약45초의
`systemd-run/xl create` 구간에서 만료됐다. 새 domain11은 paused·CPU0이고 libxl 상세 UUID가 아직 없었다.
원 scope에는 xl fork 자식과 QEMU가 남아 있었다. PID 명령행에서 VM UUID만 검색한 초기 확인은 이 자식을
놓쳤으며, 실제 cgroup membership으로 정정했다. 이 시점의 `RECONCILE_REQUIRED`와 소유 유지가 맞다.

소유 fixture만 대상으로 native unique ID·XenStore VM UUID/name·host·원 run을 재확인해 domain을 명시 정리했다.
기존 observe-only 수습이 살아 있는 scope를 거절하는 것도 확인했고, domain 부재 뒤 같은 원 scope의 QEMU만
명시 정리한 후 무잔존을 다시 확인했다. 이 수동 시험 정리와 아래 제품 `recovery-reconcile`을 구별한다.
원 기한 만료만으로 정리하거나 운영 VM을 정지하지 않았다.

`xenova_recovery.py:Recovery.reconcile`은 같은 owner의 만료한 launching/uncertain 실행에 대해
두 번의 현재 부재·정확한 VM/execution scope의 observe-only 무잔존·원 host/intent/op/run/CPU 정책을 확인한 경우,
기존 `stopped_without_replacement` 실패 종결을 지원하도록 보완했다. 현재 domain·활성 scope·관측 실패·문맥 변경은
계속 차단한다. 명시 수습은 종료 신호·새 create·force·기한 발급을 하지 않는다.

필수 저장은 runtime uncertain+원 attempt 참조 → 원 attempt failed → runtime stopped 순서다.
중간 중단은 같은 원 key로 재검증하며 `launch_aborted_before_submit`를 위조하지 않는다.
단일 저장 오류의 replace 후 응답 유실은 같은 실행의 uncertain 표지로 복원하고 원 storage errno를 보존한다.
마지막 replace와 rollback 저장이 모두 실패하면 stopped가 남을 수도 있어 **항상 uncertain gate가 유지된다는 보장**은 하지 않는다.
그 경우도 성공으로 반환하지 않고 최초 EIO·보조 ENOSPC를 나눈 `RECONCILE_REQUIRED`로 원 기록 재확인을 요구한다.
새 효과 전에는 기존의 현재 실행·의도·소유 guard가 그대로 필요하다.

| 근거 | 결과 |
|---|---|
| `test_xenova_reconciliation.py` / `board-reconcile-final04` | 40 passed. 새 helper+7시험, 기존 assertion 완화0. 다음 정상 복구·새 실행에 대한 과거 결과 replay·live/unknown/활성 scope/문맥 변경 거절·필수3저장 전후 EIO·응답 유실·중단·회복 |
| 복구 기반·review·reconciliation·stop / `board-recovery-boundaries-final` | 164 passed / 118.74초. 실제 제품 저장·판정 경로, 외부 Xen/systemd 경계는 fixture |
| 실제 보드 `partial-reconciled.out/json` | 진단 confirmable→같은 원 attempt uncertain→failed, runtime stopped. 원 명세/hash/신원/기한 보존·신규 attempt0·물리 명령0. 원 RECONCILE_REQUIRED를 reconciliation에 보존하고 최종 DEADLINE_EXCEEDED로 실패 종결. 두 호출 합계24.34초이며 각 조회 예산은15초 |
| `package-reconcile-final-summary.json` | 기존 고정 QEMU/AArch64 CPython3.14.7에서 현재131파일의 byte/mode·130개 Python 구문·17모듈 import 통과. 소스/recipe drift0 |

현재 하위 recovery 모듈 SHA는 `53ecb6e7d3a711b1346c71171c1d6f76b511fed8340f7cf02ce1420dd0fc734d`다.
기존 recipe의 명시 SRC_URI/0644 설치 대상에 이미 포함되어 포장 규칙·의존성을 추가하지 않았다.
이는 작은 모듈 설치 fixture와 실제 ARM import 수준이며 새 BitBake/RPM/이미지·운영 설치 완료가 아니다.

#### 지도 시험의 현재 연결 진단

`world-run`의 첫 실패는 시험용 과거 Authority 경로를 로그 디렉터리와 겹치게 둔 fixture 설정 오류였다.
제품의 incomplete Authority 거절은 유지하고 별도의 미사용 private 경로로 바로잡았다.
`world-r2`에서는 실제 B binding 관측이 2065ms로 기존2초 예산을 넘었다.
`world-r3`는 시험만3초 관측으로 비교했고 초기 SAFE_STOP/DISARMED·실제 v2 메모리 수신까지 진행했으나,
지속 binding 만료와 주 v1 제어/앱 이벤트 미연결이 남았다. TTL3000ms·운영 기본값·유효성 검사를 늘리거나 생략하지 않았다.
원 장치·관측이 부족한 동안 양수 구동은0이었고, 종료 시 SAFE_STOP 게시와 잔존 thread0을 확인했다.

실제 수신값은 JSON64 규약의 문자열 수치다. `drive_maps`에서 reserved81224/used0/capacity80472 bytes,
`manual_pending`에서 reserved2360/used0/capacity2360 bytes를 `ok`로 확인했고,
`collection_failed`의 null도 별도로 존재했다. 이를 유효0이나 지도 점유 증가/반환 증거로 바꾸지 않는다.
실패 당시 ARM 설정은 NET_MAX_CONN4·NET_MAX_CONTEXTS6·MQTT_MAX_SUBS4였다.
실제 shell의 `net conn`에서 TCP 세 개와 DHCP UDP68 직접 handler 한 개가 네 슬롯을 채웠고,
v1 제어 연결은 없었다. 따라서 구독/앱 readiness 문제가 아니라 네 번째 연결을 만들기 전 자원 부족이 먼저 있었다.
[Zephyr 공식 네트워크 설정 원칙](https://docs.zephyrproject.org/latest/services/connectivity/networking/net_config_guide.html)은
TCP/수신 UDP 및 DHCP/DNS도 connection endpoint를 사용함을 설명하지만 최신 문서의 수치를 운영값으로 복사하지 않는다.

보드에서 실행한1.0.8의 실제 bin SHA는 `3a06fe95af4c8c30635e91583b3d70e7fe981a0722bd5e91ac9ded642af5a77d`,
firmware revision 디렉터리는 `45e646878094c15cca0ae269923eb2f712ca3e2efdf7339b5ee0636f4a159749`다.
디렉터리 revision을 bin hash로 적지 않는다. 이전 불변 제공물1.0.8의 `05274f0b…`와 다른 빌드이므로
버전 문자열만으로 동일 firmware·동일 소스/설정을 수락하지 않는다. 실제 build receipt 대조가 남는다.

#### lattice 연결 슬롯과 정식 firmware CLI 출력 보완

고정 Zephyr `684c9e8f32e4373a21098559f748f06915f950c9`의 `connection.c`, TCP 종료 경로와 DHCP 초기화를
직접 대조했다. `NET_MAX_CONN`은 TCP/UDP handler의 공통 한도이며 DHCP는 `net_context`와 별도로
UDP68 handler를 점유한다. 정상 TCP 네 개뿐 아니라 기존 context 여섯 개와 비동기 종료 수명도 고려해,
**lattice profile만** `NET_MAX_CONTEXTS6 + DHCP1 = NET_MAX_CONN7`로 맞췄다.
새 연결 프레임워크·VM RAM·CPU·네트워크/출력 timeout·packet/ring/grant 한도는 바꾸지 않았다.
`zephyr/CMakeLists.txt`는 이 프로필의 최소 관계를 빌드 때 검사한다.

| 직접 검증 | 결과와 경계 |
|---|---|
| `platform/tests/apps/xenova-network-capacity` | 같은 고정 SDK의 실제 DHCP 초기화·`net_conn_register/unregister`를 사용. pool4/5/7 각각20회 포화·거절·반환·재충전 통과. pool4는 DHCP1+TCP3 뒤 네 번째 TCP가 -ENOENT. MQTT handshake·FIN/TIME_WAIT·실보드 실행을 대신하는 시험은 아님 |
| ARM 최소값 검사 | NET_MAX_CONN4 강제 설정은 CMake가 최소7을 요구하며 rc1로 정확히 거절. NET_MAX_CONN7 실제 ARM 빌드는45.44초에 성공하고 stack audit·포장 통과 |
| 같은 소스/도구의 단일 원인 비교 | 두 변경 파일만 전후로 비교한 `conn7-isolated-arm-delta.json`: conns256→448B(+192), text−8/data+8/BSS변화0. 전체 ELF RAM 증감이나 실제 stack 최고값으로 확대하지 않음 |
| 새 불변 제공물 | 1.0.9 bin `cc5e845843f53dff552614e3553e03d411871121fb955e13ce8a7b8621303708`, manifest `3c2a657fddb6b4a9136cf0408dea742f5e9d21f0fbb6164ed8659b7b464baefe`, source `6d8a3558538d12cde776d9cff2bd2a5eff0edd65c037004a8f87a59735954b01` |
| 소유 시험 B만 교체/기동 | `conn7-trial-installed-r3`, `drive-b-start-r4`: UUID 보존, 정지 상태 교체, start44.81초·CPU 검증 complete·drive/telemetry receipt applied. 새 execution `e10da029-b6de-4926-a1e4-06dab30c89aa`, domid15. 원래 보드 A/B는 교체/재시작하지 않음 |
| 실제 연결 | `console-r4.log`: TCP 네 개 모두 ESTABLISHED, DHCP까지 handler5. netfront connected/nonstopping, packet lock timeout0. 네 번째 연결 부재는 해소됐지만 지도/계산/출력 적격성은 별도 검사 |

tar `data_filter`가 extraction 중 추가한 owner-write 때문에 처음 bundle 계획 검증이 거절됐다.
archive 원 bytes/hash와 모드를 대조해 원0444로 되돌린 뒤 검증했으며 read-only guard를 완화하지 않았다.
다음 설치 시도의 admission 경합도 원자료에 보존한다. 운영 Worker를 일시 정지한 뒤 소유 B만 교체했다.
기본 lock·factory A/B·조립 예시의 **1.0.8 선택은 그대로**다. 새 RPM/전체 이미지 빌드나 운영 firmware 교체는 아니다.

별도로 `Platform.replace_firmware`의 JSON이 `diagnostic as print`를 거쳐 `request_output`에 흡수되는 결함을
수정했다. 이제 구조화된 결과를 반환하고 기존 `management_cli`가 대상 lease·공통 잠금·진단 문맥 해제 뒤
정식 stdout으로 출력한다. 필드·exit 의미·`--json` 지원 범위는 바꾸지 않았다.
`test_xenova_cross_boundaries.py` 11 passed: 실제 plan/execute의 JSON·UUID·무기동·revision 적용과,
stdout을 막은 동안 같은 대상 lease/공통 잠금의 다른 호출 획득을 확인했다. 상세 진단을 전부 끄거나
프로세스 전역 stdout/FD를 바꾸지 않았다. 최종 VM 모듈 SHA는 `eaff3566e8955510d45335b180fa4575b8fe57edec6689f3613e539c1438b1b2`다.

최종 Python 포장은 `package-budget-final-summary.json`에서 새 CLI까지 포함해 다시 확인했다.
기존 recipe의131파일 byte/mode·130구문·17모듈 ARM/QEMU import와 설치된 CLI 직접 import를 통과했고,
소스/네 recipe drift는0이다. 모듈 설치 fixture 수준이며 새 BitBake package QA·RPM·전체 이미지 수락은 아니다.

#### 새 연결 뒤 지도·병렬 부하 결과와 예산 판단

`world-r4`는 초기 B 관측이3074ms에 만료됐고, `world-r5`는2492ms의 관측 중 적격 r/b 표본을 확인하지 못했다.
다음 시험은 **준비 단계에서만 최대3회 새 관측**을 시도했다. 같은 오래된 관측을 연장하지 않고 원 실패를
모두 남겼으며 background failure는 계속 binding을 무효화한다. trial의 개별 관측3초·binding TTL3초를
제품 기본2초나 운영 승인값으로 바꾸지 않았다.

`binding-stages-b-r4`의 네 단독 표본은1595~2536ms였다. 실제 admission wait는5~45ms였지만,
등록 정보 읽기112~385ms, admission hold182~729ms, 각각의 xl/native helper 비용이 누적됐다.
한 건은 Xen의 `------` 재표본도 수행했다. 전체 지연을 잠금 대기 하나로 단정하지 않는다.
전후 등록부·lifecycle/intent/run·Xen unique 검사는 수명이 달라 임의 cache로 생략하지 않았다.
같은 회차 publication의 메타데이터 재검증 재사용과 scan 기한 전파는 별도 개선 후보이며 이번에 구현하지 않았다.

| 시험 | 실제 결과 | 해석 |
|---|---|---|
| `world-r6`, 관측 thread | 64.683초. v1 AUTO921/MANUAL345 events, v2 metrics243, 가상 명령382·양수 DRIVE0. actor 최근512회 중앙29.09/p95 122.47/최대287.54ms. DEVICE_APPLY_TIMEOUT, 오래된 odom과 LINK_BINDING 거절. maps_sent0 | 연결은 살아났지만 최신 장치/실행 근거를 유지하지 못함. planner 실행이나 정상0 메모리로 해석하지 않음 |
| `world-r7`, 별도 관측 process | 가상 장치 준비 중20초 한도 만료. marker가 통합 시험 모듈 import에서 멈춤 | 제품 장치 ACK 실패와 다른 시험 시작 비용. 이전 실행도 해당 import만13.17초 걸림 |
| `world-r8`, 별도 관측 process·가벼운 장치 시작 | 기존 `Vehicle` 클래스 AST와 실제 제품 codec을 그대로 사용하고 불필요한 통합 모듈 import만 제외. 60.281초. v1 AUTO788/MANUAL291 events·v2 metrics216, 여덟 memory region·가상 명령319·양수 DRIVE0. maps_sent0·잔존 trial thread0·observer 정상 종료. actor432회 중앙39.98/p95 190.03/최대697.80ms | mapper166회 모두 최신 vehicle 부재로 거절. 시작 비용을 줄여도 병렬 부하의 현재성 조건에서 실제 지도 계산은 통과하지 못함. planner나 메모리 계측 결함이라고 단정하지 않음 |

`world-r6/r8` 종료 시 device fault가 남아 phase=DISARMED의 정상 보호 종결을 수락하지 못했다.
이 조건의 `safe_stop=null`을 성공으로 바꾸지 않는다. 출력 허가는 false였고 가상 장치만 사용했으며,
이후 소유 B 정상 stop과 임시 broker 종료로 시험 연결을 닫았다. 운영 Gateway는 별도 mock/DISARMED 상태다.

E4의60초 안정/VM상한3과 create의600/900초는 명시 시험 프로필이며 운영 파일에 공급하지 않았다.
새 recovery120초에서117초 성공은 기존110초의 부족 가능성을 보여주지만 약3초 여유뿐이고 표본도 적다.
원 요청을 연장하거나120초 상한을 더 늘리지 않았다. 관측2초/TTL3초, odom clock20ms·age200ms,
가상 feedback500ms·명령 만료100ms도 부하에서 유효한지 추가 수락이 필요하다. 보드 지연의 최대 표본을
새 운영 상한으로 복사하지 않는다. 이번 결과는 새 E4/기존 예산의 **실측 입력 확보와 보호 확인**이며,
전체 일곱 묶음의 정상 진행·운영 수치 선정 완료는 아니다.

#### 시험 정리·최종 후보 보존

소유 B1.0.9의 정상 stop은 원 operation `846000cd-cf7f-4809-a77f-926feb5422bb`로19.57초에 완료했다.
네 소유 등록 VM은 기존 Manager의 `retire → purge` 여덟 호출로 정리했고 create 원본 불변을 확인했다.
첫 retire admission 경합은 같은 key·operation `bdcaadf1-bfd1-479e-aa3c-8b6efb920e0d`로 회수했다.
새 잠금·기한 발급이나 raw 자산/원장 삭제로 통과시키지 않았다. 정리 helper의 무효 budget 옵션 거절도 원자료에
남겼으며 이는 제품 실행 실패와 구분한다. 실패 Linux pilot의 앞선 `discard-private` 종결·예약 반환도 보존한다.

임시 broker PID91183만 종료하고 listener18885/18886의 부재와 private 자격 사본6개 제거를 확인했다.
TIME_WAIT를 즉시 bind 가능 여부로 검사한 첫 fixture 오류는 실제 LISTEN 상태 검사로 정정했다.
운영 broker·원 자격은 바꾸지 않았다. 두 번째 Worker 정지도 `worker-restored2.out`에서 원 active 상태로 복구했다.
`inventory-final.out`과 최초 inventory 대조에서 host·원 세 VM의 domid/UUID/RAM/vCPU/kernel 경로·설치 모듈 hash가
동일했다. Gateway PID1504·generation1·SAFE_STOP/DISARMED·output_permitted=false, Manager·Worker·broker active,
실제 serial 장치0, 운영 creation-policy 부재도 확인했다. 새 E4 환경값은 운영 프로세스에 공급하지 않았다.

비밀 없는 보드 원자료69개만 `board-nonsecret-evidence-final.tar.gz`(176742B,
SHA256 `7514c4170233a4f1944e91feae0adaa684dadb93097ea9392d889534c723193f`)로 회수했고 private 파일은0개다.
로컬 `final-board-summary.json`, `board-final-evidence/`에 E4 성공·지도 실패·정리/복구와 source/설치 수준을
분리했다. 최종 commit/push·운영 설치·실차 출력은 수행하지 않았다.

<a id="parallel-binding-feedback-20261106"></a>
### 병렬 binding·장치 피드백의 구간 계측 후속 — 로컬 보완·실보드 미수락

앞 절의 정리는 이전 시험 후보의 결과다. 후속 공유 CPU 시험은 새 소유 A/B 등록을 사용하고 새 B만1.0.9를
실행했다. 이후 별도 승인으로 원 B의 전용 CPU2에서도1.0.9를 시험한 뒤 원1.0.8과 등록 구성을 복원했다.
A·Debian은 재기동하지 않았고 이 작업이 운영 Python 설치 파일을 교체하지 않았다. 실제 지도 계산·메모리
점유 증가/임시 반환은 아직 수락하지 않았다. 최종 보드 복원 상태는 [재개 기록](#parallel-resume-20261106)을 따른다.

| 확인한 문제 | 국소 변경과 유지한 조건 | 현재 근거 |
|---|---|---|
| read보다 앞선 poll 시각을 묶음 전체에 적용해 늦은 ACK도 통과할 수 있음 | `FeedbackSerialSink.poll`이 비차단 read 완료 시각을 그 read에서 완성한 frame의 수신 상한으로 고정. partial은 완성 read 시각 사용. 이후 decode 지연으로 시각 재발급0. 길이·CRC·원 command 참조·만료 유지 | `baseline-light-1`의 실제 동일 sequence 184쌍과 보드 처리 구간 계측으로 초기 오분류 확인. 새 PTY 회귀는 read 지연의 만료 거절·decode 지연 전 기한 내 수신 사실·현재 age 만료 null·partial 완성 기한을 각각 검사 |
| serial RX가 도착해도 owner의 주기 대기까지 기다림 | 실제 POSIX serial FD와 유한 wake pipe 두 FD를 기존 owner가 함께 기다림. STOP/명령 만료 우선, writer·thread 추가0. HUP 뒤에는 기존 유한 시간 대기로 복귀하고 종료 후 FD 반환 | 실제 PTY RX·STOP wake, 단절 뒤 spin 방지·종료·watchdog 경합 시험. 옛 lock 없는 종료를 메모리에 대입하면 EBADF 재현, 수정 뒤 통과 |
| 명령 적용 미확정이 독립 encoder 수집까지 영구 중단 | `DEVICE_APPLY_TIMEOUT`의 원 사유·DRIVE 차단·applied/physical_stop=null 유지. 동일 session의 유효 encoder만 기존 clock/품질/순서/age 검사로 제공. 이후 restart·capability·FAULT·REJECTED·link 상실은 별도 센서 차단 사유를 보존하고 HELLO/표본으로 해제하지 않음 | 실제 codec·PTY에서 만료→센서 정상 관측, 후속 오류→관측 차단, link 회복에도 차단 유지. 센서 수신을 출력 허가로 바꾸지 않음 |
| binding 관측이 같은 회차 publication에서 전체 lifecycle을 다시 검색하고 전체 검색 동안 admission 점유 | 기존 `ReadContext`의 검증 자료를 그 관측 안에서만 재사용. 전체 registry/lifecycle 전후 두 회차는 잠금 밖에서 유지하고 짧은 admission에서 대상·원 create·intent/run·host를 재검증. `validate_one`이 그 호출에서 읽은 자료를 반환해 같은 admission 안의 중복 파일 읽기 시도8개 제거. 실제 Xen 검증 시각을 반환하며 후처리 시각으로 갱신하지 않음 | 전체 검색이 admission 밖임을 실제 잠금 재획득으로 확인. 두 회차 유지·세 번째 lifecycle 검색0·문맥 변경/strict 숫자 타입 변경/새 probe 오류/host 변경 거절. 필수 gate·원 create 검증 횟수 보존 |
| 공유 Platform의 임시 response_deadline 변경/복원이 A/B 병렬 조회와 다음 회차에 간섭 | 얕은 요청 사본에만 min(호출자 원 기한, 이번 조회 기한)을 적용 | 실제 `_observe`를 끝까지 실행한 뒤 A/B 반환 순서를 교차. 기존 wrapper는 2건 실패, 수정은 원 deadline 부재/기존값 보존과 다음 회차 정상 관측 모두 통과 |
| owner가 지연되면 이전 vehicle/receipt snapshot이 현재도 유효한 것처럼 보임 | `OutputOwner.device_view`가 복사한 snapshot의 원 표본 시각과 기존 age 한도를 현재 시각으로 재검사. owner lock·새 관측·시각 갱신 없이 만료 필드만 null로 투영 | 실제 PTY owner poll을 붙잡은 회귀에서 기존 본문은 오래된 vehicle/STOP 적용값을 반환. 수정 후 만료 null, 정상 새 관측 회복 확인 |
| UTC one-shot이 요청 부재·이미 응답한 nonce에도 실행을 다시 관측하고 응답을 반복 작성 | 작은 Store 요청/응답을 먼저 확인하고 미응답만 기존 binding 관측·원 transaction·현재 run/intent/host/gate 검사로 처리. 키 순서·공백은 의미 비교하며 nonce/실행 변경은 거절 | clock 제품 경로 42 passed. 정상 idle/동일 응답은 추가 Xen read·UTC sample·write 0, 새 nonce는 원 검증과 쓰기 수행. 손상·Store 오류를 정상 부재로 숨기지 않음 |

원자료는 로컬 `2026-11-06/parallel-load/`의 `ack-segments-before.out`, `after-fix-detail-1.out` 및
시험별 `segments-parent/observer/device.json`에 연결한다. 첫 cProfile 시험은 다른 thread의 CPU 시계를 섞은
불가능한 값이 나와 CPU 분석에서 제외했다. 이후 bounded method probe의 wall/thread CPU만 사용하며,
보존 창의 통계와 전체 실행 누계를 구분한다. local 최대값을 운영 상한이나 hard realtime 보장으로 쓰지 않는다.

`baseline-light-1`과 중간 `after-fix-segmented-1`은 각각 maps_sent=0이었다. 중간 후보의 owner poll 최대는
632→336ms였지만 같은 실행 전체의 단일 원인 비교나 최종 정상 진행 수락은 아니다. 늦은 ACK 거절이 강화된
뒤에도 `DEVICE_APPLY_TIMEOUT`, binding 만료와 vehicle 부재가 남았다. 이를 planner 성공·정상 메모리 0으로
해석하지 않는다. 후속 보드 후보들도 아직 전체 정상 진행을 통과하지 못했으며 아래처럼 원인을 분리한다.

`after-fix-segmented-6`까지 병렬 시험에서 장치 RTT/연속성 오류와 binding 만료가 남았다. 실제 read 이후 시각과
현재 age를 검사하므로 늦은 자료를 최신 자료로 통과시키지 않는다. Dom0의 네 vCPU는 실제 pCPU0,3에만
허용되어 있고 소유 시험 B도 같은 pCPU를 공유한다. 원 A/B의 전용 코어 조건과 다른 명시 부하 프로필이다.
2.113초 schedstat 표본에서 owner run0.129초/실행 대기0.409초, MQTT run0.322초/대기0.558초를 관측했다.
이는 실행 대기가 존재한다는 근거이며 한 서비스가 모든 지연의 원인이라는 증거는 아니다. CPU 정책은 바꾸지 않았다.

선택 UTC timer만 유한 비교 동안 멈춘 `clock-comparison-1`은 maps_sent1이었지만
`CONTROL_PUBLISH_UNCONFIRMED`로 끝나 실제 계산·점유 반환을 수락하지 못했다. timer는 자동 복구를 확인했다.
현재 UTC 후보의 첫 비교 `candidate-clock-1`은 10개 job 중9개가 기존8초 한도에 도달했다. 완료 계수가 남은
두 회차는 registry1/Xen read0으로 실패해, 이를 Xen 조회 비용으로 집계하지 않는다.

추가로 `bytecode-cost-01`에서 운영 캐시4개는 유효했지만 시험 사본의 Manager 캐시는 최근 소스와 불일치했다.
동일 보드 Python으로 시험 사본만 checked-hash 캐시를 준비한 단일 표본에서 Manager 추가 import는
wall2.494/CPU1.648초→wall0.373/CPU0.171초였다. 이는 시험 조건의 컴파일 차이이며 제품 최적화 실적으로
세지 않는다. 이어 시험 소스103개/1,270,435바이트를 같은 방식으로 준비했다. recipe의 원래 순수 `.py` 설치와
운영 최초 import는 별도이며, 설치 recipe 누락 결함으로 단정하지 않는다. 기존 수치·시간 한도는 바꾸지 않았다.

`observe`의 개별 기한을 만든 뒤 수행하던 첫 Manager/ReadContext import는 binding 모듈 로드로 옮겼다.
새 프로세스의 binding-first/management-first/vm-first 세 순서와 가상 cold 비용을 실제 sleep 없이 주입해
관측 성공·호출자 절대 기한 만료 거부를 검사했다. 이 회귀1건과 관련59건이 통과했다. 전체 process 시작 시간과
service8초 한도는 늘리지 않았다. 유효한 캐시·이 후보를 사용한 `candidate-clock-2`도60.317초에 maps_sent0,
지도 활성 대기에서 실패했다. ODOM_CLOCK_UNCERTAIN204/CONTINUITY_GAP12/PROBE_REFERENCE3,
최종 output_permitted=false·잔존 trial thread0이며 원 UTC timer active 복구를 확인했다.
이는 실제 지도 계산·pool 점유/반환 성공이 아니다. clock job도9건 중5건이8초 제한에 도달해 정상 수락하지 않는다.

로컬 `parallel-lifetime-local-01`은 binding·장치·종료 38 passed, 후속 추가 실패 수명 시험은 7 passed다.
`parallel-gateway-regression-03`은 전체 Gateway 275 passed/5 skipped다. 외부 broker/QEMU 등 선택 환경의
skip은 실보드 수락으로 세지 않는다. UDS·PTY·Authority 제품 경로의 로컬 통과와 실제 장치 수락은 별개다.

최종 read 완료 시각 보완 뒤 `parallel-receive-stamp-01`은29 passed,
`parallel-gateway-regression-04`는277 passed/5 skipped다. `validate_one` 반환 재사용 후보는
binding·동시 조회·query consistency·registration48건과 원 생성/수습/예산 guard13건을 추가로 통과했다.
기한 내 수신한 APPLIED가 처리 중 원 command 기한을 지났다는 이유만으로 사라지는 계약은 아니다.
그 사실의 현재 유효성은 수신 이후 age·최신 명령·fault로 제한하고 owner의 명령 만료 STOP은 독립 유지한다.

현재 age 투영과 가상 차량 표본 시각 보완 뒤 `parallel-gateway-regression-05`는281 passed/5 skipped다.
가상 차량은 실제 frame 수신·각 ODOM capture 시점에 기존 물리식을 진행하며 동일 microsecond에 가짜 새 표본을
만들지 않는다. 별도 PTY 회귀2건으로 한 read의 두 probe도 검사했다. 제품 planner/odometry는 대역으로 바꾸지 않았다.

`parallel-load/package-parallel-local-summary.json`은 같은 제품 후보의 recipe 선택 Python 138파일
바이트·모드, 138구문과 AArch64 CPython3.14.7/QEMU의 20모듈 import를 확인했다. feedback/output_owner/
odometry를 포함하며 source drift=0이다. 별도 ARM 익명 FD 시험에서 pipe2의 NONBLOCK/CLOEXEC와
select/poll·read/write/close를 확인했다. 기존 포장 검증기가 빠뜨렸던 gateway world 4파일·예제 3파일은
새 증거 스냅샷에 포함했다. 제품 recipe 변경이나 새 의존성 설치는 없으며 BitBake QA/RPM/이미지 수락은 아니다.

중간 포장 이후 네 제품 파일의 최종 후보는 `package-parallel-final-summary.json`에 다시 고정했다.
ARM138파일/20모듈·FD smoke·source drift0을 재확인했다. source manifest SHA256은
`847ff0193ab56d1e748e2dc4e6272676b9b7d597c69984ea3d2d003d36a8e897`이다.

그 뒤 UTC 의미 비교까지 포함한 `package-clock-semantic-summary.json`은138파일/21모듈 ARM import·FD smoke·
source drift0을 확인했다. manifest `4997d8762de9201f9d549476315084109626caaf65ab3a6675c40937812a46c1`,
tar `2e29a49ce187e197abf5c7d204fb208e81367047bf5466329e015ebde2741983`이다. 각 포장은 해당 시점의 후보이며
이후 수정은 다시 대조해야 한다. 정규 RPM·이미지 또는 운영 설치 완료를 뜻하지 않는다.

첫 import 경계까지 포함한 현재 포장은 `package-clock-eager-summary.json`의138파일/21모듈·ARM FD smoke·
source drift0이다. manifest `41c1712122df091a99aa7915cf1b14471055e19f4ae8621e5b4a55711f6027bb`,
tar `edb378bd1d8cdd7aae044e84ab7179af91d3f76ffc8d8eccb767bb2a2cf94c2f`다.

`resource-core-20261106-144611/summary.json`에는 별도의 현재 C 제품 로컬 회귀를 연결한다.
기존 pool 빌드 입력35개 raw bytes가 현재와 동일하며 native/ARM의 실제 pool wire에서
used0→80472→0, peak80472·exhaustion1 유지와 busy/null을 확인했다. ARM MMU 페이지는
실제8192바이트 감소/반환이며 내부 pool 반환과 다른 계수다. worker는 과거 입력과 `autonomous.c/.h`가 달라
현재 소스로 native/ARM을 부분 재빌드했다. native independent/expired/coalesce/memory-manual은 통과했고,
ARM memory-manual은 최초 fixture300ms 회수 실패1회 뒤 같은 ELF·같은 기한의 단독2회가 통과했다.
최초 실패를 지우거나 무조건 ARM 수락으로 합치지 않는다. 현재 health C의 ASan/UBSan 및 실제 planner/pool
pytest6건도 통과했다. 이는 실보드 MQTT 지도 계산·점유 증가/반환의 대체 근거가 아니다.

자원 시나리오 B1/B2/BO1·C1/CO1/CO3은 먼저 같은 입력의 정상 지도 계산·binding·feedback을 확보한 뒤
CPU 지연·필수 pool 부족·AUTO/MANUAL 독립성·합류를 짝지어 검사한다. CPU 사용률이나 선택 진단/후보 pool
포화만으로 VM 실패를 만들지 않는다. 추가 부하·VM·firmware hook이 필요한 범위는 대상·상한·복원 절차를
구체화해 승인받으며, 이번 소스 확인만으로 해당 실보드 시험을 완료로 표시하지 않는다.

검토 보완용의 단계0~5 요청은 위 작업에 통합했으며 중복 실보드 실행을 새로 만들지 않았다.

| 단계 | 현재 직접 확인한 범위 | 남은 실보드 수락 |
|---|---|---|
| 0 정상 기준선 | 실제 제품 앱·Gateway/Authority·격리 MQTT·PTY로 시험. 현재 candidate-clock-2까지 지도 발행 이전 실패 | 같은 firmware의 Active/Standby 준비·정상 계산과 메모리 점유 증가/반환 |
| 1 CPU 경고 대조 | 실제 health C의 합성 CPU100%/heap0 경고 분류 통과 | 소유 Debian 실제 연산 부하 중 정상 deadline·불필요한 승계0 |
| 2 실제 deadline 초과 | 실제 C planner bounded deadline/cancel과 native worker expired/coalesce 경계 통과 | 실제 CPU 압박→원 deadline 위반→준비 제한→준비된 B의 새 계산·출력 |
| 3 선택 pool 부족 | 실제 native/ARM pool 고갈·반환·peak/실패 누계, busy/null. native AUTO/MANUAL 독립 경계 통과 | 진단/새 지도 후보 부족이 필수 주행 실패로 승격되지 않는 실제 경로 |
| 4 필수 자원 실패 | 실제 pool 확보 실패와 별도 실제 health 분류기 입력 -ENOMEM 제한은 각각 로컬 통과 | 해당 planner 필수 pool 실패부터 공개 준비 보고·Gateway 승계/정지까지의 연결. 둘을 한 근거로 합치지 않음 |
| 5 회복·대칭 | 실제 C planner 실패 후 새 지도→정상 계산/추종과 pool 반환 로컬 통과 | 부하 해제 후 재준비·Standby 재합류·A/B 대칭, 회복만으로 Active 탈취0 |

현재 단계1~5를 위한 추가 실보드 자원 압박은 실행하지 않았다. SIGKILL·CPU% 상승·진단 결측을
실제 필수 계산 실패나 자원압박 승계의 대체 수락으로 쓰지 않는다. 현재 시험용 Worker 정지와 소유 자산은
후속 정상 기준선 시험 및 정리/원 active 복구 대상이며, 앞 절의 이전 후보 정리 완료와 구분한다.

후속 읽기 전용 관측 중 운영 API/Gateway/display 프로세스가 재시작되고 설치 Gateway 소스 hash가
변경된 것을 확인했다. 이 작업은 해당 운영 파일을 교체하지 않았다. 원 세 VM의 domid/UUID는 유지됐으며,
병행 작업 여부 확인 전까지 새 실보드 실행을 보류하고 로컬 검토를 계속한다. 변경 전후를 같은 설치 후보의
성능 비교로 합치지 않는다.

<a id="parallel-resume-20261106"></a>
### 17:55 보드 복원·중단과 다음 재개

사용자의20분 뒤 중단 요청에 따라 시험을 끝내고 **2026-11-06 17:55:29 KST에 이 작업의 SSH master를
종료**했다. 이후 작업은 로컬 기록·시험·포장만 수행한다. 아래 항목은 전체 자원 시나리오 완료가 아니라
안전한 시험 종료와 재개 지점이다. commit/push·실제 차량 출력은 수행하지 않았다.

#### 이번 추가 보완과 제공 수준

| 항목 | 무엇을 어떤 기준으로 바꿨는가 | 검증과 남은 범위 |
|---|---|---|
| 성공 binding의 재조회 시점 | 이전에는 조회 완료 뒤1초를 더 기다렸다. 이제 성공 `observed`만 실제 dispatch+기존1초를 기준으로 다음 조회를 예약하고 늦어진 주기는 한 번으로 합친다. 실패/비관측은 기존 완료+1초 backoff, VM별 future1개·원 관측시각·TTL·원 예산 유지 | 최종 live `test_drive_rejoin.py`22 passed, fixture cadence2개·메모리 판정10개 통과. **새 cadence는 보드 미수행** |
| 메모리 반환 수락 | idle snapshot을 저장한 뒤 무조건 성공하던 시험 판정을 제거했다. 정상 baseline·새 active·만료 뒤 새 표본과 status=ok를 요구한다. 지도/경로의 임시 슬롯이 실제 줄거나 이번 실행에서 높아진 high-water 뒤 반환된 근거가 있어야 통과 | 제품 `world.c/engine.c`는 current 지도/경로1슬롯과 마지막 search slice를 유지한다. 모든 값0·heap/page 반환을 요구하거나 주장하지 않는다. 이전 peak만으로 이번 반환 성공을 만들지 않음 |
| 추가 원인 분리 | 운영 mock Gateway를 최대7분 내 유한하게 정지·자동 복구하는 비교를 승인받았다. 소유 공유 B와 원 전용 B를 구분했다 | 한도 변경0·운영 CPU 정책 변경0. 아래 정상 지도 기준선은 여전히 미통과 |

최종 로컬 rejoin SHA256은 `e078dfcf15f0b561a5fb2ed054c764e546295fd3467ff0dbbc7c7433e9e298d8`이다.
`binding-cadence-full-final-validation.json`과 `e4-budget/binding-release-cadence-full-final.log`에 실제 명령과
22 passed/기존 Starlette 경고1개를 보존했다. 최초 WSL 시험 사본 덮어쓰기는 자동 검토가 거부했고,
사본을 바꾸지 않는 live PYTHONPATH·`-B`·`-p no:cacheprovider`로 검증했다.
새 `package-cadence-local-summary.json`은 ARM Python3.14.7의138파일 byte/mode·compile,
21모듈 import·FD smoke·source drift0을 확인한다. manifest `dfe0d1c1178af183c82e3694893377042fc9cc1a4674a8e7bed597d44ab9e1be`,
tar `70bafb2eba63dd51c54ebf23345ca320504133b38ee5df93ac6dad44fde88f73`다. 정규 RPM·이미지·보드 설치 수락은 아니다.

#### 마지막 보드 시험과 확인한 한계

| 시험 | 결과 | 판정 |
|---|---|---|
| `candidate-clock-single-gateway-1` | 메모리 판정용 제품 헤더를 임시 source에 준비하지 않아 시작 전 실패 | fixture 준비 오류. Gateway와 UTC timer active 자동 복구 확인. 헤더2개를 준비하고 실행 전 검사 추가 |
| `candidate-clock-single-gateway-2` |40.448초, maps_sent0, ODOM_CLOCK_UNCERTAIN113/gap6/reference1, 나중에 fixture `systemctl is-active`의2초 호출도 만료 | 중복 Gateway 제거만으로 정상 진행을 수락하지 못함. 최종 원인은 하나로 확정하지 않음 |
| `exclusive-b-baseline-1` | 원 B의 기존 전용 CPU2·16MiB/1vCPU·1.0.9,55.293초, maps_sent0. ODOM_CLOCK_UNCERTAIN235/gap12/reference4, DEVICE_APPLY_TIMEOUT. 남은 trial thread0 | 실제 지도/계산/메모리 증가·반환 미수락. observer는 종료 정리 중 강제 회수돼 최종 result가 없으므로 binding 오류0으로 해석하지 않음 |

원 B의 시험 start는 물리 실행17·CPU 검증 complete 뒤 `read observation budget expired`로 미확정이었다.
원 key의 operation `2f411f07-cca9-47b6-ade4-cbebcdbc2fd9`를 재호출해 같은 execution
`5080d1db-1b6b-48a3-881e-292a603a9475`로 완료 회수했다. 추가 launch·원 기한 재발급0이며,
별도의 기존 ProductionBootstrap 경로로 drive/telemetry receipt applied를 확인했다. 실패 당시 어느 내부
관측이 먼저 예산을 소진했는지는 추가 계측 대상이다. 정상 부팅과 앱 준비·현재 출력은 구분한다.

장치 시각은 현재 매 probe의 `ceil(RTT/2)`를 검사하므로20ms 한도에서 RTT40ms 초과를 거절한다.
세션·순서·품질·age200ms와 gap 뒤 새 odom anchor 조건을 유지했다. 과거 offset을 그대로 적용할
device drift/no-step 보증이 없어 임의의 오차 축약은 구현하지 않았다. 가상 장치의 고정+7초를 실장치
시계 보증으로 확대하지 않는다.

로컬 원자료 대조에서 전용 B의 vehicle 수신4건 중 한 건은 수신 후 원 표본 age144,725µs로 유효했으나,
mapper가 처음 소비한 시점에는282,455µs였다. 추가137,730µs 동안 신선도 창을 잃었다. 해당 CRC
`476417682`의 offer/publish/receive와 source age 순서를 대조했다. 기존100ms mapper polling과 GIL/CPU
대기를 각각 얼마로 나눌지는 이 계측만으로 확정하지 않는다. 따라서 모든 표본이 처음부터 stale이었다거나
planner가 계산에 실패했다고 단정하지 않는다.

보드 종료 뒤 fixture만 Event1개로 깨우도록 보완했다. 최신 표본을 합치고 기존 최대10Hz를 유지하며,
동일 host/device/Pi session·odom epoch 안에서만 sequence 증가를 비교한다. 원 표본 시각·200ms 한도와
메모리 판정·새 binding cadence는 보존했다. encode 중 만료·입력 취소도 발행 전에 다시 검사한다.
`test_board_mapper_wake.py`10건과 기존 메모리 판정10건, 전체 fixture 구문 검사를 통과했다.
최종 fixture SHA256 `44e94c5770e6570a376ea3c8da3e883344cce4ca7b253869180e8b81170df2d5`,
`mapper-wake-validation.json` SHA256 `eb48103e12bff64ad9b4b7a4f28d7c7d740a70553049fc94ac3f293c12bae444`다.
기존 원자료·수정 전 소스는 그대로 보존했다. **이 fixture도 보드 미수행**이며 실제 MQTT 전달·planner 점유를
새로 수락한 것은 아니다. 주행 중 odom epoch reset 뒤 고정 위치 offset은 실제 mapper 재localization 근거가 아니다.

#### 복원·정리 결과

- 원 B는 보존된1.0.8 manifest `45e646878094c15cca0ae269923eb2f712ca3e2efdf7339b5ee0636f4a159749`로
  복원했다. 등록 객체가 시험 전 객체와 동일함을 검사하고 원 통신 설정으로 start 완료했다.
  operation `a98bc691-afc9-46a5-9683-e0838a025939`, 새 execution `2e0f0957-d25b-437b-a6cd-a87374cfaf86`,
  domid18·CPU 검증 complete·실제 hard affinity2다. 복원 첫 CLI 종료3의 사유 출력은 fixture가 보존하지 못했고,
  당시 실제 부재·1.0.9 등록 유지 확인 뒤 지원 CLI 재시도로 복원했다. 원장/lock 삭제나 등록 raw 덮어쓰기는 하지 않았다.
- Debian은 domid1, 원 A는 domid2·1.0.8·affinity1을 유지했다. dom0·Debian의 hard mask0,3과
  RAM/vCPU·영구 UUID/비밀번호·운영 CPU 정책은 바꾸지 않았다. 원 B의 새 boot/execution은 승인된 재기동의 결과다.
- 소유 A `de33263f-8b35-492b-a83a-29be898b2767`와 B `ff2b3080-6ed8-4052-9209-c6823836e64a`는
  정지 후 기존 retire/purge 네 호출로 정리했다. 각 원 create journal의 불변도 확인했다.
- 격리 broker PID132605만 종료하고18885/18886 listener 부재·정확히 지정한 private 비밀 사본8개 제거를
  확인했다. 운영 broker·원 자격은 유지했다. 정상 firmware revision 이력과 필수 operation 기록은 삭제하지 않았다.
- 최종 Manager·Gateway·Worker·운영 broker·collector·UTC timer active,
  Gateway generation1/SAFE_STOP/DISARMED/output_permitted=false/storage_uncertain=false,
  실제 serial 장치0·등록 VM3개·임시 비밀 사본0을 확인했다. active는 기능/차량 종단 수락이 아니다.

`parallel-load/parallel-board-evidence-final.tar.gz`는 비밀 없는263파일/3,536,726바이트,
SHA256 `bd649d99f76bd7e8d9dc06e020f7449d8e6c42140a9b02e9d6c8aba051ed03b9`다.
private 디렉터리 파일은0개이며 `board-evidence-final/`과 `final-parallel-summary.json`에 로컬로 보존했다.
실제 보드 최종 상태는 `evidence/final-board-state.json`, 복원은 `original-b-restoration.json`,
정리는 `current-trial-retirement.json`·`current-broker-cleaned.json`·`current-worker-restored.json`을 따른다.

#### 집에서 이어갈 순서와 미완료

1. **현재 후보부터 재확인:** host boot·현재 VM UUID/execution·설치 모듈 hash·서비스·출력 차단을 읽기 전용으로
   확인한다. 오늘 helper의 고정 host/execution/key를 새 실행에 맞춰 무조건 바꿔 재실행하지 않는다.
   현재 원 B는1.0.8이며 오늘 시험용 private 자격·listener는 이미 정리됐다. 새 시험 범위를 준비해 다시 공급한다.
2. **전달 지연 정상화:** 새 성공 probe cadence와 fixture의 fresh vehicle 전달/원 odom epoch 처리를 같은 후보로
   검증한다.20ms clock error·200ms age·binding TTL·원 execution/intent·정리 보호는 유지한다.
   복수 프로세스·probe·MQTT callback·mapper 소비의 시각을 CRC로 연결하고 CPU 실행/대기를 별도로 본다.
3. **지도·메모리 기준선:** 먼저 출력 없는 DISARMED에서 실제 제품 planner/tracker 계산과 새 map/path/search
   점유를 확인한다. 지도/경로 임시 슬롯의 실제 반환과 retained current/search checkpoint를 구분한다.
   이후 이동·우회·복귀와 stateful 연속성은 별도 수락하며 가상 장치 결과를 실차 완료로 확대하지 않는다.
4. **자원 단계0~5:** 정상 Active/Standby 기준선 뒤 소유 CPU 실제 연산·선택 pool 부족·필수 pool 확보 실패·
   부하 해제/재합류·A/B 대칭을 연결한다. 아직 추가 자원 압박/승계 실보드 시험을 시작하지 않았다.
   선택 점유 hook·새 대상이 승인 범위를 넘으면 후보·영향·상한·원복을 정해 그 범위만 확인한다.
5. **사전 여유 경고:** 정상 유휴/정상 고부하/증가/실제 실패/회복의 원 표본과 필수 작업 결과를 짝지어
   CPU·영역별 메모리의 진입·지속·해제·정상 유지시간을 정한다. 현재 운영값은 미확정이다.
   [건강 계약의 현재 지원 표](../contracts/health-evidence-v1.md#자원별-사전-여유-경고의-현재-범위)와
   [BO1/CO1/CO3](../platform/recovery-scenarios.md#2-정상-운영-중-상시-감시)를 재사용한다.
   같은 원 표본 재평가·CPU100%·free0·과거 peak/고갈 누계만으로 승계/재시작하지 않는다.

로컬 재개 진입점은 `C:/Users/SSAFY/Documents/Codex/2026-11-06/parallel-load/resume-20261106.md`다.
이 기록은 기억/인계용이며 새 정책·새 실행 허가·완료 증거를 대신하지 않는다.


<a id="clock-retry-submit-20261106"></a>
## 2026-11-06 재부팅 후 제출 경계 보완 — 실보드 통합 검증 진행 중

현재 후보는 `feat/platform/integration-followup`, HEAD `5d05101efd39c457cb78a05b473952bc38e7a317`와 미커밋 변경이다. 이전 E4·동시성·clock 보완과 실패 원문을 보존했다. **정상 지도 계산→점유 증가→임시 슬롯 반환은 아직 미수락이며, 그 선행조건을 요구한 세 부하·승계 조건도 미수행이다.** 아래 로컬 통과를 실보드 종단 완료로 확대하지 않는다.

사용자 재부팅 뒤 host는 `754c8e16-131d-4929-a0cf-bda4134e318b`다. A는 `de113a23-d80e-4333-94b8-788babe0e41e`, Debian은 `17cad3c4-b3cd-4b29-8740-e679d0eaebd4`, 시험 B는 `5002a22d-63d6-4f7a-9106-193b14499898`다. 부팅 전후 monotonic·execution·key를 합치지 않았다. 이전 R10의 원 start `5b0334ce-1dbe-4ec7-bc70-2e09be21e6f2`와 stop `91894061-7ad2-48b0-ad49-7c697044b237`는 완료였지만 `/run`의 실제 진단 결과는 회수하지 못했다. 이를 성공 시험으로 소급하지 않았다. 새 boot에서는 B가 1.0.11 등록·stopped였고, 새 원 key와 새 시험 문맥으로 재개했다.

### 실제 시험과 확인 범위

| 시험 | 확인한 결과 | 해석·복구 |
|---|---|---|
| R11 진단형 | B start 완료. 콘솔 준비 파일이 부모의 20초 경계 직후 생겨 `CONSOLE_NOT_READY`. 지도 driver 미기동·최종 raw 0 | 제품 한도를 바꾸지 않고 fixture 기동 대기만 45초로 분리했다. 원 B 1.0.8·Worker 복구 완료 |
| R12 진단형 1.0.11 | 52.821초·지도 local queue 4, map/search/path 각 8개 유효 표본의 used/peak 0. odom 링은 864B | clock 전체 raw 16(phase5 3/폭 거절 13), guard 확정 subset 7(2/5)을 구분. MAP raw 4, 확정 2 모두 clock gate EAGAIN. 원 B 1.0.8·domid6·execution `3c10daa8-37fc-49d2-abe7-fd63b5e3a0d2`, Worker·mock Gateway 복구 |
| R13 기본형 1.0.10·콘솔 없음 | 45.074초·지도 queue 8, 필수 세 풀 각 6개 표본 used/peak 0. odom 링 432B. ODOM_CLOCK_UNCERTAIN 267/CONTINUITY_GAP 49 | 진단 콘솔만의 문제로 볼 수 없다. 원 B 1.0.8·domid8·execution `e1f5f838-0735-48a9-a52c-d2f4a78d361a`, Worker·mock Gateway 복구 |
| R14 기본형·구간 계측 | 45.019초·지도 queue 0, ODOM_CLOCK_UNCERTAIN 272/CONTINUITY_GAP 34. 원인 직전 odom 기록은 cleanup 전에 보관을 멈춰 정리 지연과 분리 | 원 B 1.0.8·domid10·execution `53570d0a-f5fc-48e5-b16e-daf41eb307ff`, Worker·mock Gateway 복구. 새 제품 보완의 변경 전 근거 |
| R15 | 같은 기본형 1.0.10에 검증한 세 Python 모듈 적용. 45.370초·지도 queue 3, 필수 세 풀 점유 미수락 | 첫 제출 RTT로 분리해도 수신 지연이 남았다. 원 B 1.0.8·domid12·Worker·mock Gateway 복구 |
| R16 | 시험 parent에만 Python switch interval 1ms를 적용한 원인 분리. 45.570초·지도 queue 1, 기준선 실패 | 운영 Python 설정 변경 없음. 이것만으로 GIL을 단독 원인으로 확정하지 않았다. 원 B 1.0.8·domid14·Worker·mock Gateway 복구 |
| R17 | 기본 interval/affinity, 제한된 원 command 내용 계측. 47.403초·지도 queue 4, 기준선 실패 | 현재 B 문맥의 833개 모두 v=0/steer=0, READY·이동 성공 아님. 원 B 1.0.8·domid16·Worker·mock Gateway 복구 |
| R18 | 시험 parent와 그 thread만 Linux vCPU 한 개에 제한한 원인 분리. 46.601초·지도 queue 8, 기준선 실패 | Xen pCPU·운영 CPU 정책 변경 없음. 원 B 1.0.8·domid18·Worker·mock Gateway 복구. as-run의 affinity 미변경 주석은 실제 동작과 달라 이 행으로 정정 |
| R19 | 1.0.12의 제한 exact-zero 시도 gate와 같은 Python 보완. 45.322초·지도 queue 6, 기준선 실패 | 현재 B 문맥 command 258개 모두 0. 반복 감소가 전체 지연 해결은 아님. 원 B 1.0.8·domid20·execution `7e77e8b8-355f-41d8-93a1-237c55ebba1c`, Worker·mock Gateway 복구 |
| R20 | 같은 1.0.12에 native owner 단회 read 적용. 43.763초·지도 queue 1, 지도/탐색/경로 peak 0으로 기준선 실패. odom 링 peak 9,936B는 별도 실제 점유 | 원 B 1.0.8·domid22·execution `fc3daeed-db3d-4492-b4b4-19522138b197`·CPU 확인 complete. 복원 start의 첫 admission 경합은 미수락이었고 같은 key 재호출로 완료. Worker·mock Gateway 복구 |

지도 queue 수락은 broker 전달·guest 수락·계산 성공이 아니다. R12의 raw `dec-g3`는 190~331µs였고, Pi 수정 후 `pi_send→publish 진입`은 중앙 0.150ms였다. C callback 이후 시각 오차와 Pi의 알려진 직렬화·mailbox 대기를 줄인 수정은 실제 동작했다. 그러나 raw만 있는 문맥 미확정 marker를 guard 확정 표본으로 승격하지 않는다. `r12-clock-map-analysis.json`은 prefix와 µs clock의 원점 차이, 원 lease와 로그 시점의 한계까지 구분한다.

### 확인한 비용과 국소 수정

1. `feedback.py`/`odometry.py`: 원 probe 생성 시각부터 serial 제출 전 packet 준비·대기를 RTT에 넣고 있었다. R14의 보관한 대응 표본에서 echo→OS write 진입은 중앙 3.477ms/p95 55.352ms였다. 원 echo는 그대로 두고 완성 frame의 **첫 제출 직전 시각**을 기존 최대 2개 pending 값에 한 번만 저장한다. RTT·Pi 중점은 첫 제출→실제 read 완료로 계산한다. 원 echo의 보존 기한도 mark/receive 양쪽에서 유지한다. 중복 mark·partial/EIO로 시각을 갱신하지 않는다. `ODOM_PROBE_EXPIRED`와 내부 `ODOM_PROBE_SUBMISSION`을 기존 실제 왕복의 `ODOM_CLOCK_UNCERTAIN`과 구분한다. prepared probe당 clock 읽기 1회와 최대 2개 작은 timestamp가 추가된다. ARM 실제 RAM 최고값 측정은 아니다.
2. `drive_link.py`: R14의 선택 publish 8회는 원 probe와 유일하게 대응했다. publish 누적 wall/CPU는 227.039/29.430ms, queue→write 진입은 175.819/12.373ms였다. 중첩 비용은 합산하지 않는다. 고정 Paho 2.1.0의 [`_packet_queue`](https://github.com/eclipse-paho/paho.mqtt.python/blob/v2.1.0/src/paho/mqtt/client.py)는 같은 owner의 `loop()`에서도 socketpair wake를 한다. [공인 외부 event loop](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html#external-event-loop-support)의 socket/select·read/write/misc를 같은 owner에서 사용해 불필요한 자기 wake를 없앴다. 기존 2ms 대기는 유지한다. Paho `max_queued_messages=2`만으로는 QoS0의 정체 queue가 제한되지 않는 것을 실제 고정 Paho의 64 packet 재현으로 확인했다. 이제 `want_write()`가 남으면 새 latest 슬롯을 꺼내지 않아 기존 packet부터 처리한다. 정체 200회 뒤 정상 입력 회복도 로컬에서 검증했다.
3. `controller.c/h`·`main.c`: WARM+LATTICE에서 같은 제한 사유의 exact-zero만 기존 lifecycle check당 한 번 시도한다. 첫 제한 STOP·제어/연결/앱 문맥 변경은 즉시 시도하고 정상 neutral·movement는 유지한다. 이는 수락/송신 ACK가 아닌 시도 이력이다. mailbox 거절·이후 TX 만료도 다음 check에서 재시도하며 새 타이머·기한 연장은 없다. 계산·progress·health·heartbeat·lifecycle의 기존 판정은 바꾸지 않았다. 앱별 metadata 합계 304B를 추가했다. 원 CTest ASan/UBSan 6개와 실제 main/JSON encoder 집중 시험 3개가 통과했고, 같은 60회 완료 fixture에서 제한 시도 40→10·정상 neutral 10→10·movement 10→10을 확인했다. fixture의 RTOS/작업/수송 대역은 실제 planner·실보드 성능 근거가 아니다.

R14 장치 write 반환→Pi read 완료는 보관한 64개 표본에서 중앙 36.915ms/p95 147.161ms였다. syscall 뒤 deschedule로 음수 cross-process 구간도 있어 이를 순수 장치·커널·GIL 지연으로 단정하지 않는다. 두 국소 수정만으로 이 수신 지연까지 해결됐다고 주장하지 않는다. R13 task CPU의 36.089642714초는 tail/cleanup을 포함한 **수집 wall 창**이다. task와 process 합계는 겹치며 Linux CPU 값은 Xen pCPU 보장이나 GIL 대기 측정이 아니다.

R19는 같은 pi/device session·echo의 수신 65개를 대응했고 source/sample·제품 수신 stamp가 일치했다. 첫 제출 RTT 중앙 66.887ms/p95 178.144ms, 장치 write 반환→Pi read 완료 중앙 34.817ms/p95 114.205ms다. 보존한 고유 Pi read 56회의 wall 중앙 16.734ms/CPU 중앙 0.512ms와 read 완료 뒤 제품 stamp 중앙 약 0.1ms를 구분했다. 나머지 보존 누락을 실패/0으로 채우지 않는다. `r19-timing-analysis.json`은 raw 구간·분모·원 실행·source hash와 계산을 보존한다. 이 경과 시간만으로 kernel/GIL/scheduler 비중을 확정하거나 clock 한도를 올리지 않았다. 기존 owner readiness 뒤 pyserial 내부의 추가 select/read 경계는 후속 R20 후보로 국소 보완했다. 실제 POSIX idle read와 HUP 모두 빈 bytes가 될 수 있어 readiness 근거 없는 raw-read 치환은 하지 않았다.

R20의 같은 세션 수신 64개·고유 nonempty read 62개를 대조했다. 단회 read의 wall 중앙은 16.734→3.947ms, CPU 중앙은 0.512→0.200ms로 줄었으나 wall p95는 38.682→44.313ms로 tail 개선은 확인하지 못했다. 첫 제출 RTT 중앙 64.449ms/p95 149.614ms, 장치 write 반환→Pi read 완료 p95 128.308ms가 남았다. source/read identity는 새 helper 경계와 모두 일치한다. 직접 pyserial read와 새 owned read는 각각의 실제 호출 경계로 측정했으며, 중첩 비용은 합산하지 않는다. 활성 phase의 완전한 24개 CPU 창은 24.157초다. owner TID의 start/context switch는 대응했지만 kernel scheduler 설정이 꺼져 run-delay 차분은 unknown이다. 이를 지연 0이나 GIL/커널 단독 원인으로 보고하지 않는다. 자세한 분모·보관 누락·원시 구간은 `r20-timing-analysis.json`에 있다.

### 같은 후보의 로컬·ARM 근거

R15~R19에 사용한 Python source SHA256:

- `drive_link.py`: `afc83178c00583437a5778353a412cfe7d16146eedcc5206afce28ef4969e265`
- `feedback.py`: `acc5591100ea2ad43393a4505e08984e8e3c1758fc96d2a0df7ea220049798fd`
- `odometry.py`: `cc38c4ca94eda13755a23cb484ea256737935cb5501936db76e793e1b08d6f3b`

집중 시험은 MQTT 39개, odom/feedback 53개다. 이를 합산해 전체 개수라고 하지 않는다. 같은 최종 hash로 `python3 -B -m pytest tests -ra` 전체 Gateway **337 passed/1 skipped(76.03s)**, 전후 source hash 동일을 확인했다. skip은 기존 native state codec 실행 파일 미공급이다. WSL host Python 3.12/Paho와 기존 ARM Mosquitto 2.1.2/QEMU fixture 결과이며 ARM Gateway 네트워크 실행 완료가 아니다.

기존 recipe 선택·설치 경로대로 새 격리 root에 42개 Python 파일/383,785B를 0644로 설치했다. payload SHA `6fb23ca6445f024290beb026d9a20c0c7e2ea42fb7a0664631ebd0045f65b86c`. 실제 ARM Python 3.14.7/QEMU·고정 protobuf/upb에서 hash·mode·import/compile·첫 제출·중복 mark 불변·원 probe 만료·Paho API 검사를 통과했다. audit hook으로 connect/bind/process/file-write를 막았다. 정규 RPM/전체 이미지/운영 설치 완료가 아니다. 기존 package 분할과 의존성을 재사용했고 새 runtime 의존성은 추가하지 않았다.

후속 native owner 읽기 후보는 `feedback.py` SHA `18f79e69323f8703299534616c82eecd06865f87b584a25a8daa41e340a37e5f`, `output_owner.py` SHA `5e908fe4f9e1cab78070666128df6fd130122bc4df9a91170b09272b87f5ba2a`다. 같은 fixed pyserial 3.5의 실제 PTY로 idle/HUP의 빈 read 반례를 확인한 뒤, 기존 select의 readable 근거를 owner poll 한 회에만 전달한다. 첫 STOP·만료·종료가 우선하며 취소는 기존 owner wake다. 직접 public poll의 pyserial 취소와 실제 socket URL·기존 no-fd fallback은 유지한다. 원 `OSError`·첫 실패·partial/joined/oversize frame·read 완료 stamp·odom TTL을 포함한 집중 59개, 전체 Gateway **349 passed/1 skipped(76.37s)**를 통과했다. 기존 native state codec 미공급 skip과 외부 대역은 앞 시험과 같다. 추가 state는 기존 fd 참조·cached hook·소비하는 bool이며 새 lock/thread/select/retry loop는 없다.

새 42개 모듈/386,105B의 ARM 설치 payload SHA는 `ea7627b4bbce316b9964c7b0883f0e4586bab097f3864071967b2259d16a78b9`다. 실제 ARM Python 3.14.7/QEMU의 import/compile·기존 codec 검사는 통과했다. 반면 QEMU에서 만든 PTY의 `ttyname`, host가 공급한 PTY의 `tcgetattr`는 각각 ioctl `ENOTTY`로 막혀 ARM PTY 실행 수락으로 세지 않았다. 이를 우회하려고 제품 termios 검증을 끄지 않았다. WSL host PTY 성공·ARM import 성공·실보드 성능은 별도다. 상세 원문은 `owned-read-local-validation.json`, `gateway-owned-read-full-tests.json`, `gateway-owned-read-arm-install.json`, `gateway-owned-read-arm-pty.*`, `gateway-owned-read-arm-hostpty.*`다. ARM source manifest의 supporting-evidence 항목 중 `feedback-owned-read-local-validation.json` 표기는 실제 `owned-read-local-validation.json`의 오기이며 runtime payload/hash에는 영향이 없다. 원 실행 기록을 보존하고 여기서 대응을 바로잡았다.

C callback 수신 시각 보완은 별도 47개 로컬 회귀 및 실제 Zephyr ARM 빌드 두 후보로 확인했다. base 1.0.10은 manifest `9c1691a75ed544263e4edb82fa4dc44e2e80394f9509585fa267e99ba378313f`/bin `66a5dad8d6c5ab315d06cebd219b904c6a294544ffce4d197103c71f9faa3275`, 선택 진단형 1.0.11은 manifest `4bbd601b637cedef14134f4de688a213cdc7fb4e54311c8715500202bf6d2c13`/bin `c819de07fbc87aad8dbed7de0ea9eec32cff25971e78bda3e33523b6b195c2c9`다. 모두 16MiB/1vCPU, 보드 B의 기존 CPU2·UUID·비밀번호를 유지했다. 새 catalog·초기 설치 default나 운영 정책값으로 승격하지 않았다. build 뒤 문서만 보강한 이력은 runtime source hash와 구분한다.

제한 명령 gate의 고정 Zephyr ARM 빌드는 39.890초에 통과했다. 1.0.12 manifest `c6960f1df498d30054ccfe6cde240bd60da310f006b0d24e018ae152b209ddab`, bin `c7bfaff8cd55c7c20b9ed676aa46495b964980506768ad7f7ee73c04ccc272e8`, bundle archive SHA `f88f27493b943b277f9a88c22987fd60b3ee581d4392b05107884b5e3e47d1a0`다. ELF 심볼의 gate BSS는 304B다. layout padding 때문에 전체 bss 순증이 0인 것을 RAM 비용 0으로 보고하지 않는다. 1.0.12의 R19 기동은 확인했으나 기준선이 미수락이므로 B는 1.0.8로 복원했다.

### 원자료·담당 연결과 남은 수락

로컬 원자료는 `C:/Users/SSAFY/Documents/Codex/2026-11-06/board-resume-evening/`에 있고, 사용자 요청 모음은 `C:/Users/SSAFY/Documents/xenova-test-docs/`다. 기존 61개 모음과 실패 원본은 유지한다. 상세 명령·설정·source manifest·표본·한계는 `resume-evening-working.md`, `gateway-submit-full-tests.json`, `gateway-submit-arm-install.json`, `drive-link-external-loop-validation.json`, `odometry-submit-boundary-local-validation.json`에 연결한다. 종료 R19 archive는 비밀 없는 176파일/9,786,859B, 압축 1,173,127B, 실제 비밀번호 값 누출 검사 0건, SHA `6e9beef859320729ca629329dbd4e64741756fa400a131405353d352a6ab1d3d`다. PC 회수 뒤 각 path·크기·hash를 전부 대조했다. raw journal·자격 원본은 보고서로 복제하지 않는다.

ESP32 요청서와 정식 feedback-2 계약에는 probe 수신 뒤 실제 센서 표본 생성/래치 조건을 현재 본문에 명시했다. 가상 Vehicle은 그 순서로 계산하지만 실제 MCU·차량 수락은 별도다. 원 echo wire를 그대로 회신하며 새 field·framing을 요구하지 않는다. R15 이후 기존 echo 전체 구간과 실제 첫 제출 기준 RTT를 별도 계측했다. 보존한 원 시각·lease·신원·출력 권한·E4·오류 원인은 축소하지 않았다.

정상 baseline이 통과하기 전에는 정상 고부하/A만 지연/양쪽 입력 한계의 승계 기준이나 운영 숫자를 확정하지 않는다. 실제 차량 출력·원 A 조작·보드 재부팅·운영 설치본 교체·commit/push는 이 재개 시험에서 수행하지 않았다. 사용자가 수행한 보드 재부팅과 그 뒤 새 문맥은 별도 경계다.

R20 종료 후 소유 broker PID7656의 실행 시각·정확한 전용 설정·실행 파일을 확인하고 종료했다. private 자격 사본 5개와 password 파일 1개만 제거하고 18885/18886 listener 부재를 확인했다. 원 자격 4개 hash·등록 VM 객체·필수 원장/Authority는 보존했다. 마지막 읽기 확인은 A domid2/execution `b5a66d21-b853-4f66-b1c4-59f319e01d6a`, Debian domid1/execution `86cda91a-5065-43d3-96a9-ce68b89e3903` 유지, 원 B 1.0.8·domid22, Worker/Gateway/Manager/broker/display/collector active, Gateway generation1/SAFE_STOP/DISARMED/output=false/storage_uncertain=false다. 소유 시험 root를 참조한 잔존 프로세스와 실제 USB serial 장치는 0이었다. `r20-owned-cleanup.out`·`r20-final-readonly.out`이 근거다.

종료 R20 archive는 199파일/11,658,640B, 압축 1,373,962B, SHA `2bfefdb44985ba36475d08ed9bc1e451ba032bb84c685f62e3db03ff80e3d5f0`다. 비밀 검사 0건과 PC 회수 후 전체 path/크기/hash를 확인했다. 이후 정리·최종 읽기 결과는 별도 원문으로 PC에 보존했다.

dom0의 Linux vCPU 4개가 hard mask 0,3을 공유하는 영향을 분리하기 위해, 사용자 승인 뒤 R21에서 논리 CPU2/3만 한시 offline했다. Xen hard mask와 A·Debian은 바꾸지 않았다. 독립 복원 프로세스를 먼저 준비하고 원 boot·PID·nonce·기한을 확인한 뒤 offline했으며, 실제 offline 구간은 보수적으로 47.373초였다. 복원 프로세스의 오류 0·Linux online 0-3을 확인한 뒤 B·서비스를 복원했다. 새 guard의 로컬 10개, parent 8개 회귀와 실보드 already-online 상태의 명시 반환/EOF 복원도 별도로 통과했다. 가짜 sysfs 시험과 실제 CPU 대조는 구분한다.

| 후속 시험 | 확인한 사실 | 해석·복원 |
|---|---|---|
| R21, fresh broker·논리 CPU 2개 | 38.510초·지도 local queue 58. map high-water 26,824B/used 0, search/path high-water 0, guest odom high-water 0 | map 슬롯은 decode 전에도 예약하므로 이 peak를 지도 수락이나 계산 성공으로 세지 않는다. CPU4 복원 뒤 B 1.0.8·domid24/execution `1eb95c15-d8d5-48f7-9298-9ac015f7ec4e`, Worker·mock Gateway 복원 |
| R22, fresh broker·논리 CPU 4개 | 39.529초·지도 local queue 4, `CONTROL_APPLY_UNCONFIRMED`, 출력 불허. 정상 지도 계산 기준선 미수락 | B 1.0.8·domid26/execution `210ef404-59cb-4798-8f5f-bf6d293052d5`, Worker·mock Gateway 복원. 새 broker로 retained 차이를 줄였지만 각각 1회 표본이므로 CPU 단독 원인이나 운영 배치로 확정하지 않는다 |

R21의 같은 session/echo 수신 62개에서 첫 제출 RTT 중앙은 35.305ms, p95는 165.611ms였다. 고유 nonempty read 79회의 wall 중앙은 0.124ms/p95 17.059ms다. R20보다 일부 중앙 지연은 줄었지만 꼬리 지연과 기준선 실패는 남았다. R20과는 fresh broker/retained 조건도 달라 변화 전부를 CPU 탓으로 돌리지 않는다. `r21-timing-analysis.json`, R22 원 자료와 후속 분석을 함께 읽는다.

게스트의 실제 차량 관측 거절 사유를 확인하기 위해 기존 선택 진단 아래 `lattice_input.c`의 검사 순서를 유지한 단계별 계수를 추가했다. 차량 진단은 단계별 최초 1건·전체 16건, 실제 pending map 진단은 단계별 2건·전체 8건으로 제한한다. 미확인 값과 유효 0, 원 clock error와 mapping 폭을 더한 값을 구분한다. 진단 비활성 경로의 추가 조회·로그는 없고, 신원·나이·trylock·epoch reset·errno·READY·기한은 바꾸지 않았다. 기존 47개와 실제 C/nanopb 신규 45개 회귀가 통과했으며, invalid map도 peak를 높일 수 있다는 실제 engine 반례를 고정했다.

고정 SDK의 기본형/진단형 ARM 부분 빌드를 통과했다. 진단형 1.0.13 manifest는 `b71563cfb585a703ab16eb22084f235d1038424eca2b986a47dca16d3da0fd20`, bin은 `c0d0607c97b4b2a794abf9368492e48c970f9439164f3cab0d403cb88b7a6f65`다. 같은 source의 선택 진단 전후 ELF `text/data/bss` 차이는 `+4674/-576/+4`B이며 layout·기존 진단을 포함한 전체 차분이다. 이 수치를 개별 metadata의 정확한 RAM 비용으로 간주하지 않는다. 운영 profile·catalog·초기 설치 선택은 바꾸지 않았고 16MiB/1vCPU 시험 bundle만 새로 포장했다. 이 시점에는 실보드 진단 결과와 세 부하 조건은 아직 미수락이다.

종료 R22까지 비밀과 분리한 archive는 261파일/15,194,165B, 압축 1,759,770B, SHA `67d2e35d3806952c41f595f6eaa8f5a48dbf795b492fe2bde897bd81c8e0a2a3`다. 실제 비밀번호 값 누출 검사 0건이며 PC 회수 뒤 모든 경로·크기·hash를 대조했다. 원 Authority·journal·자격은 정리 대상으로 바꾸지 않았다.

R23은 시험 압축 해제에서 파일 모드를 0600으로 잘못 지정해 `ARTIFACT_INVALID`로 거절됐다. driver는 시작되지 않았다. 원 포장의 0444만 복구했고 내용·검증기를 바꾸지 않았다. R24에서 같은 1.0.13의 실제 기동·콘솔 lease를 확인했으나 47.016초·지도 local queue 2에서 기준선은 다시 실패했다. 확정 clock 4건의 오차폭은 170488/48925/22907/82374µs로 원 20ms를 모두 넘었다. 차량 최초 marker는 phase2/rc−11(유효 clock 미준비)이며 ring 수락 증거가 아니다.

R24의 원 콘솔 2627B는 실행/의도 재확인 실패 시점까지만 확정됐다. 어떤 public get 필드가 조건을 벗어났는지 원 기록에 없어 실제 신원 변경이라고 단정하지 않는다. 수집기는 정상적으로 중단·lease 반환·console process join했다. 새 파서 v1은 정상 Zephyr shell의 `ESC[8D ESC[J` prefix까지 printable 검사해 차량 marker를 놓쳤다. 원 raw를 보존하고, 실측한 prefix만 분리하는 v2로 재파싱해 같은 원 표본 seq2/clock error16794µs를 읽었다. 본문 제어문자·미확인 escape·위조 marker·미확정 raw는 계속 거절하며 mapped now의 `nv=0`은 unknown이다. 이는 시험 수집기 보완이며 제품 입력·clock 한도 변경이 아니다.

R24 종료 뒤 B 1.0.8·domid29/execution `109bfec1-ab6a-4f5b-bcfe-23c630172bfd`·CPU 확인 complete, Worker·mock Gateway 복원을 확인했다. 종료 R24 archive는 297파일/16,968,959B, 압축 1,947,220B, SHA `f6752f091e7ec91781b98307d6a07a5e972791d03aaaaa00c70046b589c87e26`다. 실제 비밀값 검사 0건과 PC 전체 hash 대조를 마쳤다. `resume-evening-working.md`, `r24-console-reparsed-v2.json`, `r24-recovery-validation.json` 및 회수 원문을 사용자 시험 문서 모음에도 보존했다. 다음 대조 준비 중이며 정상 지도·메모리 기준선과 세 부하 조건은 아직 미수락이다.


### R25~R28 후속 사실과 국소 보완 (2026-11-07)

R25의 읽기 전용 콘솔 중단 원인은 `xen_state=unknown` 하나였다. 원 host/execution/domid/unique/intent는 유지됐다. 새 v3 수집기는 이 값만 읽기 전용 수집에서 미관측으로 기록하며, 신원·lease·무입력·명시 crashed/dying 거절을 유지한다. 제어/READY/Worker의 실행 허가를 넓히는 변경이 아니다. 실제 PTY/lease 60개 로컬 회귀가 통과했다.

R26은 world 공급 17초 뒤 실제 map 26,824B/search 27,312B/path 10,512B 점유를 관측했다. 다만 원 시험이 준비 전 AUTO 적용 1초 기한을 먼저 소비해 `CONTROL_APPLY_UNCONFIRMED`로 끝났다. R27은 SAFE_STOP의 실제 state proof를 먼저 확인하도록 시험 순서만 고쳤다. 7.305초에 같은 세 풀 점유를 관측했지만 proof 대기에서 끝났으며 AUTO를 발급하지 않았다. 최종 격리 Authority는 SAFE_STOP/DISARMED/output=false/failure=null이었다. 풀의 writing 예약도 used에 포함되므로 이 점유만으로 유효 path·StateApplied·READY·메모리 반환 성공을 주장하지 않는다.

차량 표본의 원 수명 안에서 새 probe가 일시 실패해도 아직 유효한 직전 표본을 즉시 폐기하던 경로를 수정했다. `odometry.py` SHA `fce59589712427ca3e3b26e2b5ebb1586a20a8f16182424e19727aa1ab45548a`는 새 probe 만료·과도한 오차폭·현재보다 과거인 미확인 echo에만 last-valid를 보존한다. 원 표본 시각/오차/seq/epoch/원 만료는 갱신하지 않는다. 역행 시각·장치/session/frame/quality/encoder/kinematic 결함의 기존 무효화와 오류는 유지한다. 같은 표본의 반복 publish도 만들지 않는다. 실제 PTY·binary MQTT 집중 54개, 전체 Gateway 370 passed/1 skipped(74.52초), 고정 ARM Python3.14.7/QEMU의 42파일 설치/import/무I/O smoke를 통과했다. skip은 기존 native state codec 실행 파일 미공급이다. payload SHA `a03a4a27f8184c8c12292f04259e2a42579261338cf553438b31069f4dad3b88`; 정규 RPM·ARM PTY·실보드 통합 통과와 구분한다.

별도로 실제 runtime snapshot의 일시 `-EBUSY`를 빈 control 문맥으로 해석해 기존 clock을 무효화하던 경로를 보완했다. `xd_inputs_status()`는 현재 coordinator 회차의 원 snapshot rc만 제공한다. 이 정확한 `-EBUSY`일 때만 lattice context 재해석을 건너뛰며, 해당 회차 입력 clear·계산/출력 거절·원 clock 만료는 유지한다. 관측된 control.error=-EBUSY, 단절·신원 변경·다른 오류는 기존대로 무효화한다. actual main/clock 43개 ASAN/UBSAN 회귀와 native_sim 원 rc/clear/회복을 통과했다. 새 state는 int 4B이며 wire·큐·lock은 추가하지 않았다. 고정 ARM 기본형1.0.14/진단형1.0.15 빌드·불변 bundle 포장·0444/내용 검증까지 마쳤다. 1.0.15 manifest `044aefb9a8e277642c9ed29a5af386b717d93654c5df63d2fbc21f50da0f12c2`, bin `063d1a43e4cf7101228d98c4390f124c9585511a744d21424094cc99744dbbc9`; 아직 이 후보의 실보드 계산 수락은 아니다.

R28은 동일 1.0.13에 Gateway 차량 표본 수명 보완만 대조하려 했으나 firmware-replace가 `OPERATION_IN_PROGRESS`, `retryable=true`, operation_id=null, details.lock=admission으로 거절됐다. 실제 계산 driver는 시작되지 않았다. B는 1.0.8·domid36/execution `8b859200-a725-4f22-8682-37b36556c3e8`로 복원했고 CPU0-3 online, Worker/Gateway/display/collector active를 확인했다. 이 시험의 결과를 last-valid 보완의 성공/실패 표본으로 세지 않는다. 다음 시험 도구는 이 완전한 미수락 envelope에만 최대5회(사이 0.5초) 재시도하고 다른 오류·수락 미확정·파싱 실패는 즉시 원 오류로 끝낸다. 제품 잠금·한도·저장 의미는 변경하지 않는다.

R26 full archive SHA `331d4d856dfdbbc9e711a00626c8567b4a0bbc02d8e87435d50032ac21c0f2e8`(361파일/21,144,594B), R27 incremental SHA `d33f5af696efb983ccaa902834045e49eef01180cbe882a6d245c260e4560165`(32파일/2,085,966B), R28 incremental SHA `7ec8a013d92af5d745efa9742dbee3582dae88720057122411f39a704bf10662`(6파일/6,526B)를 PC로 회수하고 전 경로·크기·hash를 확인했다. 비밀값 누출 검사 0건이다. R27→R26, R28→R27 predecessor hash를 보존하며 이후 archive는 변경분만 담아 반복 전체 읽기/압축을 줄였다. 필수 Authority/journal·원 비밀은 삭제하지 않았다.


### R29·R30 실제 준비 진행과 남은 적용 확인

R29는 FW13 + 새 last-valid odometry만 비교했다. 25초 점유 확인 창에서 timeout, 전체65.675초/지도 queue109였다. 뒤쪽에는 map26,824/search27,312/path10,512B 및 pathHWM21,024B가 보였으나 이를 창 안의 준비 성공으로 바꾸지 않았다. 원 B1.0.8 dom38/execution36d46dbc-42d3-405a-b826-fff6340d1f41와 CPU4/Worker/mock Gateway를 복원했다. 필수 stop 수락 후 잠금 경합은 새 key가 아니라 원 operation을 재호출해 정상 종결했다.

R30은 같은 Gateway/준비 순서/CPU2 대조에 snapshot 보완 FW15만 합쳤다. world 공급18998.389657278→세 풀 점유19005.219780029(6.830초)→rebuilt StateApplied 채택19006.436801383(8.047초)→AUTO 선택19006.691412863을 확인했다. proof는 현재 B execution8a0c0a92-dc04-4f87-b2f3-f4aa665b5204·boot22db90c5-239a-4283-b5b2-174ebb953b5e·app3ed7aa0a-e759-4d48-b428-dd3a49ea5cf3·intent116에 결합됐다. 이는 실제 제품 계산/상태 수락 단계의 진전이며 단일 대조를 일반 성능 보장으로 확대하지 않는다. 다음 ready_disarmed 대기는 CONTROL_APPLY_UNCONFIRMED로 끝났다. 전체28.472초/지도queue35, app.heartbeat7/drive.command69를 받았지만 최종 AUTO/FAILED/output=false였다. 전체 baseline·메모리 반환·세 부하 통과는 아니다.

R30 active 원1초 CPU창13개에서 system_busy는0.9937~1.0이었다. driver process 중앙0.419코어/최대0.626, 자식/스레드는 겹치므로 더하지 않았다. actor 회차470개 중20ms초과216/100ms초과28, wall중앙17.887ms/p95 115.535ms/최대363.729ms였다. sched_schedstats 비활성이라 scheduling 대기는unknown이며 GIL·단일 서비스의 원인으로 확정하지 않는다. 종료 뒤 별도 CPU4/원서비스 상태의 /proc3회 관측은 원조건 성능 대조가 아니다. 이 넓은 진단 자체도0.31~0.34코어/scan437~619ms가 들어 주기 계측으로 추가하지 않았다. PID/cgroup만 읽어 Manager·Worker·Gateway 소유를 확인했고 서비스/CPU 정책을 새로 바꾸지 않았다. 다음 R31은 원 control1초·readiness300ms를 유지한 bounded 시각 계측이다.

R30 종료 뒤 원 B1.0.8 dom40/executiond0a16574-5367-4164-aad0-9f406fc289bf, CPU0-3, Worker/mock Gateway 복원을 확인했다. R29 incremental31파일/2,914,771B SHAaa1b9a483ccda3dbe592ef6eff31460c43b8802c2669aab32b43399ea7c2fe8a, R30 incremental31파일/1,509,607B SHAd2375fcc422dd8e59561297eab91c7a1415f7de9d988be0f37975d8393b5d7c2를 회수하고 모든 경로/hash를 확인했다. 비밀값 검사0건이며 원 Authority/journal은 보존했다.

현재 netfront 소스로 고정 Zephyr native_sim/native/64 packet kernel9사례를 다시 실행해 모두 통과했다(configure19.582s/build29.169s/run합계3.968s). 20ms packet경합·100ms/1s 소유 유지·해제 후 정상 진행, 종료/close만료의 grant보존·첫 오류, backendClosing/TXID손상/커널EIO 차단을 실제 제품netfront와 Zephyr mutex/timer로 검증했다. Xen/Store/device는 fixture다. 소스22개 전후hash동일, receipt netfront-current-20261107-dcaa84fd.json SHA7a34edd1d447dc338769009995568836a220a5b46e6470c6d8cbc28409824c1f. ring/grant/shutdown native+ASan/UBSan 최근3PASS는 같은 제품6파일hash를 대조해 재사용했다. 이를 실제 hypervisor grant고장·ARM/QEMU 전체fault 수락으로 확대하지 않는다.


### R31·R32 원장 조회 보완과 clock-domain 결함 확인

R31은 stop 수락 전 admission2초 예산 안의 complete control scan에서 DEADLINE_EXCEEDED로 거절됐다(operation_id=null/물리stop0). 이2초는 scan뿐 아니라 선행등록/자산검사도 포함한다. B8 dom40/exd0a16574-5367-4164-aad0-9f406fc289bf와 CPU4/Worker는 유지·복원됐다. 원 요청·원 key 파일은 보존했다.

원장132개를 읽는 동안 parent plain과 file plain이 조상 resolve를 중복하는 것을 계측했다. 새 xenova_execution.py SHA cdfc7d33c24488bbda50d4dcfcddce4577bdcb0dd070a78d2b58d559a493227b는 read_record의 부모 검사만 제거하고 file 전체 canonical/regular/nlink 검사를 유지한다. read_requests의 전체 열거·원 reader hook·JSON/request hash·부재/손상/기한은 그대로며, 검증된 같은 non-null key의 여러 operation은 RECONCILE_REQUIRED로 차단한다. None key 여러 개는 기존대로다. 새 DB/index/cache/lock/추가 fsync/원장 삭제/timeout상향은 없다. pathname 교체를 원자적으로 고정하는 새 보장도 아니다.

같은 보드·같은process·CPU4/원 서비스에서 warmed before/after를 교대로3회씩 측정했다. 모두132개 파일읽기/작은request hash132회를 완료했고 parent plain은133→1이었다. CPU thread 시간 중앙1.052→0.847초, wall중앙1.763→1.551초였지만 새 후보 wall최대1.953초는 이전최대1.810초보다 길었다. 중복 제거와 CPU 비용 감소는 확인했으나 꼬리 지연·무제한 이력 규모의 완료 보장으로 확대하지 않는다. cold 첫 import2.632초/기록읽기0의 원 실패는 별도로 보존하며 이 paired 비교에서 제외했다.

최종 집중22PASS, 원 crash/restart2PASS와 force정책ext4의2PASS를 확인했다. 관련215PASS/1skip·guard108PASS 원 기록도 보존했다. 초기실패에는 DrvFS mode0777·자식 PYTHONPATH·고정 systemd 검증기 공급이 섞였고 이를 제품 보호 완화 없이 분리했다. systemd259/QEMU의 정상 unit그래프는 통과하지만 주입한 순환을 검출해야 한다는 후반assertion은 현재/b84 이전후보 모두 실패해 별도 미해결로 남긴다. 이 정적unit실패를 runtime 수정의 성공으로 지우거나 제품unit을 바꾸지 않았다. 검증원본은 control-scan-local-validation.json 및 before/current stdout에 있다.

R32는 R31의 test-stop key를 정확히 재사용해 operation89d313fa-c015-4751-821f-cb3c9cccdf91을 한 번 수락·완료했다. same_key=true/prior_key_file_unchanged=true/prior_acceptance=false/same_execution_verified=true를 보존했다. 기존 원 실행 기한을 재발급한 것이 아니라, 미수락 요청의 조회·수락을 새 호출에서 마친 것이다. 이후 FW15 주행 기준선은 전체44.816초/지도queue54에서 미통과했고, 준비 선택의 fixture guard SAFE_STOP_BOOTSTRAP_CHANGED가 관측됐다. 마지막 snapshot만으로 어느 조건인지 확정하지 않고 그 RAM 조건의 제한 관측을 R33에 추가한다. B8 dom42/exeb44f5b9-8f1a-42f1-b3b3-40e513303860, CPU0-3/Worker/mock Gateway를 복원했다.

추가 source검토에서 clock epoch 혼용을 확인했다. 고정 Zephyr timer의 raw counter와 kernel uptime은 같은zero가 보장되지 않으며 inventory도 same_epoch=false를 선언한다. R30 confirmed range와 기존 v3 strictprefix를 모두 통과한 CLOCK5행의 dec rawcounter−uptime logstamp는2,013,899~2,014,717µs였다(raw SHAe035333e468c49c8668eda23be991d521ec2f4177f21814a6d10e0f3e86b970b). 동일 LOG생성행의 두시각이라 MQTT/콘솔배출지연 비교가 아니다. 직전스케줄지연·1ms양자화가 있으므로 이값을 보정상수로 쓰지 않는다. r30-clock-domain-paired-evidence.json에 원byte범위5개를 보존했다. lattice의 rawcounter bound/input시각을 main/runtime uptime과 직접비교하던 publication 경계만 보수적으로 변환하도록 구현·시험 중이며 global clock·원시각·Prepared raw만료·TTL을 바꾸지 않는다. 아직 이새C보완의 실보드통과를 주장하지 않는다.

### R33·R34 준비 단계 분리와 publication 시각 경계 보완

R33은 준비 진단 FW1.0.17과 기존 1초 control 기한을 사용했다. world 공급 후 세 풀 점유는13.200초, 현재 실행의 StateApplied 채택은16.881초에 확인했다. 이후 AUTO의 원 issue→publish 등록16.273ms→PUBACK 관측150.809ms였고, 그때 원 기한은 약848.737ms 남았다. 실제 ready18회는 모두 false였다. progress0→1→5→9→14만으로 준비 완료를 만들지 않았으며, CONTEXT_CHANGED·OBSERVATION_EXPIRED·POSE_UNAVAILABLE·STATE_PREPARING 사유와 최종 CONTROL_APPLY_UNCONFIRMED/output=false를 보존했다. 전체33.762초/지도queue45였다. clock-domain 결함이 이 실패의 유일한 원인이라고 단정하지 않는다.

실제 firmware의 PREP/APPLIED 진단은 앞선 seq1/path1의 큐 등록 및 publish rc0를 확인했고, 후속 seq5의 원 raw bound보다1,161µs 늦은 준비 결과는 applied=false로 거절했다. 각 seq/path·시각이 다른 증거를 하나의 READY로 합치지 않는다. 원 콘솔9,523B, guest 입력0, lease 반환·수집 프로세스 종료를 확인했다. logger drop 표시·무효3행·미확정1행·순번 공백도 남겨 전체 시도 수집으로 주장하지 않는다. B8 dom44/executionc191415e-f57f-457d-99fe-c3743b4f259f와 CPU4/Worker/mock Gateway를 복원했다.

시각 보완은 adapter.h의 기존 padding에 private sample clock 구분을 두고 main.c의 publication 경계에서만 변환한다. counter 결과는 uptime-before→counter→uptime-after의 세 작은 관측으로 남은 기한을 보수적으로 uptime에 옮긴다. AUTO 원 입력 나이는 올림과 관측 사이 지연을 반영하고, MANUAL 원 입력 나이는 기존 uptime 의미를 유지한다. 원 sample·Prepared raw 만료·wire·TTL을 다시 쓰지 않는다. 정상 만료는 기존 -ETIME no-submit 경로로 끝내고 다음 정상 계산으로 회복한다. clock 실패·역행·overflow·알 수 없는 clock 구분의 최초 fatal 사유는 유지한다. generic uptime 경로에는 새 clock 조회가 없다.

실제 main/clock/입력 C 회귀94PASS(121.63초)와 실제 Zephyr native 수동 pending/running/result 소유·점유·최고값·취소·모드 변경 회귀를 통과했다. 고정 ARM 기본형1.0.18/진단형1.0.19의 빌드·불변 bundle 포장·보드 임시 디렉터리의 hash/0444 대조까지 마쳤다. main 심볼은4,072→4,444B(+372B), automatic 경로는+8B이며 전체 ELF text의 순차이는 layout 영향을 포함하므로 코드 비용0으로 보고하지 않는다. ARM compile-only ABI 검사는 sample128B/input_sequence offset32B/job2,360B/completion2,592B를 유지했다. 새로운 전역 lock·cache·queue·범용 시간 서비스는 없다. sample-clock-boundary-local-validation.json, counter-domain-arm-state.json, counter-domain-arm-abi.json에 명령·source/ELF/config hash를 보존했다.

R34는 이 FW19를 적용하기 전 원 B의 test-stop 조회가2.049초에 DEADLINE_EXCEEDED/control request scan budget expired로 끝났다(operation_id=null, 물리 동작0). B44와 원 firmware·CPU4/Worker는 유지·복원됐다. 따라서 R34는 새 시각 보완의 실보드 성공/실패 표본이 아니다. 추가 cProfile 계측에서도 기존2초 예산 안에서 전체 조회를 못 마쳤고, 파일마다 반복되는 canonical ancestor 확인 비용과 JSON/hash 비용을 분리했다. 계측 오버헤드를 포함하므로 이 표본을 무계측 요청 지연이나 하드 상한으로 쓰지 않는다. 전체 이력 검사·원 key 유일성을 유지한 국소 보완을 계속 검증한다.

R33 incremental archive SHAa7cb6b9aabe21d823cb4b7cc1bb99d8254193f19f4c543ed6440baf86f366b82(31파일/1,818,086B), R34 SHA19d61cb16001b8b442d2f92963ae9b50726f56df584187d5d87bbdba24d40701(7파일/12,280B)를 로컬로 회수해 전 경로·크기·hash를 확인했다. 실제 비밀값 검사0건이며, 원 Authority/journal/자격은 보존했다. 전체 정상 지도 계산·메모리 반환 및 세 부하 조건은 아직 미수락이다.


### R35·R36 재전달·기본형 대조 및 FD 조회의 실제 비용

R35는 R34의 미수락 stop key 하나만 재사용해 정상 수락·종결했다. 원 key 파일 불변·미수락 확인·같은 실행 확인 후 진행했으며, 기존 원 operation의 기한을 재발급하지 않았다. FW19에서는 지도 공급 후 풀 점유12.690초·현재 StateApplied21.120초까지 진행했으나 원1초 내 AUTO READY는 얻지 못했다. 전체39.498초/지도queue59, 실제 ready10회 false였다. 명령67개는 모두0이며, callback 완료298회나 progress 증가를 실제 tracker 정상 명령으로 확대하지 않는다. map0→26,824B/HWM53,648B, search0→27,312B/HWM27,312B, path0→10,512B/HWM21,024B, odom0→2,592B/HWM3,024B는 관측했지만 마지막 표본까지 used가 유지돼 반환은 미관측이다. 고정 예약 RAM의 OS 반환도 아니다.

원 tail 표본63건의 odometry first-submit RTT 중앙39.136ms/p95 155.100ms/최대466.910ms, device write-return→Pi read-end62건은 중앙17.633ms/p95 143.784ms/최대433.199ms였다. Pi read-end→odom 진입 중앙1.133ms/p95 9.741ms로, callback 뒤 처리만을 주원인으로 확정할 수 없다. 확인된 guest clock7행 중5행은 실제 폭25.128~116.207ms로 기존20ms guard가 거절했고, 성공2행은13.467/14.345ms였다. 별도 차량 거절1행은 clock 검사를 통과한 뒤 xd_context_get의 -EAGAIN이었다. loader 실패와 기존300ms 신선도 만료 중 어느 것인지는 아직 원 자료로 구분하지 못했다. stale 관측을 성공값으로 채우거나 한도를 올리지 않았다.

R36은 같은 C 시각 보완의 진단 비활성 기본형 FW18을 사용했다. 실제 control 안전 정지와 MQTT world 발행63건은 진행했지만25초 안의 세 풀 점유 조건을 못 얻어 전체41.979초에 끝났다. AUTO 선택은 하지 않았고 SAFE_STOP/DISARMED/output=false를 유지했다. 따라서 로그만 끄면 해결된다는 근거가 아니다. R35와 달리 하위 FD reader도 바뀌었으므로 전체 시험을 엄밀한 단일변수 대조라고 부르지 않는다. B8은 R35 뒤 dom46/ex033e5330-97a4-4620-b181-e5624058dc29, R36 뒤 dom48/ex31d6f043-106c-4e9f-a013-8cf403c54f9e로 복원했으며 CPU4/Worker/mock Gateway를 확인했다. 원 A/Debian은 변경하지 않았다.

원장 조회의 추가 보완은 한 번의 전체 scan 안에서만 디렉터리 FD를 고정한다. 시작/끝 canonical 경로·dev/ino를 대조하고, 각 파일은 openat O_NOFOLLOW/O_NONBLOCK과 fstat regular/nlink=1로 검사한 뒤 기존 JSON/schema/identity/hash/key 검증을 모두 수행한다. FIFO를 열다가 대기하지 않고, 열거 중 사라짐·부분 열거·부모 교체를 정상 부재로 만들지 않는다. root와 현재 파일의 FD는 유한 수명으로 닫는다. generic read_record와 module/class/subclass/instance/explicit reader hook, 나머지 Execution AST는 그대로다. 공개 응답·원 기한·저장·fsync·효과 직전 guard에는 변경이 없다. source SHA913d80e6178ff63eaf981f671fc3fef2563f422f0583cbc049f0d0628d39511f, 집중45PASS·관련74PASS 및 주입 fixture의 fd 입력 가정 정합화 뒤 budget19PASS를 보존했다. 최초 TypeError 로그도 삭제하지 않았다.

보드의 같은144개 원 기록을 CPU4/원 서비스·동일process·같은2초 scan 예산에서 교대로3회씩 읽었다. 모두 전체 조회를 마쳤고 request hash144회는 유지했다. 파일별 조상 resolve144회는 scan 시작/끝2회와 FD별 regular/nlink 검사로 바뀌었다. thread CPU 중앙0.899→0.608초, wall 중앙1.593→1.399초였다. 새 후보 범위0.729~1.413초/이전1.520~1.721초의 소표본이며, 전역 cache/index·이력 삭제는 없고 전체 JSON/hash O(N)은 남는다. 무제한 기록 수나 운영 꼬리 지연의 하드 보장이 아니다. control-fd-board-comparison-analysis.json에 원 표본과 명령을 연결했다.

동일 FD 후보를 recipe 선택36모듈/0644로 새 격리 설치한 실제 ARM Python3.14.7/QEMU에서도11사례를 통과했다. 실제 scandir(dirfd)/fdopen·정상 전체 조회·hash/key·손상 JSON/중복 key·symlink/FIFO·실패 후 FD 정리·후속25회 FD 증가0을 확인했다. errno/stat/os/io는 고정 target의 built-in/frozen 모듈이고 개발 소스 PYTHONPATH로 누락을 가리지 않았다. payload SHA433bb9fbce7793252509736bddab1630de8d3fc1fffddc2ec23b32fa9e3db782, evidence execution-fd-arm-validation.json SHAfe4a94bc8cb14016190c4817fafe95590afdedbb395984f20c116306971587ec. 정규 do_install/RPM QA/이미지 설치나 QEMU의 보드 지연 보장은 아니다.

R35 archive SHAcd94d31b7e72b6bd38ce24cf632be66e6a585a7c104636c9acc0d47da0f4d7f0(31파일/2,051,625B), R36 SHA5cbf1896664802c493c0ab01d89ee28870dccc0518f327b2f3f3a1ae1a900546(31파일/2,004,046B)를 회수해 모든 경로/크기/hash를 확인했다. 비밀값 검사0건, 필수 journal/Authority/원 자격은 보존했다. 다음 대조는 승인된 두 부가 서비스의 한시 중지·자동 복구로 공유 지연을 분리하며, 전체 정상 계산·반환·세 부하 수락은 계속 미완료다.


### 11/07 R37–R40: 미수락과 계측 경계

이번 후속 시험은 `feat/platform/integration-followup`의 HEAD `5d05101efd39c457cb78a05b473952bc38e7a317`와 미커밋 후보를 사용했습니다. **정상 지도 계산→READY→실제 PTY 주행→메모리 반환 baseline과 세 부하 조건은 아직 미수락입니다.** R37 wrapper 실패, R38 부가 서비스 한시 중지 대조, R39 private NODELAY-only, R40 source-barrier/FW22 결과는 중간 원자료이며 완료 근거로 바꾸지 않습니다.

| 구분 | 구현·관측과 기준 | 결과·한계 |
|---|---|---|
| source barrier | `lattice_input.c` SHA `a76a6b5596e1fcee97a77c864192f6aaa41cd88a191eb7eb3372a076fa343bf5`. 정확한 첫 context 조회 실패에만 이미 열린 소켓을 처리하고 새 연결·pending map/차량/상태 채택은 차단 | 실제 C 111개 통과. ARM22/23 ABI `128/32/2360/2592` 유지. base maps 직접 frame +48B, 대상 text +224B, bool +2B. 런타임 전체 stack 최고값·실차 수락 아님 |
| R39 | FW18/FD913/fce/CPU2/full services 유지, private broker GLOBAL NODELAY만 변경 | 42.172초·지도 로컬 queue63개 뒤 점유 만료. 운영 broker 변경0. NODELAY를 해결책으로 확정하지 않음 |
| R40 | a76/FW22 + 원 MQTT v2.1 loop_timing 수집 + motion 선택 | 41.154초·지도 로컬 queue60개 뒤 점유 만료, motion 진입0. 실제 계산·복귀·반환 성공으로 세지 않음 |
| timing 수집 | 기존 fixture allowlist가 loop_timing을 버리던 점만 새 v2에서 보완. distribution/collection 원필드·VM/boot/execution 필터·512 tail 유지, 실제 C producer fixture7개 통과 | R40 205개 중 loop_timing28개(4종 각7개), status=no_sample/count0/null. 선택·소비된 작업 없음이며 0µs나 발행 미지원이 아님. Standby 모든 계산시간·순수 CPU·planner별 시간으로 확대하지 않음 |
| 복원·보관 | R40 원 B1.0.8 dom56/ex597fcc06-0d50-420b-b251-20fc908ace5d, CPU4·Worker/mock Gateway 복원 | A/Debian·Xen pinning·운영 설치·실제 차량 출력·commit/push 변경0. R39/40 archive 31개씩 전수 경로·크기·hash 대조, 실제 비밀 검출0 |

R39 archive SHA `2814c1043a519d0b770c8a5cdff802faf43fd4efb5d5e46778f5c14d5bc36e9d`, R40 SHA `517b0e5d2b590870dfa3fdadeb504a7b6a03857654dba2e6a5e16c79c764e0db`입니다. 실제 명령·raw/JSON·후보별 freeze와 `[resume-evening-working.md](C:/Users/SSAFY/Documents/Codex/2026-11-06/board-resume-evening/resume-evening-working.md)`를 연결합니다. 사용자 지정 `C:/Users/SSAFY/Documents/xenova-test-docs/2026-11-06-evening` v8 모음은 5,138개/224,235,291B로 전수 SHA 확인했고 기존61개·원본·사용자 변경 보호를 유지했습니다. 이후 진행 파일은 다음 checkpoint로 추가합니다.

R40 시점에는 query v2의 16초 부모 제한이 실제 query 12초 앞의 cold API import까지 포함한 문제가 남아 있었습니다. 당시 원 오류·빈 결과·정리 미확정은 보존했습니다. 이후 준비/실제 query/정리를 분리한 v4의 실제 실행 결과는 아래 후속 절에 연결합니다. Store 정상 bootstrap 5왕복/호출50ms라는 소스 사실만으로 실보드 실패 원인을 확정하거나 값을 올리지 않습니다.

### 11/07 R41–R43: 계산 발행과 적용 확인의 구분

**이 시점에도 READY→PTY 주행→입력 만료 뒤 메모리 반환의 전체 기준선과 세 부하 조건은 미수락입니다.** 아래 표는 실제 진행한 단계와 남은 실패를 분리합니다. 모든 시험은 원 control 적용 기한 1초와 입력 신선도 검사를 유지했습니다.

| 시험 | 실제 관측 | 판정·복원 |
|---|---|---|
| R41, FW23·상세 계측 on | 지도 공급→풀 점유 21.951초, 현재 실행의 StateApplied/AUTO 선택 30.189초. 원 문맥의 command 85개 중 8개가 nonzero였고 첫 값은 `v_mps=0.02`, `steer_rad=0.0002` | 실제 planner/tracker 계산과 발행 경계의 근거다. 원 1초 안 READY 18회는 모두 false, serial 534건은 모두 STOP이었다. `CONTROL_APPLY_UNCONFIRMED`, output=false. B1.0.8 dom58 복원 |
| R42, 같은 FW23·상세 probe 미설치 | 점유 7.350초 후 StateApplied 대기 20초 만료. 전체 46.784초·지도 로컬 queue 81개 | 미수집 상세 자료는 0지연·0오류가 아니다. B1.0.8 dom60 복원 |
| R43, R42 + 두 부가 서비스 한시 중지 | 점유 8.634초, StateApplied 24.673초. 전체 41.795초·지도 로컬 queue 119개, command 41개 모두 0 | 적용 확인 실패. 비교 도중 서비스 복원은 없었고 비교창 valid=true/passed=false. B1.0.8 dom62/execution `aacdfb6e-d287-40d9-a44a-736e5dc73a5d` 복원 |

R41에서 원 AUTO issue→첫 nonzero 원문 관측은 264.339ms였습니다. 그러나 첫 g2 lifecycle은 Gateway에 640.007ms 뒤 `ACTIVATING/NOT_READY/OBSERVATION_EXPIRED`로 도착했고, 늦은 READY/명령은 원 기한이 지난 약 6.1초 뒤였습니다. 늦은 결과로 출력을 다시 열지 않았습니다. 같은 lifecycle의 raw 관측과 Gateway의 검증 후 관측 사이에는 최소 382.084ms가 있었지만, 네트워크·Paho callback 대기·검증·actor scheduling의 몫은 아직 분리하지 못했습니다. Timeline의 `publish_enter/exit`는 `_publish_control`의 RAM 등록 경계이며 실제 socket write 시각이 아닙니다. PUBACK 관측도 actor가 ticket을 확인한 시각입니다.

R41 활성 표본에서 maps는 used 26,824/HWM 53,648B, paths는 used 10,512/HWM 21,024B였습니다. 현재 점유가 앞선 최고값보다 줄어든 일시 슬롯 반환은 확인했습니다. 입력 종료 뒤 새 표본으로 확인하는 전체 수명 assertion은 아직 충족하지 못했습니다. 예약 RAM의 OS 반환이나 VM 전체 메모리 감소를 의미하지 않습니다.

별도 console query v4는 준비 45초·실제 query 12초·정리 5초를 구분하고, 원 실행·host·Xen unique identity guard를 유지합니다. 로컬 실제 PTY 포함 41개와 부모 연결 7개를 통과했습니다. R41–R43에서는 각각 같은 실행에 `xenova drive\r` 13byte를 한 번 쓰고 응답·lease 반환·console 자식 종료를 확인했습니다. 이 명령은 모드를 변경하지 않습니다. R41 context 누계 loads999/failures106/first_rc=-2/last_rc=0/available=true, R42 988/111, R43 1010/123입니다. 모두 CPU4 복원 뒤 읽은 부팅 누계이므로 실패 임계 구간의 현재 상태로 소급하지 않습니다.

세 시험 뒤 CPU0–3, Worker·운영 mock Gateway·display-feed·runtime-log-collector 복원을 확인했습니다. A/Debian·Xen pinning·운영 모듈·원 자격은 유지했습니다. R41 archive `e361e59f3aed5a9d46c2193abc3ad2d1a4183f9d8688e7aa74a375b5b334d862`(35개/2,524,636B), R42 `ef8179006e633a2c0e4cab85961f403945ad2466a9c99f2afa27a9fd5a57efd7`(26개/1,275,650B), R43 `cdabc229fc16ab98da10b73cbbd2e78c5c8376a8c2e4644f3b033582fde1e7f9`(30개/1,362,569B)의 모든 경로·크기·hash를 회수 대조했습니다. 원 필수 기록과 과거 실패는 보존했고, 종료한 private broker와 원 자격 hash를 먼저 확인한 뒤 R31–R42의 시험용 자격 사본 72개만 정리했습니다.

### 공통 MQTT 수신 시각 보완과 다음 원인 분리

확인된 별도 결함은 공통 `xenova_mqtt/client.py`의 수신 시각 경계입니다. 기존 코드는 JSON·envelope·현재 binding guard·중복 검사를 마친 뒤 `Message`의 기본 시각을 찍어, 검증 대기가 메시지 나이에서 빠졌습니다. callback 진입에서 `time.monotonic()`을 한 번 읽어 기존 `received_monotonic`에 전달하도록 바꿨습니다. 현재 binding 검사는 여전히 검사 시점의 현재 시각을 쓰며, 검증 순서·최초 원인·직접 `Message` 생성의 기본값·wire·의존성은 유지합니다. 이 시각도 커널 socket 도착이나 callback 이전 Paho 대기를 나타내지는 않습니다.

원 소스 `c1f53425a211eb8a2398162e249ddbc20df5f9d0ea17b2e02f01e2944cc079eb`에서 지연 주입 3반례를 재현했고, 후보 `8e0a9d5b5a9cb0d4c68f45ae658a90368d77d9036c8de2923d860e4255576653`로 client 27개·패키지 160개가 통과했습니다(실제 broker 4개 skip). 검증은 `mqtt-callback-entry-validation.json`에 연결합니다. 이 독립적인 신선도 결함을 R41 지연의 유일한 원인으로 확정하지 않습니다. 현재 보드 R44는 이전 c1 client를 고정한 별도 TCP 옵션 대조이며, 새 수신 시각 후보와 섞지 않습니다.

읽기 전용 보드 검사에서는 common Paho2.1.0 socket의 TCP_NODELAY=0을 확인했습니다. C client와 DriveLinkIO의 기존 NODELAY=1, private broker의 GLOBAL NODELAY 설정과는 다른 연결입니다. R44는 이 common Gateway 연결에만 명시적인 시험용 socket-open callback으로 1을 적용합니다. 제품 기본값으로 승격한 상태는 아닙니다. 고정 [Paho2.1.0 원문](https://raw.githubusercontent.com/eclipse-paho/paho.mqtt.python/v2.1.0/src/paho/mqtt/client.py)의 socket-open 경계와 대조했으며, [Linux tcp(7)](https://man7.org/linux/man-pages/man7/tcp.7.html)의 작은 패킷 버퍼링 동작을 scheduling·전체 지연 보장으로 확대하지 않습니다.

또한 시험 mapper가 Gateway와 같은 Python 프로세스에서 지도 생성·실제 codec 검증/인코딩을 실행하는 인공 결합을 확인했습니다. 보드 Python3.14.7/upb·CPU4·원 서비스 상태에서 cold1+warm30개씩 읽기 전용 측정한 warm 값은 64×32의 thread 아닌 **process CPU** 중앙 13.434ms·wall 중앙 18.136ms/최대55.028ms, 64×64의 CPU 중앙18.959ms·wall 중앙31.800ms/최대67.502ms입니다. 별도 실행의 micro 표본이며 원 통합 시험의 GIL 지연·순수 인코딩 시간·하드 상한이 아닙니다. 실제 Jetson의 외부 생산자 배치와 맞추는 mapper-child 대조를 준비하되, 원 codec·입력 나이·ID·발행 주기·차량 출력 차단은 유지합니다.

### R44–R47 후속: 입력의 지속 유효성과 시험 준비 조건

| 시험 | 추가로 바꾼 시험 경계 | 실제 결과 |
|---|---|---|
| R44 | 이전 common client 고정, 이 Gateway의 TCP_NODELAY만 0→1 | 실제 socket-open의 적용·종료 후 callback 반환 확인. 점유8.165초·StateApplied9.396초, 전체26.255초/지도queue65, command64개 모두0. 원1초 적용 실패 |
| R45 | R44 조건에 callback-entry 수신 시각 후보8e만 적용 | 점유6.991초·StateApplied8.974초, 전체27.659초/지도queue60, command61개 모두0. 적용 실패. 이 신선도 결함 수정의 독립 타당성과 전체 실패 원인은 구분 |
| R46 | R45 조건에 실제 mapper 생산자만 별도 Python process로 분리 | 원 world/codec·입력나이200ms·최소발행간격100ms·원ID/시각 유지. 점유7.119초·StateApplied9.534초, 전체35.545초/지도queue67, command76개 모두0. mapper 정상 종료·join 확인. GIL 결합 분리만으로는 해결되지 않음 |
| R47 | R46 조건에 기존 `_qualified(...,held=True)`를 초기 AUTO 선택 전에 연결 | 점유5.949초 뒤 원20초 준비 대기에서 held qualification을 얻지 못함. 전체45.947초/지도queue107, AUTO 발행0·generation1·SAFE_STOP/DISARMED·failure=null·output=false. 적용 후1초 기한은 시작되지 않음 |

StateApplied는 현재 실행의 사본 수락 근거이고, 최신 지도·차량 상태의 지속 유효성은 별도입니다. 기존 시험은 전자만 확인하고 AUTO를 선택했습니다. v6는 기존 신원·source·readiness lease·ready_hold 검사를 actor에서 한 번 확인하며, 불충족이면 원20초 대기 안에서 준비를 계속합니다. 새 운영 수치·READY 승격·원 generation/기한 재발급은 없습니다. 로컬 실제 machine 및 실제 C qualification 21경계를 포함한 8개가 통과했고, path owner/RTOS/clock context fixture 경계는 `bootstrap-v6-validation.json`에 명시했습니다. 제품 Gateway의 전환 규칙을 변경한 것은 아닙니다.

R46 mapper child는 실제 Paho 구독→제품 VehicleSample decode→원 지도 생성/encode→별도 broker 발행을 수행했습니다. 실행18.012초의 process CPU2.153초·주 스레드 CPU1.531초, encode83회 CPU합계0.453초·wall합계2.534초를 기록했습니다. 포함 관계가 있는 비용이므로 더하지 않습니다. 이 결과는 Pi 전체 CPU나 GIL 대기의 직접 계수가 아닙니다. actor722회 중20ms 초과344/100ms 초과28, 마지막512개의 wall p95는95.797ms였습니다. 현재 자료만으로 scheduling·GIL·동기 처리의 몫을 분리하지 못해 다음 R48에서 제한된 phase wall/thread-CPU와 callback→enqueue→feed를 연결합니다. 정상/희귀 오류의 진단은 지우지 않습니다.

새 common8e를 recipe 선택 12개 Python 모듈+pyproject/0644로 새 격리 root에 설치한 고정 ARM Python3.14.7/QEMU·Paho2.1.0 검증도 11개 통과했습니다. 느린 검증·현재 binding 만료·중복/무효 입력 뒤 정상 회복·원 Message 기본값을 확인했고 네트워크·VM·서비스 동작은 막았습니다. 새 후보를 포함한 전체 Gateway 로컬 회귀370개 통과/1개 skip(native state codec 실행파일 미제공), source hash 전후 불변입니다. `mqtt-receive-arm-evidence.json` SHA `b2c8aefe644a68c9d822e0ffad8413b211ff9e6f3652f9df7337d5d718ede51d`, `gateway-receive-full-tests.json/xml`에 실제 실행 환경과 명령을 보존했습니다. 정규 RPM/이미지·실차 수락은 아닙니다.

R47 뒤 B1.0.8 dom70/execution `37ad3bd1-f121-41a3-aecb-c8f3402d650a`, CPU0–3, Worker·운영 mock Gateway·두 부가 서비스와 query lease/자식 종료를 확인했습니다. A/Debian은 유지했습니다. R44 archive SHA `346e7d1eb4921eaf0ad5363731ae4dcf32052fe359fdc4eab3d3cf0dbc018894`(30개/882,349B), R45 `d88fe9b6274b6a47da44d69ba1bdf87765ad73dc51e37a6a84cc70e6b4706545`(30개/839,277B), R46 `11a98415350e832e5b217211d99b035649e7d82718f2e2714a0950120d9276e8`(32개/1,073,294B), R47 `6a524fc8a9ec85672973404ab8491c8deaf6f2d65f26b70737c0cdf057d92967`(33개/1,382,649B)를 전수 회수·대조했고 비밀값 검출0입니다. 필수 기록은 보존했습니다. 사용자 지정 모음의 v9는 R45까지와 동결 제공물을 포함한5,586개/241,793,115B 전수SHA 확인이며, R46 이후는 다음 checkpoint에 추가합니다. **세 부하 조건과 전체 정상 주행·반환은 계속 미수락입니다.**

### R48: CPU 사용과 경과시간, 메시지 상관 계측의 한계

R48은 R47 조건에 제한된 wall/thread-CPU 계측만 추가했습니다. 지도 점유5.368초 뒤 원20초 안에 held qualification을 얻지 못했고, 전체49.828초/지도queue95개에서 AUTO 제출0·generation1·SAFE_STOP/DISARMED·output=false로 끝났습니다. B1.0.8 dom72/execution `bd355a71-2ad7-44e3-bc64-14f5ff086cea`, CPU0–3·Worker·mock Gateway·두 부가 서비스와 query 종료를 복원 확인했습니다.

actor 894회의 wall 합계28.881초와 thread-CPU 합계3.729초를 기록했습니다. 마지막190회 p95는 wall84.042ms/CPU7.664ms였고, 공통 MQTT callback 마지막512회 p95는 wall51.128ms/CPU1.917ms였습니다. 직렬 경로·callback·actor 계측은 서로 포함되므로 합산하지 않습니다. 차이는 scheduling·GIL·I/O·잠금 등을 포함할 수 있으며 특정 원인의 몫이나 WCET로 단정하지 않습니다. 계측 자체의 시각 조회·작은 metadata 할당 비용도 있습니다.

원 v7 계측기는 message_id를 UUID 길이로 제한했지만 실제 C `publish_as()`는 uint32 counter를 문자열로 발행합니다. 따라서 R48의 빈 callback→feed 상관은 트래픽 부재나 0대기의 근거가 아닙니다. 원자료와 `critical-timing-v8-erratum.json`을 보존했습니다. 새 v8은 원 ID를 바꾸지 않고 topic·VM·app·boot·publisher·seq와 함께 묶습니다. 실제 C encoder→공통 callback→등록 handler→제품 Runner/Machine 경로를 포함한 로컬59개가 통과했으며, R49는 이 계측기 수정만 비교합니다. 제품 wire·기한·nice·출력 권한은 바꾸지 않았습니다.

R48 회수 archive SHA `88595ed5e5842e8287a8f29987ba371046558d23f95b6534f4fac3de5664430e`(1,730,416B)의 모든 member 크기·hash와 비밀값 검출0을 확인했습니다. 새 격리 ARM root의4 recipe/138파일 대조·제품82모듈·Worker9모듈·entrypoint4개 import도 통과했습니다. 다만 고정 로컬 캐시에 `libsqlite3.so.0`이 없어 전체 API/log-runtime stdlib 수락은 미완료이며, 원 실패를 `combined-provider-arm-evidence.json`에 남겼습니다. 정규 RPM·이미지·실차 수락으로 확대하지 않습니다.


### R49: 실제 C 메시지의 수신·전달 지연과 reader 대기 후보

수정된 v8 계측만 바꾼 R49에서 실제 C 메시지의 상관을 얻었습니다. 보존 표본 callback→enqueue 256개는 중앙1.269ms/p95 21.182ms/최대177.914ms, 유일하게 대응한 enqueue→feed 90개는 중앙16.481ms/p95 112.899ms/최대183.325ms였습니다. 나머지166개는 feed 미관측·덮어쓰기·거절을 이 자료만으로 구분하지 못합니다. actor 마지막166회 p95 wall95.495ms/CPU7.622ms, callback 마지막256회는 wall52.435ms/CPU2.260ms였습니다. 소표본·계측 포함이며 전체 운영 지연 상한이나 단일 GIL/잠금 원인으로 확대하지 않습니다.

R49 지도 활성→점유23.192초, 전체69.367초/지도queue180개였고 원 준비20초 안에 held qualification을 얻지 못했습니다. AUTO 제출0·generation1·SAFE_STOP/DISARMED·output=false였습니다. B1.0.8 dom74/execution `dd81553a-286d-4c83-a8cf-4b5e2fcfbaab`, CPU0–3·Worker·mock Gateway·두 서비스 복원과 query13byte/lease·자식 종료를 확인했습니다. query context는 loads1334/failures165/first-2/last0/available=true인 원 부팅 누계입니다.

소스에서 별도로 확인한 BindingCache reader는 writer의 JSON clone 중 같은 mutex를 기다립니다. 게시된 private root는 교체만 하는 현재 구조이므로 reader가 root를 한 번 캡처해 원 TTL·신원·row와 기존 세 nested map copy를 검사하는 국소 보완을 검증 중입니다. writer 직렬화·clone·invalidate 순서는 바꾸지 않습니다. 이 보완의 정합성과 R49 지연의 주원인 여부는 다른 판단이며, 새 후보 보드 비교 전까지 해결로 표시하지 않습니다.

R49 증분 archive `6e0901f11772e8748ae96dc709990367cff50cdcd55043cbd74fd96cfd3e4a1b`의32파일/2,930,059B를 전수 회수·대조했고 비밀값 검출0입니다. 종료한 private broker와 원 자격·경로·권한을 모두 선확인한 뒤 R43–R49의 시험 자격 사본42개만 정리했습니다. 필수 기록·원 자격·다음 R50 사본은 유지했습니다. 정상 주행·입력 종료 뒤 반환과 세 부하 조건은 아직 미수락입니다.

### BindingCache reader 보완과 R50 단독 비교

공통 `xenova_mqtt/drive_binding.py`의 `current()`·`observation_status()`에서 writer mutex 대기만 제거했습니다. reader는 게시된 private root 참조를 한 번 캡처해 동일 객체의 원 외곽 TTL·row TTL·신원과 기존 세 app map 복사를 검사합니다. writer의 validation·JSON clone·교체·invalidate 직렬화와 원 오류는 그대로입니다. 고정 GIL-enabled CPython 및 게시 객체를 변경하지 않는 현재 생산 경로에 한정합니다. 겹친 invalidate 전에 캡처한 완전한 옛 snapshot을 반환할 수 있지만, invalidate 완료 후 시작한 새 읽기는 unavailable이어야 합니다. 영구 허가·전역 철회 barrier·free-threaded 보장을 새로 제공하지 않습니다. 기존에 shallow alias가 가능한 추가 nested 값은 trusted reader가 변경하지 않아야 합니다.

원 source `4c1a6fc6cbcc3418869280c07785e5e439ada4c089f74763c1e97d228b1b203e`의 실제 clone 대기3반례를 재현하고, 새 `4f5aadbbd26bbabcc22372c8d937017dc1d38d00f2081e6bc9cf4abdb2c46525`의 집중14개·MQTT174개/4skip·Gateway370개/1skip·Worker관련469개/1skip를 통과했습니다. 집중14개는 MQTT 전체에 포함하므로 합산하지 않습니다. 새 coherent clone에 한 파일을0644로 교체한 실제 ARM Python3.14.7/QEMU 경합4사례도 통과했고, 소유 thread1→1·외부 효과0·source drift0을 확인했습니다. `drive-binding-reader-local-validation.json`과 `binding-reader-arm-validation.json`에 명령·원 실패·skip·기존 경고를 보존했습니다. 새 원장·영구 cache·lock·dependency·전체 deep copy는 추가하지 않았으며 겹친 reader가 끝날 때까지 이전 작은 snapshot 참조가 살아 있을 수 있습니다.

보드 R50은 R49의 FW23·CPU2·두 부가 서비스 중지·v8 계측을 고정하고 이 reader 한 파일만 시험 소스에 적용했습니다. 지도 점유14.249초, 전체60.598초/지도queue146개였으며 held qualification20초를 채우지 못했습니다. AUTO 제출0·generation1·SAFE_STOP/DISARMED·output=false였습니다. callback→enqueue 보존256개 p95는19.371ms, enqueue→feed 유일대응102개 p95는174.950ms였습니다. R49의21.182/112.899ms와 비교해 전체 지연이 해결됐다고 하지 않습니다. 로컬에서 확인한 불필요한 mutex 대기 제거와 실보드 전체 실패 원인은 구분합니다.

R50 뒤 B1.0.8 dom76/execution `7a148c64-c89e-457b-a259-a565089105de`, CPU0–3·Worker·mock Gateway·두 서비스와 query13byte/lease·자식 종료를 복원했습니다. query context 누계는1245/143/first-2/last0/available=true입니다. archive `82eaab8f1bb44846e1d0c529650c9920e0fdb48293ac20dbf99229543eae18eb`의33파일/2,648,709B를 회수·대조했고 비밀값 검출0입니다. 다음 R51은 운영 priority를 건드리지 않는 시험 cohort nice 비교입니다. 사용자 모음 v10은 R49까지 포함한6,016파일/253,246,494B 전수해시 일치이며 원61개·사용자 파일·이력은 보존했습니다. R50 이후는 다음 checkpoint에 추가합니다.

### R51: thread nice와 autogroup의 구분

R51은 R50 후보를 유지하고 시험 Gateway cohort의 기존 nice 옵션만 -5로 선택했습니다. device·mapper·observer는 작업과 thread 생성 전에 실제 nice0으로 되돌렸습니다. 알려진 Gateway native TID10개의 nice-5/scheduler0, 세 자식의 nice0 및 동일 `/autogroup-9742 nice 0`을 한 번씩 확인했습니다. 운영 PID의 우선순위·Xen pinning은 변경하지 않았습니다. 이 비교는 actor 한 thread만의 효과가 아니며, 짧은 보호조회 자식은 cohort의 nice를 상속할 수 있습니다.

전체45.587초/지도queue58개에서 원25초 점유 확인을 통과하지 못했습니다. AUTO 제출0·generation1·SAFE_STOP/DISARMED·output=false입니다. callback→enqueue 보존201개 p95 5.647ms, enqueue→feed 유일대응75개 p95 50.848ms, actor 마지막181회 wall p95 58.133ms/CPU5.709ms였습니다. 일부 Gateway 경과시간은 작아졌으나 정상 입력·점유가 함께 진행하지 않았으므로 성능 수락으로 세지 않습니다. 가상 장치·생산자가 같은 autogroup 안에서 상대적으로 불리해졌을 가능성은 가설이며, 단일 표본으로 확정하지 않습니다.

보드의 별도 읽기 전용 관측은 kernel6.18.33-v8, autogroup enabled1, CPU controller는 available이나 root/system.slice의 활성 subtree controller는 memory/pids였음을 확인했습니다. [Linux sched(7)](https://man7.org/linux/man-pages/man7/sched.7.html)은 thread nice의 같은 task group 내 효과와 autogroup 전체의 상대 배분을 구분합니다. 문서만으로 이 보드의 지연 몫을 계산하거나 CPU 보장으로 승격하지 않습니다. 새 독립 시험 세션의 group 배분을 비교할 수 있는지 권한·원상 복구부터 확인하며, 전역 sysctl·운영 cgroup을 변경하지 않습니다.

R51 뒤 B1.0.8 dom78/execution `5cb5cff2-a229-4097-bdac-f3e073ceece5`, CPU0–3·Worker·mock Gateway·부가 서비스와 query 정리를 복원 확인했습니다. archive `76c812204b69a4653883f81049494e4f55a6678325573445e483334c28c64410`의32파일/2,130,765B를 전수 회수·대조했고 비밀값 검출0입니다. 원 필수 기록과 실패를 유지하며 정상 주행·반환·세 부하 조건은 계속 미수락입니다.

### 격리 ARM 설치의 SQLite 의존성 후속 확인

이전 고정 캐시의 `libsqlite3.so.0` 부재를 제품 결함이나 설치 성공으로 덮지 않고 보드의 읽기 전용 inventory와 대조했습니다. DB 연결·network·subprocess를 차단한 probe에서 Python3.14.7/SQLite3.51.3, `_sqlite3` SHA `f178c827192e115c4e6431a0ca60536cda09060dddbd7243da446249248a57d0`를 확인했습니다. 이는 고정 RPM에서 추출한 확장과 같습니다. 기존 보드 library `/usr/lib/libsqlite3.so.3.51.3`(1,525,864B/0755/ARM64/SONAME libsqlite3.so.0) SHA `a7b12fd67093ab9889bfe1eab6b70b07f463f8268a201878b515b4903a0d00e8`만 새 PC fixture로 회수했습니다. DB·자격은 읽거나 전송하지 않았으며 운영 파일 변경도 없습니다. RPM DB를 읽지 않아 이 library의 package owner는 unknown입니다.

새 coherent root의 기존138개 제공물에 이 library와 상대 SONAME 링크만 더했습니다. Binding4f5/common8e 및 모든 원 payload bytes/mode를 확인했고, 필요한 SQLite심볼85개·외부 strong심볼89개의 고정 의존성을 대조했습니다. 실제 ARM/QEMU에서134개 Python compile·제품101모듈/stdlib15개·API/log/main의 명시적 mock assembly가 통과했습니다(26.444초). 실제 reader 경합4사례(2.639초)와 별도 `:memory:` SQL fixture1개(0.684초)도 통과했습니다. 메인 import audit의 외부 효과 시도0·thread1→1, 실제 로드된 library가 새 격리 root 사본임을 확인했습니다. 원 실패 root와 `/proc/self/maps` 경로를 과도하게 resolve한 첫 probe 오류도 보존했습니다.

최종 근거는 `combined-sqlite-complete-arm-evidence.json` SHA `b72a37588af49eb67204d16bddef5d8c89626b11888ec5e60384be04f909ab83`입니다. 이는 보드 설치 binary로 보완한 격리 ARM 검증이며 canonical RPM/package QA·전체 이미지·API lifespan/운영 서비스·실차 수락은 아닙니다. 제품 dependency/recipe 버전은 변경하지 않았습니다.

### R52–R53: 시험 그룹 배분과 중복 서비스 조회

| 시험 | 실제 관측 | 구분 |
|---|---|---|
| R52, 자기 새 session의 autogroup만0→-5 | 점유8.244초, 전체48.089초/지도queue72, AUTO0·generation1·SAFE_STOP/DISARMED·output=false. 원20초 준비 확인 중 `TimeoutExpired` | 자기 SID/PGID·단일 thread·부모와 다른 group을 확인한 뒤 적용했고 종료 시 같은 group0 readback 완료. 운영 priority/전역 sysctl·cgroup 변경0. 정상 수락은 아님 |
| R53, R52 조건 + 중복 systemctl 조회만 제거 | 점유2.765초, 전체45.414초/지도queue82, AUTO0·generation1·안전 상태 유지. 원20초 held qualification 대기의 `TimeoutError` | 불필요한 시험 관측을 제거한 뒤에도 제품 입력의 지속 유효성이 부족함. 단일 표본·서로 다른 tail을 일반 성능 개선율로 확대하지 않음 |

시험 driver는 `pi_port==18886`을 먼저 강제한 뒤, 반복 `physical_guard()`에서 `systemctl is-active` 결과와 `pi_port!=18886`을 함께 검사했습니다. 따라서 지원 모드에서 그 반환값은 거절에 쓰일 수 없지만, 조회 timeout은 원 예외로 전파됐습니다. 원 source/포트 fence를 실행한 독립6경계와 새 후보18개 집중 회귀로 확인했습니다. 새 `board_isolated_guard_timeline_trial.py` SHA `543a403d18847474b0ff647f289086b541ecc0eff61b8e8d148d8cbd225c3232`는 그3줄만 제거했습니다. 포트·USB/ACM·PTY 독점/FD·Authority·부모의 mock 정지/복원·원 기한은 유지합니다. R52 실패의 실제 cmd는 저장되지 않아 이 조회를 가장 강한 추론으로 분류하며, 제품의 clock 지연 원인과 합치지 않습니다.

R52 callback→enqueue p95 2.926ms/유일한 enqueue→feed p95 37.699ms, R53은3.641/54.252ms였습니다. 정해진 tail·조건 안에서 얻은 계측 포함 값이며 queue/kernel/GIL의 개별 지연 몫이나 운영 상한이 아닙니다. group 적용·복원 증거의 complete와 전체 주행 passed는 별개입니다. R53의 준비 전용 memory parent 초안은 한 번도 실행하지 않았고, 정상 재시험으로 이름을 재사용하기 전 정확7파일/109,140B와 SHA를 `history-r53-memory-pending-before-guard-retry`에 보존했습니다. 그 보조 시험의 로컬 수락을 실보드 결과로 세지 않습니다.

R52 뒤 B1.0.8 dom80/execution `939f1d93-4550-4405-890c-d2aa4d3f84f9`, R53 뒤 dom82/execution `c01def2b-96f8-4563-ae5c-1cc8f67ba7f1`와 CPU0–3·Worker·mock Gateway·부가 서비스·query lease/자식 종료를 확인했습니다. R52 archive `da93872e0d6cb3d2e272a86aae2774474932cde375ad6f5ce992b594ed30ea17`(33파일/2,485,174B), R53 `a11ec7777dfdc71b146e0d6aa86777f988a41a7e7a1512f29e94f8ebeb6d804d`(33파일/2,427,839B)를 회수·전수 대조했고 비밀값 검출0입니다. A/Debian·원 자격·필수 기록은 유지했습니다.

### 기존 C clock 표본의 범위와 다음 계측

`r52-clock-width-audit.json` SHA `d2adb10d7173c4ccad5461cd1ed6bcaa89de11714f6685376da4b2d73f1a0f33`은 실제 FW23/config·a76 source·원 raw offset/strict context를 대조했습니다. raw16행 중 확인 구간6행을 분리하고, 신원 거절 phase1의 g0를 reply echo와 같다고 가정하지 않았습니다. 확인된 phase2의 residual full width는43.113/100.230/126.147/392.290ms, phase5는11.691ms였습니다. 판정식은 `(g3-g0)-(p2-p1)`이며 실제 한도20,000us는 half-RTT가 아닙니다. Pi 처리26.410~77.093ms는 이 식에서 이미 상쇄됩니다.

해당 표본의 C callback 진입→판정은191~346us였습니다. 네 시각만으로 C 송신 전·전송/broker·guest pump·Pi stamp 뒤 socket 대기를 각각 분리하지 못합니다. guest state owner(priority12)는 poll0→probe/상태 작업→sleep2ms이며, 별도 v1 IO(priority8)의 wakeable10ms와 구분합니다. 다음 후보는 원 값·기한·poll 주기를 유지한 진단 전용의 bounded phase metadata와 별도 읽기 전용 `xenova_clock` 명령입니다. 기존 `dl_report`는 종료 뒤 전용이므로 현재 `xenova drive` 조회가 이 새 표본을 이미 제공한다고 주장하지 않습니다. 이 문단 작성 시 새 C 계측·FW 후보·명령의 보드 수락은 미수행이며 정상 주행·반환·세 부하 조건은 계속 미수락입니다.


### R54 준비: 같은 원 clock 요청의 송신·pump 계측

새 C `lattice_input.c` SHA `f52651d120d38c1ca9b30fa8ac381780b0db263a7c8eb0c13c3cbd982e5c8c30`는 INPUT 진단 설정에서만 원 probe의 encode/TX·state pump·다른 송신 구간을 작은 metadata로 보존합니다. 기존 오류·송신 결과·시각·기한은 그대로이며, 추가 clock 읽기 실패는 별도 진단 오류로 남습니다. state owner가 쓰고 shell은 slot CAS로 한 행만 복사·반납한 뒤 출력합니다. 첫2·완료 tail4·유효 width peak2의 8슬롯이며, `no_reply`를 네트워크 미송신의 확정 근거로 해석하지 않습니다. 알려진 값의 presence mask가 없는 raw0은 성공·관측0이 아닙니다.

동일 단일 source SHA가 base/diagnostic ARM snapshot·receipt·FW28 manifest에 있음을 대조했습니다. aggregate fingerprint `48dafee6180124aba79a87639ea7bbf83293948cd8af5df68139324119484716`는 파일 전체 목록의 해시이므로 단일 C 해시와 구별합니다. base→전체 INPUT 진단의 text +10,201B/data -1,992B/bss +4,103B는 기존 진단도 포함한 차이입니다. row360B·`ct_shell` 직접 frame560B/예약 shell4096B이며 전체 호출 체인의 최고 스택 사용을 측정한 것은 아닙니다. shell priority14·TX ring1024B·TX timeout500ms를 유지하고 느린 콘솔의 CPU/출력 대기까지 제거했다고 하지 않습니다. 질의는 시험 소유자 종료·CPU4 복원 후에만 실행합니다.

Pi의 새 시험 전용 collector는 이미 해석된 원 probe의 VM/execution/app/sequence/g0/p1을 같은 p2와 Paho의 실제 QoS0 완료 콜백에 연결합니다. 512행·손실/미확정이 명시되고, actual Paho2.1·격리 broker·부분 송신/EAGAIN·재접속·원 오류·정상 회복 등35개 집중 회귀가 통과했습니다. `on_publish`는 소켓 송신 제출 관측이며 broker·guest·장치 수신 확인이 아닙니다. 선택 clock의 시간 읽기·metadata·기존 callback mutex/ReasonCode/Properties dispatch 비용이 추가됩니다. actor/socket 소유자 join을 확인한 뒤 저장하며 원543a driver의 off 경로 전체 AST를 보존합니다. 동결은 `pi-clock-send-freeze.json` SHA `dae7e75aa5ad8fc76e889ce1143c4249e08599319bdbc65c09ad5658c8873dd3`입니다.

FW1.0.28 archive `6887b27e1ac862e3f7c52e0dc7409fc094282677cd0310c1b805754869ef259d`(2,823,303B), manifest `b6b100e9a60ddef52c5a078b2c25afcafa8bbff9e812a95b43506bd4062c29b6`, bin `81a07085d51376910babe3d61f7b8b12b097d90fe3e5ea673de72697714fee32`를 보드의 root 전용 시험 디렉터리에 stage하고 전수 바이트/0444/16MiB·1vCPU를 확인했습니다. 기존 catalog·선택 firmware·운영 설치는 변경하지 않았습니다. 이 절 작성 시 B는 원1.0.8이며, 새 `xenova_clock`의 별도 strict query 수락을 마치기 전에는 R54를 시작하지 않습니다. 기본20ms/원1초 적용/원20초 준비와 출력 보호를 유지합니다. 원 FW26의 C source는 a76이며, 현재 f526로 만들어졌다고 기록하지 않습니다. 읽기 검토 proof `clock-transport-independent-read-review.json` SHA `1fbc39d9b9d30b351dba30ae20e19f27b7bd2cd6f9f0fb752b9744518acc6db5`는 재실행 시험이 아닙니다.


<a id="clock-r54-20261107"></a>
### R54 실제 clock 구간·새 명령 수락·복원

R53과 같은 source4f5/common8e·16MiB/1vCPU·pCPU2·원20ms clock width/20초 준비/1초 적용 한도에서, 진단 C와 Pi 관측만 결합했습니다. 실제 점유는 world 입력 활성 후5.048초에 확인했습니다. 표본의 used/high-water는 maps26,824/53,648B, paths10,512/10,512B, search27,312/27,312B였습니다. 원 계산·pool 사용 근거이며 고정 예약 RAM의 OS 반환이나 입력 만료 후 전체 반환을 확인한 것은 아닙니다. 전체47.783초/지도 queue103에서 held qualification이 끝내 충족되지 않았고 AUTO 제출0·generation1·SAFE_STOP/DISARMED·output=false를 유지했습니다. 정상 주행 수락으로 세지 않습니다.

새 `xenova_clock`는 원7필드 context·lease·45/12/5초·한 번13B 입력을 유지한 별도 helper로 실행했습니다. 로컬36개/실제 PTY 회귀, 최대 parent 결과30,597B<32KiB 및 raw16KiB 초과의 실패·정리 구분이 통과했습니다. 슬롯 간 marker가 섞여도 수락하던 새 parser의 빈틈은 전체0..7/triplet 순서 검사로 보완했습니다. 큰 pre/post PTY 시험의 실패는 외부 stub의 cold-start·부분송수신 경계에서 분리했고 원 query의 기한을 늘리지 않았습니다. 동결 `clock-query-freeze.json` SHA `3ff9bff22a0f24fc8a6ee430b6aca28930b57314ffa19daa40f81072de52e5be`입니다. 실제 B 질의는7,169B·8개 원 record·전체 marker 순서/부분·충돌0으로 완료됐고 helper/console join·lease 반환을 확인했습니다. 질의 완료는 clock/READY/출력 허가가 아닙니다.

| 원 probe seq | C residual width(ms) | 같은 원 요청의 Pi p2→socket 제출(ms) |
|---:|---:|---:|
| 74 | 54.386 | 23.376 |
| 76 | 27.821 | 2.196 |
| 131 | 85.201 | 1.283 |
| 133 | 217.599 | 15.554 |
| 134 | 10.575 | 1.380 |
| 129 | 17.539 | 1.326 |
| 127 | 374.100 | 12.475 |
| 80 | 320.989 | 18.109 |

8개 C 행은 원 VM/execution/app/seq/g0/p1/p2로 Pi의 유일한 한 행씩과 결합했습니다. Pi58행은 error/drop/unconfirmed0·owner join/callback 복원 확인, 전부 publish 반환 전에 실제 on_publish가 있었습니다. p2→on_publish의 median4.652445ms, nearest-rank p95(`ceil(.95*58)`번째)29.360217ms, max31.539831ms입니다. 원 `r54-clock-phase-joined.json`의28.834278ms는 `floor((n-1)*.95)` 방식의 lower quantile입니다. 값은 보존하되 nearest-rank p95와 구분합니다. on_publish는 커널 socket 제출 관측이며 broker/guest 수신 ACK가 아닙니다.

이 8개에서 C encode14~16us, TX 함수164~214us, 최대 poll gap2.474~2.754ms, other_calls0이었습니다. 따라서 probe 이후 다른 송신이 state owner를 길게 막았다는 가설을 이 표본이 지지하지 않아 송신 순서를 바꾸지 않습니다. C TX rc0도 TCP 큐 수락이지 ring/backend 전송 완료가 아닙니다. 고정 Zephyr는 queued send의 -ENOBUFS를 내부 재전송으로 넘길 수 있습니다. 최초 RTO200~299ms와 후속×1.5가 현재 수백ms 지연의 후보이지만 재전송을 실제 확인한 것은 아닙니다. C first/tail/peak8행은 전체 probe 분포가 아니고, boot누계 probes204/no_reply153에는 Gateway 종료 뒤 질의 준비 구간도 포함되어 이를 시험 packet loss로 읽지 않습니다. Pi p1 이전 socket/owner/Paho 대기와 양방향 TCP·broker·Xen/guest 도착 경계도 남습니다. width에서 Pi interval을 뺀 산술 나머지를 단방향 network 지연으로 부르지 않습니다.

사후 읽기 전용 증거는 private broker PID435247/start4365859·SID/PGID435247·thread nice0·autogroup10788 nice0을 확인했습니다. driver는 별도 autogroup11022 nice-5입니다. 한 mosquitto가18885(world)/18886(Pi)를 함께 소유합니다. set_tcp_nodelay true는 이미 요청했고 실제 binary는 `mosquitto -h`의2.1.2/SHA `768b10d0847c54dc3103d94a112212c222621d29632db44f8a17fb58b71579f0`입니다. 설정 요청과 각 socket 옵션의 실제 성공은 구분합니다. lifetime schedstat는 active-phase 지연 증거가 아니며 기존 sampler는 broker PID를 수집하지 않았고 sched_schedstats=0이라 대기는 unknown입니다. 이 두 사후 증거는 R54 archive를 만든 뒤 별도 out/JSON으로 보존했습니다. 다음 대조는 firmware·프로토콜·원 기한을 유지하고 이 소유 broker group만 맞춰 기존 v4에 그 PID를 추가하는 시험입니다. 운영 broker와 정책은 바꾸지 않습니다.

R54 뒤 원 B1.0.8 dom84/execution `7377b9b6-b5ed-4de0-987c-ce7a23c359ae`, CPU0–3·Worker·mock Gateway·두 부가 서비스·원 query/자식 정리를 확인했습니다. archive `65b8d4604359a6945dc57f5ffd435ec340e075e316dbaf8f4ee459e69d4b6ab0`(35파일/2,608,308B; 압축268,509B)를 전수 회수·대조했고 secret scan0입니다. 이후 exact private broker를 종료하고 원 자격 해시/경로/0600을 확인한 임시 사본6개만 정리했습니다. 원 ledger/자격 삭제0·A/Debian 조작0·실제 차량 출력0·운영 설치/commit/push0입니다. 전체 READY·PTY 주행·만료 후 반환과 세 부하 조건은 아직 미수락입니다.


<a id="r54-launcher-guard-erratum"></a>
### R54 시험 런처 검증 정정과 R55 준비

R55 준비 중 root 작성 생성기의 오류를 확인했습니다. source 존재 검사의 `for name`을 고를 때 실제 body 대신 같은 loop 변수명만 사용하여, R54 launcher의 원 산출 경로16개 `lexists` 사전검사를 root 아래 helper 이름24개 검사로 잘못 바꿨습니다. AST 대조도 두 loop의 iterator를 모두 비워 그 변경을 놓쳤습니다. **R54 launcher 전체가 승인된 변경만 보존됐다는 기존 검증 주장은 잘못됐으며 철회합니다.** 원 R54 source·FROZEN JSON·실패 근거는 수정하지 않았습니다.

독립 읽기에서 원 session 보호 함수13개, 같은 key의 기존 결과·key `open('x')`, 원 work/authority `mkdir(exist_ok=False)`, 증거·로그의 배타 생성, source24개 hash·session hash, CPU/optional guard의 개별 사전검사와 실행 중 serial/격리 포트/PTY descriptor/SAFE_STOP 출력 보호는 유지된 것을 확인했습니다. 실제 R54도 출력980개가 모두 STOP·positive drive0, trial thread0, lease/자식·CPU·원 B·서비스 복원이 확인됩니다. 다만 이 결과로 과거의 모든 경로 부재나 덮어쓰기 없음을 소급 증명하지 않습니다. stale/partial 산출물이나 dangling symlink를 VM 변경 전에 모두 거절한다는 사전 보장이 누락된 결함입니다. 운영 경로의 실제 덮어쓰기나 중복 실행을 재현한 것은 아닙니다.

새 R55는 body의 `lexists`와 `is_file`로 loop를 유일하게 구분했습니다. R53의 원16개 경로와 새 broker guard5개 경로, source25개를 각각 첫 mkdir 전에 검사하도록 복원했습니다. 실제 frozen launcher AST만 격리 ext4 fixture에서 실행해 기존 오류1회 재현·21경로의 파일/디렉터리/dangling symlink 거절63회·누락 source25개 거절·정상 빈 경로1회, 총90개를 확인했습니다. 독립 읽기로 실제 iterator·4script/25dependency SHA와 원 CPU4 선복원/owner 정리를 대조했습니다. `r55-launcher-path-regression.json`에 명령 범위와 세 source SHA가 있습니다. 이 회귀는 실제 VM/서비스/네트워크를 실행한 시험이 아닙니다.

R55의 별도 test-only `broker_group_guard.py` SHA `2a87645bdbedee580f3065ad109c8a3ffee804e4d88410e7bc0ecfa62322ea2c`는 exact PID/start/session/group·실행/설정 경로를 확인한 소유 private broker 한 개만 대상으로 합니다. 원 프로세스/thread nice0과 두 포트·firmware·원 기한을 유지하고 group0→-5→0만 비교합니다. 독립180초 watchdog·parent EOF/guard 종료·PID 교체·flush/close 최초 오류·정리·0600을 로컬35개로 검증했습니다. WSL autogroup 미지원은 실제 쓰기 성공으로 세지 않았습니다. 실제 target의 읽기 전용 import/proc 확인에서 thread1→1·외부 효과0·그룹0·아직 그룹 쓰기0을 확인한 뒤에만 시험을 시작했습니다. 일반 로그/필수 저장이나 운영 broker 정책을 수정한 것이 아닙니다. 최종 수락에는 driver passed 외에 broker의 comparison_complete와 복원·join 확인이 모두 필요합니다. 이 절 작성 시 R55는 진행 중이며 결과를 미리 수락하지 않습니다.


<a id="clock-r55-20261107"></a>
### R55 broker 배분 대조: 원복 완료, 정상 기준선 미수락

동일 FW28/source4f5+8e·C/Pi/query·원 기한·가상 장치에서, private broker PID461141/start4639548의 group11412만0→-5→0으로 바꿨습니다. 두 포트18885/18886가 같은 broker를 사용하므로 지도·Pi 전달을 함께 바꾼 대조입니다. process/thread nice0·원 단일 thread를 유지했고 parent/독립180초 watchdog의 ready·applied ACK·명시 원복·join·comparison_complete를 실제 확인했습니다. 기존 v4는 Gateway threads 관측을 유지하며 이 broker PID만 추가 추적했습니다. `/proc` CPU ticks는94→217(100Hz)이지만 sched_schedstats=0이므로 raw 누계의 변화로 scheduler 대기를 추정하지 않습니다.

실제 pool 점유는 입력 활성 후6.784초에 확인했습니다. 표본의 maps used/HWM26,824/53,648B, paths10,512/10,512B, search27,312/27,312B입니다. 전체44.282초/지도 queue70에서 held qualification이 충족되지 않아 SAFE_STOP/DISARMED·generation1·AUTO 제출0·출력 false로 끝났습니다. R54와 총 시간·queue 수가 달라도 단일 대조로 성능 개선율이나 악화를 결론내리지 않습니다. **broker 배분만 바꾸는 것을 해결책으로 채택하지 않습니다.** 정상 READY·주행·만료 후 반환과 세 부하 조건은 계속 미수락입니다.

같은 원7필드로 결합한 C8행에서 width 최대372.627ms, TX 큐 수락 함수171~296us, 최대 poll gap2.458~2.866ms, other_calls0이었습니다. Pi64행 중63개는 on_publish 관측·1개는 latest_slot_replaced 미확정으로 보존했습니다. 완료63개의 p2→on_publish median3.768638ms/nearest-rank p95 42.933873ms/max79.264968ms입니다. 이 callback 시각은 실제 kernel send 시각이 아닙니다. 고정 Paho는 마지막 양수 send 반환 뒤 mutex/dispatch를 거쳐 callback을 부르므로, 이미 넘긴 데이터는 이후 scheduling 중에도 전달됩니다. 실제 seq148은 C width8.576ms보다 Pi callback 관측24.599ms가 늦고, seq86은23.609/28.540ms입니다. 따라서 두 값을 빼서 지연의 필수 포함 구간이나 network 소요를 계산하지 않습니다. R54도 같은 해석 한계를 적용합니다.

새 query는7,168B·8행·부분/충돌0과 lease/console/helper 정리를 확인했습니다. source/기한을 바꾸지 않았고, 원 broker group0·CPU0–3·두 부가 서비스·mock Gateway·Worker·B1.0.8 dom86/execution `6bf9751b-25c3-4b20-83f0-67342d3b454c`를 복원했습니다. archive `44fe73304f9ee24c729c3e390a2bbec3eade9e639bc319003f1f3cc9a6dae2ba`(42파일/2,593,035B; 압축264,600B)를 전수 회수·대조했고 secret scan0입니다. private broker 종료와 원 자격 해시·0600을 확인한 임시 사본6개 정리도 끝났습니다. A/Debian·원 자격/ledger·운영 설치·실제 차량 출력·commit/push는 변경하지 않았습니다.

다음 관측은 추측으로 새로운 배분을 반복하지 않고 기존 설치 도구의 kernel TCP 정보를 사용합니다. 실제 `/usr/sbin/ss`는 iproute2-6.19.0/SHA `30d6d0fd1dda514422fbed44d7f775d9b7096c698c3bd55ac52859574cb596a4`이고 격리18886 필터의 -i/-o/-e/-m 조회가 rc0으로 지원됐습니다. 당시 시험 연결은 이미 닫혀 출력이 비어 있었으며 이를 재전송0이나 기능 수락으로 읽지 않습니다. 새 패킷 도구·CAP_NET_RAW·본문/자격 수집 없이 별도 유한 sampler를 준비합니다. fixed TCP/RTOS 경계는 `r54-tcp-source-boundaries.json` SHA `e705e5d8fa74971bb2e9c4e61b7e67ba62829c185b2b1d086a7421bb2434a5fd`에 실제 source/config full hash·함수·행과 미확정 가설을 기록했습니다.


<a id="clock-r56-20261107"></a>
### 2026-11-07 R56 — host TCP 관측과 종료 단계 오인 분리

R56도 정상 READY·PTY 주행·입력 만료 뒤 메모리 반환 baseline은 통과하지 못했다. 61.027초 동안 지도139개를 공급했고, 실제 지도·경로·탐색 풀 점유는 원 표본에서 증가했다. 정상 고부하·A만 지연·양쪽 공통 입력 한계의 세 부하 조건은 아직 실보드 미수행이다. 실패를 정상 완료로 바꾸거나 시각·작업 한도를 늘리지 않았다.

| 실제 연결·후보 | 확인 근거와 한계 |
|---|---|
| 같은 제품 후보 | FW1.0.28 manifest `b6b100e9a60ddef52c5a078b2c25afcafa8bbff9e812a95b43506bd4062c29b6`, bin `81a07085d51376910babe3d61f7b8b12b097d90fe3e5ea673de72697714fee32`; C/Pi clock·query와 MQTT8e0a/Binding4f5a 후보 유지. 원 R54 profile인 private broker group0으로 돌아가 ss 관측만 추가했다. |
| 시험 실행 | B dom87 / execution `35b77e71-8c90-4c51-8a45-c6b0d7999e78`. 같은16MiB·1vCPU·pCPU2, dom0 Linux CPU2·optional 서비스 유한 중지·원 guard·예산·지도·PTY 조건 유지. |
| 실제 계산·점유 | world enabled48719.159599→점유48735.562496:16.403초. `drive_maps`: seq301 used/HWM0/0→seq392 26824/53648B, `drive_paths`0/0→10512/10512B, `drive_search_nodes` seq312 0/0→seq392 27312/27312B. 같은 guest boot/publisher의 원 표본이다. 반환·완주 근거는 아니다. |
| 새 host 관측 | 기존 target `ss`6.19.0, binary SHA `30d6d0fd1dda514422fbed44d7f775d9b7096c698c3bd55ac52859574cb596a4`; test-only sampler `0769392d7160fc1229869d20728b6836c69edab55eae0a0108a5bfe3a07963db`, freeze `584b1557f3803e8d87456f1dec46616e8210ebb75be61e213992aa7abd7e18d9`. local26PASS4.010초·절대기한 수정 독립 확인 뒤 실행. |
| 관측 범위 | 원 부모 end를 넘겨1Hz 이하로 격리18886 TCP metadata만 수집. packet/credential/body·raw capture·새 의존성 없음. 47sample 중44observed,3CALL_TIMEOUT이며 ss child 전부join·collector 수명 완료. 결과 SHA `e73a50c76cbabaf6735ec955c0b7e259431ad4278e1433f2a084bc7a0b828e4a`. 1Hz는 짧은 정체를 놓칠 수 있고 관측 비용도0이 아니다. |
| 실제 socket 대응 | broker 연결 metadata와 `lattice_input.state()`를 함께 대조했다. B49572=`…-state-62bc…`는 binary Clock/StateApplied, B56078은 공통v1 runtime/control/events, B63277은 telemetry. host54551은 Pi DriveLinkIO이다. 명칭이 아닌 실제 client ID·호출자로 구분했다. |
| 큐·재전송 | Clock socket의 broker Recv-Q 최대390B. 공통v1·telemetry Recv-Q 최대10640/25849B는 별도 흐름이다. 16201B는 **Pi DriveLinkIO→broker**의 broker Recv-Q이고 Gateway client Recv-Q는196B, Send-Q는1276B이다. 일부 host↔host 흐름의 `bytes_retrans` 누계 증가도 관측했으나 개별 ClockProbe 원인으로 귀속하지 않는다. guest 방향의 해당 필드 부재는 unknown이며0으로 채우지 않는다. |
| C/Pi 원 시각 | C8행 모두 원 VM/execution/app/seq/g0/p1/p2에 맞는 Pi 관측1개와 결합. Pi clock_offers85/진단수명 완료. C 폭은 첫/꼬리/peak 편향 표본이며 최대307.019ms. 성공8250/7479µs 외 >20000µs인6개는 기존 -ETIME. 상행·하행 offset 경계 변화가 각각 관측돼 한 방향의 원인으로 확정하지 않는다. |
| 원 보호·실제 효과 | generation1 SAFE_STOP/DISARMED/outputfalse, 보존된serial1366표본의 source는 모두None(STOP 경로). 종료 STOP은generation2이며 앞선 정상 주행 성공이 아니다. 실제 차량 출력0. |

추가로 종료 단계 fixture 오류를 분리했다. `SAFE_STOP_BOOTSTRAP_CHANGED`는 준비20초 대기가 끝난 뒤 cleanup의 보호 요청이 만든 PENDING을, 남아 있던 `select_auto` 분기가 예상 밖 상태로 취급하는 순서였다. R56의 대기 끝까지 actor2604회는 DISARMED로 관측됐고 마지막end48755.590795 뒤 cleanup 요청48756.531·gen2 STOP48756.609959가 나온다. 따라서 이 부수 오류가 준비 대기 중 actor를 먼저 죽였다고 해석하지 않는다. 원 TimeoutError·thread 실패·Authority 기록을 모두 보존하며, 새 fixture 후보에서 종료 선택의 수명만 국소 보완한다. 제품 보호·실제 준비 조건을 완화하는 수정은 아니다.

Clock의20ms 폭/1초lease가 실제 준비·pose gate로 연결돼 있다는 근거와, 전체 실패의 단독 원인을 확정했다는 주장은 구별한다. R55 private broker group 대조는 해결책으로 채택하지 않았다. 다음 좁은 관측은 기존 Pi binary RX owner의 `_network_once` 진입 gap·wall·thread CPU이며 원 처리·반환·기한을 유지한다. 새 우선순위·pool·retry·한도 변경부터 하지 않는다.

R56 정리·복원은 끝났다. query13B의8개행·응답 순서·원 lease 반환·console/helper join, CPU4 선복원, optional 서비스·operating mock Gateway·Worker 복원, B 원1.0.8 dom88/execution `718d773c-8ddc-4680-a238-f7feb538c2ec`·CPU 검증을 확인했다. 원 A·Debian·자격·Xen 정책은 변경하지 않았다. private broker480708/start4846748 종료 뒤 root0600인 임시 자격6개만 원본해시를 대조해 정리했다. backend가 참조하는 source-r56과 필수 기록은 남겼다.

원자료는 `evidence-through-r56.tar.gz` SHA `99d94900a496552678af46f5ba6e0a5eb1cd169d6d6768b894fd402ff227a18a`, 39파일/3,978,304B이며 전수 hash·실제 credential scan0을 확인해 회수했다. 후속 정리 stdout은 `r56-broker-closed.out` SHA `e2d4dc25e97606163c99bb193d1c2b7ac859bf1c9f561b8ae3d443b4982c3f8c`, `r56-credentials-cleaned.out` SHA `f48e1a55435778b3f353f4acdcc763408737535d909230d99f00169dc3242ba5`이다. 전송·실행·회수 명령과 소스 SHA는 각 wrapper json에 보존했다. source/로컬/ARM/QEMU의 앞선 검증 범위는 그대로이며 정규 이미지·운영 배포·7번 전체 수락·commit/push는 수행하지 않았다.


<a id="clock-r57-20261107"></a>
### 2026-11-07 R57 — 상세 시험 계측 비용 대조, 준비 실패는 잔존

R56의 상세 계측14,543개 사건에 제품 처리와 중첩 관측 비용이 섞여 있어, 같은 제품/FW28·Pi clock·C clock·ss·CPU2·optional 중지·broker group0·입력·기한에서 기존 CriticalTiming만 비활성화했다. 이 flag는 literal `off`를 허용하지 않으므로 전용 child env를 만든 직후 `env.pop('XD_TEST_CRITICAL_TIMING',None)`로 기존 지원 계약인 부재를 강제했다. 부모 `os.environ`이나 제품 parser는 바꾸지 않았다. 실제 새 interpreter에서 frozen driver를 import하고 원 설치 분기를 실행해 critical helper import/생성0을 확인했다.

종료 fixture는 별도 동결 후보 `board_clock_send_cleanup_trial.py` SHA `c97bb83f0c307bdcdcf238a3b8c618605f1f4f6b78fcb021b4667cbe8347f861`을 채택했다. 원69dd driver의 전체 AST와 비교해 cleanup 요청 직전 `select_auto.clear()` 1호출만 다르다. 실제 machine·Authority·G1 queue 로컬5PASS와 독립 읽기를 거쳤다. 종료 시점의 보완을 baseline 중 동작 개선으로 합치지 않는다. 나머지 원18경로·25개 의존성·원 절대 end·CPU4 선복원·원 key/소유/기한은 그대로다.

| 실제 결과 | 해석 |
|---|---|
| B dom89/ex `8aa1289f-ca82-4455-83e1-5e7ce7f7dd16`, driver46.087초/maps79 | 실제 지도/경로/탐색 점유는 확인했지만, 그 뒤 `prepared-state-auto-selected` 20초 대기는 TimeoutError. 원 result의 `failure_stage=actual-map-search-path-occupancy`는 마지막 성공 단계 표시이므로 그 단계의 점유 실패로 잘못 읽지 않는다. 정상 READY·주행·입력 만료 뒤 반환은 미수락이다. |
| CriticalTiming 결과 null, 원 source/env pin 확인 | 상세 계측이 빠진 실제 경로를 사용했다. Pi/C/ss 및 정식 출력·필수 저장은 유지했다. |
| world→pool16.403→8.610초(R56→R57) | 단일 대조의 관측이다. 트래픽·스케줄 차이도 있어 전부 계측 제거 효과로 귀속하지 않는다. |
| pool 뒤 held20 안의 온전한 CPU19창씩, 실제 분모19.054836/19.121595초 | Linux core 상당량: Gateway .754139→.666262, actor .260301→.223308, serial .174234→.148000, common MQTT .065075→.039223, DriveLink .077671→.080014. 감소분과 변동을 구별하며 process와 thread를 더해 중복 집계하지 않는다. |
| 같은 held20 Pi 완료 관측38/37개 | P2→on_publish 중앙2.883→2.023ms, nearest-rank p95 23.683→28.470ms, 최대40.113→37.878ms. 중앙값만으로 꼬리 지연 개선을 선언하지 않는다. on_publish는 실제 socket send 시각이나 RTT의 일부를 확정하는 시점이 아니다. |
| C 원8행 재파싱·7-key 결합 | R56 최대307.019ms, R57 최대389.961ms이며 R57 held20 안에도388.708ms 표본이 남았다. first/tail/peak 보존8개를 전체 분포로 쓰지 않는다. 이전372.627ms는 R55 값이다. |
| 종료 thread failure0, 원 TimeoutError 보존 | cleanup 후 gen2 SAFE_STOP/DISARMED 관측과 합치한다. cleanup 오인은 해소됐지만 앞선 gen1 준비 실패는 그대로다. 전체61.027→46.087초에는 cleanup 변경도 섞여 있다. |
| ss34sample/32observed/2unknown | collector/ss child join은 완료. socket 정보 수집 성공과 통신/READY 수락은 다르다. schedstats 비활성이므로 scheduler 대기는 unknown이고, ss sampler와 짧은 child의 CPU는 직접 계수되지 않았다. |

이 대조에서 확인한 범위는 선택적인 상세 계측 부담을 줄여도 잔여 지연이 남는다는 것이다. 이를 근거로 timeout·메모리 풀·우선순위부터 올리지 않는다. 다음 후보는 기존 Pi binary RX owner의 원 호출1회에 gap/wall/thread CPU의 작은 metadata를 덧붙이며, 이미 수신하는 v2.1 network raw32 표본을 보존하는 시험용 경로다. 별도 로컬 동결 RX helper는 아직 R57에 통합·보드 실행하지 않았다. network의 envelope seq·송신 uptime과 app_memory의 표본 seq·관측 uptime을 혼용하지 않는다.

R57은 원 B1.0.8 dom90/ex `a437e698-5238-4bf2-b70c-9078c69d8bf5`와 CPU 검증complete, CPU4·Worker·두 optional 서비스·operating mock Gateway·console/query lease/helper/소유 thread를 복원했다. 원 A·Debian과 실제 차량 출력은 변경하지 않았다. private broker496748/start5015226을 종료하고 원 credential hash를 대조한 임시 root0600 사본6개만 정리했다. backend가 참조하는 source-r57과 원 기록은 보존했다.

원자료 `evidence-through-r57.tar.gz` SHA `9248c8649ed5cdf37aa7412f2d37618c8cd17f6e9c385fcbdb91cb70deb3830c`, 37파일/2,370,482B를 전수 hash·비밀0으로 회수했다. `r57-status-4.out` SHA `48e3ab1d2d309fbd2272a86633de5740c74670d1cadab7d28940a86d9bc651bf`, broker 종료 `56e4ac354982100fc0e12e9a65ca8711190b5921571d540eb161a4d67f005bb3`, 임시 자격 정리 `f48e1a55435778b3f353f4acdcc763408737535d909230d99f00169dc3242ba5`로 후속 정리를 연결한다. 준비 builder의 최초 검증 직후 추가된3개 hash 검사문은 런타임4파일을 바꾸지 않았다. 원315acf…와 정확히 일치하는 역사 사본, 현 builder, `r57-builder-post-validation-note.json`을 함께 보존했다. 실제 stage/launch는 동결4파일과25개 소스 hash를 대조했다. 운영 설치·정규 이미지·실차·7번 전체 수락·commit/push는 수행하지 않았다.

<a id="clock-r58-r59-20261107"></a>
### 2026-11-07 R58–R59 — 수락 전 전체 기록 검사와 시험 실행의 구분

R58은 RX owner의 원 `_network_once()` 호출을 바꾸지 않고 wall/thread CPU/호출 사이 gap을 제한된 metadata로 관측하는 시험 후보였다. 기존에 받던 network 표본의 envelope seq·송신 uptime·collection과 raw32 상태도 보존했다. driver `04ac2884ee965a9f972dffb4d05635aaf4e5a257c362dcc50389794d09235e31`, helper `e4490dc75ad2433c0f09b2a0e6298da1a4c0bde8e4cad69ff17b41a0f977bfbc`의 실제 Paho/codec/native 표본 로컬33PASS 및 target Python3.14.7 import의 thread1→1·FD4→4·socket/process 효과0을 확인했다. 이는 실제 RX 지연 구간을 보드에서 관측했다는 뜻은 아니다.

두 실행 모두 `test-stop`의 내구 수락 전에 `DEADLINE_EXCEEDED: control request scan budget expired`로 거절됐다(R58 2.045초, R59 2.084초). operation_id는 null이며 시험 실행·driver·console·ss sampler가 생성되지 않았다. 미생성 자식의 null을 join 성공으로 채우지 않는다. 원 B1.0.8 dom90/ex `a437e698-5238-4bf2-b70c-9078c69d8bf5`는 그대로이고 CPU4·Worker·서비스 복원을 확인했다. 따라서 이번 두 실행을 기존 binding/clock 문제의 재현이나 해결 근거로 세지 않는다.

공개 `control-operation`의 원 key 조회는 완전 검색 후 `VM_NOT_FOUND`/operation_id null을 반환했다. R59는 R58의 네 역할 key를 바이트·값 그대로 재사용했고 새 key·원 기한 재발급·추가 자동 DEADLINE retry는 없다. R59에도 같은 수락 전 거절이 재현돼 반복 시험을 멈추고 검사 비용을 분리했다. 읽기 전용 두 표본에서 등록3개 약0.059–0.063초, 자산 operation19개 약0.210–0.274초, control232개 약1.259–1.407초였다. 각 검사는 기존 stop admission2초의 일부이며 profiler 자체 비용이 있는 측정과 구별한다. 실제 `_json` C accelerator는 제공·선택되어 있어 누락 원인으로 보지 않는다.

한 번의 읽기 전용 cProfile은 같은 파일명에 대한 반복 Path.stem/name 해석과 `_text_token`의 문자별 Python generator 비용을 확인했다. 전체 열거·record decode·request hash·중복 key·FD/경로 안전·기한 검사는 유지하며 이 두 국소 계산만 줄이는 후보를 검증한다. 아직 실제 board 전후 절감이나 정상 baseline 통과를 선언하지 않는다. 원자료 `r59-scan-profile.out` SHA `44365349168290d419f22834b1eb551f803f24dd753866078ec63de5cd245775`와 전체 명령·소스 SHA를 wrapper 기록에 남겼다.

R58 archive SHA `6b7d22a2e5d3f79281425a5a3c5da7bc947e85fcf330d48ffae672506543245d`(10파일/15,209B), R59 archive SHA `c143f6f7ad288decf16a081b9caa6da751387efe3de623751419d1f1e12be695`(9파일/21,215B)을 전수 hash·비밀0으로 회수했다. 각 private broker 종료·원 credential hash 대조 뒤 root0600 임시 사본6개만 정리했다. R59 정리 도구의 첫 시도는 이전 숫자 range를 남겨 이미 정리한 R58 파일 부재로 삭제 전에 실패했으며, 원 소스를 보존하고 범위를59로 바로잡아 정리를 마쳤다. 원 key·필수 원장·운영 자격과 backend가 참조하는 source-r57은 보존했다. 실제 차량 출력·운영 설치·commit/push는 수행하지 않았다.


<a id="clock-r60-r61-20261107"></a>
### 2026-11-07 R60–R61 — 검사 비용 보완·로컬 복구 의미·진단 후보와 실제 수락의 경계

R58–R61의 네 번 모두 원 stop admission 2초 안에 전체 control 기록 검사를 끝내지 못해 **내구 수락 전 안전 거절**됐다. R60은2.059초, R61은2.092초에 `DEADLINE_EXCEEDED/control request scan budget expired`, operation_id=null이었다. 원 R58 네 역할 key의 바이트·값을 그대로 재사용했고 새 key·기한 연장·자동 재시도를 추가하지 않았다. 시험 guest/driver/console/ss/query는 시작하지 않았으므로 이 결과는 RX 지연 재현이나 해결이 아니다. 원 B1.0.8 dom90/execution `a437e698-5238-4bf2-b70c-9078c69d8bf5`·CPU0–3·Worker·서비스 복원을 확인했다. 보호는 유지됐지만 **수락 가용성은 미해결**이다. `r60-status-full.out` SHA `772f2db133d39e9d98206c8f93da4a25843200a0cf290145a1b6fb84562eaf56`, `r61-status-full.out` SHA `d85c79789c2e31410edee6f68d583addbf57ecf58ba09ec3fbeefb6233486439`가 근거다.

전체 열거·JSON/schema·request hash·중복 key·원 reader hook·FD/경로/regular/nlink·신원·기한과 효과 guard는 유지하며 세 계산만 줄였다. R60은 열거에서 이미 얻은 파일명 신원을 재사용하고 C0 문자 검사를 동등한 사전 컴파일 regex로 바꿨다(Execution `fbd91c35…`, Management `0e2d62b0…`). R61은 검증된 builtin FD reader에 이미 확인한 leaf 문자열을 직접 전달해 record마다 Path를 다시 만들지 않는다(Execution `30184aa8…`; custom reader에는 기존 Path 유지). 앞선82개 회귀12.07초와 leaf 후보82개11.72초·별도 차등1개/7분기1.58초를 통과했다. 겹치는82개를 새 수락 개수로 합산하지 않는다. `control-scan-cost-local-validation.json` SHA `d420eafb6b165fc6da1d8065a5775188723afe31ee61f3d10d8d2b3d93a46996`, `control-scan-leaf-local-validation.json` SHA `bb43b78203d14109228e94444b1f12f87802b2d560c389d68a5d2ceef0a45fc8`에 원 source·AST·명령·실패 경계를 보존했다.

원 `_accept_stop`의 쓰기/수락만 대역으로 막은 읽기 경계 시험에서도 등록·state·asset·operation·intent 검사 뒤 `requests`가 남은1.549초를 쓰고 만료됐다(전체2.027초). 실제 stop 성공 시험은 아니다. 별도의 Manager 선행 import 없는 A/B와, Manager를 먼저 import한 warm A/B를 구분한다. warm 전후 각3회 모두232개 기록과 결과 SHA `fb8eaf62…`가 같았고 scan median wall1.637→1.500초, CPU1.102→0.972초였다. 선행 import wall3.459–5.159초는 이 scan 시간 밖에 따로 기록했다. 작은 CPU 절감과 결과 동등성은 전체 admission 성공이나 지연 상한을 입증하지 않는다. `r60-stop-read-boundary.out` SHA `aa711397d496fa3108f4f2117978220921986b3d59bde42d2dedef37cbf04a9f`, warm 원문 SHA `3c8b25d59d68e061541bdb73a305e80f69b17ad8490a59c00c226e702a46599b`를 대조했다.

별도 로컬 복구 의미 시험은 실제 전체 `lattice_app.c`와 engine/world/lattice/충돌 검사/tracker/primitives/nanopb를 연결했다. 초기 prepare seq1 뒤 clock 거절·복귀만으로는 새 AppliedState를 만들지 않고, 같은 geometry의 새 map/observation으로 retained path를 재준비하면 plans1·path generation/revision1을 유지한 seq2가 실제 발생했다. 원 prepared 만료의 ms floor 직후에는 seq3 `applied=false/PREPARATION_EVIDENCE_EXPIRED`를 통지하고, 원 pose 수명까지 끝나면 -EAGAIN/추가 enqueue 없음이었다. 이 negative를 성공 준비로 세지 않는다. ASan/UBSan build10.382초/run0.051초·원 wire3개를 실제 Python DriveLink/StateCopies/Warm에 넣은5개 시험을 통과했다. seq2의 현재 proof 복구·같은 seq replay 거절·binding 변경·원 link/lifecycle lease·negative 뒤 proof 철회를 확인했으며3초 held/READY를 꾸미지 않았다. RTOS/context/clock·전송/lifecycle 입력은 명시적 fixture이고 실제 planner thread 배치·보드/MQTT/READY 수락을 대신하지 않는다. `recovery-semantic-local-validation.json` SHA `cb8e4e01f21de2b5de27e665b2c5ee74d53c66c301750b2afb5ff306ccdbef56`, native 원문 SHA `862c903b6012fddafe26145755a5ca5fa8e201d61a0296aba8838f8d3dc59458`에 source drift0과 초기 잘못된 만료 기대값 시험 실패도 따로 보존했다.

FW1.0.29는 frozen FW28 source 복사본에 `NET_STATISTICS=y/NET_STATISTICS_TCP=y`만 추가한 **로컬 진단 후보**다. 고정 Zephyr `684c9e8f…` ARM 빌드33.183초와 실제 packager/bundle/catalog 소비를 확인했다. config 차이는 통계 계열뿐이며 timer/pool/한도·16MiB/1vCPU·기존 앱 직접 stack frame은 그대로다. FW28 대비 text+8,923B/data+3,544B/BSS0, 실제 stats shell의 최대 직접 frame208B다(전체 call-chain/보드 high-water 상한은 아님). bundle 전 파일0444, manifest `38c68e00b98547b55ba2e23d17dd37cabc2e3e463391ccba5d74223d4124d70f`, bin `e1947bc10650a5fa3fc49adeabd60ad2fc258f0440c5539ab59811ab5c1c34b9`, archive `4f21d2da7c4aee312a1472b99e9e410d698134e4675bc23d9907e40620f32c36`다. 실제 assembly reader는 원1.0.8 lock과 다른 후보를 거절하므로 **기본 이미지·recipe do_install/RPM·운영 선택에는 미반영**이다. 별도 `net stats\r`10B helper는 원45/12/5초·신원/lease/종료를 유지하고 실제 SDK 출력 normal/max/미지원 및 PTY34개 회귀로 동결했다. 부팅 누계·관측된 interface·비원자 snapshot이며 개별 probe/RTO 원인이나 READY 증거가 아니다. FW29 실보드 설치·통계 질의는 아직 미수행이다. `tcp-statistics-candidate-ready.json` SHA `9beeb2cc…`, selection proof `9139ab97…`, query freeze `de0c338c…`에 범위를 구분했다.

공간 정리는 원 데이터를 보존한 뒤 제한된 원격 중복 사본만 대상으로 했다. source-r58/59/60의741개 source는 retained source-r61 또는 검증한 scan before/after의 상대 경로·전 바이트 SHA 일치를 확인했고, 재생 가능한 cache75개와 함께16,605,927B를 정리했다. backend가 쓰는 source-r57·source-r61·필수 기록/원 자격은 보존했다(`superseded-test-source-cleanup.out` SHA `0c0846b3…`). 이미 전수 회수·hash 검증한 archive37개도 원격 사본만16,676,314B 정리했고 로컬 원문과 source tree는 삭제하지 않았다(`recovered-archive-copy-cleanup.out` SHA `b39746e6…`). 별도 source-r27..35 전체 archive 읽기 두 번은90초에 timeout됐고 결과 archive/삭제는 없었다. 두 소유 reader의 실제 종료를 확인했으나 timeout 원인을 공간·압축·CPU 중 하나로 확정하지 않는다. 뒤에 만든 SHA별 blob1회/gzip level1 helper는 로컬 임시 fixture12개만 통과한 미실행 제공물이며 그9개 source를 정리했다고 세지 않는다.

R60 archive `2974e63fa4c46ea98f193216be22630c766655e9b0ca9a191226d67749af9140`(13파일/20,890B), R61 archive `ff3287eb87b3e67b824c4042c653cb9ff3d37f2291f5668e15fc3c7d793dc37e`(12파일/221,672B)을 전수 hash·비밀0으로 회수했다. private broker 종료·원 credential 대조 후 각 임시 사본6개 정리도 확인했다. 위 원자료는 `C:/Users/SSAFY/Documents/Codex/2026-11-06/board-resume-evening`에 보존한다. R62는 optional 서비스를 더 이른 시험 구간에서 분리할 준비 단계이며 아직 보드 미실행이다. 정상 지도 계산의 부분 점유 근거를 전체 baseline 성공으로 확대하지 않는다. READY·PTY 주행·입력 만료 뒤 반환과 정상 고부하/A만 지연/양쪽 공통 입력 한계의 세 부하 조건은 여전히 미수행·미수락이며 실제 차량 출력·운영 설치·commit/push는 하지 않았다.


<a id="stop-request-prevalidation-local-20261107"></a>
### 2026-11-07 — stop 수락 전 제한된 사전 검증: 로컬·ARM 검증 완료, 보드 미검증

반복된 전체 기록 검사의 비용을 줄이기 위해 `xenova_execution.py`와 `xenova_management.py`에 요청 한 번 동안만 쓰는 사전 검증을 추가했다. admission 잠금을 잡기 전에 최대 128개 **열거 항목**과 raw 합계 1MiB만 읽어 검증하고, 잠금 안에서는 기존 전체 열거와 새 안전 FD 읽기를 다시 수행한다. 읽은 raw bytes와 실제 text encoding이 정확히 같을 때만 앞선 JSON decode·request hash 검증 결과를 재사용한다. 바뀐 파일·새 파일·상한 밖 파일은 원 검증을 수행하며, hint는 사용 즉시 제거한다. 부분 사전 관측으로 부재·유일성을 확정하지 않고 영구 cache·index·새 잠금·의존성·원장 삭제를 추가하지 않았다.

전체 열거·NOFOLLOW/NONBLOCK·regular/nlink·부모 경로의 시작/끝 canonical/devino·UUID/schema/request hash·중복 key·현재 신원·효과 보호를 유지했다. custom reader와 module/class/instance/subclass hook은 기존 Path 호출 경로로 돌아가며, prework 뒤 hook 변경과 private core의 unpinned 경로도 재사용을 차단한다. prework의 실제 EIO·손상·경로 오류는 원 실패로 전파하고, 용량 상한과 미지원 hook만 hint 없는 원 검증으로 진행한다. non-JSON 128개 뒤의 정상 파일도 authoritative 전체 검사에서 빠지지 않는 반례를 고정했다.

**시간 계약의 변화:** 내부 admission 2초는 제한된 prework 뒤 한 번 시작한다. 따라서 전체 요청/수락 시간은 2초를 넘을 수 있다. 기존 caller·operation 90초에는 prework가 포함되며, 재시도나 기한 갱신은 추가하지 않았다. `execution.stop_prevalidation`, `execution.stop_authoritative_requests`, 잠금 획득 뒤 `manager.stop_admission`, 기존 전체 `trace.elapsed_seconds`를 구분한다. 전체 요청이 계속 2초 이내라고 주장하지 않는다.

최종 동결 source는 Execution `ac5911d27cc150a41e7661562ad781ee5382140817c8765ecb724c06fb5f0ae3`, Management `8ee90a1e64caafa9e7652f67955bc50c716ac8430dbbbf558358774424e3d39d`다. 로컬 ext4 회귀는 **108 passed/13.74초**(신규25 + 기존83), 실제 aarch64 Python3.14.7/QEMU는 **71 passed/11.82초**(신규25 + 기존45 + 7개 파일명 경계를 포함한 차등1)였다. 두 환경의 겹치는 시험 수를 더해 고유 수락 개수로 세지 않는다. ARM 부분 설치의 compile/import는26.49초, 기존 선택139파일과 두 변경 모듈0644·원 설치본 보존·source drift0을 확인했다. 원138개 recipe 선택 Python/entrypoint와 기존 SQLite 보충1개를 재사용한 부분 포장 검증이며 새 RPM·정규 이미지·보드 지연 증거는 아니다.

[결합 증거 인덱스](C:/Users/SSAFY/Documents/Codex/2026-11-06/board-resume-evening/request-prevalidation-evidence-index.json) SHA `a633bd590cd77952ad40da01f21cd2caa2a940bdf11d7c9aaf9c25ccc4a0c0fe`에 before/r1/최종 source·실행 명령·stdout/XML·QEMU/인터프리터·한계를 연결했다. 로컬 r2 검증 SHA `867d99945178f84b9182ee04598072552c2d9fa2def2fdc5f2032a61b031ad66`, ARM 검증 SHA `36db5e651c78c40d5a70454f9ea5cbba3a7550cb17231a8d5212300c6d8bad42`다. 이 후보의 실보드 stop 수락·정상 주행 baseline·세 부하 조건은 아직 미검증이며 운영 설치·실차·commit/push는 하지 않았다.


<a id="netfront-tx-reap-r2-local-20261107"></a>
### 2026-11-07 — TX 완료 회수 누락의 국소 보완: native 동결, R63 인과·최종 ARM/보드 수락 미확정

실제 `netfront_ring.c::xnf_send()`는 고정 TX 슬롯8개가 모두 busy이면 backend가 이미 게시한 정상 완료 응답을 회수하지 않고 `-ENOBUFS/pool_busy+1`을 반환했다. 원 제품 C를 연결한 반례에서 응답1개 또는8개가 `rsp_prod`에 release 게시된 뒤에도 이 반환을 재현했고, 원 `xnf_poll()` 후에는 즉시 송신이 가능했다. 응답이 전혀 없는 실제 full 상태의 거절은 정상이다. R63의 `pool_busy` 증가3회에 완료 응답이 이미 존재했는지는 관측하지 않았으므로 **이 결함이 R63 지연·held 실패의 원인이라고 확정하지 않는다**. 최초 반례 증거 `tx-reap-counterfactual-local/native-validation.json` SHA `9a3efdd524f5f15214ae906e27b884a90491ce5b4aab3c46292d7c7137e4b7e7`를 보존했다.

최종 제품 변경은 `platform/guests/zephyr/xen-runtime/src/netfront_ring.c` 한 파일이다. private `reap_tx()`(:28)는 원 producer 범위·descriptor generation ID·busy·status 검증과 완료/negative-response counter 처리를 공유한다. `xnf_send()`(:50)는 all-busy 때만 최대8개 TX 완료를 회수하고 `rsp_event` release 게시·fence·producer 재확인 뒤 슬롯을 다시 찾는다. 여전히 없으면 원 ENOBUFS를 반환한다. `xnf_poll()`(:73)은 같은 helper와 원 TX+RX 합계64개 한도를 사용한다. 원 `netfront.c::send()`(:245)의 packet_lock·worker wake와 `loop()`(:187)의 RX 소유는 변경하지 않았다. 송신에서 full poll/RX 전달·새 대기/큐/잠금·페이지/grant/종료·헤더/ABI 변경은 없다. [Linux v6.12 공식 netfront](https://raw.githubusercontent.com/torvalds/linux/v6.12/drivers/net/xen-netfront.c)의 `xennet_tx_buf_gc()`(:365), `xennet_start_xmit()`(:786–802)는 잠금 아래 TX 완료를 따로 회수하는 참고 근거이며, 이 구현의 동일성이나 grant 정책을 대신 증명하지 않는다.

r1 검토에서 새 송신 경계가 프로토콜 오류를 먼저 감지하면 `bad()`는 EPROTO를 반환하지만 후속 worker poll이 ENETDOWN으로 기록하는 분류 공백을 찾았다. r2는 **poll의 failed-latch 반환만 EPROTO로 보존**한다. `failed=true` 대입은 `bad()`(:19, EPROTO) 한 곳이고 init(:43)의 false 초기화 외 다른 대입은 없다. 후속 send의 기존 ENETDOWN 계약은 그대로다. 반복 poll은 원 오류를 다시 집계하거나 인덱스를 움직이지 않는다. 기존 backend-error 우선순위는 유지되며 전역 시간순 최초 오류 보장을 새로 주장하지 않는다. 원 ring overflow 송신에도 있던 패턴이지만 새 TX 검증 경계의 분류 손실은 최종 수락 전에 보완했다.

native x86_64 실제 제품 C 회귀는 r1 45건, 최종 r2 **47건을 ASAN/UBSAN과 O2 각각 통과**했다(최종 compile+run 0.796초/0.562초, stderr0). 두 실행의 중복 건수를 합산하지 않는다. 기존32건·70,000-frame descriptor/ID wrap과 추가 send-side 70,000회 재사용, UINT32 producer wrap, 정상 완료1/8개, 진짜 full, 잘못된 ID/index/status·중복 ID fail-closed, negative RSP drop, 송신 중 RX 미소비, 원64개 poll budget 및 corrupt-send/initial-bad-poll 뒤 반복 EPROTO·counter1·인덱스 불변을 확인했다. 외부 backend 게시·잠금 직렬화는 명시적 native fixture이며 Zephyr thread/실제 ARM/TCP 시간 시험을 대신하지 않는다.

최종 C SHA `7310ab54a1a9cca508f00ce5e79e62286e50aaa3c6a405cd2a8995425a26486f`, native test SHA `e424abbe9d6057cd5c99921e69945ac291334c80d9d01ca3a91a945d008ac161`다. `tx-reap-fix-local/FREEZE-r2.json` SHA `fd1fbd93a54f217d0a1be5f08873e2334072229b5625ff4f280e067567b00f2c`, `native-validation-r2.json` SHA `9d2f64bdae0549211b412895ed6303cf86bac28f9d5b8318bd4c118b0de87b02`에 정확한 명령·전후 source SHA·setter 근거를 연결했다. r1 C/test 원문·diff·45건 증거·FREEZE와 최초 반례는 별도 바이트로 유지하며 r1을 최종 수락본으로 세지 않는다. 본 절 작성 시 최종 r2의 FW30 ARM 포장은 다른 담당자가 진행 중이며 **빌드 수락·보드 적용·정상 baseline·세 부하 조건 통과를 선언하지 않는다**. 이 작업의 SSH/보드/commit/push는 없다.


<a id="clock-r64-retained30-20261107"></a>
### 2026-11-07 R64 — FW30 실제 통계·수락 경계·유지 상태, 정상 기준선은 미수락

R64는 제한된 사전 검증의 최종 Execution `ac5911d27cc150a41e7661562ad781ee5382140817c8765ecb724c06fb5f0ae3` / Management `8ee90a1e64caafa9e7652f67955bc50c716ac8430dbbbf558358774424e3d39d`와 FW1.0.30을 시험용 경로에 공급했다. 실제 test-stop은 operation을 수락해12.851초에 끝났으며 이 시간에는 물리 종료가 포함된다. 별도 읽기 전용 비교의 scan 시간과 같지 않다. 그 비교는 동일236개 원장·같은 hash와 원 reader/원장 불변을 확인한 표본 한 쌍이다. 이전 후보는 total1.830초/lock 대기0.008초/hold1.820초에 읽기 경계를 완료한 뒤 쓰기 차단용 NoAcceptance로 끝났고, 새 후보는 total2.888초/대기0.393초/hold1.607초에 **DEADLINE_EXCEEDED**였다. 새 검사가 완주했다거나 수락 가용성이 보장됐다고 쓰지 않는다. prework를 포함한 전체 요청과 내부 admission2초·기존 caller/operation90초를 구분하고 기한을 갱신하지 않는다. 비교 원문 SHA `e62e0c7c2063011d2e94c815d32b4b15cd458eabfa58af6bd292fbf510d60d20`를 보존했다.

FW30은 정확한 FW29 source에서 `netfront_ring.c` 하나만 `7310ab54a1a9cca508f00ce5e79e62286e50aaa3c6a405cd2a8995425a26486f`로 바꾼 후보다. 송신 슬롯이 모두 바쁠 때 TX 응답만 최대8개 회수하고 RX·재연결·새 잠금·대기를 추가하지 않고 기존 grant/신원 검사를 유지한다. 오류 latch의 poll은 원 EPROTO를 보존한다. native47개를 ASAN/UBSAN·O2 각각 통과하고, 고정 ARM 빌드33.085초·실제 bundle reader·로컬 staging10개를 통과했다. stats/TCP·자원·한도 설정과16MiB/1vCPU는 유지했다. ELF 합계 text+8/data−8/BSS0은 배치 영향이 포함되며 send/poll 직접 frame은 각각48→64B/80→96B다. 전체 call-chain·실행 high-water·주행 성공으로 확대하지 않는다. ARM 검증 SHA `0ad4fcd94bdb7ee0a89ecedcb5cd3346c153e6360585a3d80cd7fbcbddd60799`와 manifest `2c61df2edb446e7dae02dfcf402f3204ee3a08207b0c19578d03c4b81e5d7e07`, bin `c3c20ca8523bc0cd2cb1e62088c2b669983220bbad523a12c0c39e2438cb5f75`를 연결한다.

**이번 정상 시험은 관측 도구 때문에 중단됐다.** 선택적인 console/API 신원 조회가 기존3초 경계에서 DEADLINE_EXCEEDED(최대3.045초)로 끝나자 부모가 `CONSOLE_EXITED_DURING_CPU_WINDOW`로 제품 driver를 중단했다. driver는38.824초/지도 local queue50개, 마지막 단계 fresh-synthetic-world-enabled·KeyboardInterrupt였고 점유 gate 수락에 이르지 못했다. console 수집은 collection_complete=false지만 원 raw7926B·확정/미확정 span·lease 반환·console/driver/sampler join과 최초 실패는 남겼다. 이를 제품 준비 실패의 정상 재현, 정상 기준선 통과 또는 같은 조건의 R63 비교로 세지 않는다.

종료 뒤 B dom93/execution `62b85524-3bdb-4bac-bbfe-42579391d5ce`의 기존 SDK `net stats`를 별도 lease에서10B 한 번 질의했다. Ethernet interface1의6개 TCP 행과 prompt 순서·원4회 신원 guard·lease/join을 관측했다. 원문 nonce가 없어 fresh_guest_measurement=false이며, 아래 값은 **해당 부팅 누계 한 표본**이다.

| 실제 SDK counter | 값 |
|---|---:|
| bytes_received / bytes_sent / resent | 920031 / 3321104 / 0 |
| segments_received / segments_sent / segments_resent | 10393 / 7959 / 0 |
| segments_dropped / packets_dropped | 0 / 3 |
| checksum_errors / ack_errors / reset_errors | 0 / 0 / 0 |
| resets / connections_reset / connections_dropped | 0 / 3 / 0 |

같은 driver 문맥으로 받은 network17표본은 guest boot `bf07fa2a-40d3-4728-869e-b697d68c1c89`·publisher `9c5843b8-2e4f-4e67-932a-a5fe037fe632`, envelope seq209→433·collection end uptime39748→73052ms였다. 원 raw32 tx_packets2047→4833, rx_packets1167→3418, tx_drop/rx_drop/protocol_errors는0→0, pool_busy는0→1이었다. busy1인 마지막3행은 **누계 증가 한 번**이며3개 장애가 아니다. status=ok/source_rc0·유효0을 그대로 보존하고 network의 envelope/collection을 app_memory 표본 시각으로 대입하지 않았다. 보존 표본에서 감소가 없다는 것만으로 reset/wrap 부재를 확정하지 않는다. 이 앞선17표본과 나중 TCP 질의는 계층·채집 경계가 달라 서로 빼거나 개별 probe/RTO·reset 원인으로 귀속하지 않는다. TCP 재전송 누계0도 손실·지연 부재의 증명은 아니다.

R64 종료 정책은 매회 FW8을 재설치하는 비용을 반복하지 않고 검증된 FW30을 B에 유지하는 것으로 바꿨다. 따라서 원 결과의 **original_b_restored=false / firmware_retained=true**가 정확하다. B는 FW30·dom94/execution `2e92efc1-3fd3-4386-a029-1503d15c4fea`·CPU 검증complete로 다시 시작했으며, 원 영구 drive/telemetry source의 applied receipt가 이 실행과 일치한다. 이는 통신 설정 복귀이며 인증·앱 건강·READY를 의미하지 않는다. CPU0–3 선복원·Worker·optional 두 서비스·mock Gateway·소유 자식/console/lease 정리와 실제 차량 출력false를 확인했다. retained30을 원 FW8 복원 성공으로 표시하지 않는다.

닫힌 원자료46파일/2,163,267B, archive SHA `1cc00d3bded60ffbd1f33108adb4be83bf7725c6f0ecd240dd084607c53f8380`를 전수 hash·비밀0으로 회수했다. private broker 종료 stdout SHA `4a4b8af4bdc36efe4411ac1408c013d213111bb57aca72a6e449577258d5e229`, root0600 임시 사본6개 정리 SHA `f48e1a55435778b3f353f4acdcc763408737535d909230d99f00169dc3242ba5`를 연결한다. [R64 단일 요약 근거](C:/Users/SSAFY/Documents/Codex/2026-11-06/board-resume-evening/r64-closed-counter-facts.json) SHA `7b9c62274ff10ca80b42a9e2f6d9e0ffe477ba50f896c9ad3a13c6c73a9d54ec`에 원 source hash·채집 경계·분모·실패·복원을 정리했다. 본 절에는 R65 결과가 없으며 READY·주행·만료 뒤 반환·세 부하 조건은 계속 미수락이다. 정규 이미지·기본 선택·실차·commit/push는 수행하지 않았다.

<a id="r65-pause-20261107"></a>
### R65 중단 상태: 실제 계산·점유와 준비 대기 실패를 구분

사용자 요청으로 실보드 작업을 중단한 뒤 소스 보존·커밋으로 인계한다. 해결한 국소 결함과
최종 주행 수락을 구분한다. **정상 READY·PTY 주행·입력 만료 뒤 전체 메모리 반환과
두 VM의 세 부하 조건은 여전히 미수락**이다. 현재 단계는 운영 배포나 기본 이미지 선택 변경이 아니다.

R65는 기존 B의 정확한 FW1.0.30 revision을 재사용해 재설치를 생략했다. 원 key·실행·기한·자격을
각 수명대로 보존했고, 격리 Authority·가상 PTY 장치로 실제 출력을 차단했다. 실제 world 입력 활성 후
8.462초에 map/search/path 점유를 확인했으나, 전체46.922초·지도 local queue115에서 준비 대기가
`TimeoutError`로 끝났다. queue115는 수신·계산 성공 횟수가 아니다. 종료 전 상태는
generation1/SAFE_STOP/DISARMED/output=false이며 AUTO/g2·그 뒤 1초 적용 단계에는 진입하지 않았다.

`prepared-state-auto-selected`의 **20초는 시험 도구의 조건 성립 대기 상한**이다.
20초 연속 READY 유지나 20회 중 한 번 실패하는 빈도를 뜻하지 않는다. 이 시험의 선행 조건은
현재 StateApplied 근거와 `_qualified(held=True)`이며, 후자의 기존 `ready_hold_s`는3.0초다.
시험 도구가 AUTO 선택 전에 이 조건을 두는 위치가 제품의 초기 AUTO 절차와 같은지는 재검토해야 한다.
기록된 음성 결과만으로 StateApplied 근거 부재와 held qualification 불충족을 매 actor 회차별로
분리할 수 없으며, 단순 대기 부족·특정 전송 계층의 단일 결함으로 확정하지 않는다.
R64의 선택 콘솔 실패를 엄격한 독립 binding/owner 증거 아래 분리하는 시험 도구는 추가했지만,
R65에서는 이 퇴역 분기를 사용하지 않았고 정상 기준선은 여전히 실패했다.

C clock 8슬롯은7개의 서로 다른 과거 probe다. 유효한1개 폭6.527ms와 나머지 원 판정·미관측을
유지하며 전체 분포나 단방향 지연으로 확대하지 않는다. 후기 TCP 부팅 누계의 재전송0도
개별 지연/RTO 부재의 증명이 아니다. 원 분석은 `r65-c-clock-gate-review.json`
SHA `64a3938853d1d39299a8fd730b9b601d97634ce3343d98494de1bd9405fd0259`와
`r65-bootstrap-qualification-analysis.json`
SHA `affcaea050a47299a7e62ade891e213312b1d8d6f3215d325282a64c8ee36b5b`에 보존했다.

원자료44파일/2,736,135B의 archive SHA
`cbae9e75874d8bdcc32af4ecd2a25538f7e68681618e8c033fca4cceb325ec15`를 전수 hash·비밀 제외
검증으로 회수했다. 시험 broker와 private 자격 사본6개를 정리했고 원장·영구 자격은 유지했다.
중단 확인 `pause-board-readonly.out` SHA
`76eba4f1375020b83b4735e38224d96b474897612f425d6a0da11215a87dc104`에서 소유 시험 helper0,
CPU0–3·Worker·mock Gateway·display-feed·log collector active,
Gateway SAFE_STOP/DISARMED/output=false를 확인했다. B는 FW1.0.30·dom96/execution
`78e2445c-f5a2-4ba1-b2ba-ff061e77698e`를 유지했고 원 drive/telemetry 소스의 새 실행 적용
영수증을 확인했다. A는 FW1.0.8·기존 실행, Debian도 기존 실행을 유지했다.

현재 소스와 격리 시험 사본의 보완을 운영 Python 모듈 전체의 설치·실제 로드 완료로 표시하지 않는다.
FW30은 고정 ARM·불변 포장과 보드 선택을 확인했으나 진단용 설정으로 빌드됐다.
현재 저장소의 기본 profile과 정확히 같은 전체 이미지 수락은 아니다. FW31은 정확한 FW30에
기존 유한 부하 시험 모듈을 연결한 로컬 native/ARM 포장 후보이며, A/B pair에 설치하거나
정상·A만 지연·공통 입력 한계 조건을 실행하지 않았다. 기본 lock의1.0.8 선택은 유지한다.

<a id="mr-integration-20261107"></a>
### 커밋·platform 통합 검증 — 2026-11-07

실보드 중단 뒤 사용자의 커밋·push·MR 요청에 따라 작업을 원인별로 나눴다.
이전 브랜치에 섞인 다른 파트의 develop 커밋은 포함하지 않고,
`origin/platform`의 `91733c2f5db630791427696f4c4d40089003520d`에서
`feat/platform/recovery-drive-runtime-hardening`을 만들었다. 기존 작업은 로컬
`backup/platform/integration-followup-20261107`에 보존했다.

통합 후보 `6b29348a2f196579879618adbd05942787bfcbee`에서 다음을 다시 실행했다.
Worker 충돌은 platform의 `external_reason`·`external_execution_id`와 이번 안정 구간 관측을
함께 보존했고, `xenova_execution.py` 충돌은 원 기록 검증을 설명하는 주석을 보존했다.
platform의 실행 신원 batch 조회·scheduler 상태 재검증과 이번 FD 기반 원 요청 검사·
현재 호출 안의 검증 재사용·만료 수습 함수가 각각 원본과 같은 AST임을 대조했다.

| 로컬 실행 범위 | 결과와 제한 |
|---|---|
| Worker budget/status/worker/engine/watcher/rejoin | 306개 통과. VM_UNTRUSTED의 실행별 고정·새 실행 초기화와 E4 보호를 함께 확인 |
| Manager·수습·원 요청·binding·clock와 platform의 신규 조회 회귀 | 354개 통과, 자식 프로세스의 `first_install_contract` import 1건 실패. 시험 PYTHONPATH에 `platform/first-install`을 추가한 해당 재시험은 통과. 제품 변경 없음 |
| Gateway 전체 | 378개 통과·1개 skip·1개 실패. `test_memory_pressure_switches_manual_owner_using_manual_mirror_readiness`는 target `91733c2f`의 Gateway·MQTT·world 소스 사본에서도 같은 assertion으로 실패. 이번 통합의 새 회귀로 분류하지 않으며 미해결로 보존 |
| MQTT 전체 | 174개 통과·4개 skip. 별도 broker/container 설정이 필요한 시험은 실행하지 않음 |
| 실제 main·입력 시각·transport barrier·차량 진단·가상 차량 표본 | 143개 통과·1개 skip. 진단 비활성 `.text`의 이전 소스 대조는 해당 원본 경로 미공급으로 skip |
| C controller·health·manual/preparation·codec·state transfer | 기존 고정 CMake 3.31.6·GCC 13.3으로 빌드, CTest 6/6 통과 |
| 실제 netfront ring | ASAN/UBSAN 및 O2 각각 47개 사례와 70,000회 wrap 통과. 실제 공간 부족·손상 검출 보호 유지 |

Gateway의 나머지 skip은 native state codec 실행 파일 미공급이며, CTest의 state-transfer
통과와 별도 사실이다. 기존 SDK·시험 의존성을 재사용했고 새 의존성을 설치하지 않았다.
실행 명령·JUnit·stdout/stderr·비교 사본은 로컬
`Documents/Codex/2026-11-06/board-resume-evening/mr-integration-checks/`에 보존했다.
`run_mr_integration_checks.py`의 그룹별 pytest와 `native-validation.json`에 정확한 입력·
도구·해시를 기록했다. 이 절은 위 후보 이후 문서만 추가한 기록이다.

이 통합 후보로 전체 ARM/RPM/이미지를 다시 빌드하거나 보드에 설치하지 않았다.
앞 절의 FW30 ARM·보드 근거를 새 platform 전체 설치 근거로 바꾸지 않는다.
READY·주행·메모리 반환·세 부하 조건의 실보드 미수락과 기존 출력 차단·E4 보호는 그대로다.

<a id="recovery-demo-20261107"></a>
## 장애 범위·재합류·binding 보완 — 2026-11-07

**코드·로컬 검증·ARM 부분 포장·보드 개별 적용과 정리는 마쳤지만, 세 시연의 실보드 수락은 끝내지 못했다.**
정상 준비의 기존 20초 창에서 binding·차량 관측이 유지되지 않아 실제 장애를 주입하지 않았다.
Jetson 상실→MANUAL→명시 AUTO 재개, A AUTO 실패→B AUTO→A 재자격, 실제 VM 장애→Worker 복구→Standby→다음 장애를 통과했다고 표시하지 않는다.
지도 발행은 0건이므로 이번 실보드에서 실제 계산·풀 점유 증가·반환도 입증하지 못했다.
장기 READY 분석으로 범위를 늘리지 않는 사용자 지시에 따라 미수락을 남겼다.

### 변경과 판단 기준

| 위치 | 무엇을 왜 바꿨는가 | 유지한 보호 |
|---|---|---|
| 공통 runtime `io.c`, `Kconfig` | heap 점유율·AUTO deadline 반복을 별도 `vm.trust`로 확대하던 생산 경로를 제거. 실제 `workers` health와 `main`의 앱별 lifecycle을 재사용. 85/5 설정 기호는 과거 `.config` 호환용이며 새 운영 임계값이 아님 | 주입 명령·결과·errno·원 ID/boot/기한, 실제 필수 자원 실패, AUTO/MANUAL 독립성. 오류 종류·희귀 최초 원인 축소 없음 |
| Worker `rejoin.py`, `worker.py` | FW32 보고를 엄격한 현재 binding/boot/app 검사 후 warning/function으로 보관. 이 두 보고만으로 VM 불신 latch·새 recovery attempt를 만들지 않음 | 실제 Xen 부재/crash·생존 실패, 원 attempt·현재 intent·Gateway 보호·E4. 원 사유/명령/실행 근거 보존 |
| Gateway `verified.py`, `warm.py`, `provider.py` | A만의 입력 문제와 양쪽 입력 장애를 구분. 현재 준비된 B AUTO 또는 정지 후 정상 MANUAL을 평가. 유효한 새 실행·준비와 보호의 내구 종결 뒤에만 RAM 불신 상태 해제 | 사용자 MANUAL 의도·명시 resume·control 적용·중립 후 새 조작, 저장 실패 시 원 불신/보호 유지, 자동 재무장·Active 반환 없음 |
| `xenova_drive_binding.py`, `xenova_execution.py`, `xenova_management.py` | 읽기 전용 초기 검증의 admission 대기를 제거. 이어 기존 일괄 관측을 사용해 A/B의 전후 Xen 목록·native identity 조회를 한 회차에서 공유. private `_input_ids`는 대상 외 실행 파일 의존만 줄임 | 전체 registry/tomb 검증 2회, Xen 목록 2회, native identity helper 2회, 최종 admission 1회. strict 타입·현재 run/intent/gate·원 표본 시각·최신 오류 순서 유지. TTL cache·새 전역 잠금 없음 |
| RejoinCoordinator | 기존 executor에서 due 대상만 묶고 provisioning 전후 guard는 유지. 긴 A 작업이 있어도 남은 lane에서 B만 갱신. 실제 publisher/batch/work가 살아 있으면 종료 완료를 반환하지 않음 | 2개 lane·유한 큐·원 2초 관측 예산·3초 binding lease·필수 receipt 저장 유지. 타 대상의 정상 결과·원 작업을 취소하거나 새 시각으로 꾸미지 않음 |

고정 app pool은 system heap과 다르다. 높은 점유·주입기 자신의 ENOMEM을 앱 필수 pool 실패로 대신하지 않는다.
map/path의 current 사본·마지막 결과·마지막 search slice는 정당하게 보관될 수 있다.
입력이 끊겼다는 이유로 used=0이나 예약 RAM의 OS 반환을 요구하지 않으며, 실제 writer/reader 반환과 retained floor·peak를 구분한다.

### 실행한 검증과 남긴 반례

| 묶음 | 실행 결과·범위 |
|---|---|
| C/native I/O·workers | 27개 통과. 원 명령·중복·오류·실제 작업 지연과 기능 독립성. I/O의 health/resource 대역을 실제 앱 pool 고갈 증거로 쓰지 않음 |
| Gateway | 최종 계측 전체 390개 통과·1개 skip. 앞선 3개 실패는 보존. 약 125ms actor 공백의 동일 generation/owner 반례를 HEAD 사본에서도 재현했으며, 최초 비계측 실패의 원인을 단정하지 않음 |
| Worker | 기존 관련 136개, batch 연결/warm-liveness 113개, 느린 sink 종료 1개, A provisioning/refresh 지연 중 B 갱신 등 후속 16개 통과. 집합이 겹치므로 합산하지 않음 |
| 하위 조회·실행 | 확장 198개 통과 뒤 대상 입력 제한의 최종 84개 통과. 신규 paired 시험 24개는 무관 run/intent 손상 격리·전체 등록부/대상 손상 거절·원 시각/기한·신원 변경·정상 B 보존을 검사 |
| 고정 환경 | source checkout의 정책 mode·systemd loader·주입 unit 경로 문제를 소유 fixture에서 보정. 원 실패 기록은 보존. 기존 ARM/QEMU systemd 259.5 ordering 검사 통과. FW33의 QEMU 주행 수락은 아님 |
| 실보드 준비 | 첫 두 시도는 가상 장치 프로세스 준비 경계에서 종료. 불필요한 world/protobuf 재로딩 제거와 단계별 계측 후, 세 번째는 실제 Gateway까지 실행했지만 실제 readiness 20초를 통과하지 못함 |

세 번째 종료 상태는 `SAFE_STOP`, `output_permitted=false`, A/B binding null이었다.
앱은 MANUAL `POSE_UNAVAILABLE`, AUTO `STATE_PREPARING`을 보고했다.
drive link 거절 78건 중 `LINK_BINDING` 70건, 지도 0건이다.
장치 보고 counter 241과 최신 counter 247이 달랐으며 accepted/applied/physical-stop·VehicleSample은 null이었다.
이 상태를 장치 적용·물리 정지·앱 정상으로 바꾸지 않았다.
실제 Worker의 시작 뒤 fresh A/B binding 확인은 약 13.85초였지만 지속 유지 수락이 아니다.
시험 E4 프로필은 기존에 사용한 `stable_s=60`, `vm_max_attempts=3`이며 소비 0→0, 실제 복구 시도 0건이다. 운영 수치·설정은 바꾸지 않았다.

paired 단독 대조는 Worker 정지·mock Gateway 활성 조건에서 pair 3회가 0.654/0.593/0.664초로 끝났다.
반면 서비스 활성 추가 병렬 probe는 6건 중 1건만 원 2초 안에 성공했고, 실제 합친 시연도 미수락이다.
최종 운영 mock 복원 직후 5개 파일 표본에서도 A unavailable·B의 3초 이상 노후 표본을 확인했다.
따라서 중복 조회 감소와 로컬 보호 검증을 실보드 지속 binding 해결·전체 부하 개선으로 확대하지 않는다.

### 제공물·설치·정리

기준은 `47b7304817eb9d726c7b37a80539d56bb5b5d1af`와 현재 diff다.
FW1.0.33은 고정 ARM 환경에서 38.849초 빌드했고 strict bundle·target import 10개를 확인했다.
firmware SHA256은 `8499076633aa0f458c38a2d7717d0acde0caa5b06c2b06edcb78a8b98dd659bc`,
manifest는 `497e5234ba0b5128a223bc02221b0ef2912189ce7ad9071a04100051c692f3c1`이다.
기존 recipe의 모듈·native helper 설치 경로를 재사용했으며 정규 RPM·전체 이미지·factory 기본 firmware는 갱신하지 않았다.

보드의 하위 31개·provider 59개를 대조했고 마지막 binding/execution/management/rejoin 4개를 같은 cohort로 교체했다.
첫 설치 확인의 cold import는 20초 제한으로 rollback됐고, 모듈별 실측은 총 19.59초에 interpreter 시작 시간이 추가됐다.
설치 확인만 40초로 분리한 재시험은 import 9개·90개 hash·새 서비스 PID·동일 VM 실행 검사를 통과했다.
이 변경은 관리·제어·VM·Gateway의 실행 기한을 늘리지 않는다.

최종 부분 source는 `source-closed/source-payload-manifest.json`의 141개 파일이며 fingerprint는
`c9860b80aba3c51e6f2c8b2c4da75dbc4c83fa69af275f1277175906f9aa0969`, archive SHA256은
`f7815b759d36b663f3d8ec13dfec333c1f073193ff3bfa5c3d18f0a0d87276f7`이다.
설치한 Coordinator hash는 `b85edb29f2be8b6ea48914f4160d9b10795a64635b734fc51a8e847e907e284d`다.


A/B는 FW1.0.33을 유지하고 원 통신 설정 4개의 정확한 원 바이트로 복원한 새 실행이다.
A는 domid101 / `09417bc5-bfd0-4021-aaa7-c2eb3c84ab48`, B는 domid102 / `30b4bbc5-1a6c-4c57-9269-8dc32a91a2e5`다.
각 새 실행의 drive·telemetry receipt를 확인했고 Debian domid1의 원 실행은 유지했다.
CPU 정책 hash·UUID·비밀번호는 유지했다. API/Gateway/Worker는 active이며 Gateway는 mock·SAFE_STOP·출력 차단이다.
소유 broker·scene·가상 장치 프로세스는 모두 종료했고, 원 설정 대조 뒤 소유 비밀 사본 6개를 제거했다.
VM helper가 참조할 수 있는 소스·복구용 코드 백업·필수 원장/receipt는 삭제하지 않았다.

정확한 명령·JUnit·소스 manifest·실패와 복원·설치 기록은 로컬
`Documents/Codex/2026-11-07/recovery-demo-closure/`와 `fault-scope-c/`,
Gateway의 `spring-stress-board/gateway-*.xml`에 보존했다.
`final-board-evidence.tar.gz`는 비밀을 제외한 35개 증거를 포함하며 SHA256은
`843930145e4ff9cb8af9c1bd8342180d2df83d23b0c9071d666a1345e5e6388d`다.
세 시연의 남은 실제 수락은 정상 binding·차량 관측·준비가 먼저 확보된 뒤에만 진행한다.
이번에는 실차 출력·EC2·commit/push를 수행하지 않았다.

### 후속: 정상 준비를 요구하지 않는 Spring 세 명령의 실제 주입

사용자 요청에 따라 위의 정상 준비 수락과 분리해, 같은 FW1.0.33 A 실행에
`cpu_stress`, `memory_stress`, `auto_delay`를 각각 한 번 발행했다.
위의 지도 0건·장애 주입 0건은 앞선 세 복구 장면의 선행조건 시험 결과이며,
이번 후속에서는 세 명령을 실제로 수신·실행·종결했다. 세 복구 장면 전체의 수락을 뜻하지 않는다.

| 실제 요청 | 제품 수신부의 결과 | 실제 관측 |
|---|---|---|
| `cpu_stress`, workers=1, load_percent=100, duration_s=10 | RUNNING→SUCCEEDED, duration_ms=10018 | `cpu.system.percent` 29개 정상 표본: 첫 14.483%, 최대 100%, 마지막 14.822%. 부하 종료 뒤 사용률 하락 |
| `memory_stress`, memory_mib=8, duration_s=5 | RUNNING→SUCCEEDED, duration_ms=5007 | `memory.kernel_heap.used_bytes` 0→8,389,632→0. free 9,437,184→1,046,528→9,437,184 bytes. high_water 8,389,632 bytes는 종료·다음 명령 뒤에도 보존 |
| `auto_delay`, delay_ms=100, duration_s=10 | RUNNING→SUCCEEDED, duration_ms=10003 | 주입 창 수락·종결은 확인. `loop.completed_total`, `loop.in_flight`, `loop.deadline_miss_total`은 모두 0. 실제 AUTO 작업·지연 효과·승계는 입증하지 못함 |

CPU·메모리 부하는 정상 준비 없이도 실제로 작동했다. 메모리 주입의 5초는
표본에서 할당·반환을 보기 위한 시험값이며 새 운영 교체 기준이 아니다.
커널 heap 반환을 앱의 고정 map/path/search pool 반환이나 VM RAM의 OS 반환으로 확대하지 않는다.
AUTO의 실제 지연은 `platform/apps/drive/zephyr/src/workers.c`의 AUTO 작업에서
`drive_adapter_run_until` 뒤 `drive_auto_delay_apply`가 호출돼야 발생한다.
이번 수신창의 앱 사건은 AUTO `STATE_PREPARING`, MANUAL `INPUT_UNAVAILABLE`였고,
명령의 SUCCEEDED를 계산 성공·지연 발생·기능 장애·복구 성공으로 치환하지 않았다.

전송은 기존 `direct_command_trial.py`를 재사용했다. `origin/backend`의 `5667ea6f`
Spring `AgentStressService`/`AutoDelayService`와 동일한 MQTT-04 envelope·parameters로,
`xenova/v1/vms/{vm_id}/command`에 QoS1, retain=false, 원 command/operation ID와
현재 boot를 유지해 각각 한 번 발행했다. 각 PUBACK과 같은 ID의 제품 결과 2개를 회수했다.
Spring HTTP·인증만 우회했으며 배포된 Spring REST/버튼의 종단 수락은 하지 않았다.

실행 명령은 기존 도구에 공통 `--vm de113a23-d80e-4333-94b8-788babe0e41e --observation-s 25`를 주고,
각각 `--action cpu_stress --amount 100 --duration-s 10`,
`--action memory_stress --amount 8 --duration-s 5`,
`--action auto_delay --amount 100 --duration-s 10`을 사용했다.
원 command ID는 순서대로 `e5465e7c-1a3b-4579-82d3-f5b94eff3609`,
`d9f7c8c6-c4e4-446a-a770-975833ec1455`, `c7edcec6-30e3-4c3a-a693-0ff3abb46023`이다.

후속 조회에서도 A domid101/execution `09417bc5-bfd0-4021-aaa7-c2eb3c84ab48`,
B domid102/execution `30b4bbc5-1a6c-4c57-9269-8dc32a91a2e5`, Debian domid1의 원 실행은 같았다.
Gateway는 SAFE_STOP·output_permitted=false, API/Gateway/Worker는 active였다.
새 VM 재시작·승계는 관측하지 않았다. E4 설정·필수 기록을 변경하지 않았고,
시험 발행 프로세스는 종료했다. 실제 차량 출력·서비스 교체·추가 firmware 설치·commit/push는 하지 않았다.

별도로 확인한 미보완 결함: `xm_payload.c`의 두 명령 parser는 `expires_at` 문자열을
검사하지만 값은 전달하지 않고, `io.c:receive_fault_command`도 수신 age로 만료를 거절하지 않는다.
이번 즉시 단발 발행의 성공과 별개인 만료 보호 공백이며, 만료 메시지 시험·제품 수정은 이번에 하지 않았다.
관측한 원인 구분·원 ID·현재 boot 검사·유한 주입 시간·중복 처리 보호를 삭제하지 않는다.

원 MQTT 결과·Telemetry 표본·실행/서비스 후속 조회·해시는 기존 로컬 증거 폴더의
`direct-three-commands/`에 보존했다. 정확한 request와 result는 각 `direct-*.json`,
요약은 `summary.json`, 서비스·host 조회는 `post-command-readback.json`이다.

<a id="auto-delay-map-20261108"></a>
### 시험 지도 공급 후 실제 AUTO 지연 재시험 — 2026-11-08

**실제 AUTO 계산의 선행조건을 확보하지 못해 계산 중 지연·deadline·승계 수락은 미완료다.**
이 절은 앞 절의 세 명령 수락·종결 결과와 구별한다. 실제 계산 뒤에 주입하라는 요청에 따라
이번에는 AUTO 지연 명령을 발행하지 않았고, deadline 계수 0을 정상 계산의 증거로 쓰지 않았다.

사용자가 허용한 Worker/mock Gateway 일시 정지·A/B 시험 통신 설정과 재기동 범위에서,
FW1.0.33과 기존 64×32/0.1m 시험 지도·장애물·codec·가상 장치·제품 Gateway를 재사용했다.
기존 자격은 재사용했고 firmware 재빌드·교체, 실제 장치 open, CPU 정책·E4·원장 초기화는 하지 않았다.
시험 실행은 A domid103/`5615b884-87d3-4743-a4db-4d1da4e23db8`,
B domid104/`e73ed3e5-6a03-4e48-86ad-d3a2262bd0c1`이었다. 이는 설정 공급을 위한 기동이며 자동 복구가 아니다.

| 시도 | 결과와 해석 |
|---|---|
| 첫 실행 | 시험용 가상 장치 프로세스 entry 확인이 3초를 넘어 제품 준비 전에 종료 |
| `scene-r2` | 프로세스 시작·준비를 기존 총 fixture 준비 20초 안에서 구분. 제품 readiness 20초는 유지. 차량 표본 73건 수신, mapper 만료 거절 215회, 지도 0건. `MANUAL_SELECTED_DISARMED`에서 종료 |
| `scene-r3` | 시험 mapper만 Gateway의 원 VehicleSample을 직접 소비해 MQTT 관측 대기 구간을 분리. 원 시각·200ms 검사는 유지. 지도 전송 요청/큐 수락 57건. 앱의 실제 수신·채택·계산은 입증하지 못했고 같은 준비 경계에서 종료 |

마지막 시도의 A CPU 정상 표본 11개는 14.880~18.377%(마지막 18.201%),
B 29개는 14.568~18.121%(마지막 17.706%)였다. AUTO 지연을 발행하지 않았으므로 지연 효과가 아니다.
두 VM의 `loop.completed_total`, `loop.in_flight`, `loop.deadline_miss_total`은 모두 0이었다.
앱은 MANUAL `POSE_UNAVAILABLE`, AUTO `STATE_PREPARING`, progress_seq=0을 보고했다.
마지막 snapshot에는 두 binding이 있었지만, 도중 DriveLink에서 `LINK_BINDING` 45회,
`LINK_VEHICLE_UNAVAILABLE` 11회, `LINK_STALE` 3회를 거절했다. 일회 관측을 지속 준비로 대신하지 않았다.

Gateway는 `SAFE_STOP/DISARMED`, output_permitted=false를 유지했다. B 승계는 없었다.
이는 주입 지연에 대한 대응을 입증한 것이 아니라 기존의 준비되지 않은 상태를 보호한 결과다.
종료 시 공개 protect 요청은 PROTECTED로 회수됐고 actor·IPC·link·binding·가상 장치 종료를 확인했다.
각 시험 E4의 전후 소비는 0→0이며 원 상태 파일의 실제 부재를 보존했다.
시험 mapper의 직접 VehicleSample 대조를 실제 Jetson의 MQTT 차량 관측 소비 수락으로 확대하지 않는다.

추가 확인이 필요한 경계는 Gateway 원 차량 표본→DriveLink clock/pose→앱 current_vehicle/준비 채택과
binding 지속성이다. 제품 검증을 완화하거나 장기 READY 분석·무주입 반복으로 확대하지 않았다.
정확한 결과·실행 도구·해시는 로컬 `Documents/Codex/2026-11-08/auto-delay-map/`에 보존한다.
`scene*/scene-delay-result.json`, `environment-result.json`은 실패·정리·E4를 포함하고,
최종 원 설정·서비스·VM·비밀 사본 정리는 `post-test-readback.json`으로 별도 확인한다.
제품 코드 추가 수정·EC2·commit/push는 하지 않았다.

복원 후 A는 domid105/`100e0902-5d23-4e45-b4fa-6545e836b657`,
B는 domid106/`b6611a35-7803-43cf-ab79-b1f2e37f0f51`이며 FW1.0.33을 유지한다.
원 통신 설정 4개의 정확한 원 바이트·CPU 정책·Debian 원 실행을 확인했다.
API/Gateway/Worker 모두 active, 운영 Gateway는 mock·SAFE_STOP·출력 차단이다.
시험 브로커는 returncode=0으로 종료했고 잔존 소유 프로세스는 없었다.
원 설정 대조 뒤 소유 비밀 사본 6개만 정리했으며 필수 기록·receipt·시험 Authority는 보존했다.
