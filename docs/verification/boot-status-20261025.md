# 부팅 진행·지연·실패 표시 검증 — 2026-10-25

현재 상태: **사용자와 합의한 기존 로그 기반 진행·지연·오류 표시 범위로 마감했다.** 물리 HDMI 미검증·getty 이전 직접 renderer 미구현은 별도 한계로 남긴다. 아래 마감 범위와 마지막 실보드 절을 따른다.


## 사용자와 합의한 마감 범위 — 2026-10-25

사용자는 기존 로그와 플랫폼 기록을 활용해 진행·지연·오류 상태를 찾기 쉽고 간결하게
보여주는 현재 결과로 부팅 표시 작업을 마감하기로 했다. 실제 재부팅·UART 수신·상세 조회와
보호 상태 확인 결과를 수락 근거로 연결한다. 부팅 때 반복되는 Xen vGICD 진단이 로그인 줄에
섞이는 현상은 사용자가 허용했으며 추가 수정의 차단 조건으로 두지 않는다.

물리 HDMI는 미검증이고 getty 이전 직접 renderer는 미구현이다. 이 사실과 과거의 미완료
판정은 유지하되, 추가 HDMI 구현을 이번 마감의 필수 조건에서 제외한다. 실제 HDMI 출력이나
새 이미지 수락까지 완료됐다고 확대하지 않는다. 이 마감에서는 코드·서비스·보드 상태를
추가 변경하지 않았고 commit/push도 수행하지 않았다.

## 구현·검증 상태

Zephyr 수명주기 정리와 별도 변경 묶음이다. **로그인 전 agetty 안내문·읽기 전용 상세 조회·
부가 진행 기록·부분 패키징을 구현하고 로컬 검증했다.** 실제 HDMI 전원 부팅·로그인
수락은 수행하지 않았다. 또한 고정 getty가 sysinit 뒤에 시작하므로 **getty 이전의 저장소
준비 지연을 HDMI에 실시간 표시하는 기능은 현재 지원하지 않는다.** 그 시점의 기본
hvc0 오류 경로를 유지한다. 이 제약을 물리 화면 미검증과 구별한다.

공통 명령과 현재 사용법은 [CLI 명세](../platform/cli-manager-spec.md#cmd-boot-status),
출처·완료 의미·콘솔 소유·비활성화는
[부팅 구조](../platform/management-boot-boundaries.md#부팅-진행지연실패-표시)에 있다.

## 확인한 경로와 결정

기준 HEAD는 `230e1326d32acbc51b0113e4556f149d2be93632`, branch는
`feat/platform/runtime-lifecycle-diagnostics`다. 확인한 origin/platform
`44e6ee48a6ffa130cb9bc26aa25651104563e850`과 tree 차이는 없었다.

| 확인한 사실 | 선택 |
|---|---|
| dom0는 `console=hvc0`, Xen은 `console=dtuart dtuart=/soc/serial@7e215040` | 콘솔 인자 순서·대상과 UART 로그인 경로 보존 |
| 정상 부팅은 `loglevel=3`, PID1 show_status=error, journal-or-kmsg | Linux loglevel만 4로 바꿔 KERN_ERR(3)까지 표시. debug 부팅은 보존 |
| 고정 이미지 getty는 agetty, `--noclear --issue-file=/etc/issue:/etc/issue.d:/run/issue.d:/usr/lib/issue.d` | 기존 agetty의 입력 전 issue/reload 사용. VT 직접 점유·전환·새 인증 경로 없음 |
| 고정 systemd 259.5가 util-linux-agetty를 RDEPENDS로 이미 포함 | 런타임 의존성 추가 없음. 실제 ARM agetty 2.41.5의 --reload/--issue-file 옵션 확인 |
| 최초 등록·CPU 준비·autostart는 실제 작업 지점의 정보가 있음 | 코드 지점에서 비필수 보조 기록 게시. 원 journal·신원·필수 저장·결과 stdout은 보존 |
| 관리 서비스 active는 기능·앱 준비의 증거가 아님 | unit 상태, VM 실행 검증, OS 미관측, 앱 관측 미연결을 별도로 표시 |

표시기는 5초 polling, 단일 systemctl 고정 속성 조회 2초/128KiB, 최대 600초다.
상태 변화 중심으로 갱신하고 지연 경과시간만 바뀌면 30초 단위로 갱신한다.
VM별 선택 전 분모는 null이며, 정체·이전 invocation·읽기 실패를 성공으로 만들지 않는다.
현재 조회와 과거 실패는 별도이며 마지막 16개 실패를 표시기 파일에 보존한다.

신규 상시 daemon·Manager action·readiness 소비자·MQTT·그래픽/폰트·PAM 의존성은 없다.
표시 상태는 admission·자동 복구 판정에 사용하지 않는다. `DefaultDependencies=no`,
remount 뒤 sysinit에서 시작하며 관측 대상 mount/등록/CPU/VM을 Requires하지 않는다.
core unit이나 getty도 표시기를 Requires/PartOf/BindsTo로 연결하지 않는다.

## 변경 파일

- `xenova_boot_status.py`, `xenova-boot-status`, `xenova-boot-status.service`: 조회·모형·agetty 안내문 생산.
- `xenova_boot_progress.py`, `xenova_runtime.py`: best effort 원자 게시와 선택 모듈 고장 격리.
- `xenova_vm.py`, `xenova_first_boot.py`, `xenova_cpu.py`: 실제 단계·선택·필수 결과 저장 뒤의 부가 사실.
- `xenova-platform_1.0.bb`, `boot.cmd.xen.in`: 설치·sysinit enablement와 KERN_ERR 콘솔 필터.
- `assemble.py`, first-install fixture/시험, G1 인계 packager/시험: 새 파일·enablement·지연 import 폐쇄성 검사.
- `test_xenova_boot_status.py`, `probe_xenova_boot_status.py`: 모델·실제 Platform·실제 systemd/PTY 격리 시험.
- 목적별 CLI·부팅·빌드 문서: 사용법·필드·시간·소유·지원 범위 통합.

## 실행 검증

| 검증 | 결과·한계 |
|---|---|
| 전체 플랫폼 unittest | **866개, 실패 0, root UID 1건 skip**, full-regression-r5.log, 280.068초(실행 receipt). skipped 사례는 별도 root 실행에서 PASS |
| 최종 집중 시험 | **20개 PASS**. 전체 회귀 뒤 추가한 불완전 systemctl 응답 guard를 포함해 집중 재시험. 정상/최초·기존 등록, 지연 후 성공, storage/Xen/CPU 실패·선행 보류, 부분 성공·제외·미확정, stale boot/invocation/batch, 부분 파일·symlink, 모듈 부재·고장, 선택 미확정·정체, 좁은 ASCII·제어문자, 실패 이력, unit/recipe 관계 |
| 실제 systemd 격리 | WSL systemd 255.4, 소유 transient unit. 작업 명시 실패→실제 Result/exit 관측, 표시 실패·중지·재시작에도 독립 로그인용 dummy process 유지, 새 invocation 확인. 제품 서비스·VM은 미제어 |
| 실제 agetty PTY | 2.39.3. private mount namespace의 /run·/var/log, 소유 PTY. 입력 전 reload, 사용자 이름 첫 문자 후 비간섭, 테스트용 암호 입력 경계 비간섭, 세션 경계 도달 PASS. 실제 계정 인증·물리 키보드·HDMI 검증은 아님 |
| 고정 target unit loader | 실제 이미지의 unit fixture+최종 후보, **systemd 259.5 verify rc=0**, sysinit enable 링크·getty/hvc0·VM/admission 그래프. 명령 스텁은 실행하지 않음 |
| 부분 빌드·QA | `bitbake xenova-platform xen-rpi-u-boot-scr` PASS. 전체 image task 미요청 |
| 패키지 내용 | 설치 파일·packages-split·pkgdata FILES_INFO 일치, CLI 0755·LF shebang·설치 실행 파일 --help 직접 실행 PASS |
| 실제 ARM Python | 기존 고정 rootfs Python에서 두 새 모듈의 표준 라이브러리 import와 memfd_create 확인 PASS. ARM agetty 옵션 확인 PASS |
| 생성 boot.scr | console·Xen args·PIO 경로 보존, loglevel=4/show_status=error 포함 확인. 실보드 교체·재부팅은 미수행 |

실제 systemd 격리의 a/b 시도에서는 transient unit이 reset-failed 후 회수되는 동작과
agetty controlling PTY 설정 누락을 발견했다. 시험 harness를 실제 소유 모델에 맞춰 고친
c 시도가 통과했다. 제품 실패를 숨기거나 실제 서비스를 재시작한 수정이 아니다.
전체 회귀에서 새 G1 모듈 누락, 이어서 고정 module-count 기대값을 발견해 명시적으로
갱신했다. 마지막 설치 실행 파일 검사에서 Windows CRLF shebang을 발견해 LF로 고정하고
집중 시험·직접 실행·부분 패키지 QA를 재실행했다. 좁은 화면에서 지연의 마지막 변경 시각과 오류 코드가 잘리지 않도록 별도 줄로 보존하고 20개 집중 시험을 다시 통과했다. 실패 로그는 보존한다. 마지막 부분 재패키징은 root 검증이 만든 host Python
캐시 두 파일/디렉터리의 소유권 때문에 한 번 실패했다. 해당 세 경로만 원래 빌드
계정 소유로 되돌리고, bytecode 생성 없이 최종 패키징을 재실행해 통과했다.
소스·기존 산출물·제품 권한은 바꾸지 않았다. 빌드 경고 1건은 WSL 호스트의
CONFIG_TASK_IO_ACCOUNTING 미설정이며 package QA 오류는 없었다.

### 부분 산출물

빌드 경로:
`/home/ssafy/yocto-workspace/build-rpi4-boot-status-20261025`.

| 파일 | SHA-256 | 크기 |
|---|---|---|
| xenova-platform-1.0-r0.cortexa72.rpm | `45320bb8aa14c2dd9ad468ab89ad6363cf1bddae6192819843785276cfb73685` | 146,803 bytes |
| boot.scr | `0cbae903113397e38673cc0b2a335f0887ca4c38613c2fe6f64475042bdda19a` | 3,113 bytes |

`BOOT_PACKAGE_AUDIT.json`에 설치 파일별 해시·pkgdata 의존성·고정 getty 원문·loader 결과가
있다. 기존 캐시·이전 산출물은 보존했다. 부분 RPM의 크기는 전체 패키지 크기이며
최종 이미지 증가량이나 실제 상주 메모리를 측정한 값이 아니다. WIC·SD 수락은 별도다.

## 화면 예시와 실보드 인계

PC evidence의 `SCREEN_NORMAL.txt`, `SCREEN_FAILURE.txt`는 **모형 예시이며 HDMI 캡처가 아니다.**
정상 화면은 완료된 unit 사실 요약, 등록 모드·CPU 모드, 실제 선택 분모와 실행 검증 수,
OS·앱 관측 범위를 간결하게 보여준다. 실패 화면은 대상 이름·짧은 UUID·오류 코드,
전체 UUID·unit·journalctl 명령과 과거 실패를 표시한다.

실보드 전에는 기존 kmscon/getty/키오스크 잔존 상태와 tty 소유, 실제 cmdline·kernel·VT,
SSH/UART 경로를 읽기 전용으로 재확인해야 한다. 로컬 마감 당시에는 보드를 조사하지 않았다. 승인 후 현재 보드 결과는 마지막 실보드 절에 있다.
별도 승인 검토안은 PC evidence의 `BOARD_EXECUTION_REVIEW.md`다. 표시기를 위해
로그인 서비스를 바꾸거나, 기본 VM 실패를 주입하거나, boot 파일을 교체하지 않았다.

기존 설치 적용은 정확한 설치본을 백업·대조한 뒤 표시 모듈·unit·생산자 묶음을 같은
버전으로 설치한다. 기존 부팅 unit·getty·인증 설정의 변경 없이 표시 서비스만 시작한다.
`boot.scr`의 loglevel 변경과 host 재부팅은 별도 승인·UART 확보 뒤 마지막에 검증한다.
원복은 표시 서비스 중지/disable→소유 issue 파일 제거→원 파일 해시 대조 복원이다.
getty/PAM/SSH 원복을 필요로 하는 구성 변경은 넣지 않았다.

**로컬 마감 당시 잔여:** 물리 HDMI 정상·지연·부분 실패 화면, 로그아웃/재로그인·그래픽 인계·UART 복구,
실제 부팅 후 자동 안내, getty 이전의 실시간 HDMI 표시. 마지막 항목은 단순 보드 재시험으로
닫을 수 없고 콘솔 소유를 지키는 별도 설계 보완이 필요하다. 현재 구현의 수락으로 확대하지 않는다.

## 승인 후 현재 보드 적용·출력 경로 확인

승인 범위1+2만 실행했다. 새 표시 네 파일은 고정 부분 패키지와 같으며,
core 네 파일은 설치본과 같은 공개 Git blob을 해시로 확인해 표시 전용 추가만 적용했다.
설치본에 없는 xenova_guest_process 의존성을 도입하거나 최신 관리 기능 전체를 설치하지 않았다.
설치본 의존성 조합의20개 표시/실제 Platform 생산자 시험을 통과했고, 원본 백업과8개
적용 파일의 해시를 대조했다. 기존 서비스는 재시작하지 않고 새 표시 unit만 enable/start했다.

현재 host boot ID는 `2a879e42-5a8d-4196-b77c-a30b229486c9`로 전후 동일하다.
표시 서비스는 이미 부팅한 host의 단조96,476.494초에 시작해 요약 게시 뒤 exit0으로 종료했다.
stdout=journal, TTYPath 없음이며 issue/agetty 경로로 출력한다. tty1 getty는10.771초에
시작했고 PID469, hvc0 PID471과 invocation은 전후 같았다. 현재 수동 시작·enable 확인을
다음 부팅의 getty 이전 실시간 표시 성공으로 해석하지 않는다.
과거 invocation의 세부 진행을 소급 생성하지 않아 대상 VM 수/결과는 미확인으로 표시한다.

현재 `/proc/consoles`에는 hvc0만 있고, 활성 VT는 tty1이지만 dummy vtconsole뿐이다.
`/dev/fb0`·`/dev/dri`·HDMI DRM connector가 없으며, vc4/DRM은 builtin이다.
live DT의 framebuffer·HDMI·HVS·GPU/V3D는 disabled다. framebuffer의 실제
reg/width/height/stride/format 정보도 없다. 현재 부팅의 Linux 화면 제공 조건은 확인되지 않았다.
HDMI 연결 상태와 물리 화면 직접 확인은 답변 대기이며, 물리 출력 PASS를 선언하지 않는다.

getty 이전 표시에는 조기 renderer/getty 인계 보완이 필요하고, 현재 화면 장치 부재도 별도다.
현재 boot.scr/cmdline은 이미 loglevel4·console=hvc0여서 예정된 필터 변경만으로는 해결되지 않는다.
boot.scr 변경이 사용자 공간 요약 출력의 필수 조건은 아니다. 화면 정보·Xen 전달 근거 없이
framebuffer 노드만 켜거나 console 순서를 바꾸지 않았다. 부팅화면 요구는 **미완료**다.

적용 전후 boot.scr SHA-256은
`c89cdc7f08510cc0a3dd64531e6fb301e41efd819b742fc4c4253d9ed1255e14`로 같았다.
기본 VM·실제 CPU affinity/정책·관리 Wi-Fi·getty/hvc0·PAM/SSH는 보존했다.
상세 적용 manifest·호환 diff는 PC 증거의 `LIVE_PATCHES.md`,
`board-live/DISPLAY_COMPAT_EXACT.patch`에 있다. 원복 도구는 실제 보드에서 --check로
후보8개·원본 백업4개 해시 일치를 확인했으며 실제 원복은 실행하지 않았다.

표시 제거·기존 파일 복원은 dom0에서
`python3 -B /root/xenova-lifecycle-20261025-01/display-rollback.py`로 수행한다.
`--check`를 붙이면 변경 없이 원복 조건만 검증한다. 새 표시 서비스만 disable/stop하고
기존 core 원본·mode/소유권을 복원하며, 후보 해시가 바뀌었으면 거절한다.
원복 백업은 시험 정리에서 제외했다. boot.scr 적용·host 재부팅·물리 부팅 수락은 보류다.

## UART 정리와 승인 후 정상 재부팅 검증

이 절이 최신 상태다. 앞의 부팅 미수행·boot.scr 보류는 최초 1+2 적용 시점의 기록이다.
이후 사용자가 정상 host 재부팅과 정확한 출력 전용 boot.scr 변경을 각각 승인했다.
정상 재부팅 네 번을 서로 다른 후보 검증으로 실행했으며, 응답 유실을 이유로 같은 요청을 반복하지 않았다.
마지막 host boot ID는 `c0f76b65-c4b4-47cf-baca-430cba9dd9f5`다.

### 실제 화면 자료와 남은 출력

COM3, 115200 8N1에서 입력 없이 수신한 원자료를 보존했다. 최종 수신본은 13,411 bytes다.
`uart-review/xenova-uart-boot-actual.png`는 이 수신 문자열을 이미지로 그린 자료다.
PuTTY 앱 실행이 도구 정책으로 차단되어 GUI 화면을 캡처하지 않았으며, HDMI 캡처도 아니다.
최종 이미지의 부팅 중 t=20초와 완료 t=41초는 같은 host 실행에서 가져왔다.

- 기본 안내는 완료 서비스를 한 줄로 묶고 현재 선택된 진행 대상만 표시한다. 비교한 안내문은
  이전 t≈20초 26줄에서 최종 12줄, 완료 시 9줄이다. 선택 미확정·실패·지연·미확정 결과를 성공으로 바꾸지 않았다.
- 기본 문구는 `Xenova startup`, `[START]`, `prepared for this boot`로 정리했다.
  원 단계·invocation 근거·전체 UUID·상세 조회 명령은 `--details`와 JSON에 유지한다.
- 정상 U-Boot 경로의 `/chosen` 전체 덤프를 제거했다. 진단 경로는 Xen·dom0 bootargs 두 속성만 출력한다.
  원 firmware 난수 seed가 있던 예전 raw 수신본은 공개하지 않고 public transcript에서 해당 두 줄을 가렸다.
  최종 실제 수신본에는 그 덤프가 없다. Xen/PIO/콘솔/장치/메모리/커널 인자는 바꾸지 않았다.
- **Xen vGICD 진단 3건이 로그인 줄에 직접 섞이는 현상은 남는다.** Linux printk·journald
  필터를 거치지 않는 출력이다. 가짜로 지우거나 Xen 로깅/GIC 코드를 변경하지 않았다.
- 입력 전 agetty 안내 갱신은 여러 번 보인다. 기존 agetty의 첫 입력 뒤 갱신 중지 경계는
  앞선 실제 PTY 시험을 연결한다. 이번 COM3 수신은 입력 0바이트이므로 실제 암호 인증 시험은 아니다.

### 재부팅 후 확인

최종 재접속에서 host ID 변경을 확인하고 기존 host key를 엄격히 검사했다.
Wi-Fi는 수동 명령 없이 `COMPLETED`, `192.168.123.116/24`로 연결됐다.
등록 record 10개와 VM UUID·자산 설정·generation·사용자 의도 revision은 전후 같았다.
기본 Debian/A/B만 새 execution으로 자동 시작했고 나머지 VM은 정지 상태를 유지했다.
실제 dom0 vCPU 4개와 Debian은 `{0,3}`, A는 `{1}`, B는 `{2}`이며
Credit2/Pool-0·CPU 정책 revision 1을 유지했다. 두 전용 VM의 실제 전용성 조회도 통과했다.

표시 서비스는 최종 부팅 약 3.896초에 시작했고 tty1/hvc0 getty는 약 5.790초에 시작했다.
표시기는 journal과 issue 파일을 사용하며 VT를 직접 열지 않는다. 마지막 안내는 실제
autostart의 3/3 실행 검증을 반영했다. OS 준비·앱 준비는 계속 미관측·미연결이다.
관측 종료 후 표시 서비스의 inactive/dead, Result=success는 정상 종료 상태다.
최종 상태·기본값·원복 검사 근거는 `uart-review/FINAL_UART_BOARD_AUDIT.json`,
`board-live/operator-post-baseline-10.stdout`, `operator-final-status-10.stdout`에 있다.

사용자는 HDMI 장치가 없다고 확인했다. 현재 framebuffer/DRM 장치 부재와 별개로,
**getty 이전 직접 renderer도 구현되지 않았다.** 따라서 물리 HDMI·getty 이전 진행 표시 요구는
미완료로 유지한다. 이번 출력 전용 boot.scr 변경이 이를 해결했다고 해석하지 않는다.

### 소스·부분 산출물과 원복

최종 표시 집중 시험 24개 PASS, bringup 회귀 검사 PASS다. 아래 부분 빌드를 통과했다.
전체 866개 회귀·native_sim 38개와 ARM 후보 검증은 앞선 원자료를 연결하며,
이번 24개 재시험을 전체 회귀 재실행으로 표현하지 않는다.

```bash
python3 -B -m unittest discover -s platform/tests -p test_xenova_boot_status.py -v
python3 -B platform/tests/check_xenova_bringup_regression.py
bitbake xenova-platform xen-rpi-u-boot-scr
```

| 최종 부분 산출물 | bytes | SHA-256 |
|---|---:|---|
| xenova-platform-1.0-r0.cortexa72.rpm | 147211 | `d6bba55c8b62241f8ff183f638c938c75dbba213dc0050bcde8d65e6c8849dc7` |
| boot.scr | 3339 | `c7b5b42cf6647c9915919e4b095b062ae0a155dabeac6c2379f953b154dfb19a` |
| xenova_boot_status.py | 21193 | `f7db23600f0ffbb7887b4a18dc5cf59432fa6139825fd25986773b52a8a56f6b` |

부분 빌드 실행 receipt는 `uart-review/package-polish-j.json`,
내용 대조는 `uart-review/PACKAGE_OPERATOR_AUDIT.json`에 있다. 전체 이미지/WIC/SD 작업은 아니다.

dom0에서 원복 조건만 읽는 명령:

```sh
python3 -B /root/xenova-lifecycle-20261025-01/display-rollback.py --check
python3 -B /root/xenova-lifecycle-20261025-01/boot-output-rollback.py --check
```

두 최종 실보드 검사가 통과했다. 표시 runtime은 최초 원본 4개와 후보 8개 해시를 대조한다.
표시의 실제 원복은 첫 줄의 `--check`를 생략한다. boot의 실제 원복은 둘째 줄의 `--check`를
`--restore`로 바꾼다. boot 원복 자체는 재부팅하지 않는다. 원 boot는 root 전용 백업
`display-backup/boot.scr.before-console-polish`(3017 bytes,
`c89cdc7f08510cc0a3dd64531e6fb301e41efd819b742fc4c4253d9ed1255e14`)에 보존한다.
실제 원복은 실행하지 않았다. 기존 getty·PAM·SSH·Wi-Fi·CPU 정책을 바꾸지 않는다.
