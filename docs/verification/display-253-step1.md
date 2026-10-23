# 253번 1단계 — 기존 구현·경로 조사 기록

2026-10-22 · 조사만 수행. 수집/Agent/펌웨어/ACL 제품 코드는 변경하지 않았다.

## 조사 대상

- 작업 브랜치: `feat/platform/S15P31E110-253-display-source-collection`.
- 현재 HEAD: `e32455b67b1b9a8a772b070c12b79aea3dce7839`.
- 분기 기준: origin/platform. 250번 구현 커밋이 포함된 platform 병합 커밋이다.
- 비교용 origin/develop: `6f48259`. develop의 파일은 git show로 읽었으며 작업 트리에 병합·체크아웃하지 않았다.
- 사용자 후속 요청으로 전체 원격 ref를 fetch한 뒤에도 develop은 동일했다. `origin/feat/platform/yocto-build-setup`은 `bbb01fd`, `origin/vehicle`은 `d4b85d1`, heartbeat watcher 하위 브랜치는 `3d532f4`로 갱신됐다. 해당 ref의 파일도 조회만 했다.

실제 코드·설정·README와 수신 규격을 대조했다. [계획과 확인 근거](../design/display-source-collection-plan.md)에 소스 링크, VM별 재사용 가능 부분, 미구현 부분, 사용자 결정 8개를 기록했다. 추가 원격 브랜치/Jira 확인은 [의존성 목록](../design/display-source-dependencies.md)을 따른다.

## 확인 결과

| 항목 | 관측 |
| --- | --- |
| Zephyr 수동 앱 heartbeat | 소스에 구현됨. 1초 주기, app_id=manual. 실제 Xen 통신 성공은 이번에 시험하지 않음 |
| Zephyr 네트워크 Starter | Ethernet/DHCP/ping·협조 종료 기능. TCP=n, MQTT/heartbeat 송신 없음 |
| vchan | 비교 develop의 공용 계약에는 경로가 있으나 C MQTT vchan 포트는 조사한 코드에서 미구현 |
| Debian Agent | 실행 루프는 5초 heartbeat. 제품 inventory 수집/송신은 발견하지 못함 |
| Agent 배포의 최신 작업 | `bbb01fd`에 Linux Paho·Agent 템플릿 설치 연결 있음. develop에는 아직 미포함. 실행 코드에 inventory 추가는 없음 |
| 자율 앱의 최신 작업 | vehicle의 `da29e88`에 autonomous heartbeat 구현 있음. develop에는 아직 미포함. 실제 Xen 통신/주·백업 구조 수락은 별개 |
| Debian 앱 | message-board 이름/토픽은 계약·예시에 있으나 조사 대상 apps에 앱 송신 구현을 발견하지 못함 |
| 기존 표시 feed | 이미 구현됨. UUID/app_id 매핑·실제 발행자 연결·세대 근거를 보완하는 방향 |
| 실제 QEMU 접근 | 기존 SSH 설정으로 127.0.0.1:22228에 연결 시도 → Connection refused |
| 기존 시험 venv | `/tmp/mo04-step3-venv/bin/python` 없음. 표준 Python으로 조회 재시도 |

QEMU 접속 거절은 현재 SSH 경로에 응답이 없다는 관측이며 VM 자체의 stopped/crashed 판정이 아니다. QEMU를 새로 시작하거나 게스트·네트워크·서비스·펌웨어·개인 콘솔을 변경하지 않았다. 과거 250번 시험의 UUID/domids를 이번 현재 관측값으로 재사용하지 않는다.

## 1단계의 현재 완료 경계

기존 코드와 경로의 차이를 조사하고 다음 단계의 작업·의존성을 문서화했다. 사용자 답변으로 기존 송신 재사용/수신 연결 우선, 기존 app_id+UUID 역할, Ethernet 후보 시험, Debian heartbeat 우선·OS 미수신, 상태 패널·콘솔 연결, 앱 생존·오류와 VM 상태를 함께 반영하는 방향을 확정했다. VM1→autonomous/VM2→manual은 시험용 후보만 허용했고 실제 앱 배치는 담당자 확인 전이다. VM 변경은 나중에 다시 결정한다. 실제 MQTT·게스트 연결 시험은 이번 단계에서 새로 수행하지 않았고 기존 테스트 통과 수를 이번 결과로 재기록하지 않는다.

**1단계의 코드 조사·원격 재확인·사용자 방향 선택은 완료했다.** 후속 연결에 필요한 계약/실제 배치/실행 환경의 미확정은 의존성으로 남긴다. 이는 253번 전체나 실제 게스트 수신 완료가 아니다.

문서 링크와 Git 변경 범위를 확인한다. Jira 상태 변경·커밋·푸시·platform 변경은 하지 않는다. 기존 미추적 MO-04 인계/요청 문서와 platform/boot/qemu 도구도 수정하지 않는다.
