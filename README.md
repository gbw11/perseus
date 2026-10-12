# 페르세우스 - Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼



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

│   │   └── alpine/                  # Alpine 템플릿 제작

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

&#x20;   ├── build-all.sh

&#x20;   ├── build-dom0.sh

&#x20;   ├── build-guests.sh

&#x20;   ├── deploy-dev.sh

&#x20;   └── validate-release.sh
