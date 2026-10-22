# 253번 연결 의존성과 담당별 인계

2026-10-22 · 1단계 조사/사용자 답변 반영. 원격 ref/Jira는 읽기 전용으로 확인했으며 팀원에게 메시지를 보내거나 다른 브랜치를 병합하지 않았다.

## 원격 상태 재확인

`git fetch --no-tags --no-recurse-submodules origin`을 수행했다. 이전 develop 참조와 원격의 최신 조회 결과는 **`6f48259`로 동일**하다. 현재 작업 브랜치 HEAD `e32455b`와 develop의 트리는 동일하지 않다.

| 위치 | 확인한 커밋/내용 | 현재 HEAD·develop과의 관계 |
| --- | --- | --- |
| origin/develop | `6f48259` — Zephyr network/협조 종료·플랫폼 자원/부팅 작업 포함 | Agent heartbeat 런타임과 network Starter/xen-runtime 파일은 현재 HEAD와 동일 |
| origin/feat/platform/yocto-build-setup | `bbb01fd` — Linux 게스트 Paho·Agent 설치 연결 | `bbb01fd`는 develop의 조상이 아님. 아직 develop/현재 작업 트리에 없음 |
| origin/vehicle | `d4b85d1` — 자율 앱 `da29e88` 포함 | 자율 앱은 develop/현재 작업 트리에 없음. autonomous의 1초 app.heartbeat 송신 코드 확인 |
| origin/feat/ctrl/S15P31E110-124-heartbeat-watcher | `3d532f4` — Recovery Worker·공용 MQTT SUBACK 추적 | 별도 미통합 작업. 우리 표시 수집기의 단순 connection flag와 역할/인터페이스가 다름 |
| origin/docs/common/S15P31E110-124-recovery-health-contract | `ef22339` — vm.health/복구 계약 | 선택적 후속 연동. 표시 수집 연결 완료와 실제 복구 실행을 혼합하지 않음 |

현재 원격에 별도의 `feat/agent/...` 브랜치가 광고되지는 않는다. [117번](https://ssafy.atlassian.net/browse/S15P31E110-117)은 제다빈 담당/완료이며 설명에는 과거 heartbeat 브랜치가 기록돼 있다. 그 heartbeat 소스는 develop에도 있다. 사용자가 말한 하위 작업에 해당할 수 있는 최신 확인분은 **플랫폼 브랜치의 Agent 배포 연결**이며, 이것을 새 Agent 기능/inventory 구현으로 오인하지 않는다. 개인 로컬·미푸시 브랜치의 존재나 내용은 확인할 수 없다.

Agent 배포 변경의 [실제 안내](https://lab.ssafy.com/s15-final/S15P31E110/-/blob/bbb01fd/docs/build-guides/xenova-linux-guest-agent.md)는 새 Linux 템플릿 1.0.2에 `python3-paho-mqtt`, Agent·unit·신원 gate를 설치/검사하는 경로다. 기존 VM에 자동 설치하거나 기존 게시 템플릿을 바꾸는 작업은 아니며 broker_connection=not-tested를 명시한다. Agent active/import 성공도 heartbeat의 실제 domU→dom0 도착 증거가 아니다.

## 담당별 확인 근거

| 분야 | 확인 근거 | 이 253번에서의 경계 |
| --- | --- | --- |
| Debian Agent heartbeat | [117번](https://ssafy.atlassian.net/browse/S15P31E110-117), 제다빈, 완료 | 기존 5초 송신 재사용. inventory 송신을 새로 만들지 않음 |
| Linux Agent 템플릿 배포 | 플랫폼 하위 브랜치 `bbb01fd` | 실제 적용할 템플릿/설치본·신원·서비스 준비를 해당 플랫폼 작업과 맞춤. 실제 담당은 연결 시 확인 |
| Zephyr 하위 Ethernet·템플릿 | develop/current HEAD의 network Starter와 xen-runtime | 하위 네트워크 성공과 기존 MQTT 앱 결합 성공을 구분. 통신 담당의 검증된 bundle/profile 인계 필요 |
| vchan 앱 포트 / dom0 중계 | [239번](https://ssafy.atlassian.net/browse/S15P31E110-239) / [240번](https://ssafy.atlassian.net/browse/S15P31E110-240), 공병우, 둘 다 해야 할 일 | Ethernet 후보 시험을 선택했지만 공용 vchan 계약을 임의 폐기하거나 해당 작업을 완료로 표시하지 않음 |
| 복구·건강 | [124번](https://ssafy.atlassian.net/browse/S15P31E110-124), 제다빈, 진행 중 | 앱/VM 상태의 표시와 실제 재시작 판정·복구를 구분. Worker를 새로 만들지 않음 |
| 앱 송신/최종 배치 | 수동 소스 `manual`, vehicle의 자율 소스 `autonomous` | 기존 송신 재사용. 이름만으로 VM1/2 최종 주·백업 펌웨어 배치를 정하지 않음 |

## 아직 필요한 연결 목록

| 우선순위 | 필요한 것 | 완료 근거/담당에게 받을 내용 |
| --- | --- | --- |
| 필수 | 실제 VM UUID와 app_id 매핑 | 등록 목록·현재 펌웨어/앱의 실제 app_id. VM1→autonomous/VM2→manual은 시험용 후보이며 생산 주·백업 구성을 뜻하지 않음 |
| 필수 | Zephyr Ethernet → MQTT 앱 결합 | 현재 Starter의 TCP=n을 그대로 MQTT 가능으로 간주하지 않음. TCP/소켓/MQTT 설정, netfront·기존 C 포트 결합, 자원/Store 세션 소유권, 재연결을 통신/앱 담당과 확인 |
| 필수 | 실제 Xen에서의 보고 도착 | 실제 대상 domU가 낸 고유 heartbeat가 dom0 브로커에 도달한 증거. native_sim·host 합성 시험은 별도 |
| 필수 | Debian Agent 설치·실행·신원 | 사용할 새 템플릿/기존 설치본 구분, Paho·Agent 패키지, instance-id, unit enable/active, 실제 5초 수신. Agent 설치 코드가 있다고 현재 VM에 설치돼 있다고 추정하지 않음 |
| 필수 | 브로커 주소·계정·ACL | VM/app별 실제 계정과 허용 토픽, domU→dom0 경로. 비밀번호를 문서/로그에 기록하지 않음. 네트워크 연결/구독 거부는 VM 장애가 아님 |
| 구현·실환경 확인 필요 | 구독 경로 관측 | 1단계 기준 CONNACK만 추적하던 표시 어댑터를 2단계에서 토픽별 SUBACK 추적으로 보완. pending/denied/error와 실제 앱 미수신을 구분하고 재접속 후 새 보고를 요구. 단위·loopback 시험은 통과했으며 운영 브로커 ACL/계정은 별도 확인. 124번 브랜치는 병합하지 않음 |
| 필수 | Xen 상태 + 앱 생존/오류 → 화면 정책 | running/paused/stopping/crashed/absent·관측 단절과 앱 정상/error/미수신/복귀를 교차 시험. 앱 정상 보고가 VM 정지/장애 화면을 덮지 않게 함 |
| 필수 | 실행·발행 세대 구분 | domid 재사용·앱 재시작·늦은 seq/boot를 혼합하지 않음. C boot_id는 클라이언트 생성 값이므로 검증된 실제 OS 부팅 ID로 꾸미지 않음. 플랫폼 execution_id를 연결하려면 read-only 근거/대응 계약 필요 |
| 보류 | 실제 게스트 재시작·펌웨어 교체·설치 | 사용자가 VM 변경은 나중에 결정함. 현재 QEMU SSH 경로도 접속 거절. 2단계 정적 설정/단위 시험과 실제 VM 변경 시험을 구분 |
| 후속 | 실제 OS inventory | 사용자는 heartbeat만 우선. expected_os와 observed 근거 미수신을 구분하며 Agent heartbeat나 템플릿 이름으로 관측 OS를 채우지 않음 |
| 후속 | 실제 주행 준비·미러링·승계 | 이번 범위는 앱 생존·오류 수신. 기존 autonomous/manual은 현재 최신 사용자 주·백업 모델과 의미가 다르므로 최종 앱 배치·제어 계약은 해당 담당이 확정 |
| 후속 | Debian 앱 heartbeat·그래픽 | vehicle에도 message-board는 .gitkeep만 확인. 앱 보고가 없으면 unknown. 현재 범위는 상태 패널과 기존 콘솔 연결 정보이며 그래픽 어댑터는 추가하지 않음 |
| 선택 | Worker vm.health·복구 상태 | 124번의 별도 브랜치/계약과 연동 가능. 표시 3초 stale와 Worker의 장애 판정 시간/복구 상태를 같은 의미로 표시하지 않음 |

## 다음 단계 적용 원칙

1. 기존 송신을 복제하지 않고, UUID/실제 app_id를 설정으로 연결한다. 실제 배치 확인 전 시험 설정을 운영 기본값으로 적용하지 않는다.
2. 수집기에서 앱 생존/error/미수신과 Xen 상태를 따로 유지하고 기존 policy/presentation이 이를 소비하게 한다. 주행 모드나 실제 VM 승계를 실행하지 않는다.
3. 2단계의 설정·수신 보완은 진행할 수 있지만 실제 domU 시험은 동작 중인 환경과 VM 조작 범위 확인이 필요하다. 설치/재시작을 승인받았다고 가정하지 않는다.
4. 다른 팀의 브랜치 병합·cherry-pick·패키지 설치를 이 조사 단계에서 자동 수행하지 않는다. 필요한 커밋/규격과 미통합 상태를 인계한다.
