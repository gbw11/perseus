# A/B 공통 Zephyr 주행 펌웨어

`drive/zephyr`는 기존 `auto_step()`과 `manual_step()`을 같은 펌웨어에 연결한다.
A/B에는 같은 펌웨어 번들을 설치하고, 실행 때 각 VM의 UUID·네트워크·인증 정보를
기존 Bootstrap으로 전달한다. A/B를 서로 다른 앱이나 고정 Primary/Backup으로 나누지 않는다.

## 두 가지 선택

| 선택 | 담당 | 바뀌는 것 | 유지되는 것 |
|---|---|---|---|
| AUTO/MANUAL | Gateway의 기존 모드 정책 | `mode`, `active_app`, `generation` | 활성 VM UUID·실행·펌웨어·CPU 배치 |
| 활성/대기 VM | Gateway의 UUID별 준비 관측 | `active_vm_id`, `generation` | 주행 모드. 단, 준비된 승계 대상이 없으면 `SAFE_STOP` |

두 선택은 기존 `vehicle.control` 한 메시지로 전달한다. 필드·토픽·v1/v2 봉투를
새로 만들지 않는다. `AUTO_READY`에서는 수동이 계속 제어하며, 준비 완료만으로
AUTO 출력을 시작하지 않는다. 정확한 메시지 정의는 [MQTT 계약](../../../docs/contracts/mqtt-contract-v1.md#52-주행-모드)을 따른다.

## 펌웨어 실행 구조

```text
trusted Bootstrap → Store/netfront/종료 runtime (VM당 한 번)
                  ├─ 계산 main: 동일 입력 snapshot → auto_step + manual_step
                  │              └─ 현재 모드의 명령 후보 하나 / 20ms
                  ├─ v1 I/O owner: control + manual input + perception 구독
                  │                최신 명령함 + 앱별 heartbeat 두 칸
                  └─ v2 Publisher: 별도 socket/소유권으로 실제 진단·루프 진행 발행
```

기존 두 계산 소스는 수정하거나 복제하지 않고 CMake에서 직접 링크한다. 양쪽 VM은
현재 모드의 계산 결과를 보내지만, Gateway는 활성 UUID·앱·현재 세대가 모두 맞는
명령만 시리얼 인코더로 전달한다. 대기 VM의 후보는 준비 확인용이다.

`auto_step`과 `manual_step`을 모두 마친 뒤에만 한 번의 Diagnostics 완료를 기록한다.
v2 `drive-controller`의 `loop_seq`와 계산 시간은 **두 계산을 포함한 공통 20ms 작업**의
값이다. 두 개의 독립 타이머나 실제 차량 제어 지연으로 해석하지 않는다.
v1 앱별 `app.heartbeat`는 실제 계산을 마친 main이 1초마다 생성한다. 통신 worker가
진행 신호를 대신 생성하지 않는다. `agent.heartbeat`와도 다르다.

자율 계산의 인지 부재·만료는 자율 heartbeat의 `error`, 수동 입력 부재·만료·형식 오류는
수동 heartbeat의 `error`로 보고한다. 오류가 아닌 경우 현재 활성 VM/모드만 `active`,
나머지는 `standby`다. 제어 문맥이 잘못되었으면 `idle`이며 명령을 내지 않는다.
자율 계산의 기존 장애물·인지 변화 `perception.event`는 실제 활성 AUTO에서만 발행한다.

기존 200ms 수동 입력·300ms 인지 만료, 명령함의 원 입력 시각 기반 만료,
F01 계산/송신 완료 분리, 사건/heartbeat 공정성, `TCP_NODELAY=1`, 협조 종료를 유지한다.
종료는 계산 main → v1 owner join → v2/공유 runtime 정리의 기존 소유권 규칙을 사용한다.
기존 `analyze.py`의 앱별 원자료 분석 수락은 개별 autonomous/manual 실행을 대상으로 한다.
공통 프로필의 다중 모드 원자료 분석까지 자동 지원한다고 해석하지 않는다. 아래 기능
시험은 독립 수신과 Gateway 출력 대조를 사용하며 원자료 성능 수락과 구분한다.

## Gateway 설정과 양방향 모드 전환

기존 broker 주소·인증 설정에 다음 UUID 쌍을 추가한다. 아래 UUID는 예시이며 실제
`xenova-vm list --short`의 두 등록 UUID로 바꾼다. 실제 차량 연결 전에는 `mock` sink로 확인한다.

```bash
export PYTHONPATH=platform/services/gateway:platform/services/vm-agent/mqtt-client
python3 -m xenova_gateway --sink mock \
  --vm-a aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa \
  --vm-b bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb
```

`--initial-active-vm <UUID>`는 최초 선택만 정한다. 생략하면 `--vm-a`다.
UUID 쌍을 생략한 기존 앱별 Gateway 동작은 유지한다. 한쪽만 지정하거나 같은 UUID,
쌍 바깥의 최초 활성 UUID는 거절한다. `--start-mode SAFE_STOP`은 정지 상태에서 시작한다.
환경변수와 나머지 옵션은 [Gateway 안내](../../services/gateway/README.md)에 정리한다.

| 현재 상황 | 동작 |
|---|---|
| AUTO 계산/통신은 진행하지만 인지 오류, 같은 VM의 수동 계산이 정상 | 같은 VM에서 MANUAL로 전환 |
| MANUAL 중 인지 회복 | 같은 VM의 현재 수동 명령이 신선하고 자율 계산이 3초 연속 정상인 경우 AUTO_READY |
| AUTO_READY에서 기존 `drive.resume.requested` 수락 | 기존 3초 카운트다운 뒤 조건이 계속 맞으면 같은 VM에서 AUTO |
| 준비 조건 붕괴 또는 VM 승계 중 카운트다운 | 기존 요청을 취소. 자동 출발하지 않음 |
| 활성 VM의 현재 명령/계산 준비 상실 | 현재 모드 준비를 마친 다른 UUID로 승계. 대상이 없으면 SAFE_STOP |

공통 펌웨어는 두 계산을 매번 실행하되 선택 모드의 명령만 보낸다. 따라서 MANUAL 중
AUTO 준비는 별도의 자율 명령 스트림 대신 **두 계산을 마친 현재 수동 명령의 신선도**와
자율 계산 heartbeat의 정상 유지로 확인한다. 기존의 3초 준비 유지·명시적 복귀 요청·
3초 카운트다운은 유지한다. 단일 heartbeat로 AUTO에 복귀하지 않는다.
이는 공통 펌웨어 프로필의 준비 판정이다. 기존 앱별 프로필의 명령 신선도 판정을
모든 구성에서 바꾼 것이 아니다.

제어 세대가 바뀌면 이미 제출된 이전 세대 명령이 잠시 도착할 수 있다. 현재 실행의
계산/통신 생존과 현재 제어 문맥 준비를 구분한다. 이전 세대는 출력·대기 준비·승격에
사용하지 않는다. 새 세대가 확인되기 전에는 STOP을 내보내며, 시간값을 늘려 전환을
성공으로 처리하지 않는다. 제어가 확인되지 않으면 준비 상태도 완료되지 않는다.

## 대칭 승계와 재합류

대기 준비는 현재 모드의 **같은 세대 명령을 최근 100ms 안에 수신**하고, 해당 계산의
정상 heartbeat가 3초 연속 유지되며 마지막 heartbeat가 3초 안인 상태다.
등록 UUID만 같다고 준비 완료로 판단하지 않는다. 같은 펌웨어 배포·각 VM의 신뢰된
Bootstrap/ACL은 설치 조건이며 Gateway가 펌웨어 해시를 원격 검증하는 것은 아니다.

복구한 VM의 `boot_id`/`agent_instance_id`가 바뀌면 과거 준비와 후보를 지운다.
현재 제어·새 입력을 받아 실제 계산을 수행하고 위 조건을 다시 충족해야 Standby가 된다.
그동안 기존 활성 VM은 계속 Active다. 이후 활성 VM 장애 때에는 재합류한 VM도
동일한 규칙으로 승계할 수 있다. 전환 때마다 세대를 올리고, 새 활성 VM이 새 세대
명령을 보낼 때까지 STOP을 유지한다.

Gateway는 같은 프로세스에서 퇴역한 실행의 지연 명령과 retained 진행 메시지를
거절한다. VM별 퇴역 실행은 최대 64개까지 기억하며 한도를 넘으면 새 세션을 거절한다.
Gateway 재시작 때는 retained control의 활성 UUID·모드를 보존하고 세대를 올린다.
준비·수신 세션은 다시 확인한다. retained 데이터의 세대/모드/UUID가 모순되면 시작을
거절하며, 부재 시에는 설정한 최초 상태를 사용한다.

이 동기화는 공통 펌웨어·제어 문맥·신선한 입력·대기 계산 수준이다. 앱 전체 메모리,
필터 내부 상태, 적분기, 물리 차량 상태를 복제하지 않는다. Gateway는 Xen domain 존재나
CPU 배치를 조회·변경하지 않으며, VM 재시작은 기존 Manager/복구 계층의 책임이다.
프로세스 재시작을 넘는 퇴역 세션의 영구 신원 대조에는 향후 외부 실행 바인딩 연동이
필요하다. 현재의 MQTT 관측을 Xen 실행 신원 보장으로 확대하지 않는다.

## 인증과 설치 조건

펌웨어에 UUID·암호를 넣지 않는다. A/B의 v1 계정은 각각 자기 UUID와 암호를 사용하고,
두 기존 앱 events 쓰기 및 control·수동 입력·인지 읽기를 허용해야 한다. v2는 각자의
VM telemetry 토픽에만 쓰는 기존 별도 채널을 유지한다. 앱 이름을 계정으로 사용하는
옛 `%u` ACL은 공통 A/B 구성에 그대로 사용할 수 없다.

선택한 두 UUID에만 아래 규칙을 각각 적용하고 broker 설정은 별도로 검토한다.

```text
user <해당 VM UUID>
topic write xenova/v1/apps/autonomous/events
topic write xenova/v1/apps/manual/events
topic read xenova/v1/vehicle/control
topic read xenova/v1/apps/manual/input
topic read xenova/v1/apps/perception/events
```

VM UUID·MAC·인증정보·CPU 배치는 복제하거나 역할에 따라 옮기지 않는다.
같은 번들을 두 등록 VM의 독립 자산으로 검증·적용하는 절차는 기존 firmware/Manager
계약을 따른다. 이 소스 추가만으로 기존 게시 번들이나 기본 A/B의 펌웨어는 바뀌지 않는다.

## 빌드와 검증

기존 고정 Zephyr 소스·SDK를 지정한다. 새 출력 경로를 사용하며 이미지는 조립하지 않는다.

```bash
python3 platform/scripts/build-xenova-drive-app.py --app drive \
  --zephyr-source "$ZEPHYR_SOURCE" --tools "$ZEPHYR_TOOLS" \
  --output "$NEW_OUTPUT" --execute
```

계산 두 소스와 공통 runtime을 포함한 입력 해시·compiler·ELF/DTS·stack 검사·bundle
receipt를 보관한다. A/B별로 빌드하지 않고 같은 bundle 해시를 사용한다.

로컬 회귀는 Gateway의 `python3 -m pytest`, `tests/controller.c`의 실제 계산 sanitizer
시험, 기존 `platform/tests/apps/xenova-drive-io`의 thread/종료 시험을 사용한다.

```bash
cmake -S platform/apps/drive/tests -B "$HOST_BUILD" -GNinja
cmake --build "$HOST_BUILD"
ctest --test-dir "$HOST_BUILD" --output-on-failure
(cd platform/services/gateway && python3 -m pytest)
```

`tests/run_native_pair.py`는 같은 native_sim 실행 파일 두 개·격리 TAP/Mosquitto·
실제 Gateway·독립 v1/v2 구독기로 다음 연속 시나리오와 반대 순서를 실행한다.

```text
A/AUTO → 인지 장애 → A/MANUAL → A 장애 → B/MANUAL
→ 인지 회복·준비 유지·복귀 요청·카운트다운 → B/AUTO
→ A 새 실행 재합류·준비 → A Standby / B Active 유지
→ B 장애 → A/AUTO
```

시험은 새 network namespace의 root 권한과 저장소 밖의 기존 Mosquitto·시험 Python
도구를 요구한다. 물리 출력 대신 실제 시리얼 인코더의 file sink를 사용한다.
native_sim 수락은 ARM/Xen·실보드·이미지 포함·차량 성능 수락과 별개다.
실행별 결과와 미검증 범위는 [검증 기록](../../../docs/verification/common-drive-integration-20261026.md)에 둔다.
