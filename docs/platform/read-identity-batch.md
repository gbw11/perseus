# 일반 조회의 선택형 신원 배치

`XENOVA_IDENTITY_BATCH_READS=1`은 여러 실행 중 게스트를 읽는 일반 조회에서만 신원 helper의
**프로세스 실행을 묶는다.** 기본은 꺼져 있다. 변경 명령·단건 조회는 기존 경로를 유지한다.
`xenova_execution.py`와 같은 버전의 `/usr/libexec/xenova-domain-identities-native`를 함께 배포한
뒤 해당 Manager 프로세스에서만 켠다. 구버전/누락 도구를 발견했을 때 옛 조회로 우회하지 않고 실패한다.

기존 단건 helper와 같은 고정 libxenmanage ABI를 사용한다. 입력은 `--domids 1,2,3`이며,
1..256개의 중복 없는 정규 십진수 게스트 domid만 받는다. dom0는 지원하지 않는다.
성공 응답은 `schema_version: 1`, `items` 배열이며 각 항목은 기존 domid·64비트 문자열
xen_unique_id·state_bits다. 일부 실패 시 성공 바이트를 출력하지 않는다. 한 helper 프로세스가
하나의 Xen manage handle을 열어 전 항목을 읽고 닫는다. VM 생성·변경·제어 API는 호출하지 않는다.

## 보존하는 조회 경계

- 기존의 전후 두 전체 Xen 목록 대조, 각 대상의 전후 incarnation 비교를 모두 유지한다.
- batch 자체가 원자적인 Xen snapshot이라고 가정하지 않는다. 전후 목록·신원·registry/lifecycle/
  host 검증이 모두 통과해야 결과를 반환한다.
- 새 실행·이름/UUID/domID 변경·중복/누락/다른 대상·잘못된 응답·timeout은 전체 조회 실패다.
- 기존 timed_command의 같은 전체 deadline 안에서 외부 helper를 실행한다. 중단 가능한 프로세스
  경계를 없애거나 프로세스 안에 무기한 native 호출을 넣지 않는다.
- 원 관측 시각은 두 번째 batch를 시작하기 **전**의 단조 시각을 보수적으로 쓴다.
  느린 batch가 끝난 시점으로 과거 관측을 새로 갱신하지 않는다.
- 부재 대상은 기존 부재 검증 시각을 쓴다. 다른 요청·다른 단계의 신원을 캐시하지 않는다.

실행 중 VM 3개의 정상 1회 조회는 `xl.list` 2회 + 단건 helper 6회에서
`xl.list` 2회 + batch helper 2회로 줄어든다. metadata 변경으로 기존 재확인 회차가 실행되면
이 수를 초과할 수 있다. 검사 횟수를 줄인 수치가 아니다.

## 배포와 되돌리기

Yocto xenova-platform recipe가 새 C 도구를 기존 단건 도구와 함께 빌드·설치한다.
설정은 이미지에 기본 활성화하지 않는다. 배포한 두 파일의 해시와 실제 ABI·응답을 검증한 후
설치별 Manager drop-in에서만 활성화한다. 기존 읽기 fork·인증·공유 관측 경로는 유지한다.
설정 검사/reload 후 Manager API만 재시작한다. Gateway·Recovery Worker·게스트를 재시작할 필요는 없다.

혼합 설치를 방지하려면 원복 시 본인이 추가한 flag를 제거하고 Manager API를 재시작한다.
source/도구를 복원할 때 현재 해시가 본인 후보와 같은지 먼저 확인한다. 후속 담당자가 바꾼 파일을
이전 백업으로 덮지 않는다. 실행 중인 프로세스는 이전 Python 모듈을 이미 로드했을 수 있으므로
디스크 파일 교체만으로 적용·원복 완료를 판단하지 않는다.

이 기능은 3초 공유 상태 유효기간·앱 readiness·제어 권한을 변경하지 않는다.
실보드 비용·지연 수락은 253번 검증 결과와 별도 부하 측정으로 판단한다.

[2026-11-06 실보드 비교 및 남은 만료 현상](../verification/display-253-query-latency-20261106.md)에
교차 비교·실제 두 브라우저 수신·회귀 시험 범위와 환경 의존 실패를 기록했다.
