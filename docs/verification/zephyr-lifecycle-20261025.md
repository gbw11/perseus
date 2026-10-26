# Zephyr 초기화·부분 실패·협조 종료 검증 — 2026-10-25

현재 상태: 승인한 소유 VM의 실제 수신·협조 종료·경합 시험과 정리를 완료했다. 실제 grant/Store 강제 장애와 새 이미지 수락은 별도다. 마지막 실보드 절이 최신 결과다.


## 기준과 판정

Windows와 WSL의 `feat/platform/runtime-lifecycle-diagnostics`, HEAD
`230e1326d32acbc51b0113e4556f149d2be93632`에서 시작했다. 시작 시 작업트리는 깨끗했다.
최신 origin/platform `44e6ee48a6ffa130cb9bc26aa25651104563e850`과는 내용 차이가 없었다.
브랜치를 전환하거나 과거 검토 커밋으로 복원하지 않았다. 이 결과는 **로컬 오류 주입·
회귀·후보 부분 빌드**다. 실보드 적용·실제 Xen grant/backend 수락은 수행하지 않았다.

현재 소유권과 사용 조건은 [runtime 설명서](../../platform/guests/zephyr/xen-runtime/README.md)에
정리했다. 이후 별도 부팅 표시 변경은 dom0 Python·recipe·문서 변경이며, 여기의 Zephyr C
후보와 시험 경계를 섞지 않는다. 후보의 전체 입력은 각 build receipt에 보존한다.

## 재현한 기존 실패

작업트리를 되돌리지 않고 `git show HEAD:경로`로 읽은 실제 원본 소스를 별도 시험 경로에서
컴파일했다. 원본 netfront에서 frontend 게시 응답을 잃었을 때 backend Closed 확인 없이
grant를 반환하는 경로, 원본 drive runtime에서 `xd_start` 실패 뒤 시작되지 않은 앱의
`app_done`을 기다리는 경로가 실제 경계 assertion에서 각각 SIGABRT로 재현됐다.
현재 후보의 대응 시험은 통과했다. 복사한 상태 기계로 대체한 시험이 아니다.

## 수정한 경계

| 대상 | 변경과 확인한 의미 |
|---|---|
| 공통 drive runtime·두 앱 main | startup/app 실행/정리 소유를 분리. 미시작 앱의 완료를 기다리지 않으며 모든 반환 경로가 `xd_finish`로 원 오류와 cleanup 오류를 보존 |
| startup와 stop 경합 | 짧은 admission gate와 lifecycle lock으로 늦게 생성되는 자원을 소유 목록에 반영. 잠금 안에서 I/O를 하지 않으며 stop 이후 새 publish/poll/connect 진입을 차단 |
| v1/v2 MQTT | 앱 소유 v1 close와 Publisher worker 소유 v2 close 유지. 실제 thread join 전 TCB 재사용 금지. close 결과 불확정 fd를 재사용·재연결하지 않음 |
| shutdown monitor | 외부 요청과 로컬 초기화 unwind 소유를 구분. 외부 요청을 실패 후 IDLE로 돌리거나 중복 실행하지 않음. 기능 게시 도중의 실패도 실제 watch/thread/기능 획득 범위만 정리 |
| netfront | ring·watch·event·grant·frontend 게시 가능성·worker·interface·DHCP를 각각 추적. 실제 사용자/packet 작업/join 뒤 backend·grant·페이지를 정리 |
| 불확정 자원 | 응답 유실 게시, backend 미확인, busy grant는 정리 완료로 숨기지 않음. 반환한 ref만 0으로 기록하고 잔존 ref·페이지는 재사용하지 않음 |
| Store·grant | 같은 guest-local 절대 기한을 lock·I/O·revoke에 전달. 종료 단계마다 새 예산을 만들지 않음 |
| Network Starter | 필수 shutdown/net 초기화 실패는 후속 작업을 시작하지 않고 로컬 unwind. Diagnostics exercise/Publisher의 선택 기능 실패는 별도 degraded 사실로 보존 |
| 후보 도구 | drive snapshot에 지연 import 의존성 `xenova_trace.py` 포함. Starter는 명시적 `--candidate-version`으로 새 후보 영수증만 만들며 공식 lock·factory 허용 목록은 보존 |

기본 guest 종료 예산은 15,000ms다. host와 guest의 단조 시계를 직접 비교하지 않는다.
자동 force·thread abort·무제한 대기·원 오류를 0으로 바꾸는 경로를 추가하지 않았다.
주행 알고리즘, v1/v2 wire 의미, 불변 템플릿, VM 신원·CPU 정책과 상위 제품 코드는 보존했다.

## 실제 실행한 검증

| 시험 | 결과·범위 |
|---|---|
| 기존 플랫폼 전체 unittest | **846개, 실패 0, root 전용 UID 사례 1 skip**. `full-regression-r1.log`, 320.308초. 이후 부팅 표시 통합 결과는 별도 보고서에 기록 |
| native_sim netfront/shutdown | 실제 제품 C·Zephyr kernel thread/lock/join 사용, 외부 Xen/Store/net-device 경계만 대역. 25개 독립 프로세스 사례 통과 |
| 실제 drive runtime | startup shutdown/network/loop/Publisher 실패, init-stop 경합, 정상 앱 완료, 원 오류+cleanup 오류 보존 **7개 통과** |
| 실제 Network Starter | 필수 기능 게시 1·2, grant/interface 실패와 정상 경로 **5개 통과** |
| 실제 Publisher | loader 지연·stop timeout·TCB 재사용 거절·join/restart·진단 수집 중 stop. 1개 프로세스의 여러 경합 단계 통과 |
| C transport | 실제 socket transport에 socket/clock만 대역. 일반 실행과 ASAN/UBSAN에서 fd 소유·불확정 close·전체 iovec/read 예산 통과 |
| native 실제 앱·broker | 격리 network/mount namespace, 실제 manual/autonomous·v1/v2·인증 Mosquitto·독립 수신기. 정상/단절·재접속 모두 accepted=true |
| ARM 후보 | 두 주행 앱과 Starter on/on, on/off, off/off, exercise의 고정 xenvm 부분 빌드·패키징 통과 |

native kernel 사례는 총 **38개 프로세스 실행**이다. native 기능 시험의 정상 수신은 drive
2,842건·v2 144건, 단절 시험은 drive 2,393건·v2 144건이었다. 계약 invalid는 모두 0이다.
단절 주입 중 수신기의 reader error 20건을 숨기지 않았다. 단절 시험의 로컬 accept와
독립 수신 사이에는 manual 3건·autonomous 5건 차이가 있었고, QoS0 수신 경계 차이로
남겼다. 이를 무손실 전달 또는 ARM 지터 수락으로 보고하지 않는다. native의 거친 clock
측정치와 이 시험 중의 주기 지연도 실보드 성능값이 아니다.

재실행 명령의 주요 진입점:

```bash
python3 -B -m unittest discover -s platform/tests -v
python3 -B -m unittest discover -s platform/tests -p test_xenova_zephyr_lifecycle.py -v
# 고정 도구·원본 Zephyr 경로와 새 출력 경로는 빌드 가이드대로 지정한다.
python3 platform/scripts/build-xenova-network-starter.py --help
python3 platform/scripts/build-xenova-drive-app.py --help
```

native 앱의 CMake 경로는 `platform/tests/apps/xenova-lifecycle`,
`xenova-drive-lifecycle`, `xenova-publisher-lifecycle`이다. README의 고정 도구와
native_sim/native/64를 사용한다. 이 디렉터리의 오류 주입 코드는 제품 firmware에 들어가지 않는다.

## 고정 ARM 산출물

Zephyr 4.4.0 commit `684c9e8f32e4373a21098559f748f06915f950c9`, SDK 1.0.0,
고정 SPDX/toolchain archive를 사용했다. RAM/GIC/단일-vCPU 프로필과 원 ABI는 그대로다.

| 후보 | zephyr.bin SHA-256 | linker RAM/16MiB |
|---|---|---|
| manual | `1038fcb25ba81ceb456f46906ffcc078f360ed549159e47ad74d92b9061e9caf` | 2520KiB, 15.38% |
| autonomous | `d9ad044034496c8a417ac127d19865ed366200f4a43ada24578c4349647ef334` | 2520KiB, 15.38% |
| Starter on/on | `4b5d9757e3e42dc40717c9c42d3fff05919807e098a64bad1df25c92ba294db4` | 840KiB, 5.13% |
| Starter on/off | `61776ce1e894ddc7f2b7ac884bac17e86feb36225f537336b33b67d2d1b025ff` | 736KiB, 4.49% |
| Starter off/off | `f7deed5ddc21729b5a43806724cb3026e6a0aa2720bc7f2441e1148131454fa8` | 724KiB, 4.42% |
| Starter exercise | `84c42939c253d8b5437f66634f1595a7ed3910f28f730ffc02d03ea5c34324fc` | 848KiB, 5.18% |

actual stack object는 shutdown 4096, net worker 6144, Publisher 12288, main 8192,
선택 exercise 2048 bytes다. 고정 compiler와 원 compile args로 별도 object에
`-fstack-usage`를 적용한 직접 프레임 감사에서 netfront send는 **64 bytes**로 256-byte
기준 안이었다. Publisher worker 직접 프레임은 1232 bytes, manual main은 880 bytes
dynamic/bounded였다. 이는 전체 call-chain·런타임 stack high-water 또는 ARM 성능의 증명이 아니다.

## 시험 중 발견·수정과 남은 수락

초기 실제 drive 패키징에서 `xenova_trace` import 누락을 확인해 snapshot 폐쇄성을 고쳤다.
Starter 후보를 공식 lock에 넣었던 실험은 factory 허용 목록 회귀에서 거절됐으며,
우리 변경만 원래 바이트로 되돌리고 명시적 후보 옵션으로 분리했다. 허용 목록이나
검증을 넓혀 통과시키지 않았다. G1 링크/필요 파일과 Store 최소 shim의 매크로 의존성도
해당 검증을 유지한 채 수정했다. 실패 로그와 성공 재시험을 함께 보존했다.

원자료·argv/rc/timeout·입력 snapshot·ELF/map/config/DTS 감사는 PC
`C:/Users/SSAFY/Documents/Codex/2026-10-25/xenova-zephyr-lifecycle` 및 WSL 동명 실행
경로에 보존한다. `ARTIFACT_AUDIT.json`이 파일별 전체 해시의 원문이다.
private 인증 디렉터리는 공개 원자료·소스·보고서 묶음에 포함하지 않는다.

**로컬 마감 당시 미수행:** 실제 Xen grant/event/backend 미응답·반환, 물리 packet 송수신과 협조 stop/reboot,
ARM thread stack high-water, 임의 부분 초기화의 실보드 실패 주입, 새 이미지 전원 부팅.
로컬 대역의 성공을 이 항목의 PASS로 확대하지 않았다. 별도 실보드 검토안 승인 후
소유 시험 VM만 사용하고 기본 VM·정책·Wi-Fi·SSH를 보존해야 한다.

최종 `FINAL_PROOF.json`에서는 manual/autonomous 각각 C·header·설정 입력 104개가
동결 ARM 후보와 현재 소스에서 일치했다. 별도 부팅 표시 작업으로 달라진 snapshot
지원 파일은 dom0의 `xenova_runtime.py`뿐이며, 후보의 원 입력 snapshot은 보존한다.

## 승인 후 실보드 수락

승인한 소유 Zephyr VM 두 UUID에서 Starter/manual/autonomous 동결 후보를 실제 Xen으로
시험했다. Starter 정상/협조 reboot 후 v2 49/50개, manual drive.command 900/900개,
autonomous 902/901개를 독립 수신했다. v2 총363개를 PC 원문 schema로 재검사해 오류0이었다.
manual 두 창의 각1개는 과거 Starter retained inventory이며 현재 생존으로 세지 않았다.
normal reboot 세 건은 각각 새 실행 문맥으로 연결했고, broker 단절 중 manual/autonomous의
협조 stop도 통과했다. 자동 force는 사용하지 않았다.

시작 중 paused domain 등장 직후 별도 stop을 넣은 경합에서는 원 start의
CPU_APPLY_INCOMPLETE/INTENT_MISMATCH를 보존했다. stop은 pending 수락 후 동일
operation·revision·deadline으로 재전달해 완료했다. 성공하지 않은 start를 성공으로 덮지 않았다.
특정 guest 초기화 지점을 강제 주입한 실보드 시험으로 확대하지 않는다.

domid43–49의 own Store name/backend 부재와 scope 잔존0을 확인했다. 증거 회수 뒤 두 UUID만
retire/purge했다. 원래 VM10개의 record·실행/의도, 기본 실행 세 VM의 incarnation,
실제 CPU 배치·정책/준비 기록·Wi-Fi·boot.scr은 전후 동일했다.
실제 grant busy·Store/backend 강제 무응답·ARM stack high-water·새 이미지 전원 부팅은
여전히 미검증이다. 로컬38개 오류 주입 사례와 실제 packet/종료 성공을 구별한다.

현재 설치본과 최신 관리 소스는 같지 않다. 시험은 별도 후보 runtime으로 수행했으며,
기본 설치 runtime을 최신 관리 기능 전체로 교체하지 않았다. 부팅 표시에는 설치 baseline에
표시 추가만 적용한 호환 네 파일을 사용했다. 이는 [부팅 표시 보고서](boot-status-20261025.md)의
실보드 절을 따른다.

PC 증거의 `board-live/BOARD_ANALYSIS.json`, `PROTECTED_FINAL.json`, 명령별 argv/rc/timeout과
공개 원자료78파일을 보존했다. 압축본 SHA-256은
`8169f10fa1380bd12957714b82a47d02a111416b8fc95b8bea12aadb9bf86a18`이다.
정리 후 원 journal·표시 원복 백업은 보존하고 시험 파일147개·private33개를 제거했다.
호스트 재부팅·전체 이미지/SD·commit/push는 수행하지 않았다.

## 후속 host 부팅 검증과 자료의 범위

소유 guest 시험·정리 이후 별도 승인된 정상 host 재부팅과 UART 출력 정리를 수행했다.
최종 등록 10개 record·기본 3개 VM 신원/의도·CPU 정책·Wi-Fi 보존을 확인했다.
이후 boot ID·최종 산출물·UART 원자료와 미완료 HDMI 범위는
[부팅 표시 검증](boot-status-20261025.md#uart-정리와-승인-후-정상-재부팅-검증)에 기록했다.
이 정상 재부팅 결과를 실제 grant busy·Store 강제 장애 시험으로 확대하지 않는다.
