# 253번 3단계: 실제 게스트 송신 연결

2026-10-22 · `feat/platform/S15P31E110-253-display-source-collection` · 시작 커밋 `f86c18d`.

상태: **3단계의 대표 Zephyr·Debian 실제 연결 시험 완료.** Debian 기존 Agent 및 VM2 Zephyr의 heartbeat-only 시험 펌웨어가 실제 domU 네트워크를 거쳐 dom0 수집기에 도달했다. VM1 송신·세 VM 상시 배포·실보드 수락을 완료했다는 뜻은 아니다. 2단계 합성 시험과 구분한다.

## Zephyr VM2 실제 송신 검증

사용자는 **VM2 한 대의 가상 NIC·시험 펌웨어 적용 및 재시작**을 허용했다. VM1과 Debian은 재시작하지 않았다. [빌드 준비 도구](../../platform/services/display/router/tests/prepare_zephyr_ethernet_probe.py)는 기존 manual main을 시험용 디렉터리에 복사해 명령 발행·입력 구독만 차단하고, 기존 Ethernet 런타임의 단일 XenStore 세션을 사용하도록 연결한다. 기존 앱·공용 MQTT·하위 Ethernet 원본 파일은 수정하지 않았다. 별도 MQTT 소스 사본에는 연결 오류 진단만 추가했고 봉투·토픽·QoS·재연결 규칙은 그대로 사용한다.

기존 DHCP 예약은 Debian만 포함하므로 DHCP 서버를 변경하지 않고 시험 VM2에 MAC `02:16:3e:77:00:32`, IP `192.168.77.32/24`를 일시 적용했다. TCP·소켓·MQTT와 bounded poll 설정을 추가하고 상류 grant driver는 비활성화해 기존 netfront가 단독 소유하도록 했다. 고정 Zephyr `684c9e8`·SDK GNU 14.3.0으로 빌드했으며 RAM 사용은 약 780KiB/16MiB다. 원본 C 소스 해시는 UTF-8·LF 정규화 기준이다. 시험 RNG를 암호학적 신원·실제 OS boot_id 근거로 취급하지 않는다.

[실행 도구](../../platform/services/display/router/tests/probe_zephyr_ethernet.py)는 기존 VM2 등록/펌웨어를 보존하고 시험 cfg를 별도로 부팅했다. 원본 등록의 active_firmware를 시험 이미지로 바꾸지 않았으므로 시험 중 registry만 조회해 현재 펌웨어를 증명하면 안 된다. 실제 Xen config의 kernel/NIC, 시험 바이너리 SHA256 및 수신 봉투를 [실행 증거](display-253-step3-zephyr-evidence.json)에 기록했다.

```text
실제 VM2: 기존 manual heartbeat 생성·송신 + 기존 Ethernet runtime
  → Xen netfront / dom0 netback / xenbr0
  → dom0 임시 인증 브로커 192.168.77.1:51883
  → dom0 실제 Xen 드라이버 + 표시 수집기
  → dom0 REST / WebSocket / HDMI presentation JSON
```

| 확인 | 결과 |
| --- | --- |
| 실제 VM2 UUID | XenStore에서 읽은 820a0522-243f-42f1-90e9-9b74e1522965, fallback 신원 사용 안 함 |
| 실제 앱 보고 | manual/app.heartbeat, seq 1→2→3→4, 동일 발행 세대, retain=false, idle, 약 1초 uptime 간격 |
| 명령·입력 | 생성된 시험 소스에 drive.command 발행·control/input 구독 없음. 관측한 events 봉투도 모두 app.heartbeat |
| REST/WS | app source fresh 및 동일 UUID·발행 세대 확인. 주행 mode=unknown·active_vm_id=null 유지 |
| OS 근거 | observed 미수신, current_run_verified=false. C boot_id는 클라이언트 생성 세대이며 sent_at은 1970년 기준 uptime |
| HDMI용 데이터 | VM2 결정은 status. 실제 그래픽 영상·물리 HDMI 렌더 확인으로 표현하지 않음 |
| 원본 복원 | instance.json, 원본 firmware, runtime cfg의 SHA256이 모두 원본과 일치. 시험 NIC 제거, VM2 원본 재부팅 확인 |
| 다른 VM·정리 | VM1 domid 3·Debian domid 1 유지. 임시 브로커/수집기 종료. 원본 보관 사본은 dom0 /var/tmp에 유지 |

최종 시험 VM2 domid는 11, 원본 복원 후는 12였다. 이는 이번 시험의 실행 번호이며 영구 식별자로 설정하지 않는다. 시험 펌웨어/인증정보가 들어간 빌드 산출물은 Git 밖의 접근 제한 시험 디렉터리에만 둔다. 사용한 임시 브로커 계정은 브로커 종료와 함께 비활성화된다.

### 시험 중 발견한 환경·도구 문제

- 구형 lab Manager가 실제 destroy 후 종료 관측 오류를 반환하는 경우가 있었다. 이 경우 새 Xen 전체 조회 두 번으로 대상 UUID 부재와 다른 VM domid 불변이 확인된 때만 시험 부팅을 진행했다. 설치된 관리 코드를 수정하지 않았다.
- 초기 시험 종료 코드가 xl에 UUID 문자열을 넘겨 실패했다. UUID→현재 domid/name을 즉시 대조한 뒤 제어하도록 고쳤고 VM2를 복원했다. 최종 실행은 복원·정리까지 통과했다.
- 초기 TCP 패킷 송수신은 진행됐으나 CONNACK 승인이 완료되지 않았다. QEMU TCG의 시험 브로커에서 공개 시험 문자열로 측정한 SHA512-crypt 검증 시간은 656,000 rounds 약 **6.99초**, 5,000 rounds 약 **0.047초**였다. 기존 C 클라이언트의 CONNACK 제한은 5초다. 시험용 고엔트로피 임시 계정만 5,000 rounds로 바꾼 뒤 실제 보고 수신이 통과했다. 운영 Mosquitto 인증 정책이나 sender timeout을 변경한 결과가 아니다. [측정 기록](display-253-step3-auth-timing.json)을 참고한다.
- 재실행 시 TIME_WAIT로 고정 시험 포트 예약이 실패해 시험 socket의 SO_REUSEADDR 설정을 추가했다. 활성 listener를 공유하는 SO_REUSEPORT는 사용하지 않는다.
- 최종 점검에서 SSH 프로세스 종료만으로 원격 Python 종료가 보장되지 않는 것을 확인했다. 이전 시도의 포트 없는 잔여 실행기를 정확한 `/proc/.../cmdline`의 시험 경로로 대조해 종료했다. 시험 helper에도 원격 runner 부재 확인을 추가했다. 최종 [원복·정리 기록](display-253-step3-final-state.json)은 시험 runner 0개·원래 SSH listener만 존재·VM2 NIC 없음·VM1/데비안 domid 불변을 확인한다.

현재 시험은 Ethernet 재사용 가능성의 근거다. vchan 팀 계약 폐기, 실제 주·백업 배치 확정, 미러링/승계 또는 자율·수동 주행 검증이 아니다. VM1의 autonomous 실연결과 운영 펌웨어·이미지 통합은 별도 담당 확인·의존성으로 남긴다.

## Debian 실제 송신 검증

사전 조사 뒤 사용자가 Debian·dom0의 임시 배치와 실행을 허용했다. [시험 도구](../../platform/services/display/router/tests/probe_debian_agent.py)는 기존 `xenova_agent.__main__.run`과 공용 MQTT 클라이언트를 변경 없이 호출한다. 새로운 heartbeat 발행기를 작성하지 않았다. 실제 `/etc/xenova/instance-id`를 읽고, guest 커널 boot_id와 보고 boot_id가 같은지 대조했다.

각 호스트의 고유 `/tmp/display253-*`에 소스·의존 wheel을 풀어 PYTHONPATH로 실행했다. 게스트에서 apt/pip 설치, systemd 등록/enable, 환경 파일 변경은 하지 않았다. Agent에는 Paho만 추가하고 dom0에는 시험 브로커/표시 수집기 의존 패키지를 배치했다. 의존 파일 SHA256과 실제 봉투는 [수신 증거](display-253-step3-debian-evidence.json)에 보관했다.

```text
실제 Debian 기존 Agent (5초 주기)
  → eth0 / xenbr0 (192.168.77.20 → 192.168.77.1)
  → dom0 임시 AMQTT 브로커 (시험용 임의 포트·인증 계정)
  → dom0 실제 Xen 드라이버 + 표시 수집기
  → dom0 REST / WebSocket / HDMI presentation JSON
```

브로커는 dom0 내부 브리지 주소에만 bind하고 임시 계정·토픽 ACL을 적용했다. Agent는 자기 heartbeat 토픽에만 발행하며 feed/observer는 설정된 토픽을 구독한다. SSH 포워딩은 host에서 dom0 API를 조회하는 데만 사용했고, Debian→dom0 MQTT는 SSH 우회 없이 실제 가상 네트워크를 통과했다. 운영 Mosquitto 계정/ACL 검증과는 별개다.

| 검증 | 결과 |
| --- | --- |
| 실제 등록 UUID·Xen 조회 및 토픽별 SUBACK | 통과 |
| 기존 Agent 보고 seq 1→2→3, 동일 boot/instance, retain=false | 통과. sent_at 간격 약 5초. 두 VM의 시각 동기화·전송 지연 성능은 측정하지 않음 |
| dom0 REST의 Agent fresh | 통과. 앱 상태 unknown·OS observed 미수신·current_run_verified=false 유지 |
| 인증된 dom0 WS의 동일 UUID·발행 세대 | 통과 |
| HDMI presentation JSON | app_waiting 유지. Agent 생존을 앱/그래픽 준비로 오인하지 않음 |
| 종료·정리 | Agent 정상 종료. 양쪽 임시 배치 제거. VM UUID→domid 유지, 재시작 없음 |

종료 후 dom0에서 `/tmp/display253-*/runner.py` 실행기가 없고 TCP listener가 원래 SSH만 남은 것을 추가 확인했다. QEMU와 VM은 켜 둔 상태다. 물리 HDMI 렌더링, Spring 외부 중계, Debian 앱 보고, OS inventory, 운영 자동 배포를 이번 통과 항목에 포함하지 않는다.

재현은 기존 QEMU를 부팅하고 동일 임시 배치 범위를 허용한 환경에서 수행한다. 호스트에는 httpx/passlib/websockets와 ARM64 Python 3.13용 의존 wheels가 필요하다. 도구는 기존 시험 SSH 설정·문서의 시험 계정을 사용하며 인증정보를 출력하지 않는다.

```sh
# platform/services/display/router에서 실행
python -B tests/probe_debian_agent.py --execute --wheels /path/to/arm64-wheels --output /path/to/evidence.json
```

이후 사용자가 허용한 VM2 시험까지 완료했으며 위 Zephyr 절에 결과를 기록했다. 실제 주 구동/백업 앱 매핑이나 미러링 기능을 시험용 manual 앱으로 확정하지 않는다.

## 허용된 부팅과 실제 조회 결과

사용자는 기존 QEMU 부팅과 autostart VM 시작을 허용했으며 설치·펌웨어 변경은 다시 질문하도록 했다. 28번 도구의 브랜치 제한을 수정하지 않고, 기존 command의 부팅 입력·디스크를 유지한 별도 실행으로 시작했다. 새 pid/socket/log는 `/home/ssafy/xenova-qemu-28/display253-20261022T062037Z`에 두어 이전 실행 증거를 보존했다. 기존 raw 디스크에는 정상 부팅에 따른 쓰기가 발생하며 디스크 복사/초기화/오프라인 마운트는 하지 않았다.

| 실제 확인 대상 | 조회 결과 |
| --- | --- |
| VM1 zephyr-dev | domid 3, UUID dd33284f-baf2-417f-93b2-a1af3c3f1ea2 |
| VM2 zephyr-backup | domid 2, UUID 820a0522-243f-42f1-90e9-9b74e1522965 |
| VM3 debian-dev | domid 1, UUID 33c1f55b-55dc-4b4c-9f7b-ddc247e6d5d6 |
| Xen·등록 역할 | 현재 lab-topology와 세 게스트의 UUID가 일치. Zephyr blocked는 Xen의 대기 상태이며 앱 오류/고장 판정이 아님 |
| dom0 네트워크 | xenbr0 UP, 192.168.77.1/24. 조회한 TCP listener는 SSH뿐이며 MQTT listener 없음 |
| dom0 실행 패키지 | 기본 Python에서 Paho/FastAPI/xenova_display/xenova_mqtt 모듈을 찾지 못함. mosquitto 서비스 inactive |
| Debian 신원/네트워크 | instance-id가 Xen/등록 UUID와 일치, eth0 UP 192.168.77.20/24 |
| Debian 송신 준비 | xenova-agent.service not-found, 기본 Python 및 표준 /opt 경로에서 Paho/Agent/공용 MQTT 패키지 없음. agent.env 없음 |
| Debian→dom0 브로커 | 192.168.77.1:1883 TCP 연결 불가. 네트워크 전체 장애와 동일시하지 않음 |
| Zephyr 송신 준비 | 양쪽 현재 Xen config에 nics 항목이 없음. 동일 Starter 1.0.0 펌웨어 SHA256 1b9264b36e321aa183e3f74568e59354c24c14c3bcbe3c226fd5ac9b6f699dba. topology는 콘솔 데모/주행·미러링 미구현으로 명시 |

dom0는 기존 SSH 키, Debian은 기존 문서의 시험 계정을 사용해 조회했으며 암호·전체 환경파일은 출력/기록하지 않았다. `xl console` 연결이나 게스트 입력은 수행하지 않았다. [조회 요약 증거](display-253-step3-preflight-evidence.json)를 참고한다.

이 사전 조사 이후 Debian 임시 실행과 VM2 시험용 부팅을 각각 별도로 허용받아 완료했다. 상시 자동 시작 서비스·기존 앱 원본·등록된 원본 펌웨어는 보존했고, VM2 시험 구성을 원본으로 복원했다.

## 사전 점검

- WSL Ubuntu의 `/home/ssafy/xenova-qemu-28` 시험 자산은 존재한다.
- 조회 당시 qemu-system 프로세스가 없고 `127.0.0.1:22228` SSH 포트도 닫혀 있었다. 게스트와 현재 등록 상태를 조회할 수 없다.
- `git fetch --no-tags --no-recurse-submodules origin` 후 develop `6f48259`, Linux Agent 배포 하위 브랜치 `bbb01fd`, vehicle `d4b85d1`로 1단계 조사와 동일했다. 다른 브랜치 병합·cherry-pick은 하지 않았다.
- 기존 부팅 도구는 28번 브랜치만 허용하고 이전 pid/socket/log가 있으면 중단한다. 253번에서 분기 제한을 임의 해제하거나 기존 증거를 지우지 않는다. 부팅을 허용받으면 이전 실행 기록을 보존하는 시험 실행 방법을 준비한다.
- 오프라인 `prepared-instances.json`에는 과거 Ubuntu 구성이 남아 있어 현재 VM 매핑 근거로 사용할 수 없다. 실제 실행 중 등록 목록과 Xen UUID를 대조해야 한다.
- `dom0-root` 준비 디렉터리에 Agent/Paho/Mosquitto 경로가 없었지만, 준비 디렉터리와 현재 raw 디스크·Debian 게스트 내부는 다를 수 있다. 이것으로 실제 게스트 설치 상태를 단정하지 않는다. raw 디스크는 마운트하지 않았다.

## 시험에 사용한 확인 순서

1. **부팅 범위 확인**: 사용자가 기존 QEMU 부팅을 허용해야 한다. 기존 autostart로 등록 VM이 함께 시작될 수 있음을 안내했다. 설치·펌웨어 교체는 별도 결정 대상이다.
2. **읽기 전용 조사**: dom0에서 `xl list -l`, `xenova-vm status`, `ip -br addr show xenbr0`, `systemctl is-active mosquitto`, `ss -lnt`를 조회한다. 기존 `/etc/xenova/lab-topology.json`의 역할과 실제 UUID를 대조한다. 인증정보·전체 환경변수·개인 키·콘솔 내용은 수집하지 않는다.
3. **Debian 기존 Agent 확인**: 게스트에서 instance-id와 Xen UUID 일치, `systemctl is-active xenova-agent`, `systemctl is-enabled xenova-agent`, Paho·Agent 모듈 존재 여부를 조회한다. Agent 환경 파일의 비밀번호를 출력하지 않는다. 설치/실행 기능이 없으면 필요한 변경과 담당 의존성을 먼저 설명한다.
4. **Zephyr 기존 앱 확인**: 등록 펌웨어/빌드 근거에서 실제 app_id·Ethernet netfront·TCP/소켓/MQTT 결합을 확인한다. network Starter의 TCP=n 또는 콘솔 Starter만으로 app.heartbeat 송신 가능을 선언하지 않는다. `xl console`을 표시 데이터 수집기로 점유하지 않는다.
5. **수신 관측**: 실제 발행 토픽의 구독 승인을 확인한 뒤 새 비-retained heartbeat의 UUID·type·app_id·발행 세대·증가 seq와 수신 시각을 기록한다. Debian은 5초 주기로 여러 보고를 관측하고, OS inventory가 없으면 observed OS는 미수신으로 둔다. 앱/주행 준비나 미러링 완료는 추론하지 않는다.
6. **dom0 표시 수집기 대조**: 같은 UUID·세대·보고가 feed에 반영되는지 확인한다. 계정/토픽/네트워크 오류와 앱 오류·VM 정지를 구분한다. 새로운 시험 프로세스나 임시 배포가 필요하면 범위를 정한 뒤 실행한다.

## 재사용하지 않을 시험 방식

기존 `tests/probe_qemu.py`는 게스트에 임시 발행 코드를 넣어 heartbeat와 inventory를 생성한다. 이번에는 기존 송신기를 재사용하기로 했고 Debian inventory 추가도 제외했으므로 그 시험을 그대로 실행하지 않는다. host 합성·native_sim 성공도 Zephyr domU 송신 성공으로 표기하지 않는다.

실제 송신을 확인한 Debian과 VM2만 이번 연결 증거로 사용한다. 미완성 송신/운영 통합은 [의존성 목록](../design/display-source-dependencies.md)에 남기며 다른 팀 브랜치는 병합하지 않았다. 커밋·푸시·Jira 변경은 수행하지 않았다.
