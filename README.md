# Xenova - Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼

Xenova의 CLI는 `xenova-vm`, 영구 저장소는 `/srv/xenova`, 데이터 LABEL은
`xenova-data`입니다. dom0 이미지 타깃은 `xenova-xen-bringup-image`입니다.

## 빌드

[**Xenova 빌드 가이드**](docs/platform/build-guide.md)

환경 준비부터 Ubuntu·Debian·Zephyr 템플릿 준비·게시, dom0 빌드,
seed 생성, 최종 이미지 조립·검증과 인계까지의 정규 절차입니다.
빌드 절차가 바뀌면 이 가이드를 갱신합니다.

## 주요 문서

- [Linux 자원·부팅 정책·Zephyr 네트워크/종료](platform/docs/resource-boot-zephyr.md)
- [domU xl 설정 계약](docs/platform/domu-xl-config-v1.md)
- [VM 관리 API 계약](docs/contracts/vm-api-contract-v1.md)
- [dom0 Manager OpenAPI](docs/contracts/dom0-manager-openapi.yaml)
- [MQTT 계약](docs/contracts/mqtt-contract-v1.md)

## 프로젝트 구조

```text
├── backend/                         # Spring Boot 관리 서버
├── frontend/                        # 웹 관리 콘솔·VM 화면·공통 UI
├── platform/
│   ├── yocto/                       # Yocto 레이어·설정·recipe
│   ├── boot/                        # Raspberry Pi·QEMU 부팅 환경
│   ├── first-install/               # 최초 설치 이미지 조립
│   ├── guests/                      # Linux·Zephyr 템플릿·공통 신원 준비
│   ├── network/                     # Bridge·DHCP 설정
│   ├── services/
│   │   ├── dom0-manager/            # 보드 관리 API
│   │   ├── vm-agent/                # 게스트 Agent·공용 MQTT 클라이언트
│   │   └── display/                 # 화면 라우터·키오스크
│   ├── apps/                        # VM 애플리케이션
│   ├── deploy/                      # 서비스 등록·배포 설정
│   ├── scripts/                     # 빌드·게시·검증 진입점
│   └── tests/                       # 플랫폼 회귀·픽스처
├── vehicle/                         # Jetson 차량 애플리케이션
└── docs/
    ├── platform/                    # 빌드 가이드·플랫폼 계약
    └── contracts/                   # 관리 API·메시징 계약
```
