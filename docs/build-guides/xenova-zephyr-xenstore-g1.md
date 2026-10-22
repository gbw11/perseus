# Xenova XenStore G1 빌드·실환경 시험

기존 Starter/A·B와 별도인 임시 Zephyr 도메인 하나를 사용한다. 실환경 실행은 사용자 담당이다.
packet TX/RX·IP·DHCP·ping은 이번 시험의 판정 항목이 아니다.

## 입력과 빌드

- 펌웨어 빌드 기준은 firmware/g1-build.json의 원본 HEAD·입력 해시를 따른다.
  통합 런타임은 handoff-manifest.json의 source 경로·파일 해시로 별도 추적한다.
  런타임만 갱신해도 원본 펌웨어 빌드 HEAD나 해시를 고쳐 쓰지 않는다.
- Zephyr 4.4.0: `684c9e8f32e4373a21098559f748f06915f950c9`
- SDK 1.0.0: `platform/guests/zephyr/source.lock.json`의 archive SHA-256
- Xen 4.21.0+stable: `1c72306b1f0b4dc533954f678361044f8083dccc`
- ABI·재사용한 매핑/event API: `xen-store/references.lock.json`
- 기존 xenvm RAM 0x40000000/16MiB, 1 vCPU, GICv2, runtime timer frequency.
  정적 DTS와 실제 Xen domU DT의 IRQ·GIC·메모리 일치는 실환경에서 대조한다.

변수를 실제 검증한 입력의 절대 경로로 설정한다. output은 새 경로여야 한다.

```sh
ZEPHYR_SOURCE=/absolute/existing/source/zephyr
TOOLS=/absolute/existing/host-tools
DOWNLOADS=/absolute/existing/downloads
G1_OUTPUT=/absolute/new/g1-build
python3 -B platform/scripts/build-xenova-xenstore-g1.py \
  --zephyr-source "$ZEPHYR_SOURCE" --tools "$TOOLS" \
  --spdx "$DOWNLOADS/zephyr-v4.4.0.spdx" \
  --toolchain-archive "$DOWNLOADS/toolchain_gnu_linux-x86_64_aarch64-zephyr-elf.tar.xz" \
  --output "$G1_OUTPUT" --execute
rc=$?; echo "build rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

도구는 공식 SPDX 59,501개 source checksum, Git ref와 SDK archive SHA를 검사한다.
`input/`에 앱·모듈을 복사·해시로 고정한 뒤 실제 Zephyr 헤더와 SDK로 컴파일한다.
`configure/compile.command.json`과 `.log`에 명령·종료 코드가 있다.
`firmware/`에 bin/ELF/map/config/DTS/license·`g1-build.json`이 생긴다.
일반 Starter의 firmware.json·카탈로그·검증기를 사용하거나 완화하지 않는다.

```sh
python3 -B -m unittest discover -s platform/tests \
  -p test_xenova_xenstore_g1.py -v
rc=$?; echo "local rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

native/ASan·UBSan은 동일 production C를 컴파일한다. 대역은 XenStore peer·공유 ring·시간·알림·
매핑/event 외부 경계다. production 앱 main.c도 실행해 root RO/data RW 및 data 거절·부재를 검사한다.
adapter host 검사는 kernel/hypercall 선언 대역과 2-level pending 전이 모델을 사용한다.
이것은 물리 IRQ 시험이 아니다. 실제 xenvm 빌드는 고정 Zephyr 헤더만 사용하며 host 대역을 쓰지 않는다.

## 검증된 펌웨어로 인계물 게시

검증한 G1 firmware 디렉터리와 현재 소스로 새 인계물을 만든다. 기존 경로는 덮어쓰지 않는다.
도구는 펌웨어의 모든 앱·모듈 입력과 빌드 스크립트·reference lock을 현재 소스와 비교한다.
달라졌으면 재포장을 거부하므로 새 타깃 빌드가 먼저 필요하다.
동일하면 bin/ELF/map/config/DTS와 원본 g1-build.json을 바꾸지 않고 재사용한다.

```sh
G1_FIRMWARE=/absolute/verified/g1-build/firmware
G1_HANDOFF=/absolute/new/g1-handoff
python3 -B platform/scripts/package-xenova-xenstore-g1-handoff.py package \
  --firmware "$G1_FIRMWARE" --output "$G1_HANDOFF" --execute
rc=$?; echo "package rc=$rc"; [ "$rc" -eq 0 ] || exit 1
python3 -B platform/scripts/package-xenova-xenstore-g1-handoff.py verify --directory "$G1_HANDOFF"
rc=$?; echo "verify rc=$rc"; [ "$rc" -eq 0 ] || exit 1
(cd "$G1_HANDOFF" && sha256sum -c SHA256SUMS)
rc=$?; echo "hash rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

`--execute`를 생략하면 출력·staging을 만들지 않는 계획 검사다.
runtime/은 실제 /usr/lib/xenova 레이아웃의 Python 모듈 18개와 Ubuntu/Debian cfg 2개를 포함한다.
지연 import까지 검사하며 복구 모듈을 뺀 구형 다섯 파일 묶음으로 대체하지 않는다.
handoff-manifest.json은 원소스 경로·파일 SHA, 보존한 펌웨어 출처와 전체 인계 파일을 기록한다.
manifest와 SHA256SUMS의 전체 파일 목록·해시가 일치해야 하며 추가 파일·symlink도 거부한다.

인계물 갱신 자체는 운영 /usr/lib 설치나 펌웨어 교체가 아니다.
보드에서 사용할 runtime의 해시가 인계 manifest와 다르면 별도 시험 경로로만 사용하고,
운영 /usr/lib/xenova를 인계 파일로 덮어쓰지 않는다.

## 사전 점검 — 읽기 전용

전송 대상은 새 `firmware/`, lab 스크립트와 현재 플랫폼 runtime 소스다.
보드에 동일 runtime이 있으면 `--platform-dir /usr/lib/xenova`를 사용할 수 있다.
없으면 인계 묶음의 runtime/을 별도 시험 경로에 두고 지정한다.
이 디렉터리는 공통 관리·복구·신원·cfg 모듈을 포함한 위의 전체 런타임 사본을 포함한다.
게시한 인계 경로를 기준으로 LAB_TOOL="$G1_HANDOFF/scripts/xenova-xenstore-g1-lab.py",
PLATFORM_DIR="$G1_HANDOFF/runtime"을 지정한다. 실제 Xen 라이브러리·도구는 dom0 설치본을 사용한다.
전체 저장소 소스로 실행할 때는 lab loader가 platform/first-install 의존 경로도 읽는다.
운영 /usr/lib는 덮어쓰지 않는다.

```sh
LAB_TOOL=/absolute/review/scripts/xenova-xenstore-g1-lab.py
PLATFORM_DIR=/absolute/review/runtime
G1_LOGS=/absolute/existing-parent/xg1-command-logs
python3 -B "$LAB_TOOL" --platform-dir "$PLATFORM_DIR" --log-directory "$G1_LOGS" preflight >g1-preflight.json
rc=$?; echo "preflight rc=$rc"; [ "$rc" -eq 0 ] || exit 1
uname -a
xl info
xl list
systemctl status xenstored --no-pager
systemctl status xenconsoled --no-pager
```

unit 이름이 다르면 `systemctl list-units --all --no-pager '*xen*'`로 설치 주체를 확인한다.
도구는 xl·xenstore-read/write/list/ls·ip·python3, platform import, privcmd, XenStore 읽기,
xenbr0의 주소·Xen VIF 격리를 확인한다. 누락 도구·접근 실패·잘못된 bridge는 기록하고 중단한다.
서비스·bridge·DHCP 재시작이나 네트워크 재설정으로 우회하지 않는다.

```sh
ls -ld /sys/bus/xen-backend/drivers/vif
ip -4 address show dev xenbr0
ip -details link show dev xenbr0
xenstore-ls /local/domain/0/backend/vif
```

Linux VIF가 하나도 없으면 backend/vif 노드 부재는 가능하다. netback driver 경로도 없으면
/proc/config.gz 또는 해당 빌드 kernel config의 CONFIG_XEN_NETDEV_BACKEND=y|m 및 모듈 포함을
확인한다. 이 단계에서 driver를 임의 load하거나 kernel/DT를 바꾸지 않는다.

기존 Linux VM은 실제 registry에서 주소를 확인한 뒤 `ping -c 3 <IP>`와 기존 SSH 경로로
통신 기준선을 수집한다. A/B의 기존 CLI PV console도 확인한다. 기존 VM은 정지하지 않는다.
각 명령의 rc를 기록하고 실패한 수집 파일을 정상 기준선으로 확정하지 않는다.

각 외부 명령은 G1_LOGS/command-<random>/에 argv.json, result.json, stdout, stderr를 저장한다.
result.json은 returncode 또는 timed_out, 경과시간을 포함한다. stdout/stderr는 전체 원본 bytes이며
실패·timeout에서도 부분 출력을 보존한다. 화면 요약의 4000자 제한은 원본 로그에 적용하지 않는다.
로그 디렉터리는 0700, 파일은 0600으로 생성한다. 기존 Platform의 Xen/ip 조회도 같은 기록 경로를 쓴다.
--log-directory를 생략하면 prepare/launch 등은 시험 디렉터리의 <이름>-logs 형제 경로,
preflight는 현재 경로의 xenova-g1-preflight-logs를 사용한다. cleanup의 cfg 삭제로 로그는 지워지지 않는다.

## 임시 cfg 준비

UUID·MAC은 사용자 입력이다. 기본 VM 신원을 재사용하지 않는다. MAC은 로컬 관리 unicast다.
도구가 실행 VM·등록/보관 자산·DHCP 예약과 충돌을 검사한다. G1에는 IP 입력이 없다.

```sh
G1_FIRMWARE=/absolute/new/g1-firmware
G1_DIR=/absolute/new/xg1-lab
G1_NAME=xg1-store-test
G1_UUID=REPLACE_WITH_NEW_CANONICAL_UUID
G1_MAC=REPLACE_WITH_UNUSED_LOCAL_UNICAST_MAC
python3 -B "$LAB_TOOL" --platform-dir "$PLATFORM_DIR" --log-directory "$G1_LOGS" prepare \
  --firmware "$G1_FIRMWARE" --directory "$G1_DIR" \
  --name "$G1_NAME" --uuid "$G1_UUID" --mac "$G1_MAC"
rc=$?; echo "prepare rc=$rc"; [ "$rc" -eq 0 ] || exit 1
cat "$G1_DIR/test.cfg"
```

기존 16MiB/GICv2 설정에 표준 VIF 하나만 추가한다. disk·IP·packet ring/event reference·
frontend state는 넣지 않는다. lab.json은 시험 신원이며 운영 registry 등록이 아니다.
cfg/firmware 해시가 바뀌면 후속 명령을 거부한다.

## xl 대기 근거와 기동

고정 Xen의 tools/libs/light/libxl_device.c:979는 backend InitWait(2)를
LIBXL_INIT_TIMEOUT=10s 안에 기다린 뒤 hotplug script를 실행한다.
frontend Connected(4)를 요구하는 대기가 아니다. libxl_internal.h의 hotplug 제한은 40s다.
vif-bridge 성공은 hotplug-status=connected를 쓰며 이는 XenBus state=4와 다른 노드다.
xl_vmcontrol.c는 장치 준비 뒤 -p가 아니면 unpause한다.
따라서 기본 timeout 그대로 `xl create -p → console → unpause` 순서로 관측한다.

이 실행 절차는 실환경 수락 전이다. 실제 설치된 script/driver가 고정 입력과 다른지도 확인한다.
create 실패 시 xl 전체 출력·/var/log/xen/xen-hotplug.log·kernel journal을 수집하고 중단한다.
Connected를 써 넣거나 timeout을 늘려 통과시키지 않는다.

```sh
export LIBXL_DEBUG_DUMP_DTB="$G1_DIR/libxl-domu.dtb"
python3 -B "$LAB_TOOL" --platform-dir "$PLATFORM_DIR" --log-directory "$G1_LOGS" launch \
  --directory "$G1_DIR" --execute >g1-launch.log 2>&1
rc=$?; echo "launch rc=$rc"; [ "$rc" -eq 0 ] || exit 1
unset LIBXL_DEBUG_DUMP_DTB
python3 -B "$LAB_TOOL" --platform-dir "$PLATFORM_DIR" --log-directory "$G1_LOGS" inspect \
  --directory "$G1_DIR" >g1-paused.json
rc=$?; echo "inspect rc=$rc"; [ "$rc" -eq 0 ] || exit 1
cat g1-paused.json
```

launch 전 충돌을 다시 검사하며 외부 명령 제한 60s는 libxl 10s/40s를 바꾸지 않는다.
시간 초과·오류 뒤에는 부분 생성 도메인이 남았는지 확인하며 자동 재생성하지 않는다.
dump된 DTB는 dtc가 있는 호스트에서 `dtc -I dtb -O dts`로 변환해 RAM/GIC/IRQ/trigger를
고정 Zephyr DTS와 의미 단위로 비교한다. dry run은 실제 DTB 생성이 아니다.

터미널 A에서 inspect가 반환한 현재 domid로 console에 연결한다.

```sh
xl console -t pv <INSPECT의_DOMID>
```

터미널 B에서 실행한다. resume은 UUID/name과 domid를 다시 대조한다.

```sh
python3 -B "$LAB_TOOL" --platform-dir "$PLATFORM_DIR" --log-directory "$G1_LOGS" resume \
  --directory "$G1_DIR" --execute
rc=$?; echo "resume rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

콘솔은 터미널 기록 기능으로 저장하고 Ctrl+]로 이탈한다.
초기 로그를 놓쳤으면 임시 도메인만 회수한 뒤 새 UUID/시험 디렉터리로 재실행한다.

## 관측과 시험 자극

아래 표는 판정 키워드이며 실제 측정 로그가 아니다.

| 단계 | 근거 |
|---|---|
| Store | store-map-bind rc=0, store-connected |
| VIF | vif-discovery rc=0, 동적 경로·backend_id·MAC |
| 상태 | frontend_state와 backend_state 별도 기록 |
| 생성 부모 | test-parent-permission rc=0. test-parent-missing/-denied는 중단, ACL을 넓히지 않음 |
| 전용 노드 | test-node-candidate가 자기 domid/data/xenova-g1-* 아래, permission rc=0 |
| read/write/directory | 단계별 rc=0 |
| transaction | commit 재읽기, abort 뒤 committed 값 유지 |
| watch | 초기 조회와 실제 알림·변경값 재조회 구분 |
| timeout | watch-timeout은 watch 수락 실패 |
| 정리 | unwatch/test-node-cleanup/store-cleanup rc=0 |

guest의 watch-ready 뒤 출력된 control 경로를 그대로 사용한다.

```sh
G1_CONTROL=/local/domain/ACTUAL_DOMID/data/xenova-g1-ACTUAL_HEX/control
python3 -B "$LAB_TOOL" --platform-dir "$PLATFORM_DIR" --log-directory "$G1_LOGS" signal \
  --directory "$G1_DIR" --path "$G1_CONTROL" --value dom0-1 --execute
rc=$?; echo "signal rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

signal의 written 출력은 dom0 쓰기 요청의 성공이다. guest가 알림을 처리한 직후
전용 노드를 지울 수 있으므로 쓰기 뒤 그 노드를 다시 읽어 성공 여부를 판정하지 않는다.
guest watch-reread에서 실제 알림 수와 value=dom0-1을 확인한다.
변경하지 않는 별도 실행에서는 60초 timeout이 나야 한다. 초기 이벤트만으로 변경 성공을
판정하지 않는다. 등록과 초기 조회 사이 변화는 초기 재읽기로 포착한다.

backend watch도 알림 뒤 frontend/backend를 다시 읽는다. G1 backend는 InitWait(2)에
머물 수 있다. 실제 변화가 없었다면 등록·초기 읽기와 변화 이벤트 관측을 구분해 기록한다.
상태 변화를 만들려고 운영 VIF/frontend/backend state를 직접 변경하지 않는다.
로컬 peer 시험의 Closing 알림·재읽기 통과는 실환경 변화 관측을 대신하지 않는다.

## 오류 수집·회수

```sh
python3 -B "$LAB_TOOL" --platform-dir "$PLATFORM_DIR" --log-directory "$G1_LOGS" inspect \
  --directory "$G1_DIR" >g1-final-inspect.json
rc=$?; echo "final inspect rc=$rc"
journalctl -b -k --no-pager >g1-kernel.log
rc=$?; echo "kernel collection rc=$rc"
journalctl -b --no-pager -u xenstored >g1-xenstored.log
rc=$?; echo "store collection rc=$rc"
```

실패 명령은 G1_LOGS의 원본 stdout/stderr와 result.json을 함께 수집한다. timeout 뒤에도 자동 destroy/재생성하지 않는다.
수집 실패 파일은 정상 비교 대상에서 제외한다. 필요하면 xen-hotplug.log도 별도로 복사한다.
VIF 없음·backend 미완성/timeout·권한 거절·Store/매핑/event 실패를 같은 원인으로 묶지 않는다.

시험 도메인은 협조형 종료가 없으므로 회수는 명시적 destroy다.

```sh
python3 -B "$LAB_TOOL" --platform-dir "$PLATFORM_DIR" --log-directory "$G1_LOGS" cleanup \
  --directory "$G1_DIR" --confirm-uuid "$G1_UUID" --execute
rc=$?; echo "cleanup rc=$rc"; [ "$rc" -eq 0 ] || exit 1
xl list
```

도구는 현재 UUID/name/domid를 재검증하고 지정 도메인만 제거한 뒤 두 성공한 목록으로
부재를 확인한다. 외부 xl과 완전히 원자적인 보호는 아니므로 동시에 대상에 명령하지 않는다.
이미 도메인이 없으면 보수적으로 중단하며 cfg·로그·firmware는 보존한다.
생성 실패 때 남은 도메인도 신원이 일치할 때만 이 cleanup 경로를 사용한다.

첫 cleanup에 --remove-config를 추가하면 lab.json/test.cfg 두 파일만 있는 디렉터리에 한해
두 파일과 빈 폴더만 지운다. DTB·로그가 있으면 보존한다. 원본 firmware는 지우지 않는다.
기본 절차처럼 DTB를 수집했다면 cfg/DTB/로그를 함께 증거로 보관한다.
시험 후 Linux 통신·A/B console 기준선을 다시 확인한다.

결과표에는 local / target build / real Xen 실행 / Store / VIF / backend 변화 /
transaction / watch / timeout / cleanup / 기존 VM 기준선을 별도로 기록한다.
bridge·DHCP 재시작, 운영 도메인 중단, registry 수정, SD·이미지 교체는 수행하지 않는다.

## G1 전용 event IRQ와 산출물 갱신

`xenstore-test-app/app.overlay`는 event PPI31을 Zephyr `IRQ_TYPE_LEVEL=2`로 지정한다.
실제 Xen standard DT의 level-low flags `0xf08`과 숫자 encoding이 다르다.
고정 xenvm/Starter A/B의 공통 board DTS와 source lock은 변경하지 않는다.
빌더는 생성 DTS의 PPI·trigger·priority를 확인하고 overlay를 source manifest에 포함한다.
펌웨어·ELF/map/config/DTS·build manifest와 최신 lab/runtime 인계·SHA는 같은 입력으로 갱신한다.
overlay나 빌더가 변경됐으면 기존 펌웨어의 manifest만 고쳐 재사용하지 않는다.

별도 level 후보의 실보드 Store/watch·자기 VIF backend 변화·회수는 확인했다.
정밀 masked-pending IRQ 경쟁의 실물 유발이나 G2 Ethernet/IP 수락을 뜻하지 않는다.
새 정식 입력의 펌웨어 재빌드·실보드 수락은 별도 단계다.
