# 251번 1단계 — 코드와 연결 환경 조사

2026-10-23 · `feat/platform/S15P31E110-253-display-source-collection`의 현재 로컬 작업 트리 기준.

**기존 구현 조사와 연결 준비 목록 작성을 완료했다.** 사용자 답변으로 외부 웹이 실행 중인 주소를 받았고, 추가 SSH 주소는 실보드 dom0임을 확인했다. 외부 서버↔보드 연결 방식은 아직 확정하지 않았다. 상세 구성은 [251번 계획](../design/external-web-relay-plan.md)에 있다.

## 확인한 내용

- 상태 REST/WS 중계와 MO-04 대화형 콘솔은 이미 구현돼 있다. 별도 신규 공개 API나 콘솔을 만들 필요가 있는지는 연결 시험에서 확인한다.
- 상태 경로는 표시 수집기, 콘솔 경로는 dom0-manager에 각각 접근해야 한다. 둘의 주소와 서비스 토큰 설정은 별개다.
- 프론트 개발 프록시는 `/api`·`/ws`를 지원한다. 실제 외부 서버의 정적 배포/HTTPS/프록시 구성을 증명하는 설정은 조사한 파일에서 확인하지 못했다. 실제 인프라나 팀원 브랜치의 존재 여부를 판단한 것은 아니다.
- Spring 콘솔은 Spring DB에 등록된 실행/일시정지 VM만 연결한다. bindings UUID와 Spring 등록 목록의 대응을 확인해야 한다.
- 현재 로컬 admin-console의 기존 콘솔 코드는 재사용할 수 있다. display 상태의 실제 화면 연결은 프론트 담당 코드 확인이 필요하다.

## 현재 QEMU 접속 확인

다음 읽기 전용 명령으로 기존 dom0 SSH 진입점만 확인했다.

```sh
ssh -F /home/ssafy/xenova-qemu-28/keys/ssh_config \
  -o BatchMode=yes -o ConnectTimeout=3 xenova-qemu-dom0 "xl list"
```

결과는 `127.0.0.1:22228`의 **Connection refused**였다. 현재 이 진입점으로 QEMU dom0에 접근할 수 없다는 관측이며, VM의 실제 실행 상태·장애 원인을 확인한 것은 아니다. QEMU를 부팅하거나 포워딩/서비스를 시작하지 않았다. 이전 253번의 VM 실행 ID나 서비스 종료 기록을 오늘의 실시간 실행 상태로 재사용하지 않는다.

## 수행 범위와 남은 결정

Git은 `git branch --show-current`, `git status --short`로만 조회했다. 기존 253번 미커밋 작업을 보존하고 새 조사 문서 두 개만 작성했다. 제품 코드·프론트 화면·VM·펌웨어·DB·서버 설정·터널·방화벽은 변경하지 않았다. 원격 fetch·브랜치 전환·커밋·푸시·Jira 변경도 하지 않았다.

사용자는 외부 서버에서 Spring 또는 웹이 이미 실행 중이며 관리 화면 `http://k15e110.p.ssafy.io/`, VM 화면 `http://k15e110.p.ssafy.io/screens/`을 사용한다고 답했다. 기존 VPN/터널·외부 서버 SSH·인프라 담당은 여전히 미확인이다. 실제 설정/배포에 의존하는 2단계는 시작하지 않았다. 이번 단계는 문서/코드 조회이므로 기존 통합 시험을 다시 실행하지 않았다.

## 추가 SSH 대상 확인: Raspberry Pi dom0

사용자가 제공한 `root@192.168.30.96`에 SSH 비밀번호 인증으로 접속해 다음 항목만 읽었다. 비밀번호는 파일·명령행 인자·Git에 저장하지 않았다.

| 관측 | 결과 |
| --- | --- |
| hostname / 아키텍처 | `raspberrypi4-64` / `aarch64` |
| OS | Poky 6.0.4 (wrynose) |
| Xen 권한 | `/proc/xen/capabilities`가 `control_d` |
| Xen 도메인 | Domain-0, debian-dev (domid 1), zephyr-drive-a (2), zephyr-drive-b (3). UUID·앱 보고·실제 역할은 이번 조회에서 확인하지 않음 |
| TCP listener | SSH 22 및 `127.0.0.1:8081`. 표시 수집기 기본 8082와 MQTT 기본 1883은 미관측 |
| SSH 호스트 키 | 최초 관측 SHA256 `xuqfEeu0Ilfz3NvCd1ffCP74KMUIOmEEp7kDGxGeYPw`. 별도 신뢰 경로로 대조한 것은 아님 |

사용한 원격 명령은 `hostname`, `uname -m`, `cat /etc/os-release`, `cat /proc/xen/capabilities`, `command -v xl`, `xl list`, `ss -ltn`이다. 외부 웹 서버가 아니라 실제 Xen dom0임을 구분했다. 로컬 SSH 접속 성공이 외부 서버의 보드 접근 성공을 뜻하지 않는다. 서비스 응답/인증·콘솔 활성화·실제 heartbeat·외부 웹 연동은 미검증이다. 보드의 서비스 시작·설치·재시작·설정 변경·VM 조작은 하지 않았다.
