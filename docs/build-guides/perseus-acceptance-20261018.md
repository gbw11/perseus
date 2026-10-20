# Perseus 실보드 수락 보완과 후속 시험 — 2026-10-18

이번 단계는 소스·회귀 검사·Starter 펌웨어 빌드까지다. 아래 보드/기존 VM 명령은 사용자의 **후속 배포·시험 절차**이며 이번에 실행하지 않았다. dom0/Xen 재빌드, seed/최종 이미지 조립, SD 접근, 기존 VM 펌웨어 배포, commit/push도 실행하지 않았다.

## 확인한 원인과 수정 경계

| 항목 | 확인한 사실과 소스 수정 | 아직 필요한 확인 |
| --- | --- | --- |
| F-01 | 고정 libxl의 domain qualifier는 숫자 domid 또는 이름만 처리하며 UUID 분기는 미구현이다. 영구 UUID/name은 그대로 대조하고 제어 직전 재조회한 domid를 console/shutdown/reboot/destroy에 전달한다. | 실제 Perseus CLI 콘솔·종료·재부팅 |
| F-02 | 실제 Ubuntu 255.4/openssh 9.6p1 템플릿에는 sockets.target.wants/ssh.socket과 ssh.service.requires/ssh.socket이 함께 있었다. 일반 identity 서비스를 socket 앞에 둔 기존 구성의 순환을 실제 로더로 재현했다. 새 준비/명시적 갱신은 두 링크·socket-only drop-in·socket gate를 정리하고 generator를 비활성화해 identity 성공 뒤 ssh.service를 시작한다. | 수정된 **게스트 내부** 유닛의 재부팅 후 TCP 22 수신 |
| F-03 | 고정 Xen 1c72306b1f의 리셋 순서는 PSCI system_reset → platform_reset이다. ALL64_PLAT에 Pi4 드라이버가 포함되고 기존 WIC Xen에도 Pi4/구형 PM 문자열이 있다. WIC 보드 DTB는 bcm2711-pm/pm-wdt지만 Xen은 bcm2835-pm만 찾는다. BCM2711 노드 탐색과 dom0 blacklist만 추가한다. 주소·watchdog 레지스터·비밀번호·대기 시간은 유지한다. | 실제 Working FDT의 노드·PSCI·선택 플랫폼 및 소프트웨어 재부팅 성공 |
| P-01 | 공통 shutdown-all과 보드 CLI가 같은 VM별 정책으로 사전 검사·제어·도메인 제거 확인·결과 기록을 수행한다. Linux 실패는 자동 destroy로 바꾸지 않는다. | Linux 정상 종료 + 승인 Zephyr 강제 제거 + 저장소 해제 |
| P-02 | Starter 1.0.0은 안내 1회 후 K_FOREVER 대기다. 새 firmware.json에 앱/버전/소스 해시를 기록하고 기존 진단 template.json/source.lock.json은 보존한다. | 실제 교체·콘솔·재부팅 후 자동 시작 |

F-02의 socket 링크와 00-socket.conf 정리는 [Ubuntu 공식 안내](https://discourse.ubuntu.com/t/sshd-now-uses-socket-based-activation-ubuntu-22-10-and-later/30189)와 실제 추출 파일을 함께 확인했다. Debian 257.13/openssh 10.0p1의 기존 keygen 유닛과 비밀번호·machine-id·키도 보존한다. 모든 SSH 리스너가 identity gate를 통과하도록 Debian의 별도 systemd SSH generator도 새 준비/명시적 갱신에서 비활성화한다.

**Xen 플랫폼 리셋의 실제 입력은 dom0 DTB나 Zephyr domU DTB가 아니다.** 고정 U-Boot는 유효한 펌웨어 FDT를 fdtcontroladdr로 사용하고 boot.cmd는 이를 fdt_addr_r로 복사해 Xen에 넘긴다. WIC DTB의 확정된 compatible 지원 누락은 수정했지만, 사용자 보드에서 실패한 FDT 자체는 수집하지 않았다. 해당 보드의 실패 원인 확정과 리셋 성공을 이 소스 검사로 대신하지 않는다.

## 영구 상태와 CLI

Linux schema 1 및 기존 Zephyr schema 2를 그대로 읽는다. UUID/name/MAC/IP/디스크를 재발급하지 않는다. domid는 실행 중 식별자로만 사용하며 영구 레코드에 저장하지 않는다. 외부 xl 프로세스와 재조회→호출 사이의 경쟁까지 원자적으로 막는 기능은 아니다. 콘솔 연결 중에는 플랫폼 전체 잠금을 보유하지 않는다.

```text
perseus-vm board-shutdown-set <id> graceful       # Linux
perseus-vm board-shutdown-set <id> refuse         # Zephyr: 보드 종료 거부
perseus-vm board-shutdown-set <id> force-allowed  # Zephyr: 운영자의 명시적 승인
```

정책이 없는 기존 Linux는 graceful, 기존 Zephyr는 refuse다. 새 Starter 기본 seed만 autostart=true와 board_shutdown_policy=force-allowed를 갖는다. autostart on은 종료 승인이 아니다. 기존 첫 설치 journal/spec 및 사용자가 저장한 정책을 defaults.json으로 덮어쓰지 않는다. 명시적으로 이전 synchronization 템플릿을 선택한 진단 seed는 기존 off/refuse를 유지한다.

개별 Zephyr stop/reboot에는 위 보드 정책과 별개로 여전히 --force가 필요하다. 일반 보드 종료는 Linux를 정상 종료하고 승인된 Zephyr만 강제 제거한다. 전역 --force는 **Linux까지 즉시 destroy하는 별도 요청**이며 일반 운영 절차에서 사용하지 않는다. 미등록 도메인이나 미승인 Zephyr가 있으면 어느 VM에도 종료 요청을 보내기 전에 거부한다.

결과는 /run/perseus/last-stop.json과 board-shutdown-result.json에 VM별로 남는다. 요청 수락, 도메인 제거, already_stopped, 실패/시간 초과를 구분한다. shutdown/crashed/dying인 도메인이 아직 목록에 남아 있으면 정리 완료로 처리하지 않는다. autostart-results.json은 시작 시점의 기록이며 현재 상태 모니터가 아니다. app_readiness=unknown도 별도로 유지한다.

## 기존 Ubuntu/Debian의 명시적 SSH 설정 갱신

**dom0 재빌드만으로 기존 VM 내부는 고쳐지지 않는다.** 새 seed/인스턴스는 기존 불변 템플릿 사본에 prepare_deployment_seed/prepare_instance가 서비스 정책을 적용한다. 기존 사용 중인 VM은 아래 절차로 설정만 갱신한다. prepare_template/prepare_instance/prepare_deployment_seed를 기존 VM에 다시 실행하지 않는다.

세 파일을 새 게스트 임시 디렉터리(예: /tmp/perseus-ssh-update)에 전달한다. 수정된 dom0에는 /usr/lib/perseus/update_guest_ssh.py, prepare_rootfs.py, guest_identity.py로 패키징된다. PC 소스의 scripts/update-perseus-guest-ssh.py도 같은 도구다. 파일 전송에는 이미 복구된 SSH 또는 사용자가 선택한 수단을 사용한다. 실제 설정 갱신은 **PV 콘솔에서** 수행한다.

```bash
cd /tmp/perseus-ssh-update
python3 -B update_guest_ssh.py
rc=$?; echo "plan rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

위 출력이 다른 호스트나 잘못된 경로를 가리키면 중단한다. root에서 다음 짧은 블록들을 순서대로 실행하며 실패하면 다음 블록으로 진행하지 않는다.

```bash
systemctl stop ssh.socket
rc=$?; echo "stop socket rc=$rc"; [ "$rc" -eq 0 ] || exit 1
python3 -B update_guest_ssh.py --execute
rc=$?; echo "update rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

도구는 /var/lib/perseus/ssh-policy-backup-<uuid>에 기존 유닛 설정을 보관한다. 비밀번호·machine-id·instance-id·host key와 완료 marker는 변경하지 않는다. 알려지지 않은 socket 필수 의존성이 있으면 자동으로 제거하지 않고 검토를 요구한다. 실패 시 임의 재초기화하지 말고 출력과 backup을 보존한다.

```bash
systemctl daemon-reload
systemctl start perseus-guest-identity.service
rc=$?; echo "identity rc=$rc"; [ "$rc" -eq 0 ] || exit 1
systemctl restart ssh.service
rc=$?; echo "ssh rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

```bash
systemctl --no-pager --full status perseus-guest-identity.service ssh.service
systemctl show ssh.service -p Requires -p After --no-pager
ss -lntp
```

Requires/After에 identity gate가 있어야 하며 필수 ssh.socket이 남으면 중단한다. 기본 배포는 TCP 22 리스너를 확인한다. 사용자 지정 sshd_config의 포트/주소는 이 도구가 덮어쓰지 않는다. 재부팅 수락 시험에서는 ssh.service를 수동 시작해 성공으로 처리하지 않는다.

## 펌웨어·정책의 명시적 배포

새 Starter 번들은 일반 firmware-replace로 배포할 수 있으며 기존 UUID·자동 시작·종료 정책·이전 revision은 유지된다. 현재 앱이 강제 종료를 허용해도 새 팀원 앱이 안전하다는 뜻은 아니다. 임의 팀원 앱으로 교체할 때는 먼저 보드 정책을 refuse로 두고 새 앱의 정책을 검토한다.

Starter로 교체하기로 결정한 경우, 번들을 dom0의 **새 경로**에 전달한 뒤 실제 firmware.json 해시를 사용한다. 실행 중 자산이나 원본 템플릿을 덮어쓰지 않는다.

```bash
perseus-vm stop zephyr-dev --force
rc=$?; echo "stop rc=$rc"; [ "$rc" -eq 0 ] || exit 1
perseus-vm firmware-replace zephyr-dev --bundle /absolute/new-starter --sha256 ACTUAL_HASH
rc=$?; echo "plan rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

```bash
perseus-vm firmware-replace zephyr-dev --bundle /absolute/new-starter --sha256 ACTUAL_HASH --execute
rc=$?; echo "replace rc=$rc"; [ "$rc" -eq 0 ] || exit 1
perseus-vm board-shutdown-set zephyr-dev force-allowed
rc=$?; echo "policy rc=$rc"; [ "$rc" -eq 0 ] || exit 1
perseus-vm autostart-set zephyr-dev on
rc=$?; echo "autostart rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

```bash
perseus-vm start zephyr-dev
rc=$?; echo "start rc=$rc"; [ "$rc" -eq 0 ] || exit 1
perseus-vm console zephyr-dev
```

콘솔 이탈은 xl의 Ctrl+]를 사용한다. 안내가 접속 전에 지나가도 반복 출력을 추가하지 않는다. Xen running/blocked와 앱 준비는 별개이며, PV 콘솔 안내만으로 IRQ/전체 하드웨어 호환성이나 협조형 종료를 통과시킬 수 없다.

## 후속 실보드 수락 순서

1. 새 소스의 dom0/Xen을 별도 경로에 빌드하고 실제 WIC·rootfs·QEMU·설정 파서를 기존 절차로 검증한다. 이번에는 하지 않았다.
2. 기존 Linux VM은 위 명시적 설정 갱신 또는 새 seed 사본 준비로 SSH 수정을 반영한다. OS 원본 재다운로드나 불변 템플릿 덮어쓰기는 필요 없다.
3. Perseus CLI console/start/stop/reboot만으로 Ubuntu·Debian을 각각 확인한다. 직접 xl console/shutdown/destroy로 우회해 통과시키지 않는다.
4. SSH 증거를 성공한 경우에만 before 파일로 확정한다. 보드 CLI 종료·재부팅 후 수동 ssh.service 시작 없이 접속하고 after 파일과 의미 단위로 비교한다.
5. Zephyr 미승인 상태의 보드 종료가 모든 VM을 보존하며 거부되는지 먼저 확인한다. Starter 교체와 명시적 force-allowed 후에는 Linux 정상 종료 + Zephyr 강제 종료가 각각 기록되는지 확인한다.
6. poweroff와 reboot-board를 별도 시험한다. reboot-board는 UART의 Xen reset 이후 다음 부팅과 자동 시작까지 이어져야 통과다. 전원 재인가/콜드 부팅으로 대신하지 않는다.

각 systemctl/journalctl에는 --no-pager를 사용한다. 실패나 불명확한 상태가 있으면 다음 종료 시험으로 진행하지 말고 /run/perseus 결과와 UART 로그를 보존한다.

```bash
perseus-vm status
systemctl --failed --no-pager
journalctl -b -u perseus-vms -u perseus-vm-shutdown --no-pager
```

PC에서 이미 존재하는 보존 확인 파일을 선택해 수집한다. 해당 파일이 없으면 --guest-file을 생략한다. 각 명령 실패 시 비교를 중단한다.

```bash
python3 -B scripts/collect-perseus-guest-evidence.py \
  --host root@192.168.77.10 --output "$PWD/ubuntu-before.json" \
  --guest-file /root/perseus-proof.txt
rc=$?; echo "collect rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

재부팅 후 output을 ubuntu-after.json으로 바꿔 다시 수집한다. Debian은 .20으로 같은 절차를 실행한다. 두 파일의 JSON 값이 같아야 통과하며 단순한 빈 파일 diff를 보존 증거로 사용하지 않는다.

```bash
python3 -c 'import json; a=json.load(open("ubuntu-before.json")); b=json.load(open("ubuntu-after.json")); assert a==b'
rc=$?; echo "preservation rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

일반 보드 종료에는 전역 --force를 쓰지 않는다.

```bash
perseus-vm poweroff
rc=$?; echo "poweroff request rc=$rc"
```

다시 전원을 넣어 상태를 확인한 뒤 소프트웨어 재부팅을 별도 시험한다.

```bash
perseus-vm reboot-board
rc=$?; echo "reboot request rc=$rc"
```

성공한 OS 종료 요청의 반환은 물리 전원 차단 또는 Xen 리셋 성공을 의미하지 않는다. 직접 systemctl reboot/poweroff나 물리 전원 차단은 게스트 사전 검사 진입점이 아니다. ExecStop이 같은 정책으로 정리를 시도해도 이미 진행 중인 systemd 종료를 실패로 취소할 수 없다.

## F-03 추가 수집과 판별 기준

Xen 부팅 로그의 Platform: Raspberry Pi 4 및 PSCI 버전, 실제 Working FDT의 root compatible, PM/watchdog compatible/reg와 부모 soc ranges를 수집한다. dom0가 보는 /proc/device-tree는 PM 노드가 blacklist로 빠질 수 있어 Xen 원본 FDT의 대체 증거가 아니다.

사용자가 U-Boot 프롬프트에서 **읽기만** 확인할 경우 다음을 UART에 기록한다. 저장 환경 변경, fdt set/rm, 메모리 쓰기, SD 쓰기 명령은 필요 없다.

```text
printenv fdtcontroladdr fdt_addr_r
fdt addr ${fdtcontroladdr}
fdt print / model
fdt print / compatible
fdt print /soc ranges
fdt print /soc/watchdog@7e100000
```

노드 이름이 다르면 fdt print /soc의 출력에서 PM 노드를 찾아 경로를 사용한다. fdt_addr_r가 아직 유효한 Xen 전달 사본일 때 같은 속성도 수집한다. 현재 주소 포인터 조회는 영구 환경 설정 변경이 아니다.

- WIC의 bcm2711-pm/pm-wdt와 같은 노드이고 reg가 0x7e100000/0x114, soc 변환 주소가 0xfe100000이면 이번 호환 탐색 수정의 대상이다.
- Generic System, 다른 compatible/주소, 누락 노드 또는 PSCI 실패가 확인되면 그 증거를 기준으로 다시 조사한다. 고정 MMIO 주소 강제나 무한 재시도 패치를 추가하지 않는다.
- Xen→PSCI→플랫폼 reset의 실제 성공, poweroff 동작, 다음 부팅은 별도 실보드 판정이다. Zephyr DTB의 timer/event IRQ 의미 비교는 기존 BOARD-VERIFICATION의 남은 검증으로 유지한다.

vGICD ICACTIVER0, 일시적 ------, 과거 connection reset, 시각 미동기화, Zephyr IRQ 표현 차이는 이번 세 결함의 동일 원인으로 확정하지 않았다.

## 이번 검사의 범위

실제 배포판에서 추출한 유닛/enable 링크/SSH generator를 fixture로 보존했다. 그래프는 의존성 전체 오류를 판정하는 `--recursive-errors=yes`로 검사한다. Debian 257 fixture는 257 이상 검증기가 필요하며 `PERSEUS_SYSTEMD_ANALYZE` JSON argv로 지정할 수 있다. 이번 재검사는 기존 Debian 템플릿에서 읽기 전용 추출한 실제 systemd 257.13과 기존 QEMU user 실행기를 사용했다. Ubuntu의 기존 순환과 같은 fixture의 수정 후 통과를 대조한다.  SSH generator는 새 정책에서 mask되는지 검사하고 실행하지 않는다(--generators=no). 다른 generator의 실제 부팅 시 출력과 ARM 게스트 PID1 실행은 이번 검사 대상이 아니다. 실행 파일 존재 검사에는 스텁을 쓰고 서비스는 시작하지 않는다. 로더 debug 진단에서 순환과 job 삭제를 따로 판정하므로 종료 코드 0만으로 통과시키지 않는다. drop-in Requires와 SSH/identity job enqueue를 함께 확인한다.

SSH 설정 사전 검사는 읽기 전용이다. `.wants/.requires` 부모 링크는 guest root 기준으로만 해석하며 root 밖 상대 링크는 거부한다. 절대 링크의 호스트 경로를 따라 unlink하지 않는다. `Requires = ssh.socket`처럼 유효한 공백도 인식한다. 기존 VM 갱신 도구는 plan과 `--execute` 모두 백업/identity 유닛 생성 전에 같은 검사를 수행한다. 충돌 시에는 파일·링크·디렉터리 변경을 남기지 않는다. 적용 도중의 I/O 오류나 외부 동시 수정까지 트랜잭션으로 복구한다고 주장하지 않는다.

Starter와 Xen reset 함수의 native 대역 검사는 호출·정책 계약이다. 실제 Starter ARM64 컴파일/ELF·DTS·해시 검증은 별도 산출물 검사이며 Xen 게스트 실행은 아니다. xl 실제 도메인 제어/파서, 신규 Xen 바이너리, 수정된 SSH 자동 수신, 혼합 OS 종료, 하드웨어 poweroff/reset는 후속 실보드 시험으로 남는다.


## 2026-10-18 후속 dom0 빌드·최초 설치 이미지 검증 완료

앞 절의 미수행 표기는 소스 검토 당시 상태다. 후속 승인으로 아래 산출물까지 생성·검증했으며, 실보드 시험은 여전히 미수행이다.

- 기준 소스는 `a8ff460f4f3339f723fdec7c1490ee60f56b2d2d`의 미커밋 변경을 포함한 읽기 전용 149개 파일이다. canonical source tree SHA-256은 `cffd494d645291f3935b614813aa79723d0d68075acf21ac46f365a77f7ddf6f`다. 이 절의 결과 문서 추가는 빌드·조립 이후의 기록이며 런타임 소스를 바꾸지 않는다.
- `bitbake perseus-xen-bringup-image`는 종료 코드 0으로 완료했다. 첫 WIC에서 발견한 SSH 갱신 도구의 `uuid` import 오류는 승인 후 백업 폴더 난수만 `os.urandom(16).hex()`로 바꾸고 새 고정본으로 재빌드했다. 패키지·버전 추가 없이 실제 ARM64 import/실행을 확인했다.
- 소스 회귀는 기존 248개를 포함한 253개, 부팅 회귀는 17개가 통과했다. 실제 배포판 유닛 fixture의 기존 순환·job 삭제를 검출하고 같은 fixture의 수정 후 통과를 확인했다.
- 새 WIC SHA-256은 `99aaba653f0ce807640abaa52155a6cee21daf3bf487ea657128c0c38298e993`다. 실제 Xen의 BCM2711 패치 포함과 실제 QEMU의 `-vnc none` 수용·VNC 비활성·`xenpv`/`xen-disk` 등록을 검사했다. WSL의 실제 `xl -N create`와 QEMU `xenpv` 초기화는 Xen 제어 인터페이스 부재로 파서/연동 전에 차단됐으며 통과로 판정하지 않았다.
- 기존 Ubuntu·Debian 불변 템플릿과 Starter 1.0.0 번들을 재사용해 새 독립 seed와 최초 설치 이미지를 조립했다. 두 실제 seed의 ARM64 `sshd` 설정과 완전한 root의 systemd 유닛 그래프를 검사했으며 순환·job 삭제가 없었다. generator·PID1·SSH daemon은 실행하지 않았다.
- 공식 `assemble`/`verify` 및 별도 이미지 내부 검사 104개가 종료 코드 0으로 통과했다. 기존 boot1/root2 바이트를 보존하고 data3에 원본 템플릿 사본과 독립 seed를 넣었다. Linux seed의 암호는 원본 템플릿과 같고 machine-id/host key/등록 신원은 최초 부팅에 확정하는 계약을 유지한다.
- 새 Starter seed는 `autostart=true`, `board_shutdown_policy=force-allowed`, `shutdown_policy=explicit-force`, `graceful_shutdown=false`다. 기존 설치·VM의 정책·디스크·신원·키는 변경하지 않았다.

| 산출물 | 바이트 | SHA-256 |
| --- | ---: | --- |
| 최초 설치 `.img` | 43,289,411,584 | `13725648da46c0689b30dc9e8dc4ba0a3e9b78099f41e57b5e1b5ad7d884da84` |
| 전달 `.img.zst` | 2,328,008,427 | `53cf1ca155012e443c0bccd20ad29bf176c7770639dbdb2cf04b20d2bdc7b673` |

압축본은 전체 해제 스트림의 크기·SHA-256 및 전달 사본 해시를 확인했다. 기존 보호 자산 81,102개 메타데이터와 주요 원본 25개 내용 해시, dom0 산출물 10개 해시가 일치했다. 임시 loop/mount/binfmt 등록도 정리했다. 보조 검사기의 namespace 확인·중첩 loop 정리 순서·실행 계정 문제는 검사기에서 해결했고 초기 실패 로그도 보존했다.

원본 완료 이미지·manifest는 `/home/ssafy/perseus-first-install-releases/first-install-acceptance-20261018-184222/`에 있다. 명령·종료 코드·출처·보존·검사 로그는 `/home/ssafy/perseus-first-install-assembly-acceptance-20261018-184222/`의 `FINAL-RESULT.json`, `INPUT-PROVENANCE.json`, `RESULT.txt`를 따른다. 해당 경로는 이 실행 환경의 증거 위치이며 저장소 필수 경로가 아니다. 게시 도구의 top-level 이미지 항목 계약에 맞춘 기록은 원래 dom0 성공 기록의 모든 필드를 보존한 별도 메타데이터 사본이며 원본 기록은 변경하지 않았다.

배포·SD 접근/변경은 수행하지 않았다. 실제 부팅·data 확장·CLI 제어·SSH 자동 수신·혼합 OS 종료·하드웨어 poweroff/reset·이후 자동 기동·Starter IRQ 호환은 사용자 실보드 수락으로 확정한다.
