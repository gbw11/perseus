# A/B 공통 Zephyr 주행 펌웨어

같은 `drive/zephyr` 번들을 두 VM에 사용한다. 기존 `auto_step()`과 `manual_step()`은
바꾸지 않고 링크하며, VM마다 UUID·MAC·인증정보·CPU 배치를 유지한다.
AUTO/MANUAL 계산 모드와 Active/Standby VM 선택은 서로 다른 축이다.

## 책임과 확인 근거

| 사실 | 확인하는 곳 | 대신 확인할 수 없는 것 |
|---|---|---|
| MQTT 연결·구독 | v1 socket / Gateway의 CONNACK·SUBACK | 앱 준비·제어 적용 |
| 현재 허용된 실행 | Manager Execution 관측 + 적용된 Bootstrap receipt의 root 전용 바인딩 | 게스트 본문에 적은 execution ID만으로 승인 |
| 실제 계산 완료 | 앱 어댑터의 `progress_seq` | worker 송신 횟수 |
| 제어 적용·기능 준비 | `app.readiness` | 출력 권한 |
| 출력 허가 | Gateway 영구 원본·현재 제어 적용·명시적 재무장 | heartbeat 복귀·부팅 성공 |
| 시리얼 제출 | 실제 쓰기 수락 바이트 수 | 최종 장치 적용 |

토픽·필드·자료형·준비 보고 규칙의 원문은 [MQTT 계약](../../../docs/contracts/mqtt-contract-v1.md#62-앱-준비-보고-appreadiness)이다.
현재 장치 적용 ACK는 연결되지 않았다. `vehicle.status.applied_generation`을 추측해 채우지 않는다.

## 계산과 앱 교체 경계

```text
실행별 Bootstrap → netfront/Store/협조 종료 runtime
                 ├─ main: 동일 입력 snapshot → AUTO 어댑터 → MANUAL 어댑터
                 │         실제 완료·준비 평가 / 현재 모드의 명령 후보 하나 / 20ms
                 ├─ v1 owner: control·수동 입력·인지 구독, 최신 명령 + 보고 칸 4개
                 └─ 별도 v2 Publisher: Diagnostics Core의 실제 관측값
```

`zephyr/src/adapter.h`의 `drive_adapter`가 계산·실제 완료 순번·입력 유효성·상태 준비·
유한한 출력값 검증을 맡는다. 현재 두 계산은 상태 없는 어댑터로 선언한다. 상태 있는
앱으로 교체할 때는 `requires_state_sync=true`, `state_ready=false`로 시작하고 앱이
warm-up·동기화를 확인해야 한다. 함수가 반환됐다는 사실만으로 READY가 되지 않는다.
계산 오류는 진행 순번을 올리지 않으며, 잘못된 출력은 0으로 안전 처리하고 NOT_READY다.
입력 부족·만료는 계산 실패와 별개다. 안전 정지 입력(`keys: []`, 인지 `stop`)도
형식·신선도가 유효하면 기능 준비와 구분할 수 있다. 입력을 가짜로 만들어 준비시키지 않는다.

두 계산은 현재 직렬 실행한다. Diagnostics의 공통 `loop_seq`·step 시간은 두 계산을
포함한 20ms 작업이다. 결합 비용은 이 release 안에 들어와야 하며 초과·누락은 기존
진단에 남긴다. 임의의 callback 정지나 AUTO 무한 대기를 어댑터가 선점하지는 못한다.
그 경우 MANUAL·공통 진행도 멈춘다. 예산을 지킬 수 없는 후속 앱은 이 호출 경계에서
별도 실행 구조를 검토해야 하며 통신 worker가 진행·READY를 대신 만들지 않는다.

고정 release origin, 놓친 계산을 몰아서 실행하지 않는 정책, 원 입력 수신 시각,
200ms 수동 입력·300ms 인지 만료, 명령 100ms 신선도를 유지한다. F01의 창 확정/송신
확정 분리, 최신 후보/in-flight 분리, 사건과 보고의 공정성, `TCP_NODELAY=1`,
부분 초기화·공통 종료 deadline·worker join·미회수 grant 재사용 금지도 유지한다.

## 모드·VM·출력 허가

Gateway 기본 `verified` 프로필은 영구 제어 원본과 신뢰된 실행 바인딩을 필수로 사용한다.
구형 분리 앱은 명시적 `--profile legacy`이며 공통 A/B의 준비·신원 수락으로 간주하지 않는다.

* AUTO↔MANUAL은 같은 VM 실행에서 계산 모드와 generation만 바꾼다.
* 인지 입력이 부적합하고 같은 VM의 수동 준비가 3초 유지됐다면 MANUAL로 전환한다.
  MANUAL 중 자율 준비가 3초 유지되면 AUTO_READY다. 이때 출력은 수동이며,
  현재 문맥의 명시적 복귀 요청·3초 카운트다운·새 제어 적용을 거쳐 AUTO가 된다.
* A/B 승계는 현재 모드에 준비된 다른 실행을 고른다. 없으면 SAFE_STOP이다.
  SAFE_STOP에서도 출력 없이 준비 보고를 받고 다른 VM을 선택할 수 있다.
  이 경우 MANUAL 선택은 disarmed이며 재무장 전 주행을 허가하지 않는다.
* 새 제어는 저장 → 비동기 발행/PUBACK → 앱 적용 확인 순서다. 전환 적용 기한은
  발급 때 고정한 **1초**다. 100ms 명령 신선도, 3초 준비 유지·재개, VM 복구 예산과
  다른 값이며 정상 제어 왕복을 확인하는 제한 예산이다. 재전달로 연장하지 않는다.
  PUBACK·적용 미확인 시 FAILED와 이유를 보존하고 STOP을 유지한다. 자동 세대 폭증이나
  앱 준비 실패만을 이유로 한 VM 재시작은 하지 않는다. 신선한 준비와 명시적 재무장으로
  새 전환을 시작하거나, 저장/retained 충돌이면 원본을 확인하고 운영자가 조정한다.
* 복구한 A가 준비돼도 B의 Active를 빼앗지 않는다. 이후 B 장애 시 준비된 A도
  같은 규칙으로 승계한다. 앱 전체 메모리 복제는 지원 범위가 아니다.

## 영구 원본과 설치

[Gateway 운영 절차](../../services/gateway/README.md#verified-프로필-운영)를 따른다.
원본은 `/srv/xenova/drive-control/control.json`, 초기화 표지는 같은 디렉터리의
`initialized`다. 디렉터리 0700·파일 0600이며 실제 데이터 마운트가 필수다.
기존 플랫폼의 ext4·초기화 표지·최초 설치 완료 검사를 재사용한다. 단순 bind mount를
데이터 준비 완료로 취급하지 않는다. 저장 결과가 불명확해지면 현재 프로세스의 후속
쓰기를 차단하여, 이미 replace된 새 세대를 옛 메모리 사본으로 되돌리지 않는다.
retained control은 배포 사본이다. 원본이 없거나 손상됐을 때 generation 1을 추측하지 않는다.
최초 설치를 확인한 운영자만 `--initialize-control-state`를 한 번 실행한다.
재설치·상태 복원 시에는 같은 보드의 원본과 표지를 함께 보존하고 더 최신인 retained와
충돌하지 않는지 확인한다. 세대는 uint32이며 소진 시 초기화·wrap 없이 차단한다.

Gateway/dom0 재시작은 마지막 모드·선호 owner를 복원하지만 출력은 금지한다.
현재 실행·제어·준비 유지·문맥에 결합한 새 요청·카운트다운을 다시 확인한다.
단일 dom0의 모든 Gateway 진입은 `/run/lock/xenova-drive-gateway.lock`을 먼저 획득한다.
다른 root 도구나 외부 장치 제어까지 원자적으로 통제한다는 뜻은 아니다.

## 실행 바인딩과 자동 복구

기존 Recovery Worker 안의 `RejoinCoordinator`를 선택적으로 활성화한다.
예시는 `platform/services/dom0-manager/deploy/drive-rejoin.example.json`이다.
등록된 두 UUID와 각 VM의 **별도 v1/v2** private credentials 파일 경로를 운영자가 설정한다.
공개 이미지·Git에 인증정보를 넣지 않는다. 설정 파일은 root 전용 0600으로 저장하고
기존 recovery-worker 환경 파일에 `XENOVA_RECOVERY_DRIVE_CONFIG=<설정 파일>`을 지정한다.
새 daemon, 상위 HTTP API 또는 별도 VM 재시작기는 추가하지 않는다.

```text
기존 Engine / recover_once → 원 attempt journal의 platform COMPLETED
 → 새 execution 관측 → v2 Bootstrap receipt → 같은 boot의 별도 v1 receipt
 → root 바인딩 → 현재 제어 적용·진행·입력/상태 준비 → Standby
```

플랫폼 COMPLETED는 새 Xen 실행 관측이라는 기존 뜻이다. rejoin의 PROVISIONING,
PROVISIONED, PREPARING, READY_OBSERVED는 별도 상태다. READY_OBSERVED는 출력 권한이나
지속 보장도 아니다. 보고가 만료되면 PREPARING으로 돌아간다.

`xenova_drive_binding.observe()`는 기존 Execution의 UUID/name/domid/xen_unique_id 확인과
짧은 admission 잠금을 재사용한다. 전체 관측 예산 2초, VM별 별도 작업 슬롯으로
다른 VM의 긴 물리 실행 잠금 때문에 정상 VM의 갱신이 막히지 않게 한다. Gateway는
메시지마다 xl/Manager를 호출하지 않고 root 파일을 100ms마다 읽는 메모리 사본을 쓴다.
관측은 VM별 1초 간격, 같은 host_boot_id의 3초 lease다. stopped/absent/unavailable/
provisioning을 구별하며 실패 시 과거 신원으로 새 세션을 승인하지 않는다.
이는 관측 시점의 보장이다. 외부 root의 변경과 원자적이라고 주장하지 않는다.

적용 receipt의 uncertain은 자동 재주입하지 않는다. 원 실행·Store·receipt를 확인한다.
Store C 호출이 외부에서 정체하면 작업이 남을 수 있으며 이를 join/적용 완료로 보고하지
않는다. 바인딩 만료는 출력을 막는다. Worker 종료 상한과 실제 Store 장애는 실보드에서
추가 확인해야 한다. 응답을 잃은 복구는 같은 attempt·원 deadline의 journal을 먼저
확인한다. 원 실행 기한 뒤 기존 verify 예산까지 확인이 안 되면 final
RECONCILE_REQUIRED로 운영자에게 넘기며 새 force나 재시작 시도를 발급하지 않는다.

## 발행 출처와 소비자 이행

verified 앱은 `xenova/v1/vms/{vm_id}/apps/{app_id}/events`에 발행한다.
username은 VM UUID이며 자기 두 앱 경로만 쓰도록 ACL을 제한한다. Gateway·Worker는
토픽/봉투의 VM·앱과 신뢰된 현재 boot/instance를 함께 검사한다. Legacy events와
retained READY는 우회 준비 근거가 아니다. v2 namespace·의미는 그대로다.
`platform/deploy/mosquitto/etc/acl`은 이 경로와 backend의 console/resume 권한을 정의한다.
현재 Mosquitto bbappend는 daemon 자동 활성화를 끄고 있으며 이 ACL·계정을 자동
배포하지 않는다. 운영 broker 설정/계정 반영은 별도 배포 검토가 필요하다.

현재 제품 Spring의 `ManualInputRelay`는 MANUAL일 때 눌린 키만 반복한다.
verified 프로필의 정지/Standby 준비에 필요한 실제 입력 또는 신선한 중립 입력 공급은
아직 연결되지 않았다. 출력 권한을 주어 입력을 얻거나 캐시로 READY를 만들지 않는다.
Spring/화면 담당자는 VM별 토픽 구독, `expected_generation` 및 기존 envelope의
`causation_id`를 이용한 재개 요청, 출력 권한과 표시 모드의 구분을 연결해야 한다.
이 연결 전에는 제품 HTTP/UI 종단 수락으로 표시하지 않는다.

## 빌드·시험·이미지 편입

```bash
cmake -S platform/apps/drive/tests -B "$HOST_BUILD" -GNinja
cmake --build "$HOST_BUILD"
ctest --test-dir "$HOST_BUILD" --output-on-failure
(cd platform/services/gateway && python3 -m pytest)
(cd platform/services/dom0-manager && python3 -m pytest)
python3 platform/scripts/build-xenova-drive-app.py --app drive \
  --zephyr-source "$ZEPHYR_SOURCE" --tools "$ZEPHYR_TOOLS" \
  --output "$NEW_OUTPUT" --execute
```

고정 소스·SDK, 입력 해시·ELF/DTS·스택 감사·bundle receipt를 보관한다.
A/B별 다른 바이너리를 만들지 않고 같은 최종 번들 해시를 각 독립 자산에 연결한다.
Factory UUID·자산·CPU 정책과 인증정보는 기존 설치 계약으로 별도 유지한다.
소스/ARM 번들 성공과 seed·이미지 포함 확인은 다르다. 최종 이미지 조립은 별도 단계다.

`tests/run_native_pair.py`는 같은 native_sim 파일 두 개, 실제 격리 broker/ACL,
Gateway·독립 v1/v2 구독기·실제 serial encoder와 file sink로 대칭 시나리오를 검사한다.
준비·바인딩은 명시적인 native 시험 fixture다. 실제 Engine→Manager→provisioning
연결은 `dom0-manager/tests/test_drive_rejoin.py`에서 따로 검사한다. 시리얼 제출 기록은
VM/boot/instance/message_id/실행/app/세대와 프레임을 연결한다. 펌웨어 message_id는
세션 내부 카운터이므로 전역 message_id 하나만으로 명령을 대조하지 않는다.

native/ARM/실보드/이미지/차량 성능 수락을 구별한다. 특정 후보·실패·해시는
[검증 기록](../../../docs/verification/common-drive-integration-20261026.md)에 둔다.
