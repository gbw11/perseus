# Zephyr 주행 앱 공통 통합·시험

기존 `autonomous/zephyr`의 `auto_step()`과 `manual/zephyr`의 `manual_step()`에
Diagnostics Core·Telemetry 2.0·Xen netfront·협조형 종료를 연결한다.
계산 알고리즘이나 제품 Gateway를 구현하는 모듈이 아니다.
자율 앱의 최초 입력은 `origin/vehicle`의 `e0c18d125201372b07e18e71cb8e617fb62eb851`이다.
불변 Network Starter 게시본과 기본 VM 펌웨어를 덮어쓰지 않는다.

## 실행·신원 계약

- 앱 main 한 스레드가 v1 `xm_zclient`의 init/subscribe/latest/publish/poll/close를 소유한다.
  v1 decoder/payload parser의 정적 scratch도 이 스레드만 사용한다.
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
- 펌웨어에는 VM UUID·계정·비밀번호를 넣지 않는다. bootstrap 부재 시 계산·로컬 계측은
  계속되지만 인증되지 않은 발행은 하지 않는다. Xen에 Kconfig 신원 fallback은 없다.

quiesce는 새 명령·app heartbeat를 막고 main 소유자가 v1을 abort한 뒤 Publisher를 정지한다.
그 다음 기존 runtime이 netfront를 닫는다. 브로커 복귀나 offline flush를 기다리지 않는다.
v1 TCP connect/send/read는 기존 v2 custom transport를 별도 socket 상태로 재사용하며
I/O 한 회의 전체 예산은 250ms다. 이 값은 **20ms 계산 주기와 별개**다.
연결 중에는 계산이 늦어질 수 있으며 lateness/miss/skipped로 그대로 기록한다.
재접속 구조·스레드 우선순위·주행 계산 함수를 변경하지 않는다.

## 실제 계산과 계측

`xd_step_begin/end`는 실제 계산 함수 앞뒤에 있다. elapsed는 경과시간이며 CPU time이 아니다.
`loop_seq`는 계산 완료 때만 증가한다. app heartbeat도 계산 루프에서만 발행한다.
telemetry heartbeat는 독립 worker의 상태이며 앱 생존을 대신 판정하지 않는다.

기존 `now + period` 재동기화 대신 Core의 고정 origin/next release를 스케줄 원본으로 사용한다.
지난 release는 재실행하지 않고 Core가 skipped로 센다. 주기 자체는 20ms 그대로다.
입력 취득 → 계산 → 직렬화 → MQTT 제출 → poll을 분리해 원천 기록한다.

로컬 콘솔 명령은 기존 `xenova` 그룹을 사용한다.

```text
xenova diag
xenova telemetry
xenova drive capture 30000 180000
# 측정 창 종료 후에만:
xenova drive records 0 4
# 응답 XD_PAGE.next를 다음 offset으로 전달하며 total까지 읽는다.
```

`records [offset] [count]`는 최대 4행씩 반환한다. 첫 페이지에 summary/session, 각 페이지에
`XD_PAGE {offset,next,total}`이 있다. Xen PV 출력 ring 유실을 피하도록 응답을 받은 뒤 다음 페이지를 요청한다.
native 시험의 종료 후 파일 출력은 완전한 원자료를 한 번에 보존한다.

`capture` 인자는 시작 지연·측정 기간(ms)이다. 진행 중 재설정·records 덤프를 거절한다.
최대 12,288개 행을 고정 RAM에 보관하며 덮어쓰지 않는다. 초과는 `overflow`로 보고한다.
매 주기 파일/콘솔 출력, 동적 할당, 스레드 스캔을 하지 않는다.
180초/50Hz 창은 약 9,000행이며 RAM 영향은 ELF/map의 실제 결과로 확인한다.

`XD_ROW`의 순서는 `tools/analyze.py`의 `FIELDS`다. 시각은 guest 단조 µs이며,
miss/skipped는 **그 계산 완료의 증가분**, completed는 누적 완료 번호다.
v/steer는 손실 없는 float32 bit pattern이고 입력 seq·입력 age·control seq를 함께 기록한다.
원천 records와 수신 기록은 v1 VM/boot/app instance/message ID로 연결한다.
같은 topic의 heartbeat/event가 만든 seq 빈 번호를 명령 유실로 세지 않는다.

summary의 generated는 계산 결과 수, attempted는 제출 호출 수,
accepted/failed는 로컬 호출 결과, dropped는 직렬화/종료로 제출 전에 폐기한 수다.
failed 명령도 offline queue나 재전송 없이 버린다. 따라서 로컬 비재시도 수는
`failed + dropped`이며 실제 subscriber 도달 여부와 별도로 해석한다.
QoS0의 로컬 성공을 수신 성공으로 바꾸지 않는다.

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
