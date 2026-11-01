# Xenova 하위 플랫폼 안내

Xenova는 Raspberry Pi 4에서 Xen으로 Linux·Zephyr VM을 실행하고 관리한다.
개발자는 이미지를 만들거나 Manager를 연동하고, 운영자는 dom0의 CLI로 등록 자산과
현재 실행을 조회·제어한다. 관리 접속은 SSH 또는 로컬/UART 콘솔을 사용한다.

## 구성 요소

| 요소 | 역할 |
|---|---|
| 부트로더·Xen | 보드를 시작하고 물리 CPU·메모리를 나누어 dom0와 domU를 실행 |
| dom0 | Xen 도구, 저장소·브리지·DHCP, 플랫폼 CLI/Manager, 관리 서비스를 실행하는 Linux |
| domU | 독립 UUID·자산을 가진 Linux 또는 Zephyr 게스트 VM |
| 플랫폼 Manager | 등록·실행 의도·신원·작업 journal을 검증하고 실제 Xen 실행을 제어하는 Python 라이브러리 |
| `xenova-vm`·`xenova-wifi` | dom0에서 사용하는 VM 관리·Wi-Fi CLI |
| MQTT broker | 내부 VM·dom0 소비자 간 메시지 전달. 이미지에 설정을 포함하고 보드별 계정은 최초에 별도 발급 |
| Tailscale | 선택적인 외부 원격 관리 연결. 최초 사용자 인증 뒤 데이터 파티션의 장치 상태를 재사용 |
| dom0-manager | 플랫폼 Manager를 사용하는 FastAPI 서비스. 하위 CLI와 HTTP의 지원 범위는 같지 않음 |
| Recovery Worker | Xen·heartbeat 관측과 기존 Recovery Engine 실행. 공통 주행 프로필은 복구 완료 뒤 실행별 Bootstrap·바인딩·Standby 준비를 별도로 연결 |
| Guest Agent·Zephyr 진단/Publisher | 게스트 내부 상태·실제 앱 진행·계측값을 제공. Xen domain 존재와 앱 정상 여부는 별개 |

`/srv/xenova`는 템플릿·등록 자산·의도·journal의 영구 저장소다. `/run/xenova`에는
이번 부팅의 실행·관측·CPU 준비 기록을 둔다. IP와 domid는 영구 신원이 아니다.
Zephyr의 실제 CPU·pool·이름·펌웨어·UTC 및 실행별 진단 기록은
[Telemetry Publisher](../../platform/guests/zephyr/telemetry/README.md)를 따른다.
MQTT의 payload 2.1 지원, 실제 보드 수락과 제품 소비자의 저장·표시는 구분한다.

## 읽는 순서

| 할 일 | 문서 |
|---|---|
| 책임·권한·식별자·생명주기의 큰 그림 복습 | [개념적 아키텍처](conceptual-architecture.md) |
| 플랫폼 구조와 요청의 수명 이해 | [관리 구조·수명주기](management-boundaries.md) |
| 기본 다섯 기능·원 작업·수습의 현재 구현/비용/근거 대조 | [기능별 기준선](../verification/platform-management-baseline.md) |
| 자원·필수 작업·전달의 건강 근거와 행동 책임 | [건강 판정 계약](../contracts/health-evidence-v1.md) |
| VM 조회·생성·시작·정지·복구·Wi-Fi 사용 | [CLI·Manager 명세](cli-manager-spec.md) |
| pending·원 요청 조회·시간 예산의 제품 연동 담당 범위 | [FastAPI·Spring·화면 연동 요청서](management-integration-request.md) |
| Linux·Agent의 자원·앱 진행·로컬 감독 연결 | [Linux·Agent 요청서](../requests/2026-10-27-linux-health-agent.md) |
| Worker의 전달 분류·건강 판단·복구/재합류 연결 | [Worker 요청서](../requests/2026-10-27-recovery-health-ctrl.md) |
| 외부 SSH 접속·최초 인증·SD 재설치 전 인증 상태 백업 | [Tailscale 사용법](cli-manager-spec.md#cmd-tailscale) |
| MQTT 최초 계정 설정·접속 범위 | [MQTT 사용법](cli-manager-spec.md#cmd-mqtt-init) |
| EC2에서 보드 MQTT·FastAPI 직접 접속 | [Tailnet 접속 설정](cli-manager-spec.md#cmd-tailscale-serve) |
| 내부 모듈·Gateway 포장·설정 공급·교체와 7번 인수 | [배포 준비](deployment-readiness.md) |
| 이미지 제작·템플릿/seed·조립·검증·전달 | [빌드 가이드](build-guide.md) |
| 부팅 서비스·자동 시작·부분 실패·종료 조사 | [부팅·종료 구조](management-boot-boundaries.md) |
| 전용·공유 CPU 배치 운영 | [CPU 배치](cpu-layout.md) |
| Zephyr 초기화·부분 실패·협조 종료 조사 | [Zephyr runtime](../../platform/guests/zephyr/xen-runtime/README.md) |
| Zephyr 계산·통신·Store 대기와 로컬 계측 이해 | [주행 공통 runtime](../../platform/apps/common/xenova-drive-runtime/README.md) |
| 같은 Zephyr 펌웨어의 모드 전환·A/B 대칭 승계 | [A/B 공통 주행 펌웨어](../../platform/apps/drive/README.md) |

처음 상태를 볼 때는 dom0에서 `xenova-vm list --short`를 사용한다. 전체 필드가
필요하면 `xenova-vm list --json`, Wi-Fi 상태는 `xenova-wifi status`로 확인한다.
변경 명령의 필수 인자와 실행 조건은 각 명령 절을 먼저 확인한다.

입력·필드·오류의 정식 정의는 CLI·Manager 명세에 둔다. 다른 문서는 개념과 절차를
설명한다. HTTP·MQTT 제품 계약은 [contracts](../contracts/)에서 확인한다.
특정 실행의 측정값·후보 해시·실패 및 검증 범위는
[관리 경계 검증 이력](../verification/platform-management-boundaries-history.md)에 보존한다.

Zephyr 초기화·종료와 부팅 표시의 후보별 시험·미검증 범위는
[Zephyr 수명주기 검증](../verification/zephyr-lifecycle-20261025.md)과
[부팅 표시 검증](../verification/boot-status-20261025.md)에 정리한다.
계산·통신·Store 대기 분리의 후보별 시험과 실보드 미검증 범위는
[대기 경계 검증](../verification/zephyr-wait-separation-20261026.md)에 정리한다.
