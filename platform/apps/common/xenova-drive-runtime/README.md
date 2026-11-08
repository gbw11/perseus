# Zephyr 주행 앱 공통 통합·시험

기존 `autonomous/zephyr`의 `auto_step()`과 `manual/zephyr`의 `manual_step()`에
Diagnostics Core·Telemetry 2.0·Xen netfront·협조형 종료를 연결한다.
계산 알고리즘이나 제품 Gateway를 구현하는 모듈이 아니다.
자율 앱의 최초 입력은 `origin/vehicle`의 `e0c18d125201372b07e18e71cb8e617fb62eb851`이다.
불변 Network Starter 게시본과 기본 VM 펌웨어를 덮어쓰지 않는다.

A/B가 같은 펌웨어에서 두 계산을 실행하는 구성은
[공통 주행 펌웨어](../../drive/README.md)를 사용한다. 기존 개별 앱과 같은 runtime을
재사용하며, `xd_client_prepare_dual()`은 세 입력을 한 owner에 연결한다.
개별 앱의 직렬 hook과 공통 drive의 독립 계산 완료 hook은 같은 release 원점·기한을 사용한다.
공통 drive는 `xd_completed_work`에 선택된 계산의 실제 시각과 원 입력 문맥을 전달한다.
`xd_health_record`는 최신 모드별 평가를 복사할 뿐 진행·heartbeat를 만들지 않는다.
[건강 근거·행동 책임](../../../../docs/contracts/health-evidence-v1.md)을 따른다.
매 iteration의 입력 사본에는 두 계산의 입력과 로컬 VM UUID가 함께 들어간다.
`xd_command_input()`으로 선택 모드의 원 입력을 지정해 명령 만료를 계산한다.
명시 input2/3은 원 Pi source/E와 session/action/state를 보존한다. 현대 MANUAL
계산은 실제 clock으로 변환한 원 guest 기한을 `xd_command_bound_us()`에 전달한다.
ms 전송 한도는 내림하며 수신/완료/재전송으로 원 E를 연장하지 않는다. 계산이 고정한
입력은 후보와 함께 보관하고, 송신 owner가 현재 action/keys/epoch/grant·source 철회를
다시 검사한다. same-held의 새 state는 기존 후보의 만료를 갱신하지 않는다.
수신 owner만 가진 guest 수신 나이로 현대 입력의 Pi 시각 freshness를 주장하지 않으므로
메모리 진단의 input2/3은 원 clock 검산 전 `UNCLASSIFIED`로 표시한다.
v1 heartbeat는 고정된 두 앱별 칸을 사용하므로 서로 덮어쓰지 않으며,
사건 처리 사이의 기존 제한 대기 규칙도 유지한다.

## 실행·신원 계약

- 개별 앱 main 또는 공통 drive의 AUTO/MANUAL 계산 소유자가 입력 사본·실제 계산을 소유한다.
  별도 `drive-v1` worker만 Bootstrap·v1 client·envelope decoder·socket·봉투 순번을 사용한다.
  envelope decoder의 정적 작업 메모리는 이 I/O 소유자만 사용한다. payload parser는
  호출별 제한된 작업 메모리를 사용해 계산·제어 확인 사이에 공유하지 않는다.
- v2는 기존 Publisher worker와 자체 parser/socket/buffer를 사용한다.
  v1 client ID는 `<VM UUID>-<app session 앞 8자리>`, v2는 `xt2-<VM UUID에서 하이픈 제거>`다.
- Xen은 `xshutdown_start → xnet_start → Diagnostics loop → xt_start` 순서다.
  Store 세션은 기존 `xsr` 하나이며 앱 전용 수신자를 열지 않는다.
- v1/v2는 서로 다른 인증 listener·비밀번호·ACL·receipt를 사용한다.
  v1은 자신의 앱 events 쓰기와 control/입력 읽기, v2는 자신의 telemetry 쓰기만 허용한다.
  같은 VM UUID를 계정으로 사용하므로 listener 간 계정을 합치지 않는다.
- `provision-xenova-telemetry.py`로 먼저 v2를 provision한다. v1에는 `--channel drive`와
  같은 실행의 `--guest-boot-receipt`를 넘긴다. guest boot UUID는 공유하고
  app instance와 Publisher instance는 서로 다르다. 새 Xen 실행에는 새 receipt가 필요하다.
- v1 봉투·토픽·QoS·payload는 유지한다. v1 `sent_at`은 기존 업타임 기반 1970년 표기여서
  UTC나 편도 지연 근거로 쓰지 않는다. v2 UTC는 기존 nullable 계약을 유지한다.
- 펌웨어에는 VM UUID·계정·비밀번호를 넣지 않는다. bootstrap 부재 시 로컬 진단은
  계속되지만 인증되지 않은 발행은 하지 않는다. 공통 drive는 유효한 VM·제어 문맥을
  얻기 전 계산 작업을 제출하지 않는다. Xen에 Kconfig 신원 fallback은 없다.

## 계산과 통신 사이의 전달

`xd_client_prepare(client, control_topic, input_topic)`는 v1 worker를 한 번 시작한다.
반환값 0은 worker 시작 수락이며 인증·연결 완료가 아니다. Bootstrap은 worker 안에서
기존 500ms 한도를 사용하고, v1 socket의 기존 250ms I/O 한도도 유지한다.
계산 main은 이 호출에서 Bootstrap·socket을 기다리지 않는다. worker 생성 실패는
앱 시작/정리 오류이며, 인증 입력 부재는 계산을 계속하는 전송 보류 상태다.
worker는 socket readiness를 비대기 조회한 뒤, 출력함/종료 신호로 깨울 수 있는
최대 10ms idle을 사용한다. 출력함 신호가 socket idle 만료를 기다리지 않는다.
이것은 connect·frame 송수신의 기존 250ms 한도를 줄이는 설정이 아니다.

v1 client는 TCP 연결·MQTT CONNECT 제출 뒤, 구독과 앱 메시지 제출 전에
`TCP_NODELAY=1`을 설정한다. 일반 TCP와 deadline TCP 경로 모두 새 연결과
재연결 소켓마다 적용하며, 설정 실패는 소켓 종료와 기존 backoff 재시도로 처리한다.
v2 Publisher의 소켓 옵션과 broker의 `set_tcp_nodelay` 설정은 별도로 관리한다.

매 `xd_iteration_begin()`에서 control과 입력 본문·원래 수신 단조 시각·송신자 실행
신원·seq·유효 여부를 하나의 사본으로 읽는다. 계산 도중에는 이 사본만 사용한다.
교환 잠금이 사용 중이면 기다리지 않고 입력 부재를 기존 알고리즘에 전달한다.
복사 시 입력 나이를 초기화하지 않는다. 새 payload가 잘못되어도 과거 정상값으로
되돌아가지 않는다. `vehicle.control`에는 일반 입력의 200/300ms 만료를 적용하지 않는다.

`connected`는 MQTT CONNACK을 관측한 상태다. SUBACK 확인이나 현재 연결에서의
`vehicle.control` 재수신을 뜻하지 않는다. 기존 v1 client의 latest 캐시는 재연결에도
남으므로 새 입력과 이전 제어 캐시가 함께 보일 수 있다. 제어의 원래 seq·수신 시각을
유지하며 이를 새 제어 확인으로 갱신하지 않는다. 이전 연결에서 만든 출력 후보는
connection revision으로 거절한다. 이후 계산은 마지막으로 수신한 제어 generation을
사용하고, 최종 Gateway가 현재 generation·제어권을 검사해야 한다. 변경 시 발행하는
retained 제어 상태에 임의의 입력 TTL이나 새로운 권한을 부여하지 않는다.

`xd_command()`의 로컬 결과는 최신 출력함 수락/거절이다. 아직 송신하지 않은 후보
한 개는 새 계산 결과로 교체할 수 있다. worker가 가져간 in-flight 사본, TCP byte
stream, Xen ring은 교체하지 않는다. 실제 송신 직전에는 다음을 확인한다.

- 현재 관측한 제어 내용·유효성·송신자 boot/instance가 계산 당시와 같은지
- 후보가 현재 연결 문맥에서 만들어졌는지
- 후보가 `CONFIG_XENOVA_DRIVE_COMMAND_MAX_AGE_MS`(기본 200ms) 이내인지
- 이동 출력에 사용한 입력이 원래 입력 수신 시각의 200/300ms 한도 안인지
- 종료 admission이 여전히 열려 있는지

같은 제어 내용을 새 seq로 재수신해도 제어 문맥을 바꾸지 않는다. 제어 payload나
송신자 실행이 바뀌면 새 revision이 되어 이전 후보를 거절한다. 이 검사는 아직
수신하지 않은 변경을 알 수 없으며 최종 Gateway의 generation·제어권 검증을 대체하지 않는다.
후보 나이 200ms는 계산 주기 20ms나 Gateway 수락 기한과 별개의 잠정 로컬 상한이다.
송신은 이 남은 기한과 기존 I/O 예산 중 작은 값을 사용한다. 부분 frame 송신 오류는
연결을 닫고 해당 후보를 버리며, 다음 연결에서 재전송하지 않는다.

계산이 만든 `app.heartbeat`는 최신 보고 한 개, `perception.event`는 사건 큐 4개로
전달한다. 둘 다 생성 후 1초까지만 제출하며 폐기·교체 횟수를 보존한다. worker는
heartbeat를 새로 만들거나 과거 보고에 새 uptime을 붙이지 않는다. 따라서 통신이나
v2 Publisher만 실행되는 것을 실제 앱 진행으로 해석하지 않는다.
heartbeat가 대기 중이면 사건 보고를 최대 한 개 먼저 처리한 뒤 heartbeat에 제출 기회를
준다. 이는 기존 bounded I/O 한도 안에서의 순서 규칙이며 전달 기한 보장이 아니다.
`heartbeat_replaced`는 새 계산 보고에 의한 교체, `heartbeat_dropped`는 수락 후 제출 실패·폐기,
`heartbeat_expired`는 그중 기한 만료다. heartbeat 폐기를 `event_dropped`에 합산하지 않는다.

공통 drive 프로필은 두 heartbeat와 별개인 두 `app.readiness` 최신 칸을 사용한다.
네 칸을 round-robin으로 처리하고 대기 중인 보고 앞에는 사건 최대 한 개만 통과시킨다.
준비 보고의 worker 대기 기한은 300ms이며 실제 완료 근거를 worker가 갱신하지 않는다.
`readiness_replaced/dropped/expired`로 별도 집계한다. 원문 필드·신선도 규칙은
[준비 계약](../../../../docs/contracts/mqtt-contract-v1.md#62-앱-준비-보고-appreadiness)을 따른다.

quiesce는 새 출력 수락을 차단하고 계산 종료와 v1 worker의 실제 join을 확인한다.
공통 drive의 계산 스레드도 같은 guest 정리 deadline으로 먼저 join한다. 반환하지 않은
계산 callback과 그 stack·문맥은 잔존으로 보존하며 shared 자원 종료 성공으로 바꾸지 않는다.
worker가 자신의 socket을 abort한 뒤 Publisher를 정지하고, 기존 coordinator가
netfront·Store를 정리한다. join은 같은 guest 정리 기한을 공유한다. 미종료 thread,
불확정 close 또는 grant를 성공으로 바꾸거나 강제 thread abort로 숨기지 않는다.
세부 규칙은 [Zephyr runtime](../../../guests/zephyr/xen-runtime/README.md)을 따른다.
`xd_publish()`도 보고 큐 수락이지 송신 성공이 아니다. main의 직접 `xd_poll()`은
`-EPERM`이며 종료 뒤에는 `-ESHUTDOWN`이다. socket은 worker만 사용한다.

출력함/보고의 주요 거절은 `-EXDEV`(제어·연결 문맥 불일치),
`-EBUSY`(짧은 교환 잠금 사용 중), `-ENOBUFS`(사건 큐 포화),
`-ESHUTDOWN`(종료 admission 닫힘)이다. 후보 만료는 송신 기록의 `-ETIME`으로
구분한다. join 기한 만료는 `-ETIMEDOUT`이며 worker 종료 성공으로 표시하지 않는다.
외부 종료 기한은 stop gate와 함께 게시하고 main의 join에도 그대로 적용한다.
이미 진행 중인 I/O의 늦은 반환은 소유·잔존 상태로 보존하며 기한을 새로 발급하지 않는다.

## 실제 계산과 계측

`xd_step_begin/end`는 실제 계산 함수 앞뒤에 있다. elapsed는 경과시간이며 CPU time이 아니다.
`loop_seq`는 계산 완료 때만 증가한다. app heartbeat도 계산 루프에서만 발행한다.
공통 drive에서는 선택 계산의 실제 완료를 의미하며, 앱별 progress는 별도 어댑터의 실제
완료 근거다. poll·송신·대기는 계산 완료가 아니다. 보고와 후보의 만료도 원 생산 시각과
입력 수신 시각을 보존하여 coordinator가 늦게 읽었다고 새 기한을 주지 않는다.
telemetry heartbeat는 독립 worker의 상태이며 앱 생존을 대신 판정하지 않는다.

기존 `now + period` 재동기화 대신 Core의 고정 origin/next release를 스케줄 원본으로 사용한다.
지난 release는 재실행하지 않고 Core가 skipped로 센다. 주기 자체는 20ms 그대로다.
입력 사본 → 계산 → 직렬화 → 출력함 수락을 계산 기록으로 남기고,
후보 가져오기 → 실제 MQTT 제출 → 독립 수신을 별도 기록으로 연결한다.

로컬 콘솔 명령은 기존 `xenova` 그룹을 사용한다.

```text
xenova diag
xenova telemetry
xenova drive capture 30000 180000
# 측정 창 종료 후에만:
xenova drive records 0 4
xenova drive transmissions 0 4
# 응답 XD_PAGE.next를 다음 offset으로 전달하며 total까지 읽는다.
```

`records [offset] [count]`와 `transmissions [offset] [count]`는 각각 최대 4행씩 반환한다. 첫 페이지에 summary/session, 각 페이지에
`XD_PAGE {offset,next,total}`이 있다. Xen PV 출력 ring 유실을 피하도록 응답을 받은 뒤 다음 페이지를 요청한다.
native 시험의 종료 후 파일 출력은 완전한 원자료를 한 번에 보존한다.

`capture` 인자는 시작 지연·측정 기간(ms)이다. `capture_complete`는 계산 창 종료,
`transmissions_complete`는 그 창에서 출력함이 수락한 모든 후보의 최종 처리 기록 확정이다.
송신 중인 마지막 후보가 남으면 두 값은 다를 수 있다. 둘 다 참이 되기 전에는
`records`·`transmissions` 페이지와 새 capture 설정을 `-EBUSY`로 거절한다.
최대 12,288개 행을 고정 RAM에 보관하며 덮어쓰지 않는다. 초과는 `overflow`로 보고한다.
매 주기 파일/콘솔 출력, 동적 할당, 스레드 스캔을 하지 않는다.
180초/50Hz 창은 약 9,000행이며 RAM 영향은 ELF/map의 실제 결과로 확인한다.

원자료 schema 2의 `XD_ROW`는 계산·출력함 기록이다. 예전 submit 위치는
출력함 offer 시작·반환 시각이고 message/topic seq 위치는 아직 미지정인 0이다.
이름과 위치의 정식 해석은 `tools/analyze.py`의 `FIELDS`·schema 처리에 있다.
`XD_SEND`는 `SEND_FIELDS` 순서로 계산 완료 번호, 생성·수락·가져오기·실제 송신
시각, message/topic seq, 오류, 최종 처리를 기록한다. 실제 송신하지 않은 필드의
0은 미수행 표식이다. disposition은 0=로컬 송신 성공, 1=송신 실패,
2=미송신 후보 교체, 3=만료, 4=제어/연결 문맥 변경, 5=종료 폐기다.

시각은 같은 guest의 단조 µs이며 UTC가 아니다. miss/skipped는 해당 계산 완료의
증가분, completed는 누적 완료 번호다. v/steer는 float32 bit pattern이다.
계산 기록과 송신 기록은 완료 번호로, 실제 수신은 VM/boot/app instance/message ID로
연결한다. 다른 heartbeat/event가 만든 topic seq 간격은 명령 유실이 아니다.
`XD_SUMMARY`의 generated·mailbox_offered/accepted/rejected·not_offered와
`XD_IO`의 enqueued/replaced/expired/context_dropped/stopped/attempted/sent/failed를
구별한다. `XD_IO`의 prepare/poll/exchange 최대값은 로컬 누적 관측값이다.
`delivery_overflow`나 수락 후보의 최종 기록 누락은 완전한 자료로 판정하지 않는다.
`XD_CLOCK`은 cycle 주파수, IO 시계 오류, snapshot 잠금 충돌 수를 남긴다.
종료 후 join이 확인된 worker만 stack 여유를 스캔한다. 지원하지 않거나 아직
실행 중이면 오류와 `stack_unused_bytes:null`을 반환한다. native의 거친 cycle
해상도에서 0µs로 나온 구간을 비용이 없는 작업으로 해석하지 않는다.

송신 확정 뒤에는 해당 창의 행과 송신 페이지 total을 고정한다. 명시적인 새 capture는 새 창을
만드는 작업이므로 이전 창의 회수를 마친 뒤 실행한다. 페이지를 모두 읽고 같은 실행의
수락 후보별 최종 기록이 있는지 분석기로 확인한다. 명시적으로 미확정인 자료, overflow,
수락 후보의 최종 기록 누락은 분석기가 거절한다. 이전 schema 2의 완료 필드가 없는 자료도
기존 최종 기록 대조를 수행한다.
기존 schema 1 분석은 유지하지만 출력함 수락을 MQTT 제출로 재해석하지 않는다.
QoS0 로컬 성공과 독립 수신 성공은 다르다. 종료는 offline flush나 수신 ACK를
기다리지 않으므로 종료 경계의 미수신도 원자료와 함께 별도 보고한다.

### 같은 실행의 페이지 회수

recorder가 있는 실험 구성에서 측정 창과 송신 결과가 모두 확정된 뒤 회수한다.
`tools/collect_console.py`는 기존 명령·출력만 사용한다. 매 페이지의 전후에 `records 0 1`의
VM/boot/app instance/execution과 capture 시작·종료·행 수를 대조하고, 페이지를 두 번 읽어
동일한 offset/next/total·내용·행 수를 확인한다. 신원·창 확인은 기존 CLI의 1행 조회로
불필요한 반복 출력을 줄이고, 실제 데이터 페이지는 기존 4행씩 두 번 읽는다.
실행·창·total이 바뀌면 혼합하지 않고 실패한다.
한 페이지 최대 3회, 전체 기본 300초이며 조회 재시도로 전체 기한을 갱신하지 않는다.

```sh
python3 platform/apps/common/xenova-drive-runtime/tools/collect_console.py \
  --console /dev/pts/OWNED --output /absolute/new-capture --timeout 300
```

`/dev/pts/OWNED`는 운영자가 마련한 전용 raw PTY의 실제 경로로 바꾼다. 로그인·콘솔 lease를
얻거나 다른 사용자의 콘솔을 빼앗는 도구가 아니다. 초기 prompt가 관측돼야 명령을 제출하며,
응답 prompt를 잃으면 이를 되찾기 전 추가 명령을 쓰지 않는다. flush/reset·capture 재설정·
VM 제어는 하지 않는다. advisory lock은 협조하는 수집기 사이의 잠금이며 다른 콘솔을 강제로
배제하지 않는다. 동시 사용은 지원하지 않는다. prompt가 다르면 `--prompt`로 정확히 지정한다.

완료하면 `source.stdout`과 `collection.json`, 실패하면 `source.partial`과 실패 이유를 남긴다.
페이지 회수 완료는 계산 내용·전달·성능 수락이 아니다. 기존 `analyze.py`의 원자료 구조·
수락 후보/terminal 일대일 대조를 추가로 통과해야 한다. 미제출 STOP과 QoS0 유실도 구분한다.
기존 분석기의 최종 비교 명령은 standalone manual/autonomous 시험 입력을 기준으로 하며,
공통 drive의 모드 혼합 자료를 같은 두 앱 성능 표본으로 자동 해석하지 않는다.

기존 출력에는 페이지 자체의 실행 토큰/checksum이 없다. 따라서 이 절차는 신뢰된 단일
콘솔의 직렬 prompt/echo 경계와 불변 측정 창을 전제로 한다. 외부 root 조작까지 원자적으로
통제하거나 두 번 모두 같은 방식으로 변조된 자료를 암호학적으로 검출한다고 주장하지 않는다.
PV ring 유실·정체는 제한된 재조회와 기한 실패로 드러내며, 물리 PV 전달 수락은 별도다.

### 운영 구성과 선택 계측

`XENOVA_DRIVE_RECORDER=n`이면 큰 source/transmission 배열을 링크하지 않는다. 실제 건강·
기한·CPU 계상·기본 오류 기록은 유지된다. 빌드 도구의 `operations`는 이 구성과 상세 stack
scan 비활성화를 선택하며 `experiment`는 둘을 명시적으로 활성화한다. stack sentinel과
계산 소유권·정리 규칙은 두 구성에 공통이다. native 시험의 기본 recorder 사용과 제품 운영
기본값을 혼동하지 않는다. 전체 호출 스택의 여유와 실제 Xen 성능은 빌드 크기와 별도로 확인한다.
`analyze.py`의 `accepted`는 기능·자료 정합성 수락이다. 성능 개선 판정은 별도다.
계산 시작·후보 생성/수락·실제 송신 시작/소요시간·독립 수신 간격을 각각의 단조 시계에서
분석한다. Mailbox 교체/만료, QoS0 성공 후 미수신, 긴 간격 뒤 짧은 간격이 이어지는 뭉침은
서로 다른 집계다. guest 시각과 dom0 수신 시각을 빼서 편도 지연을 만들지 않는다.

bootstrap의 실행 신원 읽기가 미수락 `OPERATION_IN_PROGRESS`로 거절되면 최대 30초 지연 범위에서
재시도한다. 수락된 변경·신원 변경·불확실한 Store 쓰기는 재시도 성공으로 바꾸지 않는다.

독립 수신기는 실행 위치의 Linux affinity와 수신 단조 시계를 기록한다. `SIGTERM`은 수신을 멈추고
bounded writer를 비운 뒤 summary와 정상 종료 코드를 남긴다. broker·guest를 종료하는 신호가 아니다.
수신기 배치가 dom0이면 이 Linux CPU 번호는 dom0 vCPU이며 Xen pCPU affinity와 구분한다.

Xen netfront의 `.send`는 앱 main뿐 아니라 기본 1KiB TCP workqueue에서도 호출된다.
MTU 지역 배열을 사용하던 ARM 함수 프레임은 1,568바이트로 이 스택보다 컸다.
현재는 기존 packet lock이 고정 TX 버퍼의 복사와 ring 제출을 함께 보호한다.
부분 builder는 같은 ARM compiler로 직접 프레임이 256바이트 예산 이내인지 검사하고 영수증에 기록한다.
이 예산은 어댑터의 회귀 방지 조건이며 전체 호출 체인이나 실시간 기한의 보장이 아니다.
두 xenvm 앱은 stack sentinel도 활성화해 인접 스레드 문맥 침범을 조기에 감지한다.

## M1: 부분 빌드·패키징

[정규 빌드 가이드](../../../../docs/platform/build-guide.md)의 고정 Zephyr/SDK를 사용한다.
`ZEPHYR_BASE`와 `XENOVA_ZEPHYR_TOOLS`는 이미 검증한 source/host-tools 절대 경로다.
출력은 저장소 밖의 새 디렉터리여야 한다.

```sh
python3 -B platform/scripts/build-xenova-drive-app.py \
  --app autonomous --zephyr-source "$ZEPHYR_BASE" --tools "$XENOVA_ZEPHYR_TOOLS" \
  --output "$HOME/drive-auto-candidate-01" --execute
python3 -B platform/scripts/build-xenova-drive-app.py \
  --app manual --zephyr-source "$ZEPHYR_BASE" --tools "$XENOVA_ZEPHYR_TOOLS" \
  --output "$HOME/drive-manual-candidate-01" --execute
```

builder는 source snapshot·공통 모듈 hash·고정 Zephyr API·compiler hash·명령/종료 코드·전체 출력을
보존한다. 실제 DTS/ELF의 기존 network 1-vCPU/16MiB/GICv2 프로필을 검사하고
`package-xenova-firmware.py --networked`로 새 후보만 만든다.
`firmware/firmware.json` hash와 `BUILD_RECEIPT.json`을 배포·동결 근거로 사용한다.
이 빌드 수락은 실보드 수락이 아니다.

계산 로직과 공통 v1 벡터는 기존 시험을 사용한다.

```sh
cmake -S platform/apps/manual/zephyr/tests -B "$HOME/drive-manual-tests" -DCMAKE_EXE_LINKER_FLAGS=-no-pie
cmake --build "$HOME/drive-manual-tests"
ctest --test-dir "$HOME/drive-manual-tests" --output-on-failure
# 자율 앱은 같은 명령의 manual을 autonomous로 바꾼다.
python3 -B -m unittest discover -s platform/tests -p 'test_xenova_telemetry.py' -v
```

## M2: 실제 생성·독립 수신

native_sim에서도 **실제 두 앱**을 빌드한다. 시험 파일 loader는 ARCH_POSIX 전용이다.

```sh
export ZEPHYR_TOOLCHAIN_VARIANT=host
cmake -S platform/apps/manual/zephyr -B "$HOME/drive-manual-native" -GNinja -DBOARD=native_sim/native/64
cmake --build "$HOME/drive-manual-native"
cmake -S platform/apps/autonomous/zephyr -B "$HOME/drive-auto-native" -GNinja -DBOARD=native_sim/native/64
cmake --build "$HOME/drive-auto-native"
# 기존 별도 Mosquitto 시험 도구(root/usr/sbin/mosquitto 포함) 경로를 사용한다.
sudo unshare --net -- python3 -B platform/apps/common/xenova-drive-runtime/tests/run_native_acceptance.py \
  --manual "$HOME/drive-manual-native/zephyr/zephyr.exe" \
  --autonomous "$HOME/drive-auto-native/zephyr/zephyr.exe" \
  --tools-root "$MOSQUITTO_TEST_TOOLS" --output "$HOME/drive-native-receipt-01" --duration 24 --extended
sudo python3 -B platform/apps/common/xenova-drive-runtime/tools/analyze.py \
  "$HOME/drive-native-receipt-01" --output "$HOME/drive-native-receipt-01/ANALYSIS.json"
```

정상 수신에서는 `--require-lossless`를 추가할 수 있다. 단절 시험의 QoS0 미수신은 위치를 단정하지 않는다.
`drive_probe.py input`만 통제 입력을 만든다. 고정 20Hz·8초 4단계 시나리오로
키/차선 offset/정지와 control generation을 바꾼다. 주행 명령을 대신 만들지 않는다.
입력 중단 후 기존 200ms/300ms stale 처리도 실제 입력 age로 검증한다.

독립 receiver는 전체 MQTT frame을 받은 직후 단조 ns를 찍고 4096칸 queue로 넘긴다.
분석·계약 검사·디스크 저장은 writer thread에서 수행한다. queue drop·writer error·수신 오류를 남긴다.
v1은 기존 Python envelope/policy, v2는 기존 독립 oracle과 외부 binding으로 검사한다.
인증정보는 private 입력에만 있고 raw 결과에는 payload와 공개 실행 식별자만 남긴다.

실보드는 별도 승인 후 같은 입력/receiver를 격리 broker와 소유 시험 VM에 연결한다.
실제 netfront 수신·브로커 단절/복귀·guest 재실행/fresh bootstrap·협조형 종료를 확인한다.
native 합격을 실보드 수락으로 대체하지 않는다. M2가 실패하면 M3를 시작하지 않는다.

## M3: CPU 정책 전체 비교

[기존 CPU 정책 계약](../../../../docs/platform/cpu-layout.md)을 그대로 사용한다.
미적용은 정책 삭제가 아니라 `exclusive=[]`, `shared_pcpus=[0,1,2,3]`인 명시적 정책이다.
적용은 시험 자율/수동 UUID를 각각 pCPU1/2, dom0와 나머지 모든 vCPU를 {0,3}으로 제한한다.
정책 전환/host prepare는 모든 관리 guest 정지와 미완료 작업 부재를 요구한다.
이를 우회하거나 실행 중인 기본 VM을 무단 정지하지 않는다.

비교 입력은 M2 통과 후 앱별 firmware hash, 20ms 주기, 우선순위, I/O 예산, 계정/ACL/QoS,
broker 설정, 시나리오, Debian vCPU/메모리/worker 수로 동결한다.
기본 계획은 warm-up 30초 + 측정 180초 × 조건별 3회다. 정책별 블록 안에서 idle/load를 교차하고
순서·초기 온도·실제 CPU time/계산량·온라인 pCPU/pool/Credit2·전체 hard affinity를 기록한다.

`tools/cpu_load.py`는 승인한 Debian guest 안에서만 실행하는 제한된 CPU 계산 부하다.
PID/run ID를 남기며 CPU time과 iterations를 확인한다. affinity·scheduler·메모리·네트워크를 바꾸지 않는다.
CPU 정책 변화는 dom0/receiver 부하도 바꾸므로 Zephyr 자체 고정만의 효과로 주장하지 않는다.

구간 source raw의 exact quantile/최대와 v2 누적 histogram의 p95/p99 **버킷 상한**은 별개다.
누적 max/분위수를 빼서 구간 통계를 만들지 않는다. guest와 receiver 시계를 빼지 않는다.
실차 제어·ECU 도달·실시간 기한 보장을 수락하지 않는다.

## 승인·원복과 결과

보드 변경 직전에 시험 VM/계정/listener, 최신 플랫폼 시험 사본/설치 범위,
보호 VM의 정지/복귀, dom0 affinity, Debian 부하, 신원·자산 백업/원복을 한 번에 구체화한다.
Wi-Fi·SSH·UART·게시 템플릿·기본 VM의 신원/펌웨어는 보존한다.
실험 policy를 제거해 미적용 조건을 만들지 않으며 최종 정책/설치본 원복은 승인한 절차대로 대조한다.

결과는 M1 앱 통합, M2 실제 생성/수신, M3 동일 바이너리 4조건을 각각 판정한다.
원천/수신/환경 영수증과 분석 명령, 실패·미검증·승인 대기를 함께 보관한다.
제품 소비자는 기존 v1 주행/앱 heartbeat와 v2 진단을 별도 신원·상태로 소비해야 한다.
