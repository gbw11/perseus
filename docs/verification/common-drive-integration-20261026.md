# 공통 Zephyr 주행 펌웨어·대칭 승계 검증

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
