# 250 계약 문서 정리 — 2026-11-02

## 범위

기준 platform 24b26ff에서 docs/platform/S15P31E110-250-contract-closeout을 분리했다. 현재 동작 코드·운영 ACL·보드·외부 서버·Jira 상태는 변경하지 않는다. 문서 갱신은 새 실보드 시험이 아니다.

## 정리 내용

- [결정 기록](../contracts/display-250-decisions.md): 현재 담당 범위·계기판/인포테인먼트·제어 권위 원본·전송 목표와 실제 측정·남은 계약 확인을 한 곳에 정리했다.
- [설계](../design/hdmi-display-plan.md): 예전 전체/상세 UI, 보드 미준비, 실행 스택 미선정 표현을 현재 근거로 갱신했다.
- [데이터 계약](../contracts/display-pipeline-v1.md): 현재와 단계별 이력을 구분하고 native backend의 선택형 배포·측정 경계를 추가했다.
- [주행 표시 연결 규격](../contracts/display-drive-state-v1.md): Gateway의 원본 구현·담당 근거와 아직 없는 표시용 연결을 구분했다.
- [초기 후속 시험](display-250-followup.md): 2026-10-22 당시 기록임을 표시하고 최신 결정으로 연결했다. 과거 evidence를 다시 쓰지 않았다.

## Git 조사와 결론

다음 읽기 전용 검색으로 기준 커밋을 대조했다.

```sh
git grep -n -E 'active_vm_id|display.drive_state|display/drive-state' 24b26ff -- docs/contracts platform/services platform/apps backend
git show 24b26ff:platform/services/gateway/xenova_gateway/authority.py
git show 24b26ff:platform/services/gateway/xenova_gateway/provider.py
git show 24b26ff:platform/services/gateway/xenova_gateway/verified_runner.py
git show 24b26ff:platform/deploy/mosquitto/etc/acl
git show 24b26ff:docs/requests/2026-10-27-authority-recovery-ctrl.md
```

Gateway는 이미 mode·owner·generation을 결정·보존하고 vehicle.control로 전달한다. provider context도 현재 상태와 출력 허가·저장 불확실성을 제공한다. 요청서는 담당을 박신영으로 명시한다. 따라서 결정 주체를 처음부터 찾거나 표시 서비스에 승계·모드 판단을 새로 만들 필요가 없다.

그러나 표시 수신기가 기다리는 display.drive_state의 1초 발행은 Gateway 코드에 없고 해당 write ACL도 없다. 저장소에 있는 원본·담당 근거가 별도 토픽 발행 동의·운영 배포 완료를 입증하지 않는다. 저장된 owner·mode를 실제 구동 허가와 동일시하지 않고, 확인 가능한 현재 상태를 표시용으로 어떻게 전달할지 기존 담당과 연결해야 한다.

## 완료 판단과 다음 작업

250의 문서상 담당 경계·두 출력 구성·상태 결정표·전환 규격·원격 소스 식별·1차 성능 기준·환경별 검증 근거는 정리했다. 실제 보드 가능성 확인을 미해결로 다시 세지 않는다.

남은 계약 검토는 아래 세 가지로 좁힌다.

1. 기존 Gateway 원본을 재사용하는 표시용 전달 경로와 구현 담당.
2. phase·output_permitted·storage_uncertain 등을 포함한 active_vm_id·mode의 표시 의미와 미확인 처리.
3. 선택 경로의 1초 보고·3초 만료·인증/ACL·재시작 세대 규칙 적용 주체.

현재 별도 표시 토픽은 사용자 선택과 수신 코드 기준이다. 다른 기존 읽기 경로로 바꾸려면 규격·수신기·인증·freshness 시험을 함께 조정한다. 이번 문서 정리에서는 런타임 경로를 바꾸지 않는다. 합의가 확인되면 결정 기록을 갱신하고 250을 마감 검토한다. 실제 보고·단절·복귀 통합은 253·255에서, 영구 native 배포와 최신 viewer 보완의 재검증은 257에서 추적한다. Jira 완료나 별도 범위 축소는 이번에 수행하지 않는다.

## 검증

변경 Markdown의 UTF-8 읽기, 로컬 상대 링크 대상 존재, 코드 fence 균형, git diff --check를 확인한다. 문서만 변경하므로 코드 테스트나 보드 시험을 새로 실행하지 않는다. 해당 시험들은 기존 252·256·257 증거를 링크한다.
