# 차량 제어 상세 명세

[아키텍처 설계서](../architecture.md)의 구현 계약이다. 설계서는 "무엇을, 왜"만 다루고, 이 문서는 게이트웨이·컨트롤러·ESP32 펌웨어·프로토콜을 구현하는 사람이 참조하는 "어떻게"를 다룬다. 처음 읽는 사람은 설계서부터 보면 된다.

> **상태: 구현 계약 초안.** 5차 리뷰까지 반영됐다. 수치는 모두 **[가정]**, **[목표]**, **[실측]** 중 하나로 표시한다. 표시가 없는 수치는 가정이다.
>
> 구현 전 확정 항목은 5.3절, 팀 결정이 필요한 항목은 9절에 있다.

---

## 1. 설계 원칙

1. **차량 ECU 구조를 따른다.** 인지(ADAS), 중앙 컴퓨터, 섀시 제어를 보드 단위로 분리한다.
2. **주행 명령은 VM에서 나온다.** 하이퍼바이저 위 VM이 실제 제어를 맡아야 VM 장애 복구 시연이 의미를 가진다.
3. **최종 안전장치는 하이퍼바이저 밖에 둔다.** VM·dom0·네트워크가 전부 죽어도 ESP32 워치독과 하드웨어 비상정지가 로봇을 세운다.
4. **중재는 한 곳에서만 한다.** 활성/대기 판단은 dom0의 차량 게이트웨이만 한다. VM 재시작은 VM 매니저의 복구 서비스만 한다.
5. **신뢰는 연결에서 나온다.** 메시지 본문의 발신자 표기는 믿지 않는다.
6. **잘못된 승격보다 정지가 낫다.** 대기 VM이 확실히 준비되지 않았으면 넘기지 말고 세운다.
7. **보드 없이 개발 가능해야 한다.** 게이트웨이·VM·에이전트는 QEMU Xen과 목(mock) 섀시로 동일하게 동작한다.

---

## 2. 시스템 구성

### 2.1 ECU 매핑

| 보드 | 차량 역할 | 맡는 일 | 연결 |
| --- | --- | --- | --- |
| Jetson Nano B01 | ADAS ECU | D435f로 장애물·차선·목표 경로 계산. **주행 명령은 내지 않음** | 이더넷 → RPi4 |
| RPi4 + Xen | 중앙 컴퓨터 (콕핏 도메인 컨트롤러) | dom0: VM 매니저, 차량 게이트웨이, MQTT, 표시 서비스 / VM: 주행 컨트롤러 활성·대기, 콕핏, 데이터 레코더 | 이더넷, CAN 또는 USB 시리얼, WiFi 내장 |
| ESP32 DevKitC | 섀시 ECU | 모터 PWM, 조향 서보, 엔코더 오도메트리, 비상정지 상태, 배터리 ADC, VL53L5CX 근거리 감지, 명령 워치독 | CAN 또는 USB 시리얼 → RPi4 |

### 2.2 블록 다이어그램

```text
  D435f ──USB3──┐
                ▼
   ┌──────────────────────────────┐
   │ Jetson Nano  (ADAS ECU)      │  perception 15 Hz [목표]
   │ 장애물·차선·목표 경로          │─────────────┐
   └──────────────────────────────┘             │ Ethernet
                                                ▼
   ┌───────────────────────────────────────────────────────┐
   │ RPi4 + Xen  (중앙 컴퓨터)                               │
   │  ┌───────────┐  ┌───────────┐  ┌───────────┐          │
   │  │ VM-A      │  │ VM-B      │  │ VM-C      │          │
   │  │ Debian    │  │ Ubuntu    │  │ Alpine    │          │
   │  │ 주행 활성  │  │ 주행 대기  │  │ 콕핏       │          │
   │  │           │  │ + 레코더  │  │ 계기판·IVI │          │
   │  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘          │
   │        └──────── xenbr0 (가상 네트워크) ─┘              │
   │  dom0: VM 매니저 │ 차량 게이트웨이(별도 프로세스) │ MQTT │ 표시 서비스 │
   └───────────────────────────┬───────────────────────────┘
                               │ CAN 500 kbps (2단계) / USB 시리얼 (1단계)
                               ▼
   ┌──────────────────────────────┐
   │ ESP32  (섀시 ECU)             │
   │ 모터·조향·엔코더·E-stop·워치독  │
   └──────┬───────────────┬───────┘
     MDD10A → 모터 ×2    UBEC → 조향 서보
```

### 2.3 네트워크

| 구간 | 매체 | 용도 | 비고 |
| --- | --- | --- | --- |
| Jetson ↔ RPi4 | 이더넷 직결 | perception, 카메라 영상 스트림 | 고대역 전용 |
| RPi4 dom0 ↔ ESP32 | 1단계 USB 시리얼, 2단계 CAN 500 kbps | 주행 명령, 차량 상태 | 5.2의 공통 봉투(envelope)를 매체별로 매핑 |
| RPi4 ↔ 외부 | RPi4 내장 WiFi | 관리 콘솔, 텔레매틱스 | Jetson에 WiFi 불필요 |
| VM ↔ dom0 | xenbr0 | 명령·역할·perception 중계 | 지연 1 ms 미만 **[가정]**, 측정 필요 |

CAN 노드는 ESP32(내장 TWAI + SN65HVD230)와 RPi4 dom0(USB-CAN 어댑터) 둘뿐이다. Xen ARM은 USB 패스스루가 실용적이지 않으므로 CAN·시리얼 장치는 항상 dom0가 소유하고, VM은 게이트웨이를 통해서만 차량에 접근한다.

---

## 3. 소프트웨어 배치

### 3.1 RPi4 자원 배분

| 도메인 | OS | 역할 | 물리 코어 | 메모리 |
| --- | --- | --- | --- | --- |
| dom0 | Yocto (Xen dom0) | 차량 게이트웨이(cpu0 고정), VM 매니저, 제어용 MQTT, 표시 서비스, CAN/시리얼 소유 | **cpu0 전용** + cpu3 (매니저·키오스크만) | 1.5 GB **[제안]** |
| VM-A | Debian | 주행 컨트롤러 **활성** | **cpu1 전용** | 1 GB |
| VM-B | Ubuntu | 주행 컨트롤러 **대기** + 이벤트 데이터 레코더(경량 로깅) | **cpu2 전용** | 1 GB |
| VM-C | Alpine | 콕핏: 계기판(OS face), 인포테인먼트, 텔레매틱스, 앱용 MQTT | cpu3 (dom0 비핵심 작업과 공유) | 1 GB |

- **메모리 전제: RPi4 8GB.** 합계 4.5 GB에 Xen 오버헤드를 더하면 4GB 모델에서는 불가능하다. 보유 보드 4대 모두 실제 용량을 확인한다.
- **dom0 메모리 불일치.** 현재 Yocto 설정(`platform/yocto/config/local.conf.fragment`)은 `RPI_DOM0_MEM = "1024M"`이다. dom0에서 Chromium 키오스크까지 돌면 1 GB는 빠듯하므로 1.5 GB를 제안한다. 플랫폼 조가 값을 하나로 확정하고 Yocto 설정과 이 표를 일치시킨다.
- dom0 메모리는 하위 영역에 배치한다. RPi4의 xHCI(USB)는 하위 3 GB, SD 컨트롤러는 하위 1 GB까지만 DMA가 가능하다. 플랫폼 조 확인 항목.
- **cpu0는 dom0 전용, 그 안에서 게이트웨이 프로세스를 cpu0에 고정**한다. 매니저와 키오스크는 `CPUAffinity=3`으로 cpu3에 고정해 VM-C와 공유한다. USB-CAN·시리얼 IRQ는 cpu0에 묶는다. 핵심 경로(게이트웨이, VM-A, VM-B)는 코어를 공유하지 않는다.
- 대기 VM에는 무거운 앱을 두지 않는다. 인포테인먼트는 VM-C로 옮겼다. VM-B의 레코더는 차량 상태를 SQLite에 쓰는 수준의 경량 앱이며, 컨트롤러보다 낮은 우선순위로 돈다.
- **4번째 VM은 전용 코어가 없다.** 동종 미러(Debian 2개)를 원하면 코어 오버커밋이 생기므로, 지연 영향을 측정하기 전에는 추가하지 않는다.

### 3.2 구성요소와 저장소 위치

| 구성요소 | 위치 | 설명 |
| --- | --- | --- |
| VM 매니저 | `platform/services/dom0-manager/` | 하이퍼바이저 추상화, 에이전트 heartbeat, **복구 서비스(VM 재시작의 단일 소유자)**, 메트릭, 웹 콘솔 프록시 |
| **차량 게이트웨이** | `platform/services/vehicle-gateway/` **[제안, 신설]** | 별도 프로세스·별도 systemd unit. 명령 중재, 승격·정지, CAN/시리얼 송수신, 전환 시간 기록 |
| VM 에이전트 | `platform/services/vm-agent/` | OS 정보·메트릭·heartbeat, 데모 명령(부하·강제 종료) |
| 주행 컨트롤러 | `platform/apps/` (VM-A, VM-B 공통 코드) | perception → 속도·조향 명령 |
| 레코더, 콕핏 앱 | `platform/apps/` | VM-B, VM-C |
| 표시 서비스 | `platform/services/display/` | HDMI 0/1 라우팅, 키오스크 |
| 관리 서버·콘솔 | `backend/`, `frontend/` | REST/WebSocket, 대시보드, 이벤트 타임라인 |
| 인지 (Jetson) | 위치 미정 (`perception/` 제안) | D435f 처리, perception 발행 |
| 섀시 펌웨어 (ESP32) | 위치 미정 (`firmware/chassis-ecu/` 제안) | 모터·서보·엔코더·워치독·CAN |

**게이트웨이를 매니저에서 분리하는 이유.** 매니저는 `xl` 호출, XenStore, 콘솔 프록시, 복구처럼 느리거나 멈출 수 있는 작업을 한다. 게이트웨이는 10 ms 주기 검사로 100 ms 초과 끊김을 감지해야 한다(감지까지 최대 약 110 ms). 한 프로세스에 두면 VM 관리 지연이 주행 중재 지연으로 번진다. 게이트웨이는 다음 조건으로 돈다.

- 별도 systemd unit, `CPUAffinity=0`, systemd watchdog(`WatchdogSec=1`)로 행(hang) 시 재시작. 주기 루프는 매 10 ms 반드시 sleep/block한다.
- **실시간 스케줄링은 1단계에서 쓰지 않는다.** CPU 고정만으로 지터를 먼저 잰다. 측정 결과 필요할 때만 2단계로 `SCHED_FIFO`를 적용하며, 그때는 우선순위(예: 50), `RLIMIT_RTTIME`(실시간 프로세스가 블로킹 없이 연속으로 쓸 수 있는 CPU 시간 상한, 예: 5 ms. 초과 시 SIGXCPU), 최악 실행 시간 측정, 폭주 프로세스 시험을 함께 한다. 폭주한 실시간 스레드는 systemd watchdog으로 못 잡기 때문이다.
- 매니저와는 MQTT 토픽으로만 연결: 매니저 → 게이트웨이 `vm/<id>/health`(retained 스냅샷, 3.3), 게이트웨이 → 매니저 `vehicle/role`, `vehicle/failover_event`. 게이트웨이는 시작할 때 `manager/health/request`를 발행해 전체 스냅샷을 다시 받는다.
- 게이트웨이가 죽으면 ESP32 워치독이 차를 세운다. 게이트웨이 재시작 후에는 세션 재수립 전까지 명령을 보내지 않는다(4.2).

### 3.3 생존 신호와 소유자

세 가지 신호는 의미가 다르고 소유자도 다르다. 섞어 쓰지 않는다.

| 신호 | 의미 | 생산자 | 소비자(판정 소유자) | 쓰임 |
| --- | --- | --- | --- | --- |
| 에이전트 heartbeat | VM과 에이전트가 살아 있음 | VM 에이전트 | VM 매니저 복구 서비스 | VM 재시작 여부 |
| 명령 신선도 | 컨트롤러 프로세스가 제어 가능한 상태 | 컨트롤러 | 차량 게이트웨이 | 활성 장애 판정, 승격 조건 |
| 상태 동기화 신선도 | 대기 컨트롤러가 활성과 같은 상태 | 활성 컨트롤러 → 대기 컨트롤러 | 차량 게이트웨이 (활성의 `controller_state` 스트림을 직접 구독해 수신 시각을 기록하고, 대기 cmd의 `applied_state`로 나이를 **직접 계산**) | 승격 조건 |
| 의도 상태(desired state) | 사용자가 VM을 running/stopped 중 무엇으로 두었나 | 관리 콘솔·API | VM 매니저 | 사용자가 정지한 VM은 재시작하지 않음 |

- VM 재시작은 **복구 서비스 한 곳**만 한다. 게이트웨이는 승격과 정지만 한다. 백엔드는 둘 중 누구의 판정도 대신하지 않는다.
- 복구 서비스는 desired state가 running인 VM만 heartbeat 장애로 재시작한다. 시연에서 `xl destroy`로 죽인 VM은 desired state가 running이므로 재시작 대상이다.
- 대기 VM이 스스로 보고하는 값(동기화 나이 등)은 승격 판단에 쓰지 않는다. 고장난 컨트롤러는 항상 "동기화됨"이라고 보고할 수 있다. 게이트웨이가 직접 관찰한 값만 쓴다.
- `vm/<id>/health`는 변경 이벤트가 아니라 **retained 스냅샷**이다(`ok, desired_state, manager_seq, ts`). 게이트웨이가 재시작해도 현재 상태를 바로 받는다. 제어 토픽의 retain 금지 규칙은 이 토픽에 적용하지 않는다.

---

## 4. 주행 제어와 핫 스탠바이

### 4.1 동작 원리

1. Jetson이 perception을 발행한다. VM-A와 VM-B가 **둘 다** 받아 **둘 다** 명령을 계산한다.
2. 두 VM 모두 명령을 dom0 게이트웨이에 보낸다. 활성 VM은 추가로 `controller_state`를 발행하고, 대기 VM은 이를 받아 자기 내부 상태를 덮어쓴다(상태 추종). 대기 VM은 자기 명령에 **마지막으로 적용한 상태의 식별자**(`applied_state`: 원본 VM, 원본 컨트롤러 세션, 순번)를 실어 보낸다. 게이트웨이도 `controller_state`를 구독해 식별자별 수신 시각을 기록하고, 그 시각으로 동기화 나이를 직접 계산한다.
3. 게이트웨이는 **활성 VM의 명령만** ESP32로 전달한다. 대기 VM의 명령은 버리되, 활성과의 편차를 기록한다.
4. 활성 VM 명령이 끊기고 **대기 VM이 승격 조건을 모두 만족하면** 게이트웨이가 대기 VM을 활성으로 바꾸고 즉시 그 명령 스트림을 전달하기 시작한다. 조건을 못 채우면 승격하지 않고 ESP32에 정지 프레임을 보낸다.
5. 복구 서비스가 죽은 VM을 정책에 따라 재시작한다. 게이트웨이는 그 VM의 명령 스트림이 안정된 뒤에만 대기로 등록한다.

같은 입력을 받는다고 두 컨트롤러의 내부 상태가 저절로 같아지지는 않는다. 그래서 활성 VM의 상태를 명시적으로 복제한다. 이것이 이 설계에서 말하는 "미러링"이다.

**상태 동기화**

- 컨트롤러를 가능한 한 무상태로 설계한다. 기본 알고리즘은 pure pursuit로, 상태는 경로 식별자, 현재 웨이포인트 번호, 주행 모드뿐이다. 속도 PI 제어의 적분항처럼 상태가 생기면 반드시 `controller_state`에 포함한다.
- 대기 VM은 자기 계산을 계속하되, 받은 `controller_state`로 `route_id, mode, wp_idx, integ_v`를 덮어쓴다.
- 동기화가 오래된 대기 VM은 승격하지 않는다(4.2 `standby_ok`). 자체 상태로 출발하는 경우는 두지 않는다. 승격 시점에 웨이포인트나 모드가 갑자기 바뀌는 것을 막기 위해서다.
- 차량 상태(`vehicle_status`)는 dom0가 두 VM에 모두 중계하므로 별도 동기화가 필요 없다.

### 4.2 게이트웨이 중재 규칙

**발신자 식별.** VM의 신원은 메시지 본문이 아니라 **연결**에서 정한다. MQTT를 쓰면 VM별 계정과 publish ACL(`vehicle/cmd/<vm>`에는 해당 VM 계정만 발행 가능)로, UDP를 쓰면 xenbr0의 vif별 고정 IP와 dom0 쪽 ebtables로 MAC·IP 위조를 막는다. 본문의 `src`는 로깅용이며 연결에서 얻은 신원과 다르면 버린다.

**세 가지 판정을 분리한다.** 메시지 하나가 유효한지, 스트림이 신선한지, 컨트롤러가 건강한지는 다른 질문이다. 섞으면 긴 공백 뒤에 정상으로 돌아온 컨트롤러를 영원히 거부하는 버그가 생긴다.

| 판정 | 정의 | 쓰임 |
| --- | --- | --- |
| 메시지 유효성 `valid(cmd, vm)` | 아래 표. 수신 간격은 보지 않는다 | 유효하면 `last[vm]` 갱신. 무효면 버림 |
| 스트림 신선도 `fresh(vm)` | `now − last[vm].t_recv ≤ 100 ms` | 활성 장애 판정, 승격 조건 |
| 건강 `healthy(vm)` | 최근 500 ms 동안 유효 명령 25개 이상, 그 사이 간격 100 ms 초과 없음 | 대기 등록, 재무장 |

긴 공백 뒤 첫 정상 명령은 **최신 후보로 받아들인다**(`last[vm]` 갱신). 다만 그 VM은 `healthy` 조건을 다시 채우기 전까지 대기로 등록되지 않는다.

**메시지 유효성 `valid(cmd, vm)`.** 하나라도 실패하면 버리고 `last[vm]`을 갱신하지 않는다.

| 검사 | 기준 |
| --- | --- |
| 신원 | 연결에서 얻은 VM == 토픽/소켓이 가리키는 VM |
| 생산자 세션 | `controller_session_id`(컨트롤러 프로세스가 시작할 때 만든 난수)가 그 VM의 현재 값과 같음. **다르면 새 실행으로 보고** 그 VM의 `seq` 기준, `healthy` 창, 편차 기록을 초기화한다. 대기 VM이면 대기 등록을 해제하고 `healthy`를 다시 채우게 한다. **활성 VM이면 권한을 상속시키지 않는다.** 그 메시지를 버리고 활성 상실로 처리해, 상태 머신이 대기 승격 또는 정지를 결정한다. 새 실행은 `healthy`를 채운 뒤에야 대기로 돌아온다 |
| 순번 | `seq`(uint64)가 같은 생산자 세션 안에서 직전 `seq`보다 큼 (중복·재생·역순 차단). 세션의 첫 메시지는 무조건 통과 |
| perception 신선도 | `perception_seq`가 게이트웨이가 마지막으로 중계한 perception 기준 200 ms 이내 |
| 수치 | `v_mps`, `steer_rad`가 유한수(NaN·Inf 거부), 범위 0~0.5 m/s, ±30° |
| 변화율 | 직전 **전달** 명령 대비, 수신 시각 차 `Δt`로 정규화: `|Δv|/Δt ≤ 2 m/s²`, `|Δsteer|/Δt ≤ 180°/s` **[가정]**. 예외: 속도를 줄이는 방향(감속·정지)은 항상 허용 |
| 스키마 | `schema_version` 일치, 필드 누락 없음, 크기 상한 이내 |

같은 유효 명령을 반복 전송해도 `seq`가 늘지 않으면 `last[vm]`이 갱신되지 않으므로, 멈춘 컨트롤러가 재생으로 장애 전환을 막을 수 없다. 컨트롤러가 재시작해 `seq`가 0부터 다시 시작해도 `controller_session_id`가 바뀌므로 역순으로 거부되지 않는다. VM이 재시작돼도 MQTT 계정과 토픽은 같아서, 이 값 없이는 새 실행을 구별할 수 없다.

**출력 편차 `dev[vm]`.** 활성과 대기의 명령은 **같은 `route_id`와 같은 `perception_seq`를 가진 쌍끼리만** 비교한다. 다른 perception에서 계산한 명령을 비교하면 정상 컨트롤러 둘도 큰 편차로 보인다. 게이트웨이는 VM별로 최근 명령을 `perception_seq`로 색인해 두고, 쌍이 맞을 때 `|Δv|`, `|Δsteer|`를 기록한다. 최근 500 ms 동안 비교 가능한 쌍이 **5개 미만**이면 편차를 알 수 없는 것으로 보고 대기 등록과 승격을 허용하지 않는다.

**동기화 나이 `sync_age(vm, ref_vm, ref_session)`.** 동기화 식별자는 `(원본 VM, 원본 controller_session_id, state_seq)` 세 값의 조합이다. 게이트웨이는 활성의 `controller_state`를 구독해 신원(연결), 세션(활성의 현재 `controller_session_id`), 순번(단조 증가)을 검증한 뒤 이 조합을 키로 수신 시각을 최근 64개까지 기록한다. 나이를 계산할 때의 기준은 전역 활성 변수가 아니라 **호출자가 넘긴 기준 스냅샷**(`ref_vm, ref_session`)이다. 대기 등록 때는 현재 활성과 그 세션을, 전환 판정 때는 권한을 잃기 직전의 활성과 그 세션을 넘긴다. 대기 cmd의 `applied_state`가 이 기준과 정확히 일치할 때만 표에서 찾아 `now − t_recv`를 나이로 쓴다. 다른 VM이나 다른 세션의 상태를 적용했거나 표에 없으면 나이 무한대로 본다. 활성이 재시작하거나 전환돼 순번이 0부터 다시 시작해도 세션 값이 달라 충돌하지 않는다.

**세션과 epoch.** 게이트웨이 → ESP32 구간만 세션을 가진다. VM → 게이트웨이 구간에는 epoch가 없다(신원과 순번으로 충분하다).

- 게이트웨이는 시작할 때마다 예측 불가능한 `session_id`(32비트 난수)를 만들고, ESP32와 `hello/ack` 핸드셰이크로 세션을 수립한다. 핸드셰이크는 차가 정지한 상태에서만 성공한다. 핸드셰이크에서 32비트 전체를 교환하고, 양쪽이 **16비트 세션 태그** `session_tag = session_id & 0xFFFF`를 합의한다. 이후 모든 프레임은 태그를 싣는다.
- 태그는 16비트라 암호학적 식별자가 아니다. 재시작 후 이전 세션과 태그가 같을 확률은 1/65536이다. counter 창은 추가 보호가 되지 못한다(새 세션도 같은 초기값에서 시작한다). 이 수준을 이 프로젝트의 허용 위험으로 둔다.
- ESP32는 현재 태그의 프레임만 받는다. 다른 태그나 세션 미수립 상태의 명령은 전부 버리고 정지 상태를 유지한다. ESP32가 재시작하면 세션이 없으므로 핸드셰이크 전까지 움직이지 않는다.
- `epoch`는 세션 안에서 활성 컨트롤러가 바뀔 때 1씩 증가하며 세션 시작 시 0이다. ESP32는 `epoch`가 현재값보다 낮은 프레임을 버리고, 현재값+1보다 큰 프레임도 버린다(한 번에 한 단계만 증가). **주행 중 epoch 순환은 허용하지 않는다.** epoch가 254에 도달한 상태에서 전환이 필요하면 승격 대신 `STOPPED`로 가서 정지 프레임을 보내고, ESP32가 정지를 적용했음을 `vehicle_status`로 확인하거나 워치독 만료 시간이 지난 뒤에야 새 세션 핸드셰이크로 epoch를 0부터 다시 시작한다. 정지 전이와 회전 전이는 이벤트 사유를 구분한다.
- 게이트웨이 → ESP32 **모든** 프레임(drive, 최초 stop, stop keepalive)은 하나의 `next_counter()`에서 8비트 순환 `counter`를 받는다. 프레임 종류별로 따로 세지 않는다. **창 규칙:** `(new − last) mod 256`이 1~127이면 전진으로 보고 받고, 0이거나 128 이상이면 중복·역순으로 보고 버린다. epoch가 바뀌면 counter 비교를 초기화한다. `hello`는 세션 수립 전이라 counter가 없다.
- CAN에서는 애플리케이션 CRC를 두지 않고 CAN 자체 CRC-15를 신뢰한다. 시리얼에서는 CRC-16을 둔다(5.2).
- 제어 토픽은 retained 금지, QoS 0, 게이트웨이는 VM별 최신 1개만 유지(latest-only). 큐에 쌓인 옛 명령이 뒤늦게 적용되는 일을 막는다.

**실행 모델: 상태 머신의 단일 소유자.** 아래 상태 머신의 모든 변수는 **제어 루프 스레드 하나**만 읽고 쓴다.

- MQTT 콜백, CAN/시리얼 수신, 콘솔 명령은 상태를 건드리지 않는다. 수신 데이터를 역직렬화·스키마 검사한 뒤 **이벤트 큐에 적재만** 한다. 큐는 유계(예: 256)이며 가득 차면 가장 오래된 비제어 이벤트부터 버리고 경고를 남긴다. `cmd`는 VM별 최신 1개만 남긴다(latest-only).
- 제어 루프는 10 ms 주기로 깨어나 (1) 큐의 이벤트를 도착 순서대로 전부 처리하고, (2) 주기 검사를 한 번 실행한다. 이벤트 처리 하나와 주기 검사 하나는 중간에 끊기지 않는다. 재진입은 없다.
- 상태 머신은 외부로 직접 쓰지 않는다. "ESP32로 X 전송", "role 발행", "이벤트 로그"는 모두 **출력 큐에 적재**를 뜻한다. 별도 출력 워커가 큐를 비우며 CAN/시리얼 쓰기와 MQTT publish를 한다. 출력 큐는 FIFO이므로 적재 순서가 곧 전송 순서다.
- 따라서 모든 전이는 "상태 변수를 먼저 전부 확정하고, 그 다음 출력을 적재"하는 순서로 쓴다. 출력이 블로킹되거나 실패해도 상태는 이미 확정돼 있다.
- **출력 실패.** 출력 워커가 CAN/시리얼 쓰기에 실패하면 `output_failure` 이벤트를 큐에 넣는다. 제어 루프는 `STOPPED`가 아니면 `enter_stopped("output failure", false)`로 가고, 연속 5회 실패면 `enter_disarmed("output failure")`로 간다. 어느 쪽이든 ESP32 워치독이 최종 안전장치다.
- 멀티스레드로 구현해야 한다면 위와 동등한 제약을 둬야 한다: 상태 머신 전체를 하나의 뮤텍스로 보호하고, 뮤텍스를 잡은 채 블로킹 I/O나 콜백 호출을 하지 않는다. 기본 선택은 단일 루프다.

**게이트웨이 실행 상태.** 게이트웨이 자체가 네 상태 중 하나에 있다. 장애 이벤트와 정지 프레임은 **상태 전이 시 한 번**만 발생시킨다.

| 상태 | 의미 | 주기 동작 | 나가는 전이 |
| --- | --- | --- | --- |
| `DISARMED` | ESP32 세션 없음 | 1 Hz로 `hello` 전송 | 세션 ID와 스키마 버전이 모두 맞는 `ack` 수신 → `STOPPED`. 버전 불일치면 콘솔 알람 후 `DISARMED` 유지 |
| `STOPPED` | 세션 있음, 활성 없음, 차 정지 | 5 Hz로 `stop` keepalive | 무장 조건 충족 → `ARMED` |
| `ARMED` | 활성 VM 명령을 전달 중 | 활성 신선도 검사 | 활성 끊김 → `FAILOVER_PENDING` 또는 `STOPPED` |
| `FAILOVER_PENDING` | 승격 직후, 새 활성의 첫 신선 명령을 기다림 | 100 ms 상한 | 명령 수신 → `ARMED` / 상한 초과 → `STOPPED` |

`DISARMED`가 아닌 상태에서 ESP32 세션이 끊기면(`vehicle_status` 500 ms 무수신) `DISARMED`로 간다. `DISARMED`에서는 세션을 다시 만들지 않는다. `hello`는 ack를 받을 때까지 같은 `session_id`로 반복하고, 태그가 다른 상태 프레임은 폐기만 한다. 세션을 새로 만들 때 초기화하는 ESP32 세션 종속 상태는 아래 의사코드 첫머리에 열거한다.

**무장(ARM) 조건.** `STOPPED → ARMED`는 다음을 모두 만족할 때다.

- `healthy(vm)`인 VM이 하나 이상 있고, 비상정지가 풀려 있고, ESP32가 정지 상태(`v = 0`)를 보고함
- 최초 무장은 **관리 콘솔의 arm 명령**이 있어야 한다
- **게이트웨이가 새 세션으로 시작한 뒤와 하드웨어 비상정지 뒤에는 자동 재무장을 하지 않는다.** 중재자가 죽었다 살아난 상황은 원인이 불확실하므로 반드시 콘솔 arm을 요구한다. 시연에서도 "안전 정지 → 새 세션 확인 → 사용자가 재무장" 순서로 보여준다
- 컨트롤러 전환 실패로 정지한 경우(`no valid standby`, `standby silent after promotion`)에 한해 정책 `auto_rearm`이 켜져 있으면 자동 재무장한다(시연·시험 모드 기본값 켬, 평소 끔). 꺼져 있으면 콘솔 arm을 요구한다
- 활성 선택: healthy한 VM이 둘이면 VM-A를 활성, 다른 하나를 대기로. 하나면 활성만 두고 `standby = none`

```text
상태: gw_state ∈ {DISARMED, STOPPED, ARMED, FAILOVER_PENDING}
      session_id, session_tag, epoch, counter, active, standby
      ctrl_session[vm], last[vm] = (cmd, t_recv, seq)   # VM별 생산자 세션과 마지막 유효 cmd
      by_perc[vm][perception_seq] = cmd        # 편차 비교용 색인 (최근 500 ms)
      state_recv[(vm, csid, seq)] = t_recv     # controller_state 수신 기록 (전체 64개)
      state_last_seq[(vm, csid)]               # (VM, 컨트롤러 세션)별 controller_state 마지막 seq
      dev[vm] = (pairs, max|Δv|, max|Δsteer|)  # 같은 route_id·perception_seq 쌍끼리만, 최근 500 ms
      t_last_switch, t_pending_since
      first_fwd_counter, t_esp32_applied, t_last_forward, 전환 시각 5종 (4.4)
      rearm_allowed, last_stop_auto_rearm_eligible
      rotate_pending, t_rotate_since, rotate_stop_counter   # epoch 소진 세션 회전 중
      sample_asm = (committed_sample, pending) # 5.2 상태 프레임 조립기
      estop_latched, t_last_status, consecutive_output_failures

ESP32 세션 종속 상태 — 새 세션마다 전부 초기화한다:
      session_id, session_tag, epoch = 0, counter = 0, sample_asm 비움,
      first_fwd_counter = none, t_esp32_applied = none, t_last_forward = none, 전환 시각 5종 = none,
      state_recv = {}, state_last_seq = {}, estop_latched = false, t_last_status = none,
      rotate_pending = false, rearm_allowed = false, last_stop_auto_rearm_eligible = false
      (VM 쪽 기록 ctrl_session, last, by_perc, dev는 ESP32 세션과 무관하므로 유지)

next_counter():
  counter = (counter + 1) mod 256; return counter   # 게이트웨이 → ESP32 모든 프레임 공용

초기화:
  gw_state = none; enter_disarmed("gateway start")   # 새 세션: 콘솔 arm 필수

수신 cmd(conn, cmd):
  vm = identity(conn)
  if cmd.controller_session_id != ctrl_session[vm]:
      if vm == active and gw_state in {ARMED, FAILOVER_PENDING}:
          # 활성 권한은 새 실행에 상속되지 않는다. 같은 수신 처리 안에서 즉시 권한을 취소하고
          # 승격 또는 정지를 확정한다. 장애 기준 (old_vm, old_session)은 기록을 지우기 전에 잡는다.
          # 이후 이 VM의 명령은 vm != active 이므로 전달되지 않는다
          on_active_lost("active controller session changed")    # 내부에서 active와 ctrl_session[active]를 캡처
      if vm == standby: standby = none; role 발행      # 새 실행은 healthy를 다시 채워야 함
      ctrl_session[vm] = cmd.controller_session_id       # 그 다음에 새 실행 등록, 기록 초기화
      last[vm] = none; by_perc[vm] = {}; dev[vm] = 초기값; healthy 창 초기화
      if vm == active: return                          # on_active_lost가 active를 바꿨어도 이 메시지는 전달하지 않는다
  if not valid(cmd, vm): 버림; return
  last[vm] = (cmd, now, cmd.seq); by_perc[vm][cmd.perception_seq] = cmd
  # 출력 편차는 standby 변수가 아니라 실제 비활성 VM의 식별자에 기록한다
  if vm == active:
      for peer in (등록된 VM − {active}):
          if by_perc[peer]에 cmd.perception_seq 있음 and by_perc[peer][cmd.perception_seq].route_id == cmd.route_id:
              dev[peer] 갱신 (쌍 수 +1, |Δv|, |Δsteer|)   # peer의 명령 vs 활성의 명령
  elif active != none and by_perc[active]에 cmd.perception_seq 있음
       and by_perc[active][cmd.perception_seq].route_id == cmd.route_id:
      dev[vm] 갱신 (쌍 수 +1, |Δv|, |Δsteer|)            # 이 VM의 명령 vs 활성의 명령
  if vm != active: return                          # 전달하지 않음
  if gw_state in {ARMED, FAILOVER_PENDING}:
      if gw_state == FAILOVER_PENDING: gw_state = ARMED   # 상태 먼저 확정
      forward(cmd)                                 # 그 다음 출력 적재

수신 controller_state(conn, st):                   # 활성만 발행한다
  vm = identity(conn)
  if vm != active: 버림; return
  if st.controller_session_id != ctrl_session[active]: 버림; return
  key = (vm, st.controller_session_id)
  if key in state_last_seq and st.seq <= state_last_seq[key]: 버림; return
  state_last_seq[key] = st.seq                    # (VM, 세션)별로 관리. 활성이 바뀌어도 초기화 불필요
  state_recv[(vm, st.controller_session_id, st.seq)] = now   (전체 64개 유지, 오래된 것부터 제거.
                                                   활성 전환 시 지우지 않는다. sync_age가 현재 활성·세션과
                                                   일치하는 항목만 쓰므로 옛 활성 항목은 자연히 무시된다)

forward(cmd):                                     # 출력 큐 적재. 블로킹하지 않는다
  c = next_counter()
  t_last_forward = now
  if first_fwd_counter == none: first_fwd_counter = c; t_first_new_cmd_forwarded = now
  출력 큐 ← drive(session_tag, epoch, c, cmd.v, cmd.steer)

fresh(vm):   vm != none and last[vm] != none and now - last[vm].t_recv <= 100 ms
sync_age(vm, ref_vm, ref_session):                # 기준은 전역 active가 아니라 호출자가 넘긴 스냅샷
  a = last[vm].cmd.applied_state                  # (src_vm, src_session, seq)
  if a.src_vm != ref_vm or a.src_session != ref_session: return ∞
  return now - state_recv[(a.src_vm, a.src_session, a.seq)]   (없으면 ∞)

dev_ok(vm):
  return dev[vm].pairs >= 5 and dev[vm].v <= 0.1 m/s and dev[vm].steer <= 5°   # [가정]

standby_ok(vm, ref_vm, ref_session):              # 게이트웨이가 직접 관찰한 값만. ref = 비교 기준 (활성 VM, 그 컨트롤러 세션)
  return fresh(vm)
     and sync_age(vm, ref_vm, ref_session) <= 500 ms
     and (ref_vm == none or last[ref_vm] == none
          or last[vm].cmd.route_id == last[ref_vm].cmd.route_id)
     and dev_ok(vm)

on_active_lost(reason):                           # ARMED·FAILOVER_PENDING에서만. 호출 즉시 권한 취소
  old_vm = active; old_session = ctrl_session[active]   # 장애 기준 스냅샷. 이 뒤로 불변
  t_fail_detected = now
  t_last_good_cmd = (last[old_vm].t_recv if last[old_vm] != none else t_last_forward)   # 둘 다 없으면 none → 전환 시간 "측정 불가"
  active = none                                   # 이 줄 이후 old_vm의 명령은 어떤 경로로도 전달되지 않는다
  if epoch >= 254:
      rotate_pending = true; t_rotate_since = now
      enter_stopped("epoch exhausted", false)     # STOPPED에서 정지 확인 뒤 회전. 새 세션이므로 콘솔 arm 필요
  elif standby_ok(standby, old_vm, old_session) and now - t_last_switch > 1 s:
      # 1) 상태를 전부 확정한다. 이 블록이 끝나기 전에는 어떤 출력도 적재하지 않는다
      epoch += 1; first_fwd_counter = none; t_esp32_applied = none
      active, standby = standby, none
      t_last_switch = t_role_assigned = now
      gw_state = FAILOVER_PENDING; t_pending_since = now
      # 2) 그 다음 출력을 적재한다. 순서: 캐시 명령 → role 공지
      forward(last[active].cmd)                   # 캐시된 신선 명령을 새 epoch로 즉시 전달
      role 발행
  else:
      enter_stopped(reason, true)

enter_stopped(reason, auto_rearm_eligible):
  gw_state = STOPPED; active = none; standby = none          # 상태 확정
  last_stop_auto_rearm_eligible = auto_rearm_eligible
  if not auto_rearm_eligible: rearm_allowed = false
  rotate_stop_counter = next_counter()
  출력 큐 ← stop(session_tag, epoch, rotate_stop_counter, reason)   # 전이 시 1회. 정지 프레임이 role·로그보다 먼저
  이벤트 로그 1회 {reason}; role 발행

output_failure 이벤트 수신 (출력 워커가 CAN/시리얼 쓰기 실패):
  consecutive_output_failures += 1 (성공 시 0으로)
  if consecutive_output_failures >= 5: enter_disarmed("output failure")
  elif gw_state not in {STOPPED, DISARMED}: enter_stopped("output failure", false)

enter_disarmed(reason):                           # 세션을 새로 만든다. 멱등: 이미 DISARMED면 아무것도 하지 않는다
  if gw_state == DISARMED: return
  gw_state = DISARMED; active = none; standby = none
  ESP32 세션 종속 상태 전부 초기화 (위 목록)
  session_id = 난수32; session_tag = session_id & 0xFFFF
  이벤트 로그 1회 {reason}; role 발행 {gateway_session_id, active: null, standby: null}

콘솔 arm 명령 수신:  rearm_allowed = true

주기 검사(10 ms):
  case DISARMED:
      1 Hz로 hello(session_id) 전송. session_id는 ack를 받을 때까지 바꾸지 않는다
      원시 상태 프레임은 조립기에 넣지 않고 폐기한다. 세션을 다시 만들지 않는다
      ack 수신 시:
          if ack.session_id != session_id: 폐기                      # 옛 hello에 대한 늦은 ack
          elif ack.schema_version != SCHEMA_VERSION:
              # 프로토콜 불일치. 같은 session_id로 hello를 계속하되 절대 STOPPED로 가지 않는다.
              # 사람이 펌웨어 또는 게이트웨이를 맞춰야 풀린다
              이벤트 로그 1회 (같은 버전 조합당 1회) {"schema mismatch", ours, theirs}; 콘솔 알람 발행
          else: sample_asm 초기화; consecutive_output_failures = 0; gw_state = STOPPED; t_last_status = now
  case STOPPED:
      5 Hz: ESP32로 stop(session_tag, epoch, next_counter(), "keepalive")
      if rotate_pending:                           # epoch 소진 세션 회전: 정지 적용 확인 뒤에만 새 세션
          stop_applied = (status.v == 0 and (status.applied_counter − rotate_stop_counter) mod 256 < 128)
          if stop_applied or now - t_rotate_since > 500 ms:   # 500 ms = 워치독 300 ms + 여유
              enter_disarmed("session rotation after epoch exhausted")
          return
      if 무장 조건 충족 and (rearm_allowed or (policy.auto_rearm and last_stop_auto_rearm_eligible)):
          active/standby 지정, epoch 유지, gw_state = ARMED, role 발행
  case ARMED:
      if not fresh(active): on_active_lost("active command stream stale")
  case FAILOVER_PENDING:
      if now - t_pending_since > 100 ms: enter_stopped("standby silent after promotion", true)
  gw_state != DISARMED 일 때만: now - t_last_status > 500 ms → enter_disarmed("esp32 session lost")

health 스냅샷 수신 vm/<id>/health:                # 대기 등록 조건은 승격 조건과 같다. UI에 승격 불가능한 standby가 뜨지 않게
  if gw_state in {ARMED, FAILOVER_PENDING} and standby == none and vm != active
     and health.ok and health.desired_state == running
     and healthy(vm) and standby_ok(vm, active, ctrl_session[active]):
      standby = vm; role 발행

vehicle_status 수신 (5.2 조립기가 완성한 스냅샷):
  if gw_state == DISARMED: 폐기; return            # DISARMED에서는 ack 외 어떤 프레임도 전이를 일으키지 못한다.
                                                   # 16비트 태그가 우연히 맞는 옛 세션 프레임으로 핸드셰이크를 우회하지 못하게
  if status.session_tag != session_tag: 폐기; return
  t_last_status = now
  # 비상정지: 상승 에지에서만 전이. 유지되는 동안은 keepalive만
  if status.flags.estop and not estop_latched:
      estop_latched = true
      if gw_state != STOPPED: enter_stopped("estop", false)
      else: rearm_allowed = false; 이벤트 로그 1회 {"estop"}
  elif not status.flags.estop:
      estop_latched = false
  # 전환 적용 확인
  if t_esp32_applied == none and first_fwd_counter != none
     and status.applied_epoch == epoch
     and (status.applied_counter − first_fwd_counter) mod 256 < 128:
      t_esp32_applied = now
```

- **명령 스트림이 곧 활성 장애 판정의 근거**다. 에이전트 heartbeat는 쓰지 않는다(3.3).
- **활성이 끊겼다는 것만으로 승격하지 않는다.** 대기 VM이 신선한 명령을 내고, 게이트웨이가 직접 계산한 동기화 나이가 최근이며, 같은 경로를 보고 있고, 출력이 활성과 크게 다르지 않을 때만 넘긴다. 아니면 즉시 정지시킨다.
- **히스테리시스.** 승격 후 1초 동안은 재전환하지 않는다. 재시작된 VM은 `healthy` 조건(0.5초 연속 정상)을 채운 뒤에만 대기로 등록한다.
- **승격 시 캐시된 명령을 즉시 전달한다.** `standby_ok`가 참이면 대기 VM의 마지막 명령은 100 ms 이내 신선하므로, 새 epoch로 바로 내보낸다. 그 다음 명령이 100 ms 안에 오면 정상 운행이고, 안 오면 정지한다. 따라서 ESP32 쪽 빈 구간은 캐시 전달 지연 수 ms 수준이다. 이 수치는 **[가정]**이며 4.4의 측정으로 확인한다.

### 4.3 ESP32 안전 규칙

**구동 모드와 fail-safe 출력. [검증 필요]**

- MDD10A는 **sign-magnitude 모드**(PWM + DIR)로 쓴다. 제조사 문서상 이 모드에서 PWM Low는 제동(모터 단자 단락)이다. 이것이 locked-antiphase 모드와 배선에 따라 달라지므로, **실제 보드에서 PWM·DIR 진리표를 측정해 문서에 붙이기 전까지는 가정으로 둔다.**
- ESP32 리셋·부팅 중 GPIO는 하이임피던스다. PWM 두 선에 **외부 풀다운 저항**을 달아 ESP32가 죽거나 리셋되면 PWM이 Low(제동)가 되게 한다. DIR은 어느 값이든 상관없다. 저항 키트에서 10 kΩ을 쓴다.
- 통신 단절·세션 미수립·워치독 만료 시 출력은 PWM 0, 서보는 현재 각 유지다.
- MDD10A는 역극성 보호가 없다. 배선 후 전원 투입 전 극성 확인을 체크리스트에 넣는다.

**규칙.**

| 규칙 | 동작 |
| --- | --- |
| 세션 | 핸드셰이크된 `session_id`가 없으면 모든 명령 무시, 정지 유지 |
| 정지 프레임 수신 | dom0가 보낸 `stop` → 즉시 제동. 워치독을 기다리지 않는다 |
| 명령 수신 워치독 | 300 ms 동안 유효 명령 없음 → 즉시 제동. dom0 자체가 죽었을 때의 최후 수단 |
| 태그·epoch·counter 검사 | 4.2 규칙에 어긋나는 프레임은 버림. 시리얼은 CRC-16 실패도 버림 |
| 근거리 장애물 | VL53L5CX 30 cm 이내 → 명령과 무관하게 즉시 제동, 후진 명령만 허용 |
| 하드웨어 비상정지 | 릴레이로 모터 전원 차단. 상태를 CAN/시리얼로 보고 |
| 속도 상한 | 0.5 m/s (펌웨어에서 클램프). 시연 트랙이 좁으면 0.3 m/s |

정지는 모두 즉시 제동이다. 감속 램프를 두지 않는다. 워치독(300 ms)은 전환 목표(150 ms)보다 길어야 전환 중 로봇이 멈추지 않는다. dom0가 살아 있는 한 모든 정지는 명시적 정지 프레임으로 더 빨리 일어나고, 워치독은 dom0까지 죽은 경우에만 작동한다.

**정지 성능 [목표, 실측 후 확정].** 아래 값은 최대 적재와 가장 미끄러운 시연 노면에서 측정해 확정한다. 0.5 m/s에서 급제동 시 전복이 없다는 것도 측정 항목이다.

| 상황 | 정지까지 목표 | 이동 거리 목표 (0.5 m/s) |
| --- | --- | --- |
| 근거리 장애물 감지 | 100 ms | 5 cm |
| dom0 정지 프레임 (대기 VM 없음) | 끊김 감지 최대 110 ms + 정지 110 ms | 11 cm |
| 워치독 (dom0 사망) | 300 ms + 100 ms | 20 cm |
| 하드웨어 비상정지 | 릴레이 10 ms + 타성 | 10 cm 미만 |

### 4.4 시간 예산과 측정

| 항목 | 값 | 상태 | 측정 방법 |
| --- | --- | --- | --- |
| perception 주기 | 66 ms (15 Hz) | 목표 | Jetson 발행 간격 |
| 명령 주기 | 20 ms (50 Hz) | 목표 | 게이트웨이 수신 간격 |
| xenbr0 지연 | < 1 ms | 가정 | VM ↔ dom0 UDP 왕복 |
| 게이트웨이 전환 판정 | 100 ms 초과 감지(10 ms 주기 검사 포함 최대 약 110 ms, 스케줄링 지연 제외) | 가정 | Xen 스케줄링 지터 측정 후 조정. 합격 기준은 110 ms |
| 전환 시간 | ≤ 150 ms | 목표 | 아래 정의 |
| ESP32 워치독 | 300 ms | 설계값 | — |
| 완전 정지 | ≤ 100 ms | 목표 | 엔코더 속도 0 도달 |

**전환 시간의 정의.** `active` 변수를 바꾼 시점이 아니라 **차가 실제로 새 컨트롤러의 명령을 받은 시점**으로 잰다. 게이트웨이가 다음 다섯 시각을 모두 기록한다.

| 시각 | 의미 |
| --- | --- |
| `t_last_good_cmd` | 옛 활성의 마지막 유효 명령 전달 |
| `t_fail_detected` | 게이트웨이가 끊김을 판정 |
| `t_role_assigned` | 새 활성 지정 |
| `t_first_new_cmd_forwarded` | 새 활성의 첫 명령을 ESP32로 전달 |
| `t_esp32_applied` | ESP32의 `vehicle_status`가 **현재 세션 태그, 새 epoch, 그리고 첫 전달 counter 이상의 `applied_counter`**를 모두 보고한 시점 |

`t_esp32_applied`는 전환 시마다 초기화하고, 세션·epoch·counter 세 조건이 모두 맞을 때만 기록한다. epoch만 보면 세션이 바뀌어 0부터 다시 시작한 경우와 구분할 수 없고, counter를 안 보면 ESP32가 실제로 새 명령을 적용했는지 알 수 없다.

보고하는 전환 시간은 `t_esp32_applied − t_last_good_cmd`이고, ESP32 상태가 늦게 오면 `t_first_new_cmd_forwarded − t_last_good_cmd`를 하한으로 함께 적는다. `t_last_good_cmd`를 정할 수 없으면(새 세션에서 아직 아무 명령도 전달하지 않은 채 활성이 사라진 경우) 전환 시간을 "측정 불가"로 기록하고 집계에서 뺀다. 이 시각들은 모두 **게이트웨이의 단조 시계** 하나로 잰다. 보드 간 타임스탬프는 비교하지 않는다. 다른 보드의 `ts`는 로깅용이며, dom0가 NTP로 Jetson·VM에 시각을 배포해 오차 ±5 ms **[가정]** 안에서만 참고한다.

---

## 5. 메시지 계약 (초안)

> **MQTT는 [MQTT 메시지 계약 v1](../contracts/mqtt-contract-v1.md)이 기준이다 (2026-10-19 RC카 시나리오 확정, S15P31E110-230 문서 통일).** VM·젯슨·게이트웨이·ESP32가 주고받는 MQTT 토픽, 공통 봉투, `drive.command`·`vehicle.control`·`vehicle.status` 필드, ACL은 그 문서를 따른다. 아래 5.1의 메시지 표는 옛 핫 스탠바이 설계의 초안이며, 게이트웨이 ↔ ESP32를 시리얼·CAN으로 연결할 경우의 프레임(5.2 CAN·시리얼)의 근거로만 남긴다. 계약은 현재 ESP32 MQTT 직결이고, 시리얼·CAN 유지 여부는 제어 계층 의견을 받아 정한다.

정식 계약은 `docs/contracts/`에서 관리한다. 전송 매체와 무관한 **공통 봉투**를 먼저 정하고, MQTT·CAN·시리얼 매핑은 별도 계약으로 둔다. 구현 시작 전 확정해야 할 최소 항목은 5.3이다.

### 5.1 공통 봉투와 메시지

모든 메시지는 `schema_version, seq(uint64), ts`를 가진다. `ts`는 발신 보드의 단조 시계이며 보드 간 비교에 쓰지 않는다. 컨트롤러가 보내는 메시지는 추가로 `controller_session_id`(프로세스 시작 시 난수 32비트)를 가지며, `seq`는 이 세션 안에서만 단조 증가한다.

| 메시지 | 방향 | 주기 | 본문 필드 |
| --- | --- | --- | --- |
| `perception` | Jetson → VM-A, VM-B | 15 Hz | `lane_offset_m, heading_err_rad, obstacles[{x,y,w}], stop` |
| `cmd` | VM → 게이트웨이 | 50 Hz | `controller_session_id, src, route_id, v_mps, steer_rad, perception_seq, applied_state{src_vm, controller_session_id, seq}` |
| `controller_state` | 활성 VM → 대기 VM, 게이트웨이 | 10 Hz | `controller_session_id, route_id, mode, wp_idx, integ_v, last_cmd{v_mps, steer_rad}, perception_seq` |
| `drive` | 게이트웨이 → ESP32 | 50 Hz | `session_tag, epoch, counter, v_mps, steer_rad` |
| `stop` | 게이트웨이 → ESP32 | 전이 시 1회 + STOPPED에서 5 Hz | `session_tag, epoch, counter, reason` |
| `hello` | 게이트웨이 → ESP32 | DISARMED에서 1 Hz | `session_id(32), schema_version` |
| `ack` | ESP32 → 게이트웨이 | hello 응답 | `session_id(32), schema_version` |
| `vehicle_status` | ESP32 → 게이트웨이 → VM 전체 | 20 Hz | `sample, session_tag, applied_epoch, applied_counter, v_mps, steer_rad, flags{estop, session_ok, obstacle, watchdog}, batt_v, odom{x,y,yaw}` |
| `estop_event` | ESP32 → 게이트웨이 | 상태 변화 시 | `sample, flags` |
| `role` | 게이트웨이 → VM 전체, UI | 변경 시 (retained) | `gateway_session_id, epoch, active, standby, reason`. **정보용이며 제어 권한이 아니다** |
| `failover_event` | 게이트웨이 → 이벤트 로그 | 전환 시 | 4.4의 다섯 시각, `from, to, epoch, reason, max_dev` |
| `vm/<id>/health` | 매니저 → 게이트웨이 | 변경 시 (retained 스냅샷) | `ok, desired_state, manager_seq, ts` |
| `manager/health/request` | 게이트웨이 → 매니저 | 게이트웨이 시작 시 | `gateway_session_id` |

### 5.2 매체별 매핑

**MQTT.** 토픽·ACL·QoS·retain·latest-only·크기 상한은 [MQTT 메시지 계약 v1](../contracts/mqtt-contract-v1.md) 1·5·9장을 따른다 (이 문서의 옛 토픽 `vehicle/cmd/<vm>` 등은 폐기).

**공통 봉투의 축약.** CAN·시리얼에는 봉투 필드를 그대로 싣지 못한다. 축약 규칙은 다음과 같다.

| 봉투 필드 | CAN·시리얼에서 | 비고 |
| --- | --- | --- |
| `schema_version` | `hello/ack`에서 1바이트로 합의. 불일치면 세션 수립 거부 | 프레임마다 싣지 않음 |
| `seq` | 게이트웨이 → ESP32: `counter`(8비트). ESP32 → 게이트웨이: `sample`(8비트, 측정 스냅샷 번호) | 창 규칙은 4.2 |
| `ts` | 싣지 않음. 수신 측이 수신 시각을 찍는다 | 보드 간 시각 비교 안 함 |
| CRC | CAN: 없음(CAN CRC-15 신뢰). 시리얼: CRC-16 | |

**CAN 500 kbps (2단계).** Classic CAN 한 프레임은 8바이트이므로 메시지를 쪼갠다. 리틀엔디언, 정수 스케일링. **ID 하나는 생산자 하나만 소유한다.** 양방향 메시지는 ID를 나눈다.

| ID | 생산자 | 이름 | 바이트 배치 | 길이 |
| --- | --- | --- | --- | --- |
| `0x010` | 게이트웨이 | stop | `[tag:u16][epoch:u8][counter:u8][reason:u8]` | 5 |
| `0x011` | ESP32 | estop_event | `[sample:u8][flags:u8]` | 2 |
| `0x100` | 게이트웨이 | drive | `[tag:u16][epoch:u8][counter:u8][v:i16 mm/s][steer:i16 mrad]` | 8 |
| `0x200` | ESP32 | motion | `[sample:u8][v:i16 mm/s][steer:i16 mrad][flags:u8][batt_mv:u16]` | 8 |
| `0x201` | ESP32 | applied | `[sample:u8][tag:u16][applied_epoch:u8][applied_counter:u8]` | 5 |
| `0x310` | ESP32 | odom_xy | `[sample:u8][x:i24 mm][y:i24 mm]` | 7 |
| `0x311` | ESP32 | odom_yaw | `[sample:u8][yaw:i16 mrad]` | 3 |
| `0x7F0` | 게이트웨이 | hello | `[session_id:u32][schema_version:u8]` | 5 |
| `0x7F1` | ESP32 | ack | `[session_id:u32][schema_version:u8]` | 5 |

- `flags` 비트: 0 estop, 1 session_ok, 2 obstacle, 3 watchdog_tripped.
- ESP32 상태 프레임(`0x200`, `0x201`, `0x310`, `0x311`)은 같은 측정 스냅샷이면 같은 `sample` 값을 가진다. **조립 규칙:**
  - 게이트웨이는 `committed_sample`(마지막으로 완성한 스냅샷)과 `pending`(조립 중인 스냅샷 하나)을 둔다.
  - 프레임의 `sample`이 `committed_sample` 기준 창 규칙(`(new − committed) mod 256`이 1~127)에 들면 받는다. `pending`이 없으면 새로 연다. `pending.sample`과 같으면 `pending`에 넣는다. `pending.sample`보다 **새로우면**(`(new − pending) mod 256`이 1~127) 미완성 `pending`을 버리고 새 `pending`을 연다. `pending.sample`보다 **오래됐으면** 지연된 프레임이므로 폐기한다. 지연·재정렬이 있어도 조립이 뒤로 되돌아가지 않는다.
  - `pending`에 같은 ID가 두 번 오면 뒤의 것을 중복으로 버린다.
  - 네 ID가 다 모이면 `vehicle_status` 하나로 발행하고 `committed_sample = pending.sample`로 갱신한다. 50 ms 안에 다 모이지 않으면 `pending`을 버린다. `committed_sample`은 완성된 스냅샷에서만 갱신되므로, 같은 `sample`의 나머지 프레임이 중복으로 거부되지 않는다.
  - 게이트웨이가 `DISARMED`일 때는 원시 상태 프레임을 조립기에 넣지 않는다. 정상 `ack`를 받는 시점에 조립기를 비운다. 핸드셰이크 대기 중 들어온 옛 세션 프레임이 `committed_sample`을 움직여 새 세션의 첫 `sample`을 거부하는 일을 막는다.
- `counter`(게이트웨이 → ESP32)의 창 규칙은 4.2와 같다. `sample`의 창 규칙은 위와 같이 `committed_sample` 기준이다.
- `reason:u8`, `flags:u8`의 값 배정과 예약 비트는 `docs/contracts/`의 계약 문서에서 관리한다. 이 문서의 비트 배정은 초안이다.
- `i24`는 부호 있는 24비트 리틀엔디언(±8,388 m).

**USB 시리얼 (1단계).** 프레임 본문은 `[id:u16 LE][len:u8][payload ≤ 8][crc:u16 LE]`이고, 전체를 COBS로 인코딩한 뒤 `0x00`으로 끝낸다. `payload`는 위 CAN 배치와 동일하다. 수신 측은 CRC 실패 프레임을 버리고 다음 `0x00`까지 재동기화한다.

- CRC: **CRC-16/CCITT-FALSE**. 다항식 `0x1021`, 초기값 `0xFFFF`, 입력·출력 반전 없음, XOR-out `0x0000`. 적용 범위는 `id, len, payload`(COBS 인코딩 전). 검증 벡터: ASCII `"123456789"` → `0x29B1`.
- 최대 인코딩 프레임 길이 16바이트.

### 5.3 구현 전 확정 항목

- ~~MQTT 인증·ACL, retain 정책, latest-only 정책~~ → MQTT 메시지 계약 v1에서 확정
- CAN ID 소유권, 프레임 배치, 스케일, `sample` 조립 규칙
- 시리얼 CRC 검증 벡터를 양쪽 구현의 단위 테스트에 포함
- `schema_version` 운용 규칙(hello/ack 합의, 불일치 시 거부)
- 변화율·편차 상한의 실측 근거
- 무장 정책(`auto_rearm`) 기본값. 적용 범위는 컨트롤러 전환 실패 뒤 정지에 한정하며, 새 세션·비상정지 뒤에는 적용하지 않음
- `reason`, `flags` 값 배정표

---

## 6. 시연 시나리오

| # | 조작 | 기대 결과 | 측정 항목 |
| --- | --- | --- | --- |
| 1 | 대기 VM 정상 상태에서 활성 VM 강제 종료 (`xl destroy`) | 최대 약 110 ms 안에 장애 감지, 대기 VM이 이어받아 로봇이 계속 주행. 대시보드에 전환 시간 표시 | 4.4의 다섯 시각, 전환 시간 |
| 2 | 대기 VM 정상 상태에서 활성 VM에 CPU 부하 (stress-ng) | 명령 지연이 100 ms를 넘기면 최대 약 110 ms 안에 전환. 대기 VM·콕핏 VM은 영향 없음 | 전환 시간, 타 VM 메트릭 |
| 3 | 두 컨트롤러 VM 모두 종료 | 최대 약 110 ms 안에 장애 감지 후 정지 프레임으로 정지. 이어서 dom0까지 끊으면 300 ms 워치독으로 정지 | 정지까지 시간, 이동 거리 |
| 4 | 자동 재시작 | 복구 서비스가 죽은 VM을 올리고 게이트웨이가 대기로 복귀시킴. 타임라인에 기록 | VM 재시작 시간, 복구 시간 |
| 5 | 하드웨어 비상정지 | 모터 전원 차단, 계기판에 E-stop 표시 | — |
| 6 | 사용자가 콘솔에서 대기 VM 정지 | desired state가 stopped이므로 재시작되지 않음. 게이트웨이는 standby=none으로 운행 | — |
| 7 | 주행 중 게이트웨이 프로세스 강제 종료 | ESP32 워치독이 정지. systemd가 게이트웨이를 재시작하면 새 세션으로 핸드셰이크하고 `STOPPED`에서 대기. 자동 재무장 없음. 콘솔에서 새 세션 ID를 확인하고 arm을 누르면 출발 | 정지까지 시간, 재무장까지 시간 |

시나리오 2는 "한 VM 장애 시 다른 화면 유지"라는 격리 시연 항목을, 4는 자동 복구 항목을, 6은 사용자 의도와 자동 복구의 경쟁 조건이 없음을, 7은 세션 설계가 재시작을 안전하게 넘기고 중재자 장애 뒤에는 사람이 확인해야 출발한다는 것을 보여준다.

---

## 7. 전원·배치 (후보, BOM 검증 후 확정)

아래 퓨즈·전압은 **후보값**이다. 실제 부하 전류, 스톨 전류, 배선 허용 전류, 변환기 디레이팅, 돌입 전류를 재고 확정한다.

배터리 3S LiPo → 메인 퓨즈 → 키 스위치(이그니션) → 분배 단자대. 분기는 다섯 개다.

| 분기 | 퓨즈 후보 | 근거 | 변환 | 설정 전압 | 부하 |
| --- | --- | --- | --- | --- | --- |
| 메인 | 20 A | 전 분기 합산 최악 약 12 A + 돌입 | — | — | — |
| 모터 | 10 A | 모터 스톨 3.2 A × 2 = 6.4 A **[제조사 사양]**, 배선 16 AWG | 없음, 비상정지 릴레이 경유 | 배터리 직결 | MDD10A |
| Jetson | 7.5 A | 5.1 V × 4 A ÷ 효율 0.9 ÷ 11.1 V ≈ 2 A, 돌입 여유 | DFR1025 벅 | 5.1 V (모듈 최대치) | 배럴잭 4 A |
| RPi4 | 5 A | 5 V × 3 A ÷ 0.9 ÷ 11.1 V ≈ 1.5 A | DFR0946 벅부스트 | 보드 입력 실측 기준 5.0~5.1 V | 3 A |
| 서보 | 5 A | 서보 스톨 약 2.5 A **[가정]** | UBEC | 7.4 V | 조향 서보 |
| 보조 | 3 A | 디스플레이 1 A + ESP32 + LED + 팬 ≈ 2.5 A at 5 V → 약 1.3 A | 5 V 고정 벅 | 5.0 V | 디스플레이, ESP32, LED, 팬 |

- **전압은 설정값이 아니라 실측으로 확정한다.** DFR1025는 출력 상한이 5.1 V이고 설정 오차가 있어 제조사도 실측을 요구한다. 각 레일은 무부하와 최대 부하에서 보드 입력 지점(Jetson 배럴잭, RPi4 GPIO 5V 핀)의 전압을 재고, Jetson은 4.9 V 이상, RPi4는 4.9~5.25 V에 들도록 조정한다. 강하가 크면 설정이 아니라 케이블 굵기를 바꾼다.
- 비상정지 스위치는 릴레이 코일만 끊고(b접점), 릴레이가 모터 전원을 끊는다. a접점은 ESP32 입력으로 상태를 알린다. 릴레이 코일에 플라이백 다이오드를 단다.
- 모든 변환기의 −와 모터 드라이버 B−는 − 단자대에서 한 점 접지한다. 모터 전류의 귀환 경로가 로직 전원 귀환 경로와 겹치지 않게 배선한다.
- 검토 항목(BOM 검증과 함께): 모터 전류 변동이 5 V 레일을 흔들어 dom0가 재부팅하지 않는지, 변환기 입력 벌크 커패시터 필요 여부, 메인 입력 역극성 보호, 입력 TVS.
- 배치: 배터리는 하층 중앙, 보드·퓨즈박스는 상층, D435f는 전면 상단, VL53L5CX는 범퍼 높이, 계기판 디스플레이는 상층 후방.

---

## 8. 이미지·빌드

- Yocto를 기반으로 Xen과 dom0 실행 환경을 구성한다. 레이어 커밋은 `platform/yocto/layers.lock`에 고정되어 있으며, **Xen 버전은 meta-virtualization 커밋(2807579)이 제공하는 버전을 따른다.** 브링업 가이드에 실제 Xen 버전을 기록한다.
- Xen 공식 기능 문서는 라이브 마이그레이션을 x86 지원 기능으로 분류한다([Xen Feature: Migration](https://xenbits.xen.org/docs/unstable/features/migration.html)). Remus·COLO도 x86 기반이다. 따라서 이 프로젝트는 VM 수준 미러링이 아니라 애플리케이션 수준 핫 스탠바이를 쓴다. 이 판단은 사용하는 Xen 버전 기준이며, 버전이 바뀌면 재확인한다.
- Debian·Ubuntu·Alpine은 각 배포판의 독립적인 VM 템플릿으로 관리한다.
- 이미지 빌드 설정과 스크립트를 GitLab에서 관리하여 환경 재현성을 확보한다.
- VM별 애플리케이션의 웹 UI는 해당 VM에서 제공하고, dom0 키오스크에서 렌더링한다.

---

## 9. 결정 필요 사항

| 항목 | 선택지 | 비고 |
| --- | --- | --- |
| dom0 메모리 | Yocto 현재 1024M / 문서 제안 1.5 GB | 키오스크가 dom0에서 도는지에 따라 결정. 둘을 일치시킬 것 |
| RPi4 실제 메모리 | 보유 4대 용량 확인 | 4GB면 VM 배분 재설계 |
| VM → 게이트웨이 전송 | MQTT(dom0 브로커, ACL) / UDP(vif별 고정 IP + ebtables) | 지연 측정 후 결정. 신원 검증 방식이 달라짐 |
| 마지막 구간 매체 | 1단계 USB 시리얼 → 2단계 CAN | CAN은 ESP32 트랜시버 + dom0용 USB-CAN 1개 필요 |
| 대기 컨트롤러 위치 | VM-B(Ubuntu, 이종 이중화) / 4번째 Debian VM(동종 미러) | 4번째 VM은 전용 코어가 없어 측정 전 보류 |
| 게이트웨이 폴더와 part | `platform/services/vehicle-gateway/` 신설 시 CONTRIBUTING part 표 추가 | 인지·펌웨어 폴더(`perception/`, `firmware/chassis-ecu/`)도 같이 |
| 컨트롤러 알고리즘 | pure pursuit(무상태) / PI 포함 | 상태가 늘수록 동기화 부담 증가 |
| 게이트웨이 전환 판정 시간 | 100 ms 초과 감지 기준, 실측 후 조정 | Xen 스케줄링 지터 측정 선행. 감지 상한 110 ms는 주기 검사 포함값 |

## 10. 결정 기록

| 결정 | 내용 | 비고 |
| --- | --- | --- |
| 문서 분리 | 설계서(`architecture.md`)와 이 구현 계약으로 분리 | safety·contracts·power로 더 나누는 것은 구현 파일이 생긴 뒤 검토. 3일차 인터페이스 확정 시 메시지 계약은 `docs/contracts/`로 이동 |
