# Xenova - Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼

## 현재 Xenova 플랫폼

현재 CLI는 `xenova-vm`, 영구 저장소는 `/srv/xenova`, 데이터 LABEL은
`xenova-data`입니다. 이미지 타깃은 `xenova-xen-bringup-image`입니다.

- [Xenova 실행·빌드·최초 설치 가이드](docs/build-guides/xenova-platform.md)
- [기본 템플릿 커스터마이징·버전 게시](docs/build-guides/xenova-template-workflow.md)
- [리브랜딩 범위와 후속 검증](docs/build-guides/xenova-rebranding.md)
- [Zephyr Starter App](platform/guests/zephyr/starter-app/README.md)

## 로컬 컨테이너 스택

루트 `compose.yaml`은 Nginx, MySQL 8.4, Redis 7, Backend, 관리 콘솔, VM 모니터 화면을 함께
실행합니다. 비밀값을 저장소에 넣지 말고 `.env.example`을 복사한 로컬 `.env`에서 반드시
교체한 뒤 실행합니다.

```bash
cp .env.example .env
# .env의 change-me 값을 모두 교체
docker compose config
docker compose up -d --build
docker compose ps
```

Nginx는 `/`를 관리 콘솔로, `/screens/`를 VM 모니터로, `/api/`·`/ws/`·`/actuator/`를
Backend로 전달합니다. MySQL, Redis, Backend와 두 정적 웹 컨테이너는 호스트에 포트를 직접
공개하지 않습니다.

### Jenkins EC2 배포

Jenkins는 테스트와 이미지 빌드를 통과한 `master` push만 EC2에 배포하고, 배포 후 Nginx·두 프론트·Backend의 HTTP 응답을 확인합니다. Merge Request와 다른 브랜치는 배포하지 않습니다.

Jenkins Credentials에 다음 항목을 등록해야 합니다.

| ID | 종류 | 내용 |
|---|---|---|
| `xenova-ec2-ssh-key` | SSH Username with private key | 사용자 `ubuntu`와 EC2 PEM 개인키 |
| `xenova-ec2-known-hosts` | Secret file | 검증한 EC2 SSH 호스트 공개키 한 줄 |
| `xenova-production-env` | Secret file | `.env.example`을 기반으로 작성한 운영 `.env` |

서버에는 Docker Engine과 Docker Compose v2가 설치되어 있어야 하며, `ubuntu` 사용자가 승인 요청 없이 Docker를 실행할 수 있어야 합니다. 초기 배포 전 UFW에서 HTTP 80 포트를 허용해야 합니다. 운영 비밀값과 PEM 키는 Git 저장소에 커밋하지 않습니다.

### Jenkins 컨테이너

Jenkins는 운영 서비스와 별도인 `compose.jenkins.yaml`로 실행합니다. 전용 이미지에는 Java 21, Node.js 22, Python 3, Git, SSH, Docker CLI·Buildx·Compose와 파이프라인 플러그인이 포함됩니다. Jenkins 웹 포트는 `127.0.0.1`에만 바인딩하고, 운영 Nginx와는 공유 `xenova_app` 네트워크를 통해 `/jenkins/` 경로로 연결합니다.

```bash
cd /opt/xenova
cp deploy/jenkins/.env.example deploy/jenkins/.env
sed -i "s/^DOCKER_GID=.*/DOCKER_GID=$(getent group docker | cut -d: -f3)/" deploy/jenkins/.env
docker compose --env-file deploy/jenkins/.env -f compose.jenkins.yaml config --quiet
docker compose --env-file deploy/jenkins/.env -f compose.jenkins.yaml up -d --build
```

공개 HTTPS를 적용하기 전에는 로컬 PC에서 SSH 터널을 연 뒤 `http://127.0.0.1:8080/jenkins/`로 접속합니다.

```bash
ssh -i K15e110T.pem -L 8080:127.0.0.1:8080 ubuntu@k15e110.p.ssafy.io
```

초기 관리자 비밀번호는 EC2에서 아래 명령으로 한 번만 확인합니다.

```bash
docker compose --env-file deploy/jenkins/.env -f compose.jenkins.yaml exec jenkins \
  cat /var/jenkins_home/secrets/initialAdminPassword
```

`/var/run/docker.sock` 연결은 Jenkins 파이프라인에 호스트의 root에 준하는 권한을 부여합니다. 관리자 이외 사용자가 임의의 Jenkinsfile을 실행할 수 없도록 Jenkins와 GitLab 권한을 제한해야 합니다.
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
