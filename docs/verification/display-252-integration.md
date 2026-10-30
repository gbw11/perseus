# 252 — HDMI 렌더링·역할·이벤트 계약 통합

현재 차량 화면은 계기판·인포테인먼트 구성이다. [2026-10-28 실보드 렌더 검증·720p 보완](display-252-board-render-20261028.md)을 최신 결과로 따른다. 아래 전체 VM/상세 화면 설명은 2026-10-26 당시 기록으로 보존한다.

2026-10-26. `platform` 66e1b3a에서 252번 브랜치를 만들고 `develop` 859f687 및 254번 커밋 d4a9fea를 반영했다. **실보드에 접속·설치·재시작하지 않았으며, 배포와 물리 HDMI 검증도 수행하지 않았다.**

## 최종 구조

- HDMI: dom0 수집기 → 254 정책 → `/display/monitor-1` 전체 상태 / `/display/monitor-2` UUID 상세 화면. 공통 Xenova 로고·디자인 토큰·상태 배지를 재사용한다.
- VM 역할: VM1 주 구동(AUTO/MANUAL 모두), VM2 동일 Zephyr 백업, VM3 Debian 인터랙션. AI/인식은 Jetson이다. 역할·모드·앱 생존·Xen 실행 상태를 분리한다.
- 기본/`display` 빌드는 Spring 로그인·주행 텔레메트리 Provider를 시작하지 않는다. `/split`·`/detail/:app`은 canonical 화면으로 이동하며 URL app 이름으로 선택 대상을 바꾸지 않는다. 실서버 Docker의 `live`/`legacy-live` 앱 미리보기는 명시적 설정으로 유지하고 안내문을 표시한다.
- 관리 콘솔은 `VITE_VM_ROLE_BINDINGS`의 명시적 UUID가 있을 때만 역할을 붙인다. 값이 없거나 잘못되면 VM 이름과 역할 미확인으로 남긴다. `autonomous`·`manual` 앱/이름으로 주·백업을 추측하지 않는다. mock은 별도 합성 UUID만 사용한다.
- 활성 구동 표시는 `active_vm_id`와 유효기간을 사용한다. 앱 생존은 일치하는 VM UUID의 heartbeat로 확인하며, 명령이 없다는 이유만으로 백업 VM을 오류/복구 중이라고 판단하지 않는다. 앱 생존을 미러링 완료·주행 준비로 표시하지 않는다.
- Spring 이벤트 타입 및 프론트 분류에 recovery.recovered/cancelled를 추가했다. completed는 새 Xen 실행 관측으로 표현하고 앱 준비·주행권은 별도 확인한다. 종료 이벤트는 오래된 복구 표시 마커만 해제하고 주행 모드를 변경하거나 정상 상태를 만들어내지 않는다.

## 보완한 문서

- [표시 경로 계약](../contracts/display-route-v1.md): 실제 로컬 정책과 legacy Spring 129번의 차이, 아직 없는 WS→로컬 정책 어댑터, DOM/물리 출력 확인 수준.
- [카메라 표시 계약](../contracts/camera-stream-v1.md): Jetson 인식과 Debian 표시 역할, MJPEG 표시 주소와 미배치 범위, HDMI 픽셀 스트림과의 구분.
- [254 검증](display-254-policy.md): 합성 상태 전이 검증. 해당 로컬 커밋은 이 브랜치에 병합했다.

## 배포 시 필요한 설정

HDMI frontend는 base `/`, `VITE_TELEMETRY=display`(또는 비움)로 빌드해 dom0 표시 서비스의 `XENOVA_DISPLAY_WEB_ROOT`로 제공한다. 신뢰된 키오스크가 서비스 Bearer로 화면 쿠키를 생성한다. 서비스 토큰을 VITE 변수·URL·번들에 넣지 않는다.

관리 콘솔의 `VITE_VM_ROLE_BINDINGS` JSON은 primary_drive/backup_drive/interaction UUID 세 개를 모두 포함한다. frontend Dockerfile과 Compose build args를 제공한다. 공개 식별자이며 비밀번호가 아니다. 프론트 빌드 값이므로 기존 컨테이너 환경만 바꿔서는 반영되지 않고 이미지 재빌드가 필요하다. 실제 UUID를 설정하지 않은 이번 로컬 빌드에서는 역할 미확인을 유지한다. 앱 ID·브로커 ACL·펌웨어는 바꾸지 않는다.

## 남은 통합 의존성

- 실제 제어 발행자·ACL·mode/active_vm_id 계약 및 물리 HDMI/시연 성능: 기존 255·256·257 검증.
- Spring에는 복구 이벤트 종류·조회 필터가 준비됐지만, 현재 Java 소스에서 recovery MQTT 소비·백필 수집기는 확인되지 않았다. 타입 추가를 실제 수신 구현 완료로 간주하지 않는다. 해당 제어/백엔드 담당과 수신 연결 및 event_id 중복 제거를 별도로 확인한다.
- Spring 129번 `/ws/v1/display-service`→로컬 정책 어댑터는 미연결이다. 기존 mock `DisplayFollower`를 실제 HDMI 성공 ACK로 사용하지 않는다.
- 실제 앱 그래픽/카메라 어댑터가 없으면 상태 패널과 연결 대기를 표시한다. 그래픽 handle을 임의 URL로 실행하지 않는다.
- 이 MR에는 최신 develop 반영이 포함되어 platform 대비 변경 범위가 크다. platform/develop의 최신 변경을 포함한 개인 작업 브랜치이며 공유 브랜치 직접 push나 자동 merge는 하지 않는다.

## 검증 결과

- 프론트 전체 107개(15 파일) 통과, 모든 workspace 타입 검사, 두 앱 production 빌드 통과.
- Python 표시 수집·정책·출력 테스트 101개 통과.
- 백엔드 복구 이벤트 필터/직렬화, 이벤트 REST/WS, 표시 상태/라우팅 검사: 28개 통과, 선택형 DisplayPipelineIntegrationTest 1개는 기본 설정에서 skip. 이 skip을 실제 통합 성공으로 계산하지 않는다.
- 프론트 전체 lint·Prettier 검사 통과(ESLint 오류 0개, Fast Refresh 관련 경고 6개). 기존 admin bundle 크기 경고 및 Python Starlette/httpx 사용 중단 예정 경고는 남아 있다.
- 브라우저에서 실제 빌드와 합성 정책 응답을 연결해 전체/상세, 같은 Zephyr 두 역할과 Debian, VM2 활성 및 상세 표시, 수신 끊김의 미확인/마지막 관측 안내를 확인했다. `/split`·`/detail/manual`이 canonical 로컬 화면으로 이동하는 것도 확인했다. 합성 입력 서버는 검증용이며 제품의 인증을 우회하는 코드로 포함하지 않는다.
- 작은 화면의 상세 패널 여백을 줄였다. 브라우저 viewport 높이 720px에서 상세 화면 높이 720px, footer 하단 약 688px로 화면 안에 표시되는 것을 DOM과 스크린샷으로 확인했다. 물리 HDMI 해상도 적용이나 모니터 검증은 아니다.

## 2026-10-28 두 HDMI 후속 검증

현재 화면은 계기판·인포테인먼트다. 상태 알림을 보완하고 두 실제 HDMI에서 합성 상태 전환·정상 복귀 26개 항목을 검증했다. 표시 테스트는 후속 변경 후 30개 통과했다. [최신 결과·정리 한계](display-252-dual-state-20261028.md)를 참고한다.
