# 253번 — 게스트 표시 데이터 수집 계획과 1단계 조사

2026-10-22 · `feat/platform/S15P31E110-253-display-source-collection`.
조사 기준 HEAD는 `e32455b`이며 `origin/platform`에서 분기했다. 원격 전체 ref를 갱신한 뒤에도 develop은 `6f48259`로 이전 확인과 같았다. 현재 작업 트리가 develop과 동일하다는 뜻은 아니며 develop/하위 브랜치를 병합하지 않았다. 최신 하위 브랜치 조사와 연결 의존성은 [인계 목록](display-source-dependencies.md)을 따른다.

## 목적과 경계

실제 domU의 보고와 Xen 실행 관측을 dom0에서 수신·구분해 외부 REST/WebSocket과 HDMI용 표시 데이터 양쪽이 소비하게 한다. 250번에서 작성한 표시 수집기를 재사용한다. 외부 페이지 라우팅, 실제 주행·미러링·승계 엔진, 새 xl console 점유를 수집 기능으로 추가하지 않는다.

VM1은 Zephyr 주 구동, VM2는 같은 Zephyr의 백업·미러링, VM3는 Debian 인터랙션이다. 자동/수동 모드와 활성 VM은 별개이며 실제 권위 제어 상태는 표시 전용 `display.drive_state` 계약을 따른다. 구형 앱 이름을 현재 차량 역할로 추론하지 않는다.

## 확인한 구현과 공백

| 대상 | 실제 코드에서 확인 | 남은 연결/주의 |
| --- | --- | --- |
| dom0 수집기 | [config](../../platform/services/display/router/xenova_display/config.py), [state](../../platform/services/display/router/xenova_display/state.py), [MQTT 구독](../../platform/services/display/router/xenova_display/mqtt.py) 구현 | 실제 앱/UUID 매핑, 게스트 보고 연결 필요. 기존 수집기를 재작성하지 않음 |
| Xen 실행 상태 | [별도 표시 feed](../../platform/services/display/router/xenova_display/main.py)가 읽기 전용 드라이버로 약 1초 간격 조회 | VM running은 앱 준비/OS inventory/주행 준비의 증거가 아님 |
| Zephyr 수동 앱 | [main.c](../../platform/apps/manual/zephyr/src/main.c)가 1초 주기 `app.heartbeat` 발행. [manual.h](../../platform/apps/manual/zephyr/src/manual.h)의 app_id는 `manual` | 실제 VM에서 브로커까지 연결됐다는 증거는 별도. idle/standby/active는 구형 active_app 의미이며 주·백업/모드와 같지 않음 |
| C MQTT 클라이언트 | [Zephyr 포트](../../platform/apps/common/xenova-mqtt-c/ports/zephyr/xm_zclient.c)는 Zephyr net/mqtt·소켓 사용 | 현재 네트워크 Starter와 결합되지 않음. [README](../../platform/apps/common/xenova-mqtt-c/README.md)에 vchan/posix 포트 미구현 명시 |
| 네트워크 Starter | [main.c](../../platform/guests/zephyr/network-starter-app/src/main.c)는 netfront와 협조 종료를 시작하고 diagnostics 제공 | MQTT/heartbeat 송신을 포함하지 않음. 주행 상태·물리 출력도 없음 |
| Starter TCP 설정 | [prj.conf](../../platform/guests/zephyr/network-starter-app/prj.conf)에 `CONFIG_NET_TCP=n` | Ethernet·DHCP/ping 성공을 MQTT/TCP 접속 성공으로 취급하지 않음. 기존 MQTT 앱은 TCP/소켓/MQTT 기능을 요구 |
| Debian Agent | [HeartbeatSender](../../platform/services/vm-agent/agent/xenova_agent/heartbeat.py), [실행 루프](../../platform/services/vm-agent/agent/xenova_agent/__main__.py)는 5초 주기 `agent.heartbeat` 송신 | 실제 Agent 실행 경로에 inventory 수집/송신은 없음. 메시지 수신 규격·예제와 송신 구현을 구분 |
| Agent 배포 하위 브랜치 | `origin/feat/platform/yocto-build-setup`의 `bbb01fd`에 Linux Paho·Agent 템플릿 설치 연결 추가 | 현재 HEAD/develop에는 미포함. 새 템플릿 설치 준비와 실제 기존 VM 설치/heartbeat 수신은 별개 |
| 자율 앱 하위 브랜치 | `origin/vehicle`의 `da29e88`에 `autonomous`의 1초 app.heartbeat 송신 구현 | develop/현재 HEAD에는 미포함. 기존 수동 앱과 함께 재사용 후보지만 주·백업 미러링 구현은 아님 |
| OS inventory | [공용 계약](../contracts/mqtt-contract-v1.md)에 nested `os.id`, kernel/hostname 등 정의 | 이전 [QEMU probe](../../platform/services/display/router/tests/probe_qemu.py)의 임시 inventory 송신은 Agent 제품 구현이 아님 |
| Debian 앱 | 조사한 `platform/apps`에 `message-board`의 실제 앱/heartbeat 발행 구현을 찾지 못함 | 이름이 문서/수집기 예시에 있다는 것만으로 실제 앱을 구현·배포한 것으로 간주하지 않음 |
| 계정·토픽 | [ACL](../../platform/deploy/mosquitto/etc/acl)은 app_id별 events 쓰기를 제공 | primary-drive/backup-drive 예시를 쓰면 기존 manual/autonomous 계정·토픽과 안 맞을 수 있음. 역할/앱 이름 분리 필요 |

## platform과 develop을 섞지 않는 기준

현재 platform 계열의 [MQTT 문서](../contracts/mqtt-contract-v1.md)와 [C 클라이언트 설계](mqtt-c-client.md)는 이전 주행 역할/네트워크 가정이 남아 있다. 비교한 develop `6f48259`의 MQTT v1.1에는 Zephyr vchan+dom0 중계 경로가 명시돼 있지만 같은 develop의 C MQTT README는 vchan 포트가 아직 없다고 설명한다. 문서의 선택과 실제 구현 완료를 구별한다.

현재 브랜치의 network Starter는 새로운 Ethernet 구현이다. Ethernet 후보가 있다는 사실만으로 공용 vchan 계약이 폐기되거나 기존 수동 앱의 통신이 이미 완성됐다고 결론 내리지 않는다. 1단계에서는 두 경로의 재사용 가능성과 미구현 부분을 기록하고 사용자·실제 통신 담당의 결정 전 공용 계약/제어 앱/ACL을 변경하지 않는다.

## 수신기와 발행자 사이의 구체적인 차이

1. [QEMU bindings](../../platform/services/display/router/examples/qemu-bindings.json)는 `primary-drive`, `backup-drive`, `message-board` 예시다. 기존 실제 수동 앱은 `manual`이다. 매핑을 먼저 확인하고 현재 모드나 VM 역할을 이름에서 만들지 않는다. 수집기 Binding은 현재 app_id도 세 VM에 대해 서로 달라야 하므로, 실제 주·백업 앱이 같은 app_id를 쓰기로 한다면 이 제약과 ACL/신원을 함께 검토해야 한다.
2. 수집기는 `app.heartbeat` active/standby/idle을 모두 `app_state=ready`로 정규화한다. 이것은 현재 코드의 보고 수신 표현이며 실제 주행 준비·미러링 완료의 증거로 노출하면 안 된다. 세부 준비 상태를 이번 범위에 넣을지는 사용자 결정 대상이다.
3. Linux Agent 5초 보고와 수집기의 Agent 15초 freshness는 맞는다. 앱은 1초 보고/3초 freshness다. 제어용 millisecond watchdog과 표시 유효기간을 혼동하지 않는다.
4. inventory는 retained 과거 근거다. 현재 수집기는 먼저 비-retained heartbeat로 부팅/보고 세대를 받아야 inventory를 수락한다. 발행·재접속 순서와 늦게 도착한 retained 정보는 후속 시험 대상이다.
5. C 클라이언트는 `xm_zclient_init` 때 random boot_id와 instance_id를 생성한다. 이 boot_id를 Linux `/proc/.../boot_id`나 검증된 Xen 부팅 세대와 동일시하지 않는다. [net runtime](../../platform/guests/zephyr/xen-runtime/README.md)의 timer test RNG도 인증/차량 권위 근거가 아니다.
6. 수집기의 run_id는 observer epoch + domid라 빠른 재부팅/동일 domid 재사용을 완전히 증명하지 못한다. OS `current_run_verified=false`를 유지하고 후속 단계에서 플랫폼의 실행 신원 관측을 실제로 연결할 수 있는지 확인한다.
7. 현재 수집기는 payload의 앱 heartbeat만 처리하고 `drive.command`는 상태 근거로 수용하지 않는다. 콘솔 텍스트·로그인 암호·키 입력을 OS 근거나 정상 판정용 데이터로 저장하지 않는다.

## 사용자 결정표

2026-10-22 사용자 답변을 반영했다. 다음 내용은 253번 진행 방향이며 실제 게스트 접속/팀원 계약/작동 검증의 완료를 의미하지 않는다.

| ID | 결정할 내용 | 상태 |
| --- | --- | --- |
| D1 | 송신 코드 담당 범위 | 기존 송신 재사용. 본인은 dom0 수신·정규화·화면 정책 연결 우선. 없는 송신 기능은 담당자 의존성 |
| D2 | app_id와 UUID 역할 | 기존 app_id 유지, UUID로 주 구동·백업 역할 매핑. VM1→autonomous/VM2→manual은 시험용 후보일 뿐 실제 배치는 담당자 확인 후 확정 |
| D3 | Zephyr 통신 | Ethernet 재사용 가능성을 먼저 시험. 공용 vchan 계약은 임의 변경하지 않고 팀원과 별도 확인 |
| D4 | Debian OS 근거 | 기존 heartbeat부터 연결. inventory 송신은 이번에 추가하지 않고 실제 OS 근거는 미수신 유지 |
| D5 | 표시 소스 | 상태 패널과 기존 웹 콘솔 연결 정보까지 우선. 새 그래픽 앱 영상 연결은 이번 범위에서 제외 |
| D6 | VM 변경 | 나중에 다시 결정. 기존 VM 시작/정지/재시작·펌웨어 교체/패키지 설치를 현재 승인으로 취급하지 않음 |
| D7 | 앱 상태와 라우팅 | 앱 생존·오류 우선. Xen VM 상태에 따라 화면 선택/대체 화면도 달라져야 함. 주행 준비·미러링 완료는 추론하지 않음 |
| D8 | 담당과 최신 브랜치 | Zephyr 템플릿/하위 통신과 Debian Agent는 담당 분리. 원격/Jira를 조회한 구체적 근거·연계 이슈는 인계 목록에 기록. 개인의 미푸시 작업은 확인 불가 |

250번에서 확정한 VM 역할·모니터 구성·영상 목표를 다시 결정하지 않는다. 실제 승계/모드 권위 발행자의 팀 합의와 실보드 그래픽 확인은 기존 미완료로 유지한다. 이 1단계는 해당 합의를 대신하지 않는다.

## VM 상태와 앱 상태가 화면 선택에 미치는 영향

아래는 250번의 기존 [표시 정책](../../platform/services/display/router/xenova_display/policy.py)을 수집 연결 시 지켜야 할 기준으로 명시한 것이다. 1단계에서 새 정책 코드를 추가한 것은 아니다.

| Xen/입력 조건 | 앱 보고 | 표시·선택 기준 |
| --- | --- | --- |
| 최초 Xen 상태 미수신 | 무엇이든 | 상태 수신 대기. 앱만으로 VM running을 생성하지 않음 |
| Xen 관측 지연·단절 | 마지막 값 존재 | 지연/끊김 안내. running/정상·승계를 새로 확정하지 않음 |
| stopped / stopping / paused / crashed | 과거 정상 보고 존재 | 해당 VM 상태의 화면이 우선. 과거 앱 보고로 정상 화면을 유지하지 않음 |
| running | heartbeat 미수신·만료 | VM 실행 중 / 앱 상태 미확인 대체 화면 |
| running | 신선한 error | 앱 오류 화면. VM 전체 장애·실제 승계로 단정하지 않음 |
| running | 신선한 active/standby/idle | 앱 보고 정상. 주행 준비·활성 구동 VM·미러링 완료의 증거로 사용하지 않음 |
| 권위 drive 보고로 active VM 변경 | fresh | pinned 상세는 유지, 명시적 follow_active에서만 대상 변경 |

따라서 2~5단계에서는 정상 보고 수신뿐 아니라 각 Xen 상태가 앱 정상 보고보다 우선하는지, 앱 보고 복귀 시 올바른 화면으로 돌아오는지를 함께 검증한다. 253번이 차량 제어권이나 실제 복구를 실행하지 않는다.

## 다섯 단계와 완료 증거

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| 1 | 기존 코드·경로·식별/주기 차이 조사, 사용자 선택/미결정 목록 | 이 문서와 [조사 기록](../verification/display-253-step1.md). 조사 완료와 결정 대기를 구분 |
| 2 | 환경별 VM UUID·실제 app_id 매핑, 발행자/수신 규격 보완 | 구현 및 합성 시험 완료. [2단계 결과](../verification/display-253-step2.md). 후보/실제 배치 분리, 잘못된 VM·토픽·세대 거부 및 SUBACK·재접속 시험. 실제 UUID 배치는 미확정 |
| 3 | 선택된 경로로 실제 게스트 보고 연결 | 대표 경로 검증 완료: 실제 Debian 기존 Agent 및 VM2 Zephyr heartbeat-only 시험 이미지→dom0 브로커/수집기. [실행 기록](../verification/display-253-step3.md). VM1 송신·상시 배포·실보드 검증은 포함하지 않음 |
| 4 | 미수신·중복/역순·재접속·재시작·콘솔 비점유 검증 | 허용된 시험 환경의 장애/복귀·세대별 기록. 오래된 값 정상화 금지 |
| 5 | 외부 REST/WS와 HDMI presentation 양쪽 소비 확인 | 같은 UUID·소스·보고 내용이 양쪽에 전달되는 증거와 미지원/미검증 목록 |

실제 게스트를 쓸 수 없다면 synthetic MQTT 시험 성공을 실제 Zephyr domU→dom0 완료로 표시하지 않는다. 보드 HDMI·원격 시연 수락은 별도 이슈 범위를 유지한다.
