# Xenova — Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼

Raspberry Pi 4 + Xen 기반의 멀티 OS 관리 프로젝트입니다.
차량 기능의 격리·장애 전환·복구를 검증하는 것을 목표로 합니다.

## 프로젝트 구조

```text
├── platform/    # Yocto·Xen 빌드, 부팅 환경, 게스트 템플릿
├── backend/     # Spring Boot VM 관리 서버
├── services/    # dom0 제어, VM 에이전트, 표시 서비스
├── frontend/    # 관리 콘솔용 예약 폴더
├── apps/        # VM 애플리케이션용 예약 폴더
├── contracts/   # 서비스 간 계약
├── deploy/      # 배포 설정용 예약 폴더
├── scripts/     # 빌드·설치·검증 실행 스크립트
├── tests/       # 자동화 테스트와 검증 자료
└── docs/        # 아키텍처·설계·계약·개발 가이드
```

## 문서 안내

| 목적 | 문서 |
| --- | --- |
| 시스템 구성과 장애 대응 | [아키텍처 설계서](docs/architecture.md) |
| 플랫폼 폴더별 역할 | [플랫폼 구조](platform/README.md) |
| 실행·빌드·최초 설치 | [플랫폼 가이드](docs/build-guides/xenova-platform.md) |
| VM 관리·작업 보존·삭제 | [VM 관리 가이드](docs/build-guides/xenova-vm-management.md) |
| 템플릿 수정과 버전 게시 | [템플릿 개발 가이드](docs/build-guides/xenova-template-workflow.md) |
| Linux 게스트 Xen 설정 | [게스트 템플릿 안내](platform/guests/README.md) |
| WiFi 관리 접속 | [WiFi 접속 가이드](docs/build-guides/xenova-wifi-access.md) |
| Zephyr 앱 개발 | [Zephyr Starter App](platform/guests/zephyr/starter-app/README.md) |
| dom0 제어 서비스 실행 | [dom0-manager README](services/dom0-manager/README.md) |
| 백엔드 실행 | [백엔드 README](backend/README.md) |
| MQTT 토픽·메시지 | [MQTT 계약](docs/contracts/mqtt-contract-v1.md) |
| 브랜치·커밋·MR 규칙 | [기여 가이드](CONTRIBUTING.md) |

## 플랫폼 기준

| 항목 | 현재 값 |
| --- | --- |
| 관리 CLI | `xenova-vm` |
| 이미지 타깃 | `xenova-xen-bringup-image` |
| 영구 저장소 | `/srv/xenova` |
| 데이터 LABEL | `xenova-data` |
| 외부 레이어·버전 | `platform/yocto/layers.lock` |

이전 `perseus-*`, `yocto-rpi4-xen-*` 문서는 당시의 기록으로 보존합니다.
현재 명령은 플랫폼 가이드를 따르며, 기존 이미지·SD는 자동 변환되지 않습니다.
명칭 변경 범위는 [리브랜딩 기록](docs/build-guides/xenova-rebranding.md)을 참고합니다.
