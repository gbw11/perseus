# 종료 관측 경쟁조건 및 dom0 SSH 보완 — 2026-10-18

이번 작업은 소스·회귀 검사와 후속 절차 작성까지다. dom0/Xen 빌드, seed/템플릿/Starter 생성, 최종 조립, 보드 접근, SD 변경, commit/push는 실행하지 않는다. 기존 [실보드 수락 절차](perseus-acceptance-20261018.md)에 더해 이번 두 수정의 범위를 설명한다. 이전 문서의 F-01 콘솔·게스트 SSH·Pi 소프트웨어 리셋 미검증 표시는 사용자의 최신 실보드 성공 보고로 갱신되며, 그 코드는 이번에 다시 수정하지 않는다.

## 원인과 변경

**FIX-01 확인 사실:** 기존 stop_many는 요청 전 검사뿐 아니라 요청 후 대기에도 domains()를 호출했다. domains()는 -l 설정 목록과 짧은 목록의 시점이 다르면 안전하게 거부한다. 실보드 오류는 이 경로와 일치한다. 당시 두 원본 출력이 없어 정확한 불일치 순간은 미확정이다.

요청 전 전체 레지스트리 신원·경로/디스크 보호와 domains()의 엄격한 대조는 유지한다. 제어 직전 UUID/name/domid를 재검사하고 검증된 domid에 요청을 한 번만 보낸다. 요청 뒤에는 고정 Xen의 `xl list -v`에서 UUID/name/domid/state를 관측한다. 디스크 설정을 다시 읽는 작업이 아니며 일반 domains()를 완화하지 않는다.

전체 목록을 두 번 성공적으로 읽고 **두 목록 모두에서 부재**인 대상만 domain-removed로 처리한다. 목록 사이에서 제거됐다면 한 번 더 관측한다. 계속 존재하면 원래의 monotonic 마감시간까지 기다리고 실패한다. 개별 xl 호출도 남은 시간(최대 15초)을 적용한다. 빈 출력·잘못된 헤더/행·중복 신원·Domain-0 부재·명령 실패는 즉시 실패하며 빈 목록으로 간주하지 않는다. domid 재사용/동명 다른 UUID/동일 UUID의 다른 domid는 교체로 거부한다. 보드 종료 중 새 도메인은 거부한다.

Linux는 정상 종료, 승인 Zephyr만 강제 제거한다. 자동 force·재시작·복구는 추가하지 않는다. 모든 게스트가 제거된 뒤, 백엔드 정리 직전과 저장소 해제 직전에 두 번씩 다시 전체 부재를 확인한다. 내부 shutdown marker는 CLI의 새 기동을 막는다. 외부 xl과 조회→제어/정리 사이를 완전히 원자적으로 통제하지는 못한다.

`last-stop.json` 외에 `stop-attempt-<attempt_id>.json`을 /run/perseus에 보관한다. 실패 후 재시도의 already_stopped 및 새 결과를 이전 시도와 구분한다. 관측 변화는 count와 최근 4쌍(각 최대 32 domid)만 기록한다. 이 기록은 **현재 부팅 동안** 보존되므로 재부팅 증거는 UART 로그를 외부 PC에 수집한다. autostart-results는 시작 시점 기록이며 현재 모니터가 아니다. app_readiness=unknown은 유지한다.

**FIX-02 확인 사실:** 고정 openssh_10.3p1은 기본 PACKAGECONFIG의 socket mode에서 sshd.socket/sshd@만 설치한다. 실제 WIC에도 sshd.service는 없고 socket enable 링크와 enable preset이 있었다. Perseus는 socket에도 Requires/After=sshdgenkeys.service를 추가했다. 일반 키 서비스의 기본 After=basic.target과 socket의 Before=sockets.target을 통해 실보드와 같은 순환·job 삭제가 재현된다.

프로젝트 openssh bbappend에서 socket mode를 제거하고 고정 레시피의 service mode를 선택한다. 같은 레시피가 sshd.service와 enable preset/링크를 생성한다. Perseus 키 drop-in은 sshd.service에만 설치한다. 기존 sshdgenkeys.service를 hard Requires/After로 유지하고 `sshd -t $SSHD_OPTS`를 시작 전 실행해 키/설정을 검증한다. sshd daemon의 실패 무시 접두사도 제거해 오류를 기록한다. systemd 259 SSH generator의 별도 socket 경로는 /etc mask로 막는다. 서비스나 키 생성의 기본 의존성은 제거하지 않는다.

기존 키가 있으면 고정 sshd_check_keys가 재생성하지 않는 계약을 유지한다. 키 준비 실패 또는 sshd -t 실패 시 리스너를 시작하지 않는다. 네트워크·비밀번호·machine-id·게스트 내부 SSH 설정은 수정하지 않는다.

## 검사와 후속 산출물

새 회귀는 실제 main→records→stop_many 경로를 실행하고 Xen 명령 경계만 대체한다. 실제 WIC의 전체 systemd unit/link/preset fixture, 고정 OpenSSH 일반 서비스 원본, 출처/해시와 재실행 코드를 tests/platform/fixtures/dom0-systemd에 포함한다. 최소 스텁 유닛으로 부팅 그래프를 바꾸지 않는다. 서비스 실행 파일은 존재 확인용 스텁이며 실행하지 않는다.

```bash
# JSON argv는 준비된 분석기의 경로로 지정한다. dom0는 >=259, guest는 >=257.
export PERSEUS_DOM0_SYSTEMD_ANALYZE='["/path/to/systemd-analyze-259"]'
export PERSEUS_SYSTEMD_ANALYZE='["/path/to/systemd-analyze-257-or-later"]'
bash scripts/check-perseus-platform.sh > regression.log 2>&1
rc=$?; echo "regression rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

의존성을 평가하는 verify/recursive-errors를 사용하며 rc 외에 순환·job 삭제·skip 진단도 검사한다. 기존 dom0 순환과 수정 후 통과, 의도적으로 재주입한 순환을 같은 전체 fixture로 확인한다. 이번 dom0 그래프 로더는 실제 빌드와 같은 ARM64 systemd 259.5다. WSL 환경과 QEMU user emulation이므로 실제 Pi PID1 부팅, generator 실행, 키 서비스 실패 전파·TCP 리스너·접속은 후속 확인이다.

후속 승인 시 **dom0 OpenSSH/perseus-platform 패키지와 WIC**를 재빌드하고 새 SSH 서비스/enable/preset/generator mask 및 관리 모듈 일치를 검사한다. 변경된 조립기 platform_root_ok는 이전 socket 구성이나 이전 관리 코드의 WIC를 거부한다. 이번에는 BitBake metadata 평가·패키지 생성·새 WIC 검증을 실행하지 않는다. Xen 소스·버전·성공한 reset 패치는 그대로다. 새로운 리셋 패치는 필요 없다.

Ubuntu/Debian의 이미 수정된 seed·기존 VM과 Zephyr Starter 번들은 재사용 가능하며 이번 변경 때문에 재생성하지 않는다. 향후 최종 조립에는 새 dom0를 입력하고 기존 자산을 재사용한다. 기존 보드 갱신은 별도 승인된 배포 경로에서 dom0 키/machine-id 및 데이터 파티션 보존을 계획한다. 공통 WIC를 덮어쓰는 것 자체가 기존 rootfs 신원을 보존한다고 가정하지 않는다.

## 후속 보드 수락 절차 — 이번에 실행하지 않음

UART 전체 로그를 PC에 수집한다. root 콘솔에서 각 블록의 종료 코드를 확인하고 실패하면 다음 블록을 실행하지 않는다. 직접 xl 종료로 우회하거나 sshd를 수동 시작해 통과시키지 않는다.

### 1. 사전 상태와 SSH/신원 증거

```bash
perseus-vm status > /tmp/perseus-before.json.tmp
rc=$?; echo "status rc=$rc"; [ "$rc" -eq 0 ] || exit 1
mv /tmp/perseus-before.json.tmp /tmp/perseus-before.json
systemctl --no-pager --full status sshdgenkeys.service sshd.service
systemctl show sshd.service -p Requires -p After --no-pager
ss -lntp
```

세 인스턴스의 UUID/name, Xen running/blocked, Ubuntu/Debian 정상 종료 정책과 Zephyr force-allowed를 확인한다. autostart는 각자 on이어야 한다. 이미 승인된 정책을 이 시험 때문에 자동 변경하지 않는다. 새 정책 승인이 필요하면 시험을 중단해 운영자와 먼저 결정한다. 등록 안 된 domU가 있으면 제거하지 말고 종료 요청을 중단한다.

```bash
journalctl -b --no-pager -u sshdgenkeys.service -u sshd.service -u sshd.socket
journalctl -b --no-pager | grep -E 'ordering cycle|deleted to break|Ordering cycle found, skipping'
echo "cycle search rc=$?"  # 1=검색 결과 없음, 2 이상=수집 오류
```

sshd.service active, 키 gate 성공 및 TCP 22 리스너를 확인한다. dom0 SSH는 미리 준비된 개발 PC 경로로 실제 접속까지 확인하며 네트워크/NAT/물리 NIC를 이 작업에서 새로 구성하지 않는다. systemctl --failed 0만으로 통과시키지 않는다.

dom0 및 두 Linux 게스트의 machine-id, 호스트 공개 키 지문, 기존 시험 파일 SHA-256, VM 등록 UUID/MAC/IP·펌웨어 참조를 PC에 수집한다. SSH 수집은 다음처럼 **성공할 때만** 최종 파일로 확정한다. 접속 실패로 생긴 빈 파일을 비교해 데이터 손실이라고 판정하지 않는다.

```bash
# 개발 PC: 실제 접속 주소와 시험 파일 경로로 치환한다.
if ssh root@GUEST_ADDRESS 'cat /etc/machine-id && ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub && sha256sum /actual/test-file' > before.tmp; then
    mv before.tmp before.txt
else
    rc=$?; echo "collection rc=$rc"; exit "$rc"
fi
```

게스트/보드의 실제 키 종류에 맞는 공개 키를 선택한다. 고정 dom0 기본은 ECDSA다. 비밀번호/개인 키는 로그에 출력하지 않는다. SSH 접속 자체가 수동 시작 없이 성공해야 한다.

### 2. 세 VM 실행 상태에서 일반 reboot-board 한 번

```bash
perseus-vm reboot-board
rc=$?; echo "reboot-board rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

전역 --force를 사용하지 않는다. UART에서 Linux graceful 요청→domain-removed, 승인 Zephyr forced→domain-removed, 백엔드/저장소 정리, 하드웨어 reset, 새 부팅을 연속 수집한다. 명령 수락·도메인 제거·하드웨어 reset·자동 시작·실제 SSH를 각각 별도 판정한다. 재부팅으로 세션이 끊긴 사실만으로 성공시키지 않는다. 실패하면 /run/perseus/last-stop.json, stop-attempt-*.json, board-shutdown-result.json, `xl list -v`와 `xl list -l`의 시각별 출력을 수집하고 중단한다. 예전 실패 파일을 새 결과로 덮어쓰지 않는다.

부분 실패 후 관리 CLI로 재시도할 때 이미 제거된 VM은 already_stopped여야 하며 중복 종료/자동 기동이 없어야 한다. 무관한 VM이나 Linux를 강제 제거해 통과시키지 않는다.

### 3. 재부팅 후 자동 준비와 보존 확인

```bash
perseus-vm status > /tmp/perseus-after.json.tmp
rc=$?; echo "status rc=$rc"; [ "$rc" -eq 0 ] || exit 1
mv /tmp/perseus-after.json.tmp /tmp/perseus-after.json
systemctl --no-pager --full status sshdgenkeys.service sshd.service
ss -lntp
journalctl -b --no-pager -u sshdgenkeys.service -u sshd.service -u sshd.socket
```

세 VM 자동 기동, dom0 SSH와 Linux 게스트 SSH의 리스너·실제 접속을 확인한다. 수동 start는 하지 않는다. 이전과 같은 성공-후-파일-확정 방식으로 after 증거를 PC에 수집해 신원·공개 키·시험 파일 및 활성 펌웨어/정책을 비교한다. 앱 준비는 별도이고 Zephyr 콘솔 성공만으로 정적 DTB/IRQ 전체 호환성을 확정하지 않는다.

간헐 경쟁 확인을 위해 우선 **5회 연속** 같은 시험을 제안하며, 회차별 시작 시각·종료 결과·reset·세 VM 자동 시작·각 SSH·보존 결과를 표로 남긴다. 5회는 임시 수락 기준이며 운영 요구에 따라 조정한다. 한 번이라도 실패하면 해당 회차 로그를 보존하고 원인을 확인한다. poweroff는 세 VM 실행 상태에서 `perseus-vm poweroff`로 별도 시험하고, 전원 재인가 후 부팅은 소프트웨어 reboot 성공과 구분한다.

직접 systemctl reboot/poweroff는 CLI 사전 검사 밖이다. 종료 훅은 같은 stop_many/정책을 사용하지만 이미 진행 중인 OS 종료를 ExecStop 실패로 취소할 수 있다고 가정하지 않는다.
