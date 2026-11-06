# 253 — 정상 수집 서비스 배포와 공유 관측 지연 확인

2026-11-05. 최신 `platform` d459ee5에 MR !431이 반영된 것을 fetch로 확인했다.
작업 브랜치는 `feat/platform/S15P31E110-253-normal-deployment`다.
[실측 JSON](display-253-normal-deployment-20261105.json)을 함께 기록한다.

## 정상 서비스에 남긴 구성

이전 시험의 `/run` 임시 수집기 대신 기존 `xenova-display-feed.service`를 loopback 8082에서 실행했다.
자동 시작은 원래 enabled였으며 마지막에도 enabled/active를 확인했다. Manager와 feed 모두
`/run/xenova/vm-observations.json`을 사용한다. feed가 별도 Xen 드라이버를 만들지 않는다.

| 보드 경로 | 내용 |
|---|---|
| `/etc/xenova/display-bindings.json` | 실제 등록 UUID 3개와 역할, 두 drive VM의 warm-drive-1 명시 |
| `/etc/xenova/display.env` | 기존 observer 읽기 계정, 별도 feed 토큰, Gateway transport와 공유 파일 경로. root:root 0600 |
| `/etc/systemd/system/xenova-dom0-manager.service.d/253-observations.conf` | 공유 writer와 검증한 `XENOVA_PLATFORM_READ_FORK=1`. root:root 0600 |
| `/etc/xenova/display-253-receiver/` | root 전용 0700, 원본 코드 백업·배포 manifest·임시 SSH 설정의 원본 백업 |

기존 콘솔 활성화·토큰·VM/펌웨어·CPU 할당·MQTT 계정/ACL·Gateway·Recovery Worker·Tailscale은 변경하지 않았다.
읽기 계정은 기존 broker의 export-account 기능으로 사용했다. 초기 export용 중복 credential 파일은 최종 삭제했다.
Git·결과 JSON·브라우저 코드에는 토큰/비밀번호를 넣지 않았다.

설치된 수집기 4개 파일은 원본 해시를 대조·백업한 뒤 머지된 코드로 적용했다.
이번에 보완한 `main.py`, `shared.py`도 원본/신규 여부를 확인한 뒤 적용했다.
그 밖의 표시 모듈은 초기 해시와 같았다. 이미지 파일 전체나 팀원의 하위 관리 코드를 덮지 않았다.
**공유 파일 추적 최적화는 보드에 적용했으며 이 작업 브랜치에 포함했다. platform 머지는 별도다.**
다음 이미지에도 남기려면 이 변경을 검토·머지해야 한다. 기존 Yocto recipe의 Python 모듈 설치가
새 `shared.py`도 포함하므로 새 시스템 패키지 의존성은 필요 없다. 실제 UUID·계정·토큰 설정은 설치별 자료다.

## 지연을 나눠 확인한 결과

공유 파일의 `observed_mono`와 파일 수정 시각을 함께 읽었다. 데이터의 원 관측 시각은 바꾸지 않았다.
아래 값은 같은 보드에서 순차로 측정한 짧은 표본이며, 부하를 고정한 인과 성능 비교는 아니다.

| 30초 공유 파일 표본 | 기본 읽기 설정 | 읽기 요청 분리 설정 |
|---|---:|---:|
| 표본 수 | 118 | 118 |
| 평균 원 관측 나이 | 2,040ms | 1,449ms |
| 최대 원 관측 나이 | 5,004ms | 4,528ms |
| 3초 만료/불가 표본 | 18 | 7 |

변경 전부터 파일 자체가 늦게 갱신되는 구간이 있었다. 따라서 원인을 화면 WebSocket만으로
해석하지 않았다. 기존 공개 Manager의 읽기 전용 list를 진단용으로 한 번씩 호출했다.
마지막 호출은 약 1.50초, 그 안의 외부 명령 8회 합계는 약 0.81초였다.
`xl.list` 2회 약 0.21초와 `xenova-domain-identity` 6회 약 0.60초를 확인했다.
이 합계는 중첩 trace를 중복 합산한 값이 아니다. 첫 진단은 외부 Trace가 내부 요청 Trace로 교체돼 비었고,
두 번째에는 기존 `Manager.last_trace`를 읽었다. 진단 프로세스는 종료됐고 지속 진단 서비스를 추가하지 않았다.

읽기 요청 분리 옵션을 켜기 전에 보드의 xenova_management/execution/runtime 세 소스 해시를
최신 platform과 대조했다. 기존 동시 읽기 지원 코드와 같았으며, 해당 옵션 관련 기존 회귀 시험 7개도 통과했다.
이 옵션은 새 요청 객체에서 일반 조회를 수행한다. 변경 명령의 직렬화·guard를 제거하지 않는다.
상위 조회 지연과 보드 CPU 경합 가능성은 남았다. 이 자료만으로 모든 지연의 원인이나 해결을 확정하지 않는다.

## 표시 수집기 자체의 보완

수집기는 공유 파일 갱신 후에도 기존 1초 조회까지 기다릴 수 있었다. 다음과 같이 바꿨다.

- 250ms 간격으로 inode·수정 시각·ctime·권한 등 파일 변경만 확인한다.
- 새 파일일 때만 기존 ObservationFile의 소유권·부팅 ID·형식·원 관측 시각 검증과 JSON 읽기를 수행한다.
- 같은 파일을 반복해서 읽거나 시간을 새로 찍지 않는다. StateStore가 원래 관측 시각으로 3초 만료를 계산한다.
- 삭제·권한 변경·불가 프레임은 실패로 처리하고 새 유효 파일에서 복귀한다. Xen/Manager 우회 조회는 없다.
- 한 번에 하나의 읽기만 진행하며 느린 호출 뒤 따라잡기 요청을 중첩하지 않는다.

최종 25회 API 표본은 두 실제 화면 브라우저 소비자가 연결된 상태에서 측정했다.
HTTP 응답은 25회 모두 200, VM 관측은 fresh 19회·stale 6회였다.
만료된 6회는 앱 lifecycle current를 모두 비우고 표시 결정도 stale로 반환했다.
네 앱 current가 모두 유효한 표본은 17회였다. 나머지 fresh 표본 중 일부는 개별 앱의 300ms 신선도 때문에
current가 2개/3개였다. **이 결과를 전 구간 안정·지연 해결 완료로 기록하지 않는다.**

## 정상 서비스 재시작·실제 소비 경로

표시 feed만 재시작했다. source_epoch가 `2b4cbd2e-3091-4388-9ca9-b0c40a70b84c`에서
`acd5fb4e-fb0f-4725-b931-2f4c209d710c`로 바뀐 뒤 같은 실제 VM 3대의 보고 수신을 확인했다.
정상 8082의 상태 WS도 HTTP 101, sequence 3→4 두 프레임, 세 UUID와 네 앱 보고를 전달했다.

보드의 기존 빌드된 vm-screens 자산을 수정하지 않고 읽었다. HTML 501바이트, 주 JS 479,967바이트와
presentation JSON의 정상 응답을 확인했다. PC loopback 임시 뷰어를 통해 다음을 실제 브라우저에서 확인했다.

- `/display/monitor-1`: 표시 연결됨, 드라이버 계기판, 안전 정지 모드/SAFE_STOP.
  속도·배터리는 수신 대기였다.
- `/display/monitor-2`: 표시 연결됨, 인포테인먼트·게스트 데이터, 세 VM의 켜짐,
  VM1의 ‘앱 보고 수신 · 일부 준비 대기’, 설정 Zephyr와 OS 보고 미수신의 구분.
  네비·음악·오류/복구 콘텐츠는 미수신 상태로 표시됐다.

시험 뷰어의 최초 502는 새 이미지 SSH 허용 목록에 loopback 8082가 없어서 발생했다.
이전에 허용된 API 터널 범위 중 8082만 임시 추가했다. 빈 본문은 시험용 뷰어의 HTTP/SSH 연결 종료 처리였으며
수정 후 정상 보드 화면을 읽었다. 두 현상을 수집 서비스 장애로 잘못 기록하지 않는다.
뷰어는 읽기와 출력 ACK만 허용했고 서비스 토큰은 PC의 서버 메모리에서만 사용했다.
끝난 뒤 SSH 원본 해시·sshd 설정 검사·임시 포트 제거를 확인하고 뷰어와 SSH 연결을 종료했다.
외부 공개 포트·Tailscale 설정·Spring DB·외부 서버 설정은 바꾸지 않았다.

이번 실제 브라우저 소비 시험은 **물리 HDMI 두 포트의 출력/자동 시작 시험이나 외부 Spring 배포 시험이 아니다.**
계기판·인포테인먼트 페이지가 공통 정상 feed를 소비하는 것을 검증했다.
실제 HDMI 실행 설정과 휴대폰 연결은 이번에 변경하지 않았다.

## 마지막 상태와 마감 판단

- Manager와 표시 feed가 active, feed는 enabled였다. 설정 파일은 0600, 백업 폴더는 0700이었다.
- 인증된 Manager VM 목록은 첫 시도 HTTP 200, 세 VM 모두 running이었다.
  Xen domid 1/2/3과 실행 incarnation도 기존과 같았다.
- Mosquitto/Gateway/Recovery Worker/실행 로그 수집기/Tailscale PID는 시작 전과 같았다.
  Gateway는 generation 1, SAFE_STOP, DISARMED, output_permitted=false를 유지했다.
- 정상 배포한 수집 서비스와 설정은 의도적으로 유지했다. 임시 뷰어·SSH 규칙은 유지하지 않았다.
- 표시 수집기 전체 416 passed, 1 skipped. 읽기 fork 관련 기존 시험 7 passed.
  Starlette/httpx 사용 중단 예정 경고 1개가 있으며 관련 의존성은 변경하지 않았다.
- Jira 상태 변경은 하지 않았다. 변경 검토와 platform 머지는 별도다.

253의 실제 수신·정규화·재연결·공통 API/WS·화면 소비 근거는 확보했다.
그러나 최종 표본에도 원 관측 지연/만료가 남아 있고 추적 최적화 코드의 검토·머지도 남았다.
**253 전체 완료 처리는 보류한다.** 하위 검증 조회·실행 스케줄링의 지연을 관련 담당과 조율하고,
동일 조건으로 재측정해야 한다. 신원 검사를 줄이거나 3초 기준을 늘려 통과시킨 결과가 아니다.

정상 구성의 확인 명령은 다음과 같다. 토큰을 출력하거나 명령 인자로 붙이지 않는다.

```sh
systemctl status xenova-dom0-manager.service xenova-display-feed.service
systemctl is-enabled xenova-display-feed.service
journalctl -u xenova-display-feed.service -n 40 --no-pager
```

원복이 필요하면 해당 설치의 root 전용 manifest·백업을 기준으로 현재 파일 해시를 먼저 대조한다.
후속 작업자가 바꾼 파일을 이전 백업으로 강제로 덮지 않는다. 공유 writer 설정을 제거할 때는
API가 원래 상태로 돌아오도록 설정 reload 후 Manager API를 재시작해야 한다.
