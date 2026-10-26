# 공통 Zephyr 주행 펌웨어·대칭 승계 검증 이력

최신 후속 결과는 [공통 계약 보완 검증](#공통-계약-보완-검증--ba1b198-기준-후속)에 있다. 아래 초기 결과는 당시 상태로 보존한다.

## 범위와 기준

- 기준 소스: `e5b4293ac1bc9ff9cf2e7327237d511bd6de8cb8`, `chore/platform/latest-image-20261026`.
- 공통 펌웨어 통합, 같은 실행 안의 양방향 AUTO/MANUAL, 별도 축인 A/B 활성·대기·재합류·승계를 구현했다.
- 기존 `auto_step`/`manual_step` 소스, UUID/자산/CPU 정책, netfront/Store/협조 종료,
  계산·통신 분리, F01 기록 봉인, v1 `TCP_NODELAY`를 유지했다.
- 보드 접속·VM/서비스 제어·CPU 변경·host 재부팅·이미지 조립·WIC/SD·commit/push는 수행하지 않았다.

현재 구조와 사용법은 [공통 펌웨어 안내](../../platform/apps/drive/README.md),
[Gateway](../../platform/services/gateway/README.md),
[MQTT 프로필](../contracts/mqtt-contract-v1.md#공통-ab-펌웨어-프로필)에 둔다.
이 문서는 특정 실행의 결과와 한계를 보존한다.

## 변경 묶음

| 묶음 | 주요 파일 | 결과 |
|---|---|---|
| 동일 펌웨어 | `platform/apps/drive/zephyr/` | 기존 두 계산을 직접 링크. 단일 runtime·20ms 공통 작업 |
| 공통 I/O | `drive_io.h`, `drive_runtime.h`, `io.c`, `runtime.c` | 세 입력의 같은 snapshot, 선택 입력의 원 시각 만료, 두 앱의 독립 heartbeat 칸 |
| v1 control 파싱 | `xm.h`, `xm_payload.c` | 기존 `active_vm_id`를 읽고 제공된 UUID 형식 검사. 생략/null의 legacy 파싱 유지 |
| 대칭 제어 | Gateway `redundancy.py`, `config.py`, `runner.py`, `__main__.py` | UUID 쌍별 관측·재합류·현재 세대 출력. retained 모드/활성 UUID 보존 |
| 빌드·시험·문서 | `build-xenova-drive-app.py`, 새 controller/native 시험, 기존 owner/Runner 시험 | `--app drive`와 입력 고정, 실제 TCP/MQTT·file sink 검증 |

제품 런타임 의존성은 추가하지 않았다. 별도 시험 경로에는 승인된 pytest 8.4.2,
paho-mqtt 2.1.0, pyserial 3.5를 설치했다. 기존 고정 Mosquitto를 재사용했다.

## 고정 입력과 산출물

| 항목 | 값 |
|---|---|
| Zephyr | `684c9e8f32e4373a21098559f748f06915f950c9`, 4.4.0 |
| SDK/컴파일러 | 기존 SDK 1.0.0 / AArch64 GCC 14.3 |
| 최종 ARM 입력 해시 | `de645421f9c1b98f38bd01b7b46e5e21c07560c5fa85ee8d44bdd674a93bab80` |
| ARM `zephyr.bin` | `d795da973a92efe08d42e07ee7935e0f154eb300f8da83a99def06faaef311d9`, 387,384 bytes |
| ARM `firmware.json` | `aa4ba8c016113935fcb2d3f57bdf7b866247abbb7150477b446cdbfd1b0f8900` |
| native_sim 실행 파일 | `076a1cc4718a1d72324e50101f33bb55b3507cb1c18823996f98b9a7d1b010a2` |
| ARM 프로필 | 기존 1 vCPU / 16MiB / GICv2. ELF·DTS 검사 통과 |
| 링크 메모리 | RAM 3,424KiB / 16MiB, 20.90%. 실사용 peak 측정이 아님 |
| netfront 송신 직접 프레임 | 48 bytes, 기존 256-byte 감사 기준 및 stack sentinel 통과 |

WSL 증거 루트는 `/home/ssafy/xenova-latest-image-20261026-121448/common-drive`다.
ARM 산출물은 `arm-drive-final/`, 명령·입력 해시·공개 결과는 PC의
`C:/Users/SSAFY/Documents/Codex/2026-10-26/xenova-latest-image/`에도 회수한다.
비밀 Bootstrap/인증 파일은 Git·문서·공개 증거에 넣지 않는다.

## 수락 시험

| 층 | 시험 | 판정 |
|---|---|---|
| 순수 C | 실제 controller + 변경하지 않은 두 계산, ASan/UBSan, MQTT 코어 vectors | 통과 |
| Gateway | 기존 앱별 동작, 대칭 연속 시나리오, 준비 전 승격/옛 실행/retained 거절, 세대 출력, 복귀 요청·취소, 재시작 문맥 | 66개 통과 |
| 실제 Zephyr thread | 기존 owner 24개 경계 + dual 입력/두 heartbeat | 25개 통과 |
| ARM | 고정 도구로 공통 firmware build·bundle·ELF/DTS·stack 검사 | 통과 |
| 실제 격리 통신 | 같은 native 바이너리 2개, 두 시작 순서, 실제 broker/Gateway/독립 v1·v2 구독기/시리얼 인코더 | 최종 후보 A 시작·B 시작 각각 10단계 통과 |
| 플랫폼 공통 | `python3 -m unittest discover -s platform/tests -v`, 고정 systemd 259 loader | 895개 중 894 통과·기존 1 skip |
| 최종 이미지 포함 | 공통 번들 게시·seed/기본 A/B 반영·조립·WIC 검사 | 미수행 |
| 실제 Xen/보드 | 같은 ARM 번들 A/B 적용, 실제 모드 전환·승계·종료·CPU 배치 유지 | 미수행 |

모드·활성 VM·generation과 실제 Gateway 인코더 프레임을 독립 수신 명령에 대조했다.
모든 DRIVE 출력은 선택 UUID·앱·같은 세대의 실제 수신 명령에서 유래해야 한다.
합성 입력은 기존 계약으로 주입했으며 새로운 주행 계산이나 Mock 앱은 만들지 않았다.

최종 원자료는 `pair-final-proof-a`, `pair-final-proof-b`다. 동일 실행 파일로 두 VM과
재합류 실행을 시작했다. 독립 구독기의 실제 `drive.command` 수신은 각각 1,066/1,068개,
v2 inventory 3개·heartbeat 19개·metrics 106개씩이다. 기존 v1 검사와 독립 v2 oracle의
오류는 없었다. 이는 각 순서 1회의 로컬 기능 표본이며 손실률·최대 지연의 보장이 아니다.

호스트 계산 시험의 재현 명령은 공통 안내의 CMake/ctest 명령이다. Gateway는
`cd platform/services/gateway && python3 -m pytest`를 실행했다. 전체 회귀에는 기존
`verifier-259-prefix.json`의 JSON argv를 `XENOVA_DOM0_SYSTEMD_ANALYZE`와
`XENOVA_SYSTEMD_ANALYZE`에 지정했다. 원 명령과 버전·결과는 증거 루트의 각 JSON/로그에 보관한다.

연속 시나리오는 A/AUTO → 인지 장애 → A/MANUAL → A 장애 → B/MANUAL → 인지 회복·
3초 준비·기존 복귀 요청·3초 카운트다운 → B/AUTO → A 새 실행 재합류·Standby →
B 장애 → A/AUTO다. 처음 활성 VM을 B로 바꾼 반대 순서도 시험했다.
재합류 준비 전에는 `unready`, 완료 후에만 `standby`이며 기존 Active는 유지한다.
단일 heartbeat 자동 복귀, 오래된 세대 출력, 퇴역 실행 재합류는 거절한다.

## 실패 기록과 확인한 수정

1. 초기 native 시험에서 새 제어의 MQTT 왕복 동안 이전 세대 후보가 도착했다.
   이를 곧바로 실행 실패로 분류해 SAFE_STOP/세대 변경이 반복됐다. 기존 현재 실행의
   생존 관측과 출력/준비의 세대 일치를 분리했다. 새 문맥 전에는 STOP이며, 이전
   세대는 대기 준비나 승격에 사용하지 않는다. 100ms·3초 설정은 늘리지 않았다.
2. 독립 구독기의 Paho ReasonCode 검사와 수신/시리얼 자료 분석의 deque 경합을 수정했다.
   제품 실패와 시험 도구 실패를 구분하고 초기 실패 원자료를 보존했다.
3. 세대 변경만으로 장애 이벤트를 내지 않도록 VM 통신 생존과 현재 문맥 준비를
   진단에서도 구분했다. 실제 명령 중단의 기존 `drive.fault.detected`는 유지한다.
4. 전체 플랫폼 회귀의 첫 실행은 systemd 259 검사기 지정을 누락해 4 failure/8 error가
   발생했다. 실패는 기존 unit loader 검사에 집중됐으며, 저장된 고정 검사기를 지정한
   재실행은 895개 중 894 통과·1 skip이었다. 첫 로그는 별도 이름으로 보존했다.

## 남은 수락과 제한

- ARM 빌드 성공과 native_sim 통신 성공은 실제 Xen/보드 수락이 아니다. 이미지에
  포함되었다는 판정도 하지 않았다. 기본 A/B·Gateway 설치본은 변경되지 않았다.
- 공통 프로필의 AUTO_READY는 두 계산의 실제 완료를 선행하는 현재 수동 명령과 자율
  heartbeat로 준비를 확인한다. 기존 분리 앱 프로필의 자율 명령 스트림 조건과 구분한다.
  명시적 복귀 요청·3초 카운트다운은 그대로다.
- 부팅/heartbeat만으로 재합류·승격하지 않는다. 다만 전체 앱 메모리·필터 상태·물리
  차량 상태 복제, Gateway의 Xen 신원 직접 조회, 프로세스 재시작을 넘는 퇴역 세션의
  영구 바인딩은 구현 범위가 아니다.
- 기존 v1/v2 인증·ACL 배포는 별도다. 공통 v1 계정은 VM별 UUID로 두 앱 토픽 쓰기를
  허용해야 하며, 실제 broker에 예시 ACL을 자동 적용하지 않았다.
- CPU pinning 정책을 바꾸거나 지터/차량 성능 비교를 수행하지 않았다. 시간 측정은
  로컬 기능 시험의 소요시간이며 실시간 상한이나 성능 보장이 아니다.
- 기존 F01 봉인·정합성 및 개별 앱 분석 회귀를 보존했다. 기존 `analyze.py`는 공통
  펌웨어의 모드별 원자료 분석 수락 도구로 확장하지 않았으며, 이번 기능 수락은 실제
  명령 수신·현재 제어 문맥·Gateway 인코더 출력 대조로 판정했다.
- 실보드 수락은 소유 두 VM·격리 broker·file sink부터 준비하고 기본 VM·CPU 정책·
  Wi-Fi/SSH 보호, 후보/원복 번들, 실패 주입·종료 범위를 별도로 확정해야 한다.


## 공통 계약 보완 검증 — ba1b198 기준 후속

이 절은 `feat/platform/drive-recovery-integration`의 `ba1b198c05e7d3ed64eb23fae26122658b9c668a`
이후 미커밋 후보의 기록이다. 위의 초기 검증 결과·실패·미검증 사실은 당시 상태로
보존한다. 현재 사용법은 목적별 README와 MQTT 계약에 있으며 이 보고서를 실행 지시로
사용하지 않는다. 이번 실행은 보드에 접속하지 않았고 이미지 조립·SD·commit/push도 하지 않았다.

### 책임별 변경

| 묶음 | 파일/책임 | 결과 |
|---|---|---|
| 앱 교체 연결부 | `drive/zephyr/src/adapter.[ch]`, `controller.[ch]`, `main.c` | 기존 알고리즘 그대로 링크. 실제 완료 순번·입력/상태 준비·유한 출력 검사. SAFE_STOP/Standby 준비 보고 |
| 준비 계약 | C xm.h/topic/payload/policy, Python topics/policy/readiness, shared vectors | 명시적 app.readiness QoS0/no-retain/1KB, uint64 진행 string, VM별 events |
| I/O 소유 | drive-runtime io/runtime/drive_io.h | readiness 두 칸과 별도 drop/expiry 통계. 기존 F01·heartbeat 공정성·join/deadline 유지 |
| 제어 원본/출력 | Gateway authority/verified/verified_runner/config/main/sink | 영구 세대·원자 저장, 전역 flock, 재시작 disarmed, 고정 적용 기한·SAFE_STOP 다른 VM 복귀·bounded drain·실제 serial 결과 |
| 실행 신뢰 | 공용 drive_binding + 하위 xenova_drive_binding | 기존 Execution 관측/짧은 admission guard, per-VM 비동기 lease. MQTT에서 처음 본 세션을 선출하지 않음 |
| 복구→준비 | 기존 Engine/Worker/Watcher/Observer/MQTT와 rejoin | durable COMPLETED 이후 실제 provisioning/receipt/바인딩 연결. 준비는 별도 상태, 반복 VM restart 금지 |
| 배포 준비 | 기존 platform/manager recipe, drive-rejoin 예제, broker ACL | helper/module/예제 설치와 QA. 실제 broker/서비스·factory 자산은 변경하지 않음 |

공개 Manager action/spec/envelope·정상 VM 응답과 복구 completed의 뜻은 유지했다.
신규 공개 메시지는 readiness, 신규 v1 수락 프로필은 verified-drive-1이다. v2를 새
제어 namespace로 쓰지 않았다. 재개는 expected_generation + 기존 causation_id를 쓰며
요청 ID 중복을 원본에 저장한다. 구형 분리 앱은 명시적 legacy이고 새 준비로 fallback하지 않는다.

### 장애와 불확실성의 종결

| 확인 사실 | 판정/후속 주체 |
|---|---|
| 도메인 absent/crashed | 기존 Watcher 확인·Engine·Manager recover_once. 의도/신원/force 승인 유지 |
| 신뢰 바인딩 불명·관측 실패 | Gateway 출력 금지, Watcher UNKNOWN/OBSERVATION_UNAVAILABLE. 과거 세션 승인 금지 |
| 현재 실행의 실제 앱 heartbeat 손실 | 기존 전달 경로·grace·window·의도 재확인 뒤 Engine. 새 실행에서 한 번도 받지 못한 경우는 ACL/기동 실패를 구분할 수 없어 UNKNOWN |
| 입력 부족·상태 warm-up·READY 부재 | Gateway 준비 미완료·STOP. 이것만으로 정상 VM을 반복 재시작하지 않음 |
| 제어 미전달/미적용 | 원 전환 1초 안에 APPLIED 또는 FAILED. 생존과 별개. 같은 전환 재전달로 세대/기한을 늘리지 않음 |
| 복구 응답 유실·stop 동시 도착 | 취소로 단정하지 않고 같은 attempt journal 우선. 원 D+기존 verify 예산까지 불명확하면 final RECONCILE_REQUIRED, 운영자 확인. 새 물리 시도/자동 force 없음 |
| Store uncertain receipt | 새 Bootstrap 신원 발급·자동 덮어쓰기 금지. 원 execution/receipt/Store 확인 필요 |
| 원본 유실/손상·retained 충돌·세대 소진 | 출력 금지·운영자 복원/조정. retained 부재를 최초 설치로 추정하지 않음 |
| 시리얼 제출 실패 | OUTPUT_PATH_FAILED, 출력 금지 및 STOP 시도. 장치 적용은 unknown, 기존 watchdog/E-stop 유지 |

### 실행한 검증

| 층/명령 | 결과·해석 |
|---|---|
| 수정 전 `baseline.py` | 옛 세대 통신이 5초 지속해도 전환 종결이 없고 SAFE_STOP에서 다른 준비 VM만으로 복귀하지 못하는 경로 재현 |
| `cmake -S platform/apps/drive/tests ...`, ctest | 실제 C 알고리즘·adapter와 ASan/UBSan, 상태 준비/입력 만료/invalid output/uint64·토픽 검사 통과 |
| 공용 MQTT pytest + C shared vectors | 126 pass, 기존 broker 선택 시험 4 skip. 신규 readiness·VM 토픽·범위·binding/ticket 검사 |
| Gateway pytest | 84 pass. 대칭 연속 시나리오, 고정 적용 기한, 원본 손상/중단/중복 실행, 80회 실행 교체, 재시작·오래된 승인·flood·serial/aux 실패 등 |
| dom0-manager pytest | 380 pass. 실제 Engine→실제 Manager/Execution→기존 provisioning 함수/적용 receipt→binding→준비 연결 포함. Xen/Store만 외부 대역. absent 시작 및 별도 승인된 FakeXen present 재시작 모두 검사 |
| 실제 Zephyr native owner | 27개 경계 통과. 기존 25개 + readiness 교체·만료/별도 통계 |
| 같은 native_sim 파일 2개 + 실제 Mosquitto/ACL/Gateway/독립 구독기 | A 시작·B 시작 각각 13단계 통과. 서로 다른 VM 계정의 타 VM 토픽 쓰기 및 legacy 경로 우회 거절 |
| 실제 배포 ACL 격리 broker | 자기 VM 허용·타 VM/legacy/control/resume 우회 거절·backend resume 허용·잘못된 암호 거절, 7개 통과. 설치 broker는 미변경 |
| 전체 플랫폼 unittest | 897개 중 896 pass·기존 1 skip. 고정 systemd 259 loader. 신원·CPU·복구·수명주기·부팅/종료 공통 회귀 |
| 고정 ARM xenvm 빌드 | Zephyr 4.4.0 고정 commit 684c9e8f..., 기존 SDK1.0.0/GCC14.3, ELF/DTS·16MiB·netfront stack audit 통과 |
| BitBake `-c package_qa xenova-platform xenova-mqtt-client xenova-dom0-manager` | 2459 tasks 중 2424 재사용, 전부 성공. 실제 packages-split 모듈·예제와 소스 해시 일치. 공유 sstate/호스트 I/O 통계 관련 기존 경고 2개 |

FastAPI/Pydantic/HTTPX 등 시험 의존성은 사용자 승인 후 저장소 밖 test-tools에만
설치했다. 제품 runtime 의존성은 추가하지 않았다. 패키지 QA는 전체 이미지 포함 수락이 아니다.

### 고정 산출물·수치

| 항목 | 값 |
|---|---|
| 최종 ARM 입력 revision | sha256:0fdcafbc2ad5e745027c45873ba518b6cff025dd9cbc141f404c01e1e06e0dd3 |
| zephyr.bin | 391,480 bytes / 0a6367dd2a83f2e6fb658ec7d04bab867e1182666b555a2ad9786dc5d0beea72 |
| firmware.json | 88e124e927453b9fee38de6bb0a3c1429e10717899670a76f9ef329e8050fe44 |
| 같은 native 실행 파일 | c4686b90b0674b46487aa64cf96cc4718022a08cbe80ecc341fe08a6182392ce |
| 링크 RAM | 3,432KiB / 16MiB, 이전 3,424KiB 대비 +8KiB. 실사용 peak 아님 |
| 명령 출처→시리얼 제출 대조 | A 시작 938건, B 시작 938건. VM/boot/instance/message_id/seq/app/실행/세대·정확한 값/프레임 연결 |
| 준비 보고 | 독립 수신 1,724건 필드/uint64 string 검사 통과, 최대 envelope 394 bytes. 100ms 생성·1KB 상한. 두 보고 칸의 static 할당 증가 704 bytes(각 160-byte payload 포함) |
| ARM 직접 스택 frame | main 1,328B / controller_step 288B / adapter_run 80B / auto adapter 256B / manual adapter 144B. 호출 전체 peak·실사용 측정 아님 |
| control 요청→적용 확인 | 같은 Gateway 단조 시계의 12표본: 관측 최대 230.567ms. 1초 정책의 현지 관측 근거이며 Xen/차량의 보장 상한 아님 |

주기·성능 향상을 주장하지 않는다. 계산/게스트 송신/독립 수신의 서로 다른 시계를
빼서 편도 지연을 만들지 않았다. 기존 100ms·200/300ms·3초 값은 늘리지 않았다.
시리얼 쓰기 수락까지 확인했으며 실제 ESP32 적용 ACK는 확인하지 않았다.

최종 저장 오류 주입에서 replace 후 parent fsync 실패도 검사했다. 불명확한 쓰기 뒤에는 후속 쓰기를 막아 옛 메모리 세대로 롤백하지 않는다. mountpoint만 존재하는 경우에도 기존 플랫폼의 ext4/설치 표지 검사를 통과하기 전에는 원본·receipt를 만들지 않는다.

### 중간 실패와 수정 근거

* 최초 ARM에서는 최소 libc의 isfinite 미제공으로 컴파일 실패했다. compiler builtin
  finite 검사로 수정하고 최종 ARM을 새 출력 경로에서 빌드했다.
* 최초 native 통합은 시나리오를 진행한 뒤 message_id 하나로 출처를 색인한 시험 도구가
  실패했다. 기존 펌웨어 계약의 세션 내부 카운터 예외에 맞춰 복합 신원으로 수정했다.
* 중간 후보는 실제로 허가된 새 Active를 node_status에서 추가 3초 동안 unready로
  표시했다. 선택 전 준비 유지와 새 적용 이후 Active 표시를 구분했다. 입력/준비 검사나
  100ms 신선도는 완화하지 않았다. 그 실행에서는 출력 근거 상실 후 SAFE_STOP 재진입도
  기록됐다. 당시 자료만으로 모든 정체 원인을 확정하지 않으며 장시간 안정성 수락은 아니다.
* AUTO_READY 전환에서 manual의 새 보고가 먼저 오면 autonomous의 새 적용 보고가
  도착하기 전에 MANUAL로 되돌아가는 경합을 재현했다. 두 앱의 현재 세대 적용 확인을
  pending 완료 경계에 넣었다. 별도 순수 상태 시험과 최종 두 방향 통신 시험이 통과했다.

실패 결과·원자료는 성공 결과로 덮어쓰지 않았다. WSL의 arm-drive/arm-final 중간 후보와
pair-a/pair-verified-a/pair-proof-a, 최종 arm-release/pair-closure-a/pair-closure-b (직전 pair-final-a/b도 보존)를 구분한다.
공개 증거는 `C:/Users/SSAFY/Documents/Codex/2026-10-26/xenova-drive-recovery/RESULT.json`,
원 로그/수신본/제출본, WSL `/home/ssafy/xenova-drive-recovery-20261026/`에 있다.
인증 파일은 별도 root 전용 private 경로이며 Git·공개 보고서·펌웨어에는 넣지 않았다.

### 아직 연결·수락하지 않은 범위

| 담당/경계 | 남은 최소 연결·판정 |
|---|---|
| Spring `ManualInputRelay.manual/update/repeat` | MANUAL일 때 눌린 키만 공급하는 현재 구조는 SAFE_STOP/Standby 준비와 순환한다. 실제 입력 또는 신선한 중립 입력을 출력 권한과 독립적으로 공급하는 정책 연결 필요. 가짜 입력·TTL 완화 없음 |
| Spring resume 발행 및 화면 소비자 | expected_generation·현재 control causation_id·원 요청 ID·응답 유실 재확인, VM별 events 구독. 구형 events를 제어 입력으로 되먹이지 않음 |
| Display drive_state/화면 | 게스트 heartbeat의 active/표시 모드와 Gateway 실제 output_permitted를 구분하는 연결 필요. 현재 control.mode만으로 운행 허가 표시 금지 |
| 실제 장치 | 시리얼 전체 쓰기 수락과 적용 ACK는 다름. 현행 vehicle.status.applied_generation 실제 회신 연결·watchdog/E-stop 수락은 미수행 |
| 기존 설치/이미지 | 새 receipt/binding 설정·같은 최종 번들 A/B 편입·Gateway recipe/자동 시작·factory/seed/최종 WIC 포함 확인은 별도. 현행 Mosquitto bbappend는 자동 시작을 끄며 배포 ACL/계정을 설치하지 않는다. QA만으로 이미지 포함을 주장하지 않음 |
| 실보드 | 실제 Xen netfront·각 실행 Bootstrap·현재 CPU 정책/신원 보존·Store/backend 장애와 종료·재부팅 후 disarmed·물리 적용은 미수행 |

실보드 후속은 별도 승인 뒤 새 소유 VM 2개·격리 broker·mock serial에서 먼저 수행한다.
설치 runtime/기본 VM·실제 ACL·CPU·Wi-Fi/Tailscale·host를 바꾸는 범위와 원복은 각각
구체화해야 한다. 이번 후보는 기존 기본 A/B나 서비스에 배포하지 않았다.
