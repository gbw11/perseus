# Zenova - Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼

## 현재 Zenova 플랫폼

현재 CLI는 `zenova-vm`, 영구 저장소는 `/srv/zenova`, 데이터 LABEL은
`zenova-data`입니다. 이미지 타깃은 `zenova-xen-bringup-image`입니다.

- [Zenova 실행·빌드·최초 설치 가이드](docs/build-guides/zenova-platform.md)
- [기본 템플릿 커스터마이징·버전 게시](docs/build-guides/zenova-template-workflow.md)
- [리브랜딩 범위와 후속 검증](docs/build-guides/zenova-rebranding.md)
- [Zephyr Starter App](platform/guests/zephyr/starter-app/README.md)
- 외부 Layer·버전은 `platform/yocto/layers.lock`을 따릅니다.

`docs/build-guides/perseus-*` 및 이전 `yocto-rpi4-xen-*` 문서는 당시
Perseus 소스·이미지·실보드의 역사적 기록입니다. 기존 로그·경로·해시는 유지하며
현재 Zenova 명령은 위 가이드를 따릅니다. 기존 이미지·SD를 실시간 변환하지 않습니다.

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
