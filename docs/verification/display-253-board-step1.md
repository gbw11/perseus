# 253번 실보드 1단계 — VM 식별·역할 설정·Xen 상태 대조

관측: 2026-10-27 14:35:16 KST. 대상: `root@192.168.30.96` Raspberry Pi dom0. 기존 SSH 호스트 키 대조 후 접속했다.

이 커밋은 253번의 실보드 검증 기록만 추가한다. 실제 보드 시험 소스는 `70c3ed8` 기반의 별도 배치 `/opt/xenova-display-probe/20261027-70c3ed8-6695dc`였다. 256·257번 구현을 이 브랜치에 병합한 결과가 아니다.

**결과: 이번 1단계 검사 통과. 253번 전체 완료를 뜻하지 않는다.**

| 구분 | 실제 Xen 이름 | 실제 UUID | 현재 domid | 확정 역할과 설정 대조 | 수집기 Xen 상태 |
| --- | --- | --- | --- | --- | --- |
| VM1 | zephyr-drive-a | 64b1ab38-89cc-4454-8d94-b8201f4cdd33 | 2 | primary_drive 일치 | running / fresh |
| VM2 | zephyr-drive-b | a77fa3fc-64e5-4d66-8225-2a49920bda2a | 3 | backup_drive 일치 | running / fresh |
| VM3 | debian-dev | 055226af-b876-4707-8b5d-99e7f33ad3d7 | 1 | interaction 일치 | running / fresh |

## 실제 수행

1. 실보드에서 `xl list`, `xl list -l`로 실제 도메인 목록·UUID·현재 도메인 번호·상태를 조회했다. dom0는 게스트 검사에서 제외했다.
2. 보드의 `/opt/xenova-display-probe/20261027-70c3ed8-6695dc/config/bindings.json`을 읽었다. 실제 UUID를 기준으로 기존 확인 UUID·사용자 확정 역할·기대 OS 설정과 대조했다.
3. 실보드에 설치된 `dom0_manager`의 `xen` 조회 드라이버로 실제 목록을 읽고, 배치한 표시 수집기의 `StateStore`에 넣어 정규화 결과를 확인했다. HTTP 서버를 추가로 실행하지 않았다.
4. 약 2초 후 실제 Xen 목록을 다시 조회해 이름·UUID·domid의 일관성을 확인했다.

UUID·domid 중복 없음, 예상 게스트 정확히 3대, 표시 바인딩 3개, 3대 모두 running/fresh 확인.

## 해석과 제한

- UUID는 VM 식별에 사용하고, domid는 현재 Xen 실행에 붙은 번호다. 이후 재생성 시 domid가 바뀔 수 있으므로 역할 식별자로 쓰지 않는다.
- `xl list`의 3개 게스트 상태는 `-b----`였다. Xen blocked/대기 상태를 수집기에서 running으로 정규화한 것으로, 이것만으로 앱 장애나 정지를 뜻하지 않는다.
- 역할은 사용자 확정 배정과 설정의 일치 증거다. Xen이 주행 제어권이나 백업 미러링 완료를 입증한 것이 아니다.
- Zephyr/Debian은 기대 OS 설정만 대조했다. 실제 OS inventory·앱 실행 정상·백업 준비·활성 제어권·HDMI 표시를 확인한 것은 아니다.
- 바인딩 프로필은 `candidate`다. VM 역할은 사용자 확정됐지만 autonomous/manual/message-board 앱 ID의 운영 배치 매핑을 확정했다는 뜻이 아니다.
- 이번 검사는 기존 파일을 읽기만 했다. 보드 파일·설정·VM 상태·서비스를 변경하지 않았다. Python bytecode 기록도 비활성화했다.
- 다음 검증은 실제 게스트의 앱/Agent 보고가 dom0까지 도착하는지 확인하는 것이다. 아직 이번 검사에서 MQTT나 게스트에 접속하지 않았다.

기계 판독 근거: [실제 Xen 조회와 대조 결과](evidence/display-253-board-20261027/board-identity-step1-20261027.json). 검사 스크립트 `board_identity_step1.py`는 작업공간의 로컬 `outputs/display-253/`에 보관했다. SSH 비밀번호·서비스 토큰은 기록하지 않았다.

후속: [Debian 실제 보고 수신 확인](display-253-board-step2.md).
