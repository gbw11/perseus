# XenStore 모듈

고정 Zephyr 4.4.0의 ARM64 `xenvm`에서 XenStore wire client와 읽기 전용 VIF 관측을 제공한다.
`references.lock.json`이 Xen public ABI와 재사용한 Zephyr API의 revision·SHA-256을 고정한다.
`abi/`의 두 헤더는 Xen MIT 원문이며 나머지 신규 구현은 Apache-2.0이다. Mini-OS 코드는 복사하지 않았다.

## 사용

앱 CMake에서 `ZEPHYR_EXTRA_MODULES`에 이 디렉터리를 추가하고 `CONFIG_XENOVA_XENSTORE=y`를 선택한다.
`xenstore-test-app`이 같은 모듈을 사용하는 예제다.

```c
static struct xs_zephyr store;
int rc = xs_zephyr_open(&store); /* main 등 thread context */
struct xs_client *client = &store.link.client;
/* xs_read_text / xs_write / xs_directory / xs_transaction / xs_watch */
rc = xs_zephyr_close(&store, 3000);
```

클라이언트 하나는 호출자 하나가 소유한다. 요청 중 중첩 호출은 `-EBUSY`이며
open/close와 transaction callback의 전체 수명은 호출자가 직렬화한다.
callback은 transaction 재시도 시 다시 실행되므로 외부 부작용을 넣지 않는다.
ISR은 semaphore만 올리고 파싱·대기·상태 변경은 호출 thread에서 실행한다.

## 제한과 오류

- 경로: NUL 포함 256 bytes 미만, ASCII 경로 구성요소, `.`/`..` 금지.
- write 값: 2048 bytes, wire 수신 payload: 4096 bytes.
- 일반 요청 1개, 응답 대기/지연 요청 합계 4개, watch 4개, transaction 1개, 충돌 재시도 최대 4회.
- API timeout: 0~120000ms. transaction과 VIF 대기는 하나의 monotonic 마감시간을 공유한다.
- `xs_read`는 byte count, `xs_read_text`는 종단 NUL을 추가한 문자열을 반환한다.
  directory/get_perms는 NUL-separated 목록과 byte count다.
- watch 경로는 절대 경로를 사용한다. read의 상대 경로 domid 조회는 지원한다.
- watch 등록 직후 `xs_watch_next`는 초기 재조회를 요구한다. `initial`과 실제 수신 알림 수는 별도다.
  알림은 합쳐질 수 있으므로 값은 항상 다시 읽는다.
- `-ETIMEDOUT`은 값·연결 성공이 아니다. 늦은 일반 응답은 request ID로 폐기한다.
  완전히 송신한 요청의 일반 대기 만료는 client를 poison하지 않는다.
  부분 송신 중 중단되면 byte stream을 재사용하지 않는다. watch가 유입되는 동안에도 송신
  전체 deadline을 검사한다. 송신 0 byte 만료는 pending slot을 반환한다.
- backend 정보 미완성은 probe의 `-EAGAIN`, VIF 없음은 `-ENODEV`, 거절은 `-EACCES`,
  형식 오류는 `-EPROTO`, backend 교체는 `-EXDEV`다.
- close는 자기 transaction/watch를 제한시간 안에 정리한다. 실패하면 음수로 반환한다.
  응답이 사라진 서버 자원은 정리됐다고 가정하지 않으며 임시 도메인 회수로 종료한다.

HVM Store PFN은 guest physical page 번호다. `k_mem_map_phys_bare`로 WB/RW 별도 VA를 얻는다.
이 API의 VA 부족은 Zephyr kernel panic이며 반환형 오류로 바꿀 수 없다.
해제는 그 매핑 VA에만 적용한다. Store page 자체나 빌려온 event port는 free/close하지 않는다.
console PFN/port와 겹치면 bind 전에 거절한다. 이 모듈이 VM 내 Store port의 유일한 소유자여야 한다.
Store 포트는 mask 상태에서 기존 HYPERVISOR_event_channel_op(EVTCHNOP_unmask)를 호출한다.
Xen의 test-and-clear/replay가 기존 pending을 selector/IRQ로 전달한 뒤 Zephyr ISR이 pending을
소비한다. shared mask를 먼저 지우지 않는다. semaphore kick은 ring 재확인용이며 pending ACK를
대체하지 않는다. 기존 Xen event ISR·console callback·grant table은 수정하지 않는다.

한 domain lifetime에 Store session은 하나다. 초기화 중 실패는 정리 후 다시 시도할 수 있지만
성공적으로 연 session을 닫은 뒤에는 `-EALREADY`를 반환한다.
borrowed XenStore connection을 임의로 reset해 요청 ID를 재사용하지 않기 위한 계약이다.

`xvif_*`는 frontend/backend 값을 구별해 읽는다. state, ring-ref, packet event-channel을 쓰지 않는다.
클라이언트 event port는 Store 통신용이며 VIF packet event-channel과 별개다.

## 재실행

저장소 루트에서:

```sh
python3 -B -m unittest discover -s tests/platform -p test_xenova_xenstore_g1.py -v
```

host 검사는 production main.c와 adapter도 직접 컴파일한다. tests/host의 Zephyr 선언·함수
대역은 kernel/hypercall 경계만 제공하며 xenvm 빌드에는 포함하지 않는다. adapter 시험은
고정 Xen의 2-level pending/mask/selector와 Zephyr ISR 순서를 모델링하며 물리 IRQ 시험은 아니다.

실제 xenvm 빌드·실환경 절차는 `docs/build-guides/xenova-zephyr-xenstore-g1.md`를 따른다.
