# Xenova - Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼

## 현재 Xenova 플랫폼

현재 CLI는 `xenova-vm`, 영구 저장소는 `/srv/xenova`, 데이터 LABEL은
`xenova-data`입니다. 이미지 타깃은 `xenova-xen-bringup-image`입니다.

- [Xenova 실행·빌드·최초 설치 가이드](docs/build-guides/xenova-platform.md)
- [기본 템플릿 커스터마이징·버전 게시](docs/build-guides/xenova-template-workflow.md)
- [리브랜딩 범위와 후속 검증](docs/build-guides/xenova-rebranding.md)
- [Zephyr Starter App](platform/guests/zephyr/starter-app/README.md)
- 외부 Layer·버전은 `platform/yocto/layers.lock`을 따릅니다.

`docs/build-guides/perseus-*` 및 이전 `yocto-rpi4-xen-*` 문서는 당시
Perseus 소스·이미지·실보드의 역사적 기록입니다. 기존 로그·경로·해시는 유지하며
현재 Xenova 명령은 위 가이드를 따릅니다. 기존 이미지·SD를 실시간 변환하지 않습니다.

## 프로젝트 구조(기존 구성안)

아래 구성안에는 아직 구현하지 않은 항목이 포함됩니다. 현재 Yocto 빌드에 사용하는 파일 목록은 [빌드 버전 기록](docs/build-guides/yocto-rpi4-xen-build-versions.md)을 참고합니다.

```text
├── backend/                         # Spring Boot 관리 서버 (테스트: src/test/)
│
├── frontend/                        # React (npm workspaces, 테스트: frontend/ 안)
│   ├── apps/admin-console/          # 웹 관리 콘솔
│   ├── apps/vm-screens/             # 모니터 화면 (3분할·상세)
│   └── packages/ui/                 # 공통 컴포넌트·디자인 토큰
│
├── platform/                        # 보드(dom0·domU) 위에서 돌거나 보드 이미지를 만드는 것
│   ├── yocto/                       # Yocto-Xen 설정 및 패키지
│   ├── boot/
│   │   ├── rpi4/                    # 실보드 부팅 환경
│   │   └── qemu/                    # QEMU 부팅 환경
│   ├── first-install/               # 최초 설치 이미지 조립
│   ├── guests/
│   │   ├── common/                  # 공통 초기화·설치
│   │   ├── debian/                  # Debian 템플릿 제작
│   │   ├── ubuntu/                  # Ubuntu 템플릿 제작
│   │   └── zephyr/                  # Zephyr Xen 펌웨어 템플릿
│   ├── network/                     # Bridge·DHCP 설정
│   ├── services/
│   │   ├── dom0-manager/            # Python 제어 계층 (테스트: tests/)
│   │   ├── vm-agent/                # VM 에이전트
│   │   └── display/                 # 화면 라우터·키오스크
│   ├── apps/                        # VM 애플리케이션 (RC카 시나리오 앱)
│   ├── deploy/                      # dom0 서비스 등록·환경별 배포 설정
│   ├── scripts/                     # 빌드·검증 스크립트
│   └── tests/                       # 플랫폼 테스트·픽스처
│
├── vehicle/                         # 젯슨(차량 쪽, Jetson Nano ADAS ECU) 코드
│
└── docs/                            # 설계·가이드 문서
    └── contracts/                   # VM 간 메시징·에이전트·관리 API 계약 (OpenAPI 포함)
```

- [VM 관리 계약·작업 기록·보관/복원/삭제](docs/build-guides/xenova-vm-management.md)
- [dom0 Wi-Fi 관리 접속](docs/build-guides/xenova-wifi-access.md)

- [플랫폼 실행 의도·조건부 복구·종료 조율](docs/build-guides/xenova-recovery-foundation.md)
- [Linux 자원·부팅 정책·Zephyr 네트워크/종료](platform/docs/resource-boot-zephyr.md)
