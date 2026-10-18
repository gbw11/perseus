# 페르세우스 - Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼

## Yocto / Xen 문서 읽는 순서

GitLab 프로젝트와 그 안의 `platform/yocto/layers.lock`을 기준으로 새 Linux 작업 경로에 환경을 구성합니다.

| 목적 | 문서 |
| --- | --- |
| WSL·Yocto 준비·커널 필수 검사·이미지 빌드·domU 통합 개발 | [재구성 및 domU 인계](docs/build-guides/yocto-rpi4-xen-reproduce.md) |
| 생성 이미지·추가 커널 진단·실보드 Xen/dom0·domU 검증 | [최소 bring-up 검증](docs/build-guides/yocto-rpi4-xen-minimal-bringup.md) |
| 빌드 구성별 버전·레이어·파일 목록 | [빌드 버전 기록](docs/build-guides/yocto-rpi4-xen-build-versions.md) |
| 이전 환경·성공 로그·검증 한계 조회 | [2026-10-15 감사 기록](docs/build-guides/yocto-rpi4-xen-audit-20261015.md) |

환경만 준비하려면 재구성 문서의 1-1~1-6을 따르고 `setup-yocto.sh --verify` 완료 후 멈추면 됩니다. 처음 이미지까지 만들려면 이어서 같은 문서의 3-1~3-3을 순서대로 실행합니다. 감사 기록은 당시의 관측 기록이며 현재 실행 절차는 재구성 문서를 따릅니다.

## 프로젝트 구조(기존 구성안)

아래 구성안에는 아직 구현하지 않은 항목이 포함됩니다. 현재 Yocto 빌드에 사용하는 파일 목록은 [빌드 버전 기록](docs/build-guides/yocto-rpi4-xen-build-versions.md)을 참고합니다.

```text
├── docs/                     # 문서

│

├── contracts/                     # VM 간 메시징 계약, Agent 통신 계약, 관리 API 계약 등

│

├── platform/

│   │

│   ├── yocto/                       # Yocto-Xen 설정 및 패키지

│   │

│   ├── boot/

│   │   ├── rpi4/                    # 실보드 부팅 환경

│   │   └── qemu/                    # QEMU 부팅 환경

│   │

│   ├── guests/

│   │   ├── common/                  # 공통 초기화·설치

│   │   ├── debian/                  # Debian 템플릿 제작

│   │   ├── ubuntu/                  # Ubuntu 템플릿 제작

│   │   └── zephyr/                  # Zephyr Xen 펌웨어 템플릿

│   │

│   └── network/                     # Bridge·DHCP 설정

│

├── services/

│   ├── dom0-manager/

│   │   └── app/

│   │         ├── api/

│   │         ├── drivers/

│   │         ├── metrics/

│   │         ├── recovery/

│   │         └── console/

│   │

│   ├── vm-agent/

│   │   ├── src/

│   │   ├── config/

│   │   └── deploy/

│   │

│   └── display/

│       ├── router/

│       └── kiosk/

│

├── apps/

│   ├── alpine-app/

│   ├── debian-app/

│   └── ubuntu-app/

│

├── frontend/

│   └── admin-console/               # React 관리 콘솔

│

├── backend/                          # Spring Boot

│

├── deploy/                          # 환경별 배포 설정

│

├── tests/

│   ├── integration/

│   ├── qemu/

│   ├── hardware/

│   └── fixtures/

│

└── scripts/

    ├── build-all.sh

    ├── build-dom0.sh

    ├── build-guests.sh

    ├── deploy-dev.sh

    └── validate-release.sh
```
