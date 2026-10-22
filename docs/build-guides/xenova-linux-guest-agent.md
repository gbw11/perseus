# Linux 게스트 Agent 패키징

## 적용 경계

dom0의 Yocto Paho 패키지는 domU에 설치되지 않는다.
새 Ubuntu·Debian 템플릿의 recipe와 기본 선택 게시 버전은 1.0.2이며 Agent 패키지 정책을 포함한다.
새 factory Debian도 1.0.2를 사용한다. 기존 1.0.1 게시본·설치된 VM·Zephyr A/B·개인 Wi-Fi 설정은 변경하지 않는다.
편집 중 recipe가 선택된 게시 버전보다 앞설 수 있으며, factory 버전은 선택 lock과 계속 일치해야 한다.
새 게시본의 검증과 manifest가 생기기 전에는 default-templates.lock.json의 해시를 추정해서 갱신하지 않는다.

실행 경로:
`prepare-xenova-guests.py template → template_recipe → prepare_template
→ guest_agent_template.prepare → chroot apt → 기존 install_into_rootfs.install
→ verify_guest_agent → boot_assets → 소스 재대조 → 새 템플릿 게시`.

## 패키지 설치와 출처

두 recipe는 `guest_agent: {"packages": ["python3-paho-mqtt"]}`를 선언한다.
지원 목록 밖 패키지는 거절하며 설치는 새 작업 rootfs에서만 수행한다.
배포판의 기존 서명 검증과 apt source를 유지한다. 별도 저장소·pip 설치·전체 upgrade는 추가하지 않는다.
네트워크·DNS·패키지 설치 실패 또는 timeout은 생성 실패이며 기존 게시본을 대체하지 않는다.

apt 동안에만 policy-rc.d로 서비스 실행을 막고 호스트 resolver를 임시 제공한다.
성공·실패 모두 기존 resolver 링크·파일과 policy-rc.d 내용·모드를 복원한다.
이번 apt 명령의 전용 설정에서만 추천 명령·GUI·MOTD 등의 Update/Post-Invoke 후처리를 제외한다.
배포판 apt 설정 파일은 유지하며 명령 전용 임시 설정은 종료 시 제거/원복한다.
패키지 서명·의존성 해석과 dpkg maintainer script는 그대로 실행한다.
apt timeout/실패 시 자신이 시작한 프로세스 그룹을 회수한 뒤 임시 설정을 복원한다.
이 과정에는 외부 배포판 패키지 저장소 접근이 필요하다.
준비 중 Agent 서비스와 Worker·앱은 실행하지 않는다.

준비 입력 해시에는 공통 준비 코드, Agent installer·unit·identity drop-in,
Agent와 공용 MQTT 패키지 파일·pyproject가 포함된다.
`/etc/xenova/agent-install.json` 및 template.json의 `guest_agent`에는 설치 버전,
이번 다운로드 deb 해시, 소스 해시와 import·enable 검사 결과를 기록한다.
이는 mutable apt 저장소를 영구 고정한 lockfile은 아니다. 재현 시 기록된 패키지와 원본 cloud image를 보존해야 한다.

## 신원과 두 생성 경로

- 일반 VM: 템플릿 복사 후 UUID·machine-id·SSH 키를 준비하고 instance-id를 기록한다.
  identity unit의 Requires를 추가하지 않는다.
- Factory seed: machine-id·SSH 키를 공유하지 않고 identity gate가 최초 부팅에 신원을 만든다.
  Agent 설치가 있는 seed에만 동일 identity Requires/After drop-in을 추가한다.
- 공통 make_instance는 SSH 검사에 이어 Agent import·enable·신원 의존성을 검사한다.
  실패하면 새 instance/seed를 게시하지 않는다.
- Agent가 없는 기존 템플릿은 legacy로 구분하며 자동 설치·재초기화하지 않는다.
  이미 설치된 VM의 패키지·신원 변경은 별도 승인된 업데이트 절차다.

## 준비·검증 명령

아래 템플릿 생성은 ARM64 실행이 가능한 Linux 빌드 PC에서 새 출력 경로로 실행한다.
이는 dom0/최종 이미지 빌드 명령이 아니다.

```sh
python3 platform/scripts/prepare-xenova-guests.py --execute template \
  --source ubuntu-24.04-20260926 --input /absolute/verified-ubuntu.qcow2 \
  --output /absolute/new-ubuntu-1.0.2
# Debian은 --source debian-13-20261015와 해당 고정 원본을 사용한다.
```

mount된 새 rootfs의 읽기 전용 검사:

```sh
python3 platform/scripts/check-xenova-guest-agent.py --root /absolute/rootfs
python3 platform/scripts/check-xenova-guest-agent.py --root /absolute/seed-rootfs --seed
```

실제 Xen 게스트 내부에서는 이 저장소의 검사 도구와 prepare_rootfs.py를 함께 전달한
시험 사본에서 `python3 platform/scripts/check-xenova-guest-agent.py --live`를 실행한다.
도구는 import·enable·instance-id·현재 unit active 상태를 확인하며 서비스나 Broker 연결을 시작하지 않는다.
소스 없는 설치본을 단순히 PASS 처리하지 않는다. 설치 전후 파일 해시와 시험 VM UUID를 보관한다.

Broker 연결·heartbeat 수신은 주소·인증·서버 준비 후 별도 시험한다.
Agent active/import 성공은 앱 준비·차량 운행 허가·복구 완료와 별개다.

## 검증 범위와 인계

자동 시험은 실제 준비·installer·판정 코드를 호출하며 apt/chroot·loop 경계만 대체한다.
실제 Ubuntu/Debian unit은 systemd 로더로 일반 VM/seed의 ordering·Requires를 대조한다.
ARM64 chroot import는 실제 Python 사용자 공간 검사이며 Xen 기동·PID 1 시험을 대체하지 않는다.
현재 작업의 실행 결과와 실보드 미검증은 별도 실행 보고서에 기록한다.

prepare_instance/prepare_rootfs는 dom0 recipe에 이미 설치되는 공통 파일이므로
정식 배포 시 dom0 재빌드가 필요하다. 새 host 전용 guest_agent_template 모듈은 dom0에 설치하지 않는다.
새 Linux 템플릿과 factory seed를 검증·게시한 뒤 선택 manifest를 갱신하고 최종 조립 검사를 수행한다.
기존 dom0 WIC와 구형 템플릿 조합을 이 변경의 통합 완료본으로 취급하지 않는다.
