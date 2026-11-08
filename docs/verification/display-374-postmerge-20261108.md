# 374 병합 코드 적용·연속 수신 시험 — 2026-11-08

**Execution 한 파일 적용과 API·Recovery Worker 반영은 끝났지만, 374는 미완료다.** 약 141초 동안 HTTP 203개는 모두 200이었으나 VM source가 fresh 134 / stale 13 / disconnected 56으로 나타났다. 종료 점검의 VM 목록 API에서도 503이 관측돼 정상 연속 freshness 수락을 통과하지 못했다.

## 기준·승인·사전 확인

MR !469와 vm-stress-integration이 모두 병합된 `platform` `1cd50cf3`을 기준으로 했다. 현재 작업 브랜치는 `feat/platform/S15P31E110-374-freshness-recheck`이며 `git merge --ff-only origin/platform`으로 맞췄다. 다른 작업 브랜치를 수정하지 않았다.

사용자는 한 파일 적용·API/Recovery Worker 각 1회 재시작·120초 수신 시험을 승인했다. 적용 직전 보드 해시를 다시 대조했고 VM 세 대와 healthz·VM 목록 API가 정상이었다. 관련 Management/binding/telemetry/Coordinator/Worker/Gateway 파일 8개는 병합 소스와 일치해 유지하고 Execution만 교체했다.

- 대상: `/usr/lib/xenova/xenova_execution.py`
- 이전 SHA256: `8538dadda458e190e7b597b39e6c90bc47a1dc312640c11c98639f91ffd2d368`
- 적용 SHA256: `5ac73bbc1c284db17c1a57249fc62f985db62fe18e9aa13a346f611eb5a03ea5`
- 원본/후보/manifest: `/etc/xenova/display-374-integrated-1cd50cf3-20261108/`, root 전용.

원본 해시·소유권·정규 파일·후보 문법을 검사하고 동일 디렉터리 임시 파일의 rename으로 교체했다. 대상 및 다른 관련 파일 해시가 달라졌으면 적용하지 않는 guard를 사용했다. 원본 백업은 보드 private 위치에 보존했고 원본 소스 내용은 PC로 반출하지 않았다.

두 MR 병합 후 로컬에서 경합·A/B 관측 64개 및 하위 검증 5개, Coordinator/Worker/warm liveness 116개가 통과했다. 앞선 367개 통합 후보 결과와 구분해 현재 조합의 집중 검증을 다시 수행했다.

## 재시작·로드 근거

재시작 직전에 사용자에게 알렸고 다음 명령을 각각 한 번 실행했다.

```bash
systemctl restart xenova-dom0-manager.service
systemctl restart xenova-recovery-worker.service
```

두 명령은 exit 0, 서비스는 active/running이었다. API PID는 826360→1278686, Worker는 861652→1278905로 바뀌었다. 갱신된 Python bytecode 캐시의 소스 timestamp·크기가 현재 파일과 맞고, 적용 뒤 healthz·VM 목록 모두 200, 기존 세 UUID는 running이었다. 이는 새 소스로 재시작하고 정상 호출을 확인한 근거이며 프로세스 메모리 전체를 검증한 것은 아니다.

## 연속 수신 방법

정상 보드 표시 자산을 PC loopback 임시 중계기로 열었다. 서비스 토큰은 보드 내부 HTTP 중계에만 사용했고 브라우저에 제공하지 않았다. SSH 포트 허용 목록은 변경하지 않고 SSH stdio 채널에 묶인 임시 relay 프로세스를 사용했다.

실제 두 브라우저에서 `/display/monitor-1`, `/display/monitor-2`를 열었고 현재 제품 동작에 따라 `/display/combined`로 이동했다. 물리 HDMI 두 출력 검증은 아니다. 두 페이지가 로딩된 뒤 기록을 시작했고 120초 미만은 저장하지 못하도록 시험 도구에 하한을 뒀다. 초기 로딩 화면에서도 VM 상태 미확인을 관측했다. 이를 최종 기록의 정상 표본으로 바꿔 세지 않았다.

측정 중에는 이 브라우저가 요청하는 presentation 응답만 수집했다. 별도 VM 목록·상태 진단 API·MQTT 소비자는 추가하지 않았다. 오류 구간을 잘라내거나 통과할 때까지 반복 측정하지 않았다.

## 결과

| 항목 | 결과 |
| --- | ---: |
| 기록 시간 / 첫~마지막 응답 간격 | 141.297 / 140.032초 |
| presentation 응답 / HTTP 200 | 203 / 203 |
| VM source fresh / stale / disconnected | 134 / 13 / 56 |
| MQTT 연결·구독 granted | 203 / 203 |
| Gateway fresh·SAFE_STOP | 203 / 203 |
| 만료/관측 불가 VM의 current 앱 잔류 | 0 |
| 비정상 source 뒤 fresh 복귀 표본 전환 | 17 |
| 관측된 최대 VM source age | 10204ms |
| HTTP 중계 RTT 중앙값 / p95 / 최대 | 359 / 625 / 922ms |

집계는 응답 단위이며 세 VM의 source 상태는 각 응답에서 같았다. `disconnected`는 이 source의 관측 불가 상태다. HTTP·MQTT·브라우저 전송 연결이 끊어졌다는 뜻이 아니다. RTT 역시 영상 지연이 아니다.

API 재시작으로 공유 producer epoch가 `c8d11b98-...`에서 `6f341bfd-...`로 바뀌었다. 기록 내 세 VM의 run_id는 각각 새 producer epoch를 포함한 한 값으로 유지돼 구 producer 식별자로 돌아온 표본은 없었다. current 앱은 정상 구간에서 일부 수신됐고 만료 구간에는 남지 않았다. 별도 게스트 재시작을 유도한 시험은 아니므로 모든 이전 실행 혼입 경계를 실제로 재현했다고 해석하지 않는다.

## 원인 확인 범위

시험 뒤 기존 관측 파일의 오류 메타데이터와 journal을 읽기만 했다. 해당 시점의 세 VM last_error는 비어 있고 이후 정상 성공 시각이 있었다. 서비스 journal 설정은 journal이었으나 제한된 최근 구간과 현재 API PID 조회 모두 항목 0개여서 상세 실패 원인을 확보하지 못했다. 로그가 없다는 결과를 오류가 없었다는 근거로 쓰지 않는다.

과거 시험보다 비정상 source 표본이 많지만 부하·시각·동시 작업·재시작 후 상태가 통제되지 않았다. 이 차이를 오류 순서 보호 코드의 회귀나 시계 모듈의 직접 원인으로 단정하지 않는다. 로컬에서 입증한 늦은 실패 보호와 실보드의 모든 부하에서 3초 연속 갱신은 별개의 수락이다.

## 종료 상태

- API/Worker는 새 PID로 계속 active/running이다. 재시작은 각 1회였고 추가 재시작은 하지 않았다.
- 조회한 관련 9개 파일은 종료 시 병합 소스 해시와 모두 일치했다. 변경한 live 파일은 Execution 하나다.
- feed/Gateway/MQTT/Tailscale PID, 나머지 플랫폼 파일 해시, SSH 정책 해시, host boot ID는 전후 같았다.
- VM 제어·펌웨어·CPU 배치·Gateway·MQTT·Tailscale·토큰·Spring DB 설정 변경 명령은 실행하지 않았다.
- 마지막 healthz는 200, VM 목록은 503이었다. 끝 시점의 목록 조회 실패를 VM 종료로 해석하지 않으며, 최종 세 VM running을 다시 증명했다고 기록하지 않는다.
- 두 시험 탭, PC 서버, 임시 SSH stdio relay와 SSH 진단 연결을 종료했다.
- 승인된 병합 소스와 root 전용 원본 백업을 보드에 유지했다. manifest는 시험 종료·수락 미완료로 기록했다. 임의의 추가 수정이나 원복·재시작을 하지 않았다.

## 남은 작업

다음에는 VM 원본 조회 완료→검증된 관측 게시→feed 수신의 어느 구간에서 기한을 넘기는지 구분할 계측 근거가 필요하다. 기존 읽기 전용 계측이 제공하는 범위를 먼저 확인하고, 진단 패치나 다른 모듈 변경이 필요하면 담당 범위와 영향을 제시해 별도로 조율한다. TTL·CPU·팀원 내부 기한을 임의로 늘려 통과 처리하지 않는다. 이후 같은 두 소비자 조건에서 120초 연속 freshness를 다시 수락해야 374/253 마감을 판단할 수 있다.

[구조화 증거](display-374-postmerge-20261108.json). 원시 수신/상태/배포/종료 결과는 `outputs/display-374-postmerge-20261108/`에 보관했다. 이번 작업은 아직 커밋·푸시하지 않았고 Jira 상태도 변경하지 않았다.
