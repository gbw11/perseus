# Xenova Zephyr 초기화·협조 종료

이 모듈은 XenStore 공유 세션, netfront Ethernet, 협조형 종료 감시를 제공한다.
Network Starter와 자율·수동 앱은 같은 전송 기반을 사용하지만 시작 실패 정책은 다르다.
앱 계산·차량 제어권·dom0의 실행 의도는 이 모듈이 결정하지 않는다.

## 자원 소유와 상태

| 구성요소/자원 | 확보·사용 조건 | 소유자와 정리 | 실패 또는 기한 만료 |
|---|---|---|---|
| 공유 Store 세션·수신 event·매핑 | `xsr_open` 성공 후 bounded session lock 안에서 사용 | 도메인 공용 `xsr`; 마지막 coordinator가 모든 사용자를 멈춘 뒤 닫음 | 개별 구성요소는 자기 watch만 제거. 실패했다고 다른 사용자의 세션을 닫지 않음 |
| 종료 watch·hook·worker | watch 등록 → hook/worker 준비 → 기능 게시 완료 → 초기 reread | `xshutdown`; 로컬 unwind와 외부 요청이 단일 소유권을 경쟁 | watch 불확정·부분 게시를 소유 상태로 보존. 게시 실패면 worker를 중단하고 coordinator가 철회·join |
| ring·packet grant | ring/data page별 share 성공 시 참조 기록 | netfront의 one-shot 초기화·종료 | 회수 성공한 참조만 반환. 실패·불확정 참조와 정적 페이지는 재사용하지 않음 |
| event channel·callback | allocate/bind/unmask의 각 성공을 구분 | netfront; 사용자/worker 종료 확인 후 mask·unbind·close | bind 전 실패면 unbind하지 않음. close 실패면 port 소유 유지 |
| frontend 게시 | transaction 제출 직전부터 backend 노출 가능 | netfront; Store를 이용해 Closing/Closed 확인 | 응답 유실도 노출 가능 상태. `published=false`를 회수 근거로 쓰지 않음 |
| network worker·interface·DHCP | worker 생성, interface 활성화 시도, DHCP 시작을 구분 | netfront; 생산 차단 → drain → join → backend/event/grant 정리 | 살아 있는 worker가 있으면 페이지를 닫지 않음. interface 오류도 잔존 상태에 포함 |
| 주행 v1 client | 전용 worker만 Bootstrap/init/subscribe/latest/poll/publish/close | worker가 socket을 닫고 `xd_finish`/quiesce가 실제 join | 미시작 worker는 기다리지 않음. join/close 실패는 잔존·정리 오류로 전달 |
| Publisher v2 client·socket | `xt_start`로 시작한 단일 worker만 사용 | worker가 직접 socket을 닫고 caller는 join | join timeout 뒤 TCB/stack 재사용 금지. 불확정 close 오류면 재연결·재시작 금지 |
| 선택 진단 exercise | 시작한 thread만 대상 | Starter가 종료 요청 후 join | 자연 종료·초기화 실패도 실제 thread 종료를 join으로 확인 |

`xnet_status_get`은 미시작, 초기화 실패 후 잔존, 정리 중, 정리 완료를 구별할 근거를
반환한다. 초기화/정리 잠금이 사용 중이면 `-EBUSY`이며, 이를 빈 상태로 해석하지 않는다.
`started=false`는 아직 초기화를 시작하지 않은 상태다. `stopping=true, closed=false`는
정리 중이거나 미정리 상태이며 `grants_held`, port, watch, interface, 게시 가능성을 함께 본다.
`closed=true`는 현재 자원이 정리됐다는 뜻이다. 첫 `initial_error`·`cleanup_error`는
나중 정리가 성공해도 보존하므로 시작 성공이나 오류 이력 소멸을 뜻하지 않는다.

종료 감시의 `features_possible`은 기능이 게시됐거나 게시 효과를 배제할 수 없다는 뜻이다.
앱 준비 완료를 뜻하지 않는다. `worker_joined=false`도 곧바로 thread 실행 중이라는 뜻은 아니다.
thread 내부 완료 신호와 kernel의 종료 확인을 구별한다.

공개 함수·상태 필드의 정식 선언은 [runtime.h](include/xenova/runtime.h),
[netfront.h](include/xenova/netfront.h), [shutdown.h](include/xenova/shutdown.h),
[drive_runtime.h](../../../apps/common/xenova-drive-runtime/include/xenova/drive_runtime.h),
[telemetry.h](../telemetry/include/xenova/telemetry.h)에 둔다.

## 시작과 부분 실패

주행 앱은 종료 감시 → netfront → Diagnostics loop 등록 → Publisher → 앱 루프 순서로 시작한다.
종료 감시·netfront·loop·Publisher 시작 자체의 실패는 앱 시작 거절이다. main은 모든 반환에서
`xd_finish(client, app_result)`를 호출하며, 최초 앱 오류와 정리 오류를 각각 기록한다.
아직 시작하지 않은 앱의 `app_done`을 기다리지 않는다. 실행 중인 앱에 대해서는 앱 소유자가
계산을 끝낸 소유자의 완료 신호와 v1 worker join을 확인한다. 이 신호는 자원 quiesce 확인이며 main thread의
TCB/stack 재사용 허가가 아니다.

Network Starter는 종료 감시나 netfront 실패 뒤 exercise·Publisher를 추가로 시작하지 않는다.
필수 기반을 정리하고 로컬 콘솔·오류 기록은 남긴다. 선택 exercise 또는 Publisher 시작 실패는
Core에 기록하는 저하 상태다. broker나 bootstrap 부재는 두 앱 모두에서 비동기 전송 보류이며,
앱 계산 성공이나 건강 상태로 대체하지 않는다. 이미 살아 있는 v2 worker는 coordinator가 join한다.

각 초기화 호출은 짧은 admission gate에서 허용된 뒤 실행한다. 종료는 같은 gate를 닫고
진행 중 초기화의 lifecycle lock을 기다린다. 따라서 수락 전 이미 허용한 호출의 늦은 성공은
초기화 소유자가 기록하고 정리하며, gate가 닫힌 뒤 다음 자원 확보를 시작하지 않는다.
긴 I/O·join·사용자 hook은 spinlock이나 Store lock 안에서 실행하지 않는다.

netfront와 종료 감시는 같은 guest 실행에서 one-shot이다. 초기화 오류 뒤 flag를 초기화해
재연결하지 않는다. 자원 cleanup의 재호출은 가능하나, 회수 성공한 ref를 다시 반환하지 않는다.
Publisher의 명시적 재시작은 이전 thread 종료 확인 및 새로운 host-issued Publisher session을
필요로 하며, netfront 재연결 허용과는 다른 계약이다.

## 협조형 종료와 로컬 unwind

유효한 외부 `poweroff`/`reboot` 요청은 한 번만 소유권을 얻는다. ACK는 요청 확인이다.
앱의 새 작업·v1 출력/재연결과 Publisher의 새 일반 전송을 차단하고, 이미 허용한 I/O가 반환한 뒤
각 소유자가 자신의 client를 닫는다. 앱/exercise quiesce와 worker join → netfront drain·backend
Closing/Closed·event/grant 정리 → 기능 철회·watch 제거·Store close → Xen shutdown 순서다.
현재 앱에는 별도의 차량 최종 상태 저장·미러링이나 마지막 안전 출력 계약이 없다.
로컬 quiesce를 실제 차량 정지로 보고하지 않는다.

`xshutdown_stop(deadline)`은 초기화 실패를 정리하기 위한 로컬 호출이며 Xen 종료를 제출하지 않는다.
외부 요청이 이미 소유권을 얻었으면 `-EBUSY`다. 이 경우 앱 소유자는 quiesce 완료만 알리고
외부 handler가 같은 요청의 정리를 이어간다. 실패/timeout/hypercall 반환 뒤에도 외부 요청을
다시 IDLE로 돌리거나 같은 요청을 재실행하지 않는다. returning hypercall은 반환값 0도 실패다.

실패한 cleanup은 성공한 자원 반환과 구분한다. 독립적으로 가능한 watch/event 정리는 진행하되,
살아 있는 사용자·불확정 backend가 필요로 할 수 있는 페이지는 남긴다. 잔존 자원은 같은
guest 실행 동안 재사용하지 않는다. 앱 hook 거절, Store 오류, broker 미응답, grant busy를
자동 force·thread abort·성공 ACK로 덮지 않는다. 실제 domain 제거는 dom0가 관측한다.

## 시간과 기능 선택

`CONFIG_XENOVA_SHUTDOWN_BUDGET_MS`의 기본 15,000ms는 guest가 유효 요청을 받은 뒤 사용하는
로컬 정리 예산이다. 그 절대 기한 하나를 app 대기, Publisher join, netfront drain/backend/grant,
Store lock·I/O에 전달한다. 초기화 실패의 로컬 unwind도 별도의 제한 예산 한 번을 사용한다.
정리의 재시도가 끝난 외부 요청에 새 물리 종료 권한을 주지는 않는다.

host의 90초 관리 작업 기한과 guest의 uptime은 직접 비교하지 않는다. 프로토콜은 host의
절대 기한을 전달하지 않으므로 전송/관측 대기를 포함한 종단 종료시간을 이 guest 값으로
보장하지 않는다. 이미 진행 중인 bounded I/O 때문에 join이 만료되면 미정리 상태를 보고한다.
broker 복귀·PUBACK·진단 snapshot 전달은 종료의 무한 선행조건이 아니다. 미전달 자료의 drop과
snapshot 만료는 기존 v2 counter 계약을 따른다.

Starter의 Diagnostics/Telemetry on/on, on/off, off/off 및 선택 exercise 구성을 유지한다.
off/on은 기존 의존성상 지원하지 않는다. 주행 공통 runtime은 Diagnostics·Telemetry·MQTT를
필수로 사용한다. 시험용 failpoint와 경계 대역은 `platform/tests/apps`에만 두며 기본 펌웨어에는
새 원격 장애 명령이나 활성 실패 주입을 추가하지 않는다.

## 패킷 처리와 backend 관측

패킷 worker는 Store API를 호출하지 않는다. 별도 상태 감시 worker가 기존 공유
Store 세션과 backend watch를 사용한다. watch는 알림이므로 값을 다시 읽으며,
알림이 합쳐진 경우를 위해 기존 250ms 주기 재관측도 유지한다. watch 대기는
최대 1ms 세션 사용과 잠금 밖 idle로 나누고, 상태 조회의 기존 50ms 예산은 유지한다.
새 Store 수신자나 세션, system workqueue의 긴 blocking 작업은 추가하지 않는다.

`xnet_status_get`의 backend_observation은 0=미관측, 1=확인된 Connected,
2=이번 관측 불가, 3=확인된 Closing/Closed, 4=신원/프로토콜/전송 오류다.
이미 연결된 VIF에서 세션 획득 실패 또는 사용 가능한 Store의 `-ETIMEDOUT`,
`-EAGAIN`, `-EBUSY`는 관측 불가다. 기존 ring·grant와 패킷 worker를 유지하고
다음 250ms 재관측에서 확인한다. 관측 불가 중에는 watch가 몰려도 상태 재조회를
앞당기지 않는다. 한 조회의 50ms 예산을 네트워크 연결의 수명으로 쓰지 않으며,
현재 관측이 확인됐다고 표시하거나 MQTT 연결·앱 입력의 신선도를 보장하지 않는다.

반면 실제 Closing/Closed, backend 경로 변경, 잘못된 응답 및 사용할 수 없는 Store는
전송을 차단한다. 부분 요청 전송으로 Store가 poisoned가 된 `-ETIMEDOUT`도 여기에
포함한다. 오류 코드만 보고 일시 실패로 바꾸지 않으며, 같은 세션 잠금 아래 확보한
poisoned/closed 상태를 함께 확인한다. 지원하지 않는 XenBus 상태에서 자동 reconnect나
새 ring·grant 초기화를 하지 않는다. 초기화·종료의 handshake와 grant 회수 조건은 그대로다.

`backend_error`는 전송을 차단한 종결 오류다. `observation_error`는 최근 관측 오류이며,
정상 재관측 뒤 0으로 돌아간다. `first_observation_error`는 최초 오류를 계속 보존한다.
`connected`는 TX 허용 상태이며 앱 건강이 아니다. 복귀 시 이 값을 true로 다시 설정하지
않으므로 stop 요청 뒤 도착한 정상 응답이 전송을 재개하지 않는다.
`backend_wait_stage`는 최근 세션 획득(1), 응답 대기(2), 정상 종료(0)의 구분이다.

패킷 worker의 `packet_lock` 획득은 기존 20ms 예산을 유지한다. 고정 Zephyr에서
제한 시간 대기 만료인 `-EAGAIN`이면 ring에 접근하지 않고 한 scheduler tick을
양보한 뒤 다시 시도한다. 이번 회차의 경합만으로 worker나 링크를 종료하지 않는다.
다른 잠금 오류는 원 rc로 종결하며, 잠금 획득 뒤에도 close·backend 종결을 다시 확인한다.
송신 호출은 기존처럼 자기 잠금 획득 실패를 `-ETIMEDOUT`으로 반환하며, 숨은 재송신 큐는 없다.

`xnet_stop_request`는 새 송신을 차단하지만 이미 수락한 송신의 completion 처리까지
즉시 중단하지 않는다. close는 기존 원 기한으로 drain·join·사용자 종료를 확인한다.
살아 있는 잠금 소유자가 남으면 grant를 회수하거나 mutex를 강제로 풀지 않으며,
소유자가 종료한 뒤 같은 close를 재호출해 정리한다. 지속적인 잠금 경합은 정상 진행이
아니다. TX 허용 여부와 실제 poll 진행을 구별하고 대기·보유 시간 및 마지막 poll을 함께 본다.

링 poll의 64건 한도와 검증·메모리 게시 순서를 유지한다. 처리할 응답이 남았으면
일반 10ms idle 대신 한 scheduler tick 뒤 재실행하여 낮은 우선순위 worker에도
실행 기회를 준다. TX/RX 바이트를 임의로 버리거나 무제한 링 drain으로 바꾸지 않는다.
일반 상태 감시 worker의 join을 먼저 확인한 뒤 기존 종료 주체가 Store를 이용해
backend Closing/Closed·watch·grant를 정리한다. Store 자체는 마지막에 닫는다.

Network Starter의 `xenova stats waits`는 패킷 poll 간격, packet lock 대기/보유,
Store 세션 획득/응답 대기의 누적 최대와 관측 상태를 조회한다. `clock=cycles`는
cycle counter, `kernel_ticks`는 tick 해상도다. 관측 최대는 최악 지연 보장값이 아니다.
`store_unavailable`, `store_recoveries`는 관측 불가·복귀의 포화 누적 계수이며,
`last_backend_success_ms`는 마지막 Connected 확인 시점의 guest uptime이다.
`packet_lock_timeouts`는 packet worker의 잠금 대기 만료 누계이며 포화시킨다.
`packet_lock_first_error`는 같은 worker에서 최초로 반환된 잠금 오류를 보존한다.
`packet_last_poll_ms`는 마지막 실제 ring poll 시점의 guest uptime이다. 송신·통계 호출의
잠금 실패를 이 worker 계수에 섞지 않는다. uptime 값은 host 시각과 직접 빼지 않는다.
반복 실패마다 콘솔이나 stack을 출력하지 않는다. 카운터 조회도 Store를 재조회하지 않는다.
이 로컬 계측은 MQTT 계약을 늘리거나 앱 정상·차량 안전을 판정하지 않는다.

## 고정 전송 기반

One XenStore receiver/session, one VIF and one TX/RX queue pair per domain.
G1 wire client, permissions, transaction retries and pending-event replay are
reused. Store users acquire a bounded mutex and never retain the client.
Watch consumers reread values; no application callback runs under that mutex.

Ethernet uses Zephyr net_if and standard IPv4/ARP/ICMP/DHCP. MTU 1500, RX-copy,
8 packet pages per direction, 256 ABI ring entries. IRQ only wakes a thread.
TX pages are held until matching completion; descriptor generations, indices,
length and flags are checked. No checksum offload, extra-info, scatter/gather,
GSO, jumbo, IPv6 or live hotplug reconnect is claimed.

The profile disables upstream XEN_GRANT_TABLE and owns one grant-v1 frame with
32 bounded entries above reserved references 0..7. Static pinned pages use the
frozen permanent-RAM translation helper, never pointer-cast GFNs. Busy revoke
never frees a reference or reuses its page. This avoids the frozen upstream
driver's inverted end-access/free-list path without patching upstream.
The netfront profile must be the sole grant table owner.
Store/shutdown alone can coexist with an existing grant driver.

A separate thread watches control/shutdown and rereads after registration.
Empty ACK means receipt. App hooks must honor the local uptime deadline and
return only after final-state acknowledgement. The network worker stays alive
through the hook. Drain, XenBus Closing/Closed and owned grant cleanup precede
Store close and SCHEDOP_shutdown. A returning hypercall is unconfirmed, even 0.
No output rearm, force escalation or vehicle readiness is provided.

Native tests compile the production ring, grant pool and shutdown flow.
They do not prove actual Xen packets or board shutdown. Pinned Xen public MIT
ABI headers are in references. Linux netback is inspected only, not copied.
Zephyr/SDK retain the original source.lock.json revision.

Thread priorities and teardown budgets are configurable provisional application
settings. They are not host RT scheduling or vehicle safety/timing thresholds.
