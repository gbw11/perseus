# Xenova - Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼

Xenova의 CLI는 `xenova-vm`, 영구 저장소는 `/srv/xenova`, 데이터 LABEL은
`xenova-data`입니다. dom0 이미지 타깃은 `xenova-xen-bringup-image`입니다.

## 빌드

[**Xenova 빌드 가이드**](docs/platform/build-guide.md)

환경 준비부터 Ubuntu·Debian·Zephyr 템플릿 준비·게시, dom0 빌드,
seed 생성, 최종 이미지 조립·검증과 인계까지의 정규 절차입니다.
빌드 절차가 바뀌면 이 가이드를 갱신합니다.

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

운영 Spring은 MQTT를 활성화하고 Mosquitto의 전용 계정을 사용합니다. `.env`의
`MQTT_BROKER_URL`, `MQTT_USERNAME`, `MQTT_PASSWORD`를 실제 배포값으로 교체해야 하며,
정상 연결되면 Backend 로그에 `MQTT connected`와 `MQTT subscribed`가 차례로 남습니다.

Nginx는 `/`를 관리 콘솔로, `/screens/`를 VM 모니터로, `/api/`·`/ws/`·`/actuator/`를
Backend로 전달합니다. MySQL, Redis, Backend와 두 정적 웹 컨테이너는 호스트에 포트를 직접
공개하지 않습니다.

### Jenkins EC2 배포

Jenkins는 테스트와 이미지 빌드를 통과한 `master` push만 EC2에 배포합니다. Merge Request와 다른 브랜치는 검사만 수행합니다.

Jenkins Credentials에 다음 항목을 등록해야 합니다.

| ID | 종류 | 내용 |
|---|---|---|
| `xenova-ec2-ssh-key` | SSH Username with private key | 사용자 `ubuntu`와 EC2 PEM 개인키 |
| `xenova-ec2-known-hosts` | Secret file | 검증한 EC2 SSH 호스트 공개키 한 줄 |
| `xenova-production-env` | Secret file | `.env.example`을 기반으로 작성한 운영 `.env` |

서버에는 Docker Engine과 Docker Compose v2가 설치되어 있어야 하며 `ubuntu` 사용자가 승인 요청 없이 Docker를 실행할 수 있어야 합니다. UFW에서 HTTP 80과 HTTPS 443 포트를 허용하고, 운영 비밀값과 PEM 키는 Git 저장소에 커밋하지 않습니다.

Standalone 방식으로 발급한 Let's Encrypt 인증서는 `deploy/certbot/renewal-hooks`의 pre/post hook으로 Nginx의 80번 포트를 잠시 비운 뒤 자동 갱신합니다. 두 스크립트를 `/etc/letsencrypt/renewal-hooks`의 동일한 하위 폴더에 설치하고 실행 권한을 부여한 뒤 `sudo certbot renew --dry-run`으로 검증합니다.

### Jenkins 컨테이너

Jenkins는 운영 서비스와 별도인 `compose.jenkins.yaml`로 실행합니다. 전용 이미지에는 Java 21, Node.js 22, Python 3, gcc·CMake·Make(C 호스트 시험), Git, SSH, Docker CLI·Buildx·Compose와 파이프라인 플러그인이 포함됩니다. Jenkins 웹 포트는 `127.0.0.1`에만 바인딩하고, 운영 Nginx와는 공유 `xenova_app` 네트워크를 통해 `/jenkins/` 경로로 연결합니다.

```bash
cd /opt/xenova
cp deploy/jenkins/.env.example deploy/jenkins/.env
sed -i "s/^DOCKER_GID=.*/DOCKER_GID=$(getent group docker | cut -d: -f3)/" deploy/jenkins/.env
docker compose --env-file deploy/jenkins/.env -f compose.jenkins.yaml config --quiet
docker compose --env-file deploy/jenkins/.env -f compose.jenkins.yaml up -d --build
```

초기 관리자 비밀번호는 EC2에서 아래 명령으로 한 번만 확인합니다.

```bash
docker compose --env-file deploy/jenkins/.env -f compose.jenkins.yaml exec jenkins \
  cat /var/jenkins_home/secrets/initialAdminPassword
```

`/var/run/docker.sock` 연결은 Jenkins 파이프라인에 호스트의 root에 준하는 권한을 부여합니다. 관리자 이외 사용자가 임의의 Jenkinsfile을 실행할 수 없도록 Jenkins와 GitLab 권한을 제한해야 합니다.

## 주요 문서

- [하위 플랫폼 구성과 문서 안내](docs/platform/README.md)

- [Xenova CLI·Manager 기능 명세서](docs/platform/cli-manager-spec.md)
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
