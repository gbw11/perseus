# 복구 상태 파일 마감 — S01·S02

## 결과와 후보

**S01·S02의 제품 국소 수정·로컬 오류 주입/재시작 회귀·부분 패키징을 완료했다.**
실제 Xen·보드·SD 전원 상실 수락은 수행하지 않았다. D02·D08 등 다음 작업으로 진행하지 않았다.

| 기준 | 내용 |
|---|---|
| 후보 | `refactor/platform/recovery-contract-closure`, HEAD `40ab68aaefa05fae032abb23944ba2927b78fab4` + 미커밋 diff·새 파일. 과거 커밋으로 전환하지 않음 |
| 최신 원격 대조 | Windows fetch 성공. `origin/platform=3c7e844e8bad9e873da5c20c8100a3d5da80e642`, develop `3de2deeec6cb78a28b8026a5e415329cb3c29509`. 대상 recovery 제품 모듈은 HEAD와 동일해 S01·S02가 별도로 수정되지 않았음. 원격의 Linux 앱 heartbeat 시험 추가는 병합하지 않음 |
| 수정 전 근거 | [저장 감사 S01·S02](persistence-transaction-audit-20261027.md), [기존 복구 보장 A06·D03](recovery-architecture-audit-20261027.md). 당시 보고서·JSON·archive는 보존하고 후속 링크만 추가 |
| 소유 문서 | [Worker 상태 파일·사건 기록](../../platform/services/dom0-manager/README.md#worker-상태-파일과-사건-기록), [관리 경계](../platform/management-boundaries.md#제품-서비스-연동과-지원-경계) |
| 증거 위치 | PC `C:/Users/SSAFY/Documents/Codex/2026-10-27/xenova-recovery-s01-s02/`, WSL `/home/ssafy/xenova-recovery-s01-s02-20261027/` |

`BASELINE.json`은 착수 전 모든 추적 파일과 기존 감사 미추적 파일의 해시를 보관한다.
`before-FILE_PROBES.json` 등은 과거 실패 근거, `FILE_PROBES_AFTER.json`은 수정 후 관측이다.
`SOURCE_MANIFEST.json`, `candidate.patch`, `ARTIFACTS.json`, `FINAL_VERIFICATION.json`으로
HEAD만으로 식별할 수 없는 미커밋 후보와 테스트·RPM을 연결한다. commit/push·브랜치 병합은 하지 않았다.

## 변경한 책임

| 파일·함수 | 최소 변경 |
|---|---|
| `recovery/store_files.py`의 `read_state/preserve_corrupt` | 두 Worker 저장소 전용 작은 helper. 직접 읽기로 진짜 부재와 읽기/조회 오류를 구분. 부재 시 `.corrupt/.tmp` lstat 확인. 원본을 이동하지 않는 hard link 보존, 기존 사본 덮어쓰기 금지 |
| `recovery/state.py`의 `load/_lost/save` | 정상 schema·records·bool save 유지. 보존 사본 확보 후에만 차단 본 파일로 교체. 읽기/격리 실패에는 원본과 흔적을 덮어쓰지 않고 save=False. history_lost는 같은 객체의 load로 해제하지 않음 |
| `recovery/arming.py`, `watcher.py` | 동일한 손상·부재 판정. ArmingStore도 flush/file fsync 뒤 교체. 기존에 실패를 로그로만 삼켰던 **내부 save를 bool 반환**으로 바꾸고 실제 Watcher의 두 저장 지점에서 False를 받아 history_lost 보호 유지. 공개 Manager/HTTP schema는 그대로 |
| `recovery/events.py`의 `append/_read` | bool 계약 유지, False를 효과 미확정으로 정정. 부분 기록 뒤 재시도가 붙어 유실되지 않도록 필요한 경계 개행만 추가하고 short write 거절. binary line을 엄격한 UTF-8로 해석해 잘린 글자도 gap으로 보존 |
| `recovery/engine.py`, `__main__.py` | 원 pending ID/내용 재시도·실제 sink 순서 유지. 실패 효과 설명과 “파일 삭제로 재개” 로그를 정정. 실제 sink는 로컬 저장 확인 후 MQTT 제출, 제출 예외에도 HTTP 원천이 있으면 True |
| 정규 시험 5개 파일 | Store·별도 프로세스 시험 2개 신설. 기존 Engine/Watcher/EventLog 시험은 잘못된 삭제 해제·아무 기록 없음 설명을 정정하고 보완 |
| `platform/tests/audit_persistence_contract.py` | 현재 S01 재현은 보호 유지·논리 호출 0을 assert. S02는 미확정 append·동일 ID dedup 관측으로 표시. 수정 전 도구·결과는 저장소 밖에 보존 |

SQLite·ORM·daemon·범용 저장 프레임워크나 새 의존성은 없다. schema 1, 원 recovery/attempt/event ID,
spec/deadline·시도 수·저장 위치·Worker 단일 실행 소유는 유지한다. 하위 Manager·공용 플랫폼
atomic helper·guest·unit·recipe·부팅/CPU/네트워크 정책은 수정하지 않았다.

## S01 판정 순서

선택한 방식은 **A+B의 최소 결합**이다. hard link로 원본이 남은 상태에서 보존 사본을 확보하고,
그 다음에만 차단 본 파일을 원자 교체한다. 새 파일조차 쓸 수 없는 상황에는 원본이 남는다.
이전 구현이 이미 원본을 이동한 상태는 격리 흔적 검사로 처리한다.

| 관측 | 결과·재시작 해석 |
|---|---|
| 유효 본 파일, history_lost=false | 원 ID/spec/deadline/예산/pending 복원. 남은 `.tmp/.corrupt`는 본 파일을 대체하지 않음 |
| 유효 본 파일, history_lost=true | 차단 복원. Recovery Engine은 새 복구 실행 안 함. Arming은 기존 대상 불명 보호 |
| 본 파일·두 흔적 모두 실제 부재 | 정상 최초 빈 상태 |
| 본 파일 부재 + `.corrupt` 또는 `.tmp` | 차단, 증거 유지, save=False. 임시 파일 검증 없는 승격 없음 |
| 손상 본 파일, 보존 가능 | `.corrupt`와 원본이 같은 inode인 동안 차단 파일 저장. 성공 후에도 손상 사본 유지 |
| 보존/차단 저장 ENOSPC·EIO | 원본 또는 이미 확보한 `.corrupt`가 남아 다음 프로세스도 차단. 새 marker 성공에 의존하지 않음 |
| 다른 기존 `.corrupt` | 양쪽 증거 유지, 본 파일 교체 거절·save=False. 동일 inode는 앞선 저장 실패의 재시도로 인정 |
| 읽기·경로 조회 오류, dangling 경로 | 정상 부재가 아님. 이력 상실로 처리하고 원본/흔적 덮어쓰기 거절 |

Arming의 history_lost는 capability 미선언·실제 신호 미관측 VM을
OBSERVATION_UNAVAILABLE로 유지한다. 손상을 APP_HEARTBEAT_LOST로 바꾸지 않는다.
기존처럼 특정 VM의 실제 신호를 확인하면 그 VM은 armed로 판정할 수 있지만,
이는 저장소 전체 이력 상실 차단 해제가 아니다. 운영 증거 삭제·자동 reset·새 수습 CLI는 없다.

## S02 효과와 추가로 확인한 부분 기록 결함

감사의 fsync EIO 반례는 그대로 성립한다. **append=False여도 완전한 줄이 있을 수 있다.**
같은 event_id/내용 재시도 뒤 raw 2줄·논리 조회 1건은 정상 보호이며 고유 ID를 새로 만들지 않는다.

추가 로컬 주입에서는 수정 전 EventLog가 `부분 줄 → 같은 사건 재시도`에서 True를
반환했지만 **논리 조회 0건**이었다. 개행 없는 이전 줄에 새 JSON이 붙었기 때문이다.
`PARTIAL_APPEND_BEFORE.json`에 이 실제 HEAD 코드 반례를 보존했다. 수정 후에는 원문을 자르지
않고 경계 개행을 넣어 같은 사건을 회수하며, 손상 줄은 gap/tombstone으로 남긴다.
잘린 UTF-8·short write도 같은 규칙으로 검증했다.

Engine의 `_commit`은 기존처럼 상태+pending을 한 JSON으로 먼저 저장한다.
append 실패 때 pending을 빼지 않고, append 성공 후 pending 제거 저장 전 중단되면
같은 ID로 재전달한다. 실제 sink의 QoS1/retain=false/wait_s=0은 유지하며 PUBACK·소비자 적용·
영구 MQTT 재전송 보장으로 바꾸지 않았다.

## 실제 실행한 검증

WSL Ubuntu-24.04의 기존 격리 pytest·FastAPI/Pydantic/HTTPX·공용 MQTT 경로를 재사용했다.
새 도구 설치 없음. 아래 명령은 `platform/services/dom0-manager` 기준이다.

```sh
python3 -B -m pytest -p no:cacheprovider \
  tests/test_recovery_store.py tests/test_recovery_store_process.py \
  tests/test_recovery_engine.py tests/test_recovery_events.py \
  tests/test_recovery_watcher.py tests/test_recovery_lock.py
python3 -B -m pytest -p no:cacheprovider
python3 -B -m pytest -p no:cacheprovider tests/test_recovery_engine.py
```

| 실행·결과 | 실제 경계와 assertion |
|---|---|
| 첫 집중 회귀 **197 passed** | 실제 Store·ArmingStore·EventLog·Engine·Watcher. filesystem 오류와 외부 플랫폼만 대체 |
| 보완 후 Manager 전체 **451 passed**, skip 0 | 정상 API·Driver·복구/rejoin·토큰·PTY·계약 포함. 기존 Starlette 경고 1개. 제품 HTTP의 별도 D02 해결을 뜻하지 않음 |
| 최종 Engine 로그 정정 뒤 **56 passed** | 전체 회귀 후 남은 “remove to resume” 로그만 정정하고, 해당 Engine 회귀와 삭제 안내 부재 assertion 재실행. 마지막 기능 변경은 없음 |
| 별도 process 재시작 | 두 저장소×write/fsync/replace/link×ENOSPC/EIO. 실제 spawn 프로세스 종료 뒤 새 프로세스가 load. Recovery 새 논리 recover_once **0**, Arming 미선언 대상 관측 불가·판정 없음 |
| 보호 도중 SIGKILL | link 전/후, replace 전/후 각각 두 저장소. 기존 원본·격리 흔적·차단 파일 중 안전 근거가 남고 다음 프로세스도 차단 |
| 최초/정상/과거 잔존/오류 | 정상 ID/spec/deadline/예산/pending의 새 프로세스 복원, 유효 history_lost schema, 이전 `.corrupt`·고립 `.tmp`, 기존 증거 충돌, 읽기/조회 권한 오류·반복 load. 정상 부재와 구별 |
| 상태·pending 원자 교체 | 실제 RecoveryStore rename 전/후 SIGKILL. 각각 이전 또는 새 상태+사건 단위, 반쪽 노출 없음. **기존 보장 유지** |
| append 경계 | 기록 전 EIO·부분 JSON·부분 UTF-8·short write·fsync EIO. 원문 prefix 유지, 재시도 후 논리 사건 1개, 손상 시 gap·compaction 뒤 tombstone 유지 |
| 실제 Engine/sink | append fsync 실패에서 pending·원 request/attempt 보존. append 성공 뒤 pending 정리 전 SIGKILL·새 프로세스에서 같은 사건 ID, raw 3줄/논리 사건 2건, 추가 논리 recover_once 0(실제 Xen은 대역). 실제 entrypoint가 전달하는 sink로 로컬 미확정 시 MQTT 미제출·게시 예외 시 True·wait_s=0 확인 |
| 수정 후 감사 도구 | 파일 probe 6묶음 실행. S01 logical call **1→0**, reopened history_lost **false→true**. S02 raw 2/logical 1. 종료 0과 개별 assertion을 대조. 다른 미완료 보장은 전체 성공으로 바꾸지 않음 |

시험 세부 ID는 `test_recovery_store.py`, `test_recovery_store_process.py`에 있고 명령·출력은
`focused.log`, `all.log`, `engine-final.log`, `audit-after.log`에 보관한다. 이전 A06의 정상 보존
재시작 근거는 유지하며, 이번에는 손상 격리·차단 저장 실패 경계를 추가했다.

## 부분 패키징과 소스 결합

고정 Yocto build `build-rpi4-latest-image-20261026-121448`에서 다음을 실행했다.

```sh
bitbake -c package_qa xenova-dom0-manager
bitbake -c package_write_rpm xenova-dom0-manager
```

QA는 **2,200 tasks / 2,189 cached**, RPM은 **2,200 / 2,199 cached**, 모두 성공했다.
기존 shared sstate·host CONFIG_TASK_IO_ACCOUNTING 경고 2개다. recipe의 기존
`file://dom0_manager` 재귀 설치가 새 helper까지 포함하므로 recipe/RDEP 변경은 필요 없었다.
설치 staging과 **실제 RPM에서 읽은 7개 변경 모듈** 모두 후보 바이트와 일치했다.
Worker unit도 기존 소스와 동일하다. 기존 RPM 3개는 `before-packages/`에 보존했다.

| 모듈 | 최종 SHA-256 |
|---|---|
| state.py | `a86bd4cf3cec8fb67a3cdf04b4fb1bb414564edbbf3a4e5e550cf92e3db407ef` |
| arming.py | `bab202766a53376abfdb475567086e3e46b37de4abe1d9e5b12fe35b68d740ca` |
| store_files.py | `6a1df69b84e76bb419d2271bd1666f93cfab747a820806a52a432a7519bfc557` |
| watcher.py | `bd5203861964079e4663c895e4848470d518a29a0fbafbd6306f7c4e174c3e69` |
| engine.py | `abddbca988918d4b41ae35aa4670470b9669cf0f94a11c7235bfc609048cd09c` |
| events.py | `0784b58c6074bd21edf2e621050b7c1511081c925ec28f5adecc9201046225a8` |
| __main__.py | `3f59f7edb00392ccd252e06dda4d32b5a7e2ef7932f38e5443a8d7eec09bc5af` |

`xenova-dom0-manager-0.1.0-r0.noarch.rpm`은 100,966B이며 SHA-256은
`06d93f7e79fed63efc639c7cfabf7f37a597dc17fe57f2fa3e79fc521e73c092`다.
기존 native RPM 도구가 출력한 gzip/tar에서 파일 바이트를 읽었으며 보드에 설치하지 않았다.

## 남은 범위

기본 `/run` tmpfs·host 재부팅 정책은 유지했다. file fsync와 SIGKILL·오류 주입은 실제 SD 전원
상실 보장이 아니다. 새 RPM은 보드에 설치하지 않았으며 서비스·VM·broker·CPU·네트워크·
Wi-Fi/SSH·실제 상태 파일에 접근하거나 변경하지 않았다. guest 변경이 없어 Zephyr ARM 재빌드도
하지 않았다. 전체 이미지·WIC·SD 작업은 없다.

S03의 영구 저장 모드, D01의 판단, D02·D08 HTTP/시간 연결, D03의 하위 stopping·receipt uncertain·
Authority 수습은 이 묶음의 해결 범위가 아니다. 자동 차단 해제나 journal 삭제 대신 기존 증거와
원 작업을 보존해야 하며, 지원 수습 절차는 별도 후속 결정으로 남긴다.
