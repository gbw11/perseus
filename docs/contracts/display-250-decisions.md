# S15P31E110-250 계약 확정 기록

> **후속 사용자 결정(2026-11-02):** 별도 MQTT 표시 송신 대신 기존 Gateway의 읽기 전용 정보를 직접 읽고 미허가 선택 상태는 ‘구동 준비 중’으로 표시하도록 선택했다. [새 연결 계약](display-gateway-read-v1.md)을 따른다. 아래 MQTT 발행·ACL 합의 대기는 이전 모드의 이력이며 새 경로의 필수 작업으로 중복 추적하지 않는다. 구현·실보드 전제는 [255 검증 기록](../verification/display-255-gateway-read-20261102.md)에서 구분한다. Jira 상태를 자동 변경하지 않는다.

2026-11-02 정리. 기준 origin/platform **24b26ff**. 2026-10-22 사용자 결정과 이후 실제 보드 시험을 대조했다. 문서 정리 과정에서 보드·서비스·Jira·제어 구현은 변경하지 않았다.

## 1. 담당 범위와 현재 화면 구성

외부 웹의 페이지 라우팅은 별도 담당이며, 본인은 domU→dom0→외부 웹의 데이터·텍스트 콘솔 연결, HDMI 표시 정책·렌더링·출력, 같은 HDMI 픽셀의 원격 시연 전송을 담당한다. 표시 서비스가 차량 주행·VM 승계·재시작을 결정하지 않는다.

| 출력 | 현재 구성 | 데이터·선택 경계 |
| --- | --- | --- |
| monitor-1 / HDMI0 | 계기판 (screen=instrument) | 속도·배터리·확정 주행 모드·주요 경고·대기 연출. 미수신 값은 미확인 |
| monitor-2 / HDMI1 | 인포테인먼트·게스트 데이터 (screen=infotainment) | 네비·음악·주행 모드·현재 실행 VM·게스트 콘텐츠·오류/작업 알림. 별도 VM 상태 요약 패널은 두지 않음 |

Pi4의 HDMI0는 USB-C 전원 가까운 micro-HDMI, HDMI1은 먼 포트다. 설치 시 output_id·connector·EDID·좌표 매핑을 검증하며 XRandR와 DRM 이름을 같은 문자열로 가정하지 않는다.

2026-10-21의 ‘VM 전체/선택 VM 상세’는 변경 전 설계다. view=overview|detail은 선택 API 호환 필드이며 현재 화면의 외형이 아니다. monitor-2의 pinned/follow_active는 게스트 콘텐츠 선택에만 적용한다. 수집기와 프론트의 screen 매핑을 함께 배치한다.

VM1·VM2는 같은 Zephyr 번들의 주 구동·백업이고 VM3는 Debian이다. 각 Zephyr VM은 autonomous/manual 앱 ID를 모두 사용하며 VM UUID로 구분한다. 주행 모드와 활성 VM은 독립 값이다. 미러링 완료·주행 준비는 단순 heartbeat로 증명하지 않는다.

휴대폰 자체 렌더링 모드는 별도 검토 중인 옵션이다. 이 문서는 그 모드의 구현·채택이나 기존 HDMI 범위 대체를 확정하지 않는다.

## 2. 제어 상태: Git에서 확인한 원본과 남은 연결

### 이미 있는 권위 구현

- [MQTT 계약 5.4](mqtt-contract-v1.md#54-payload)는 Gateway의 source_id=dom0-gateway, vehicle.control의 generation/mode/active_app/active_vm_id를 정의한다.
- [AuthorityStore.payload](../../platform/services/gateway/xenova_gateway/authority.py)는 저장된 mode·owner로 제어 payload를 만든다. [VerifiedRunner](../../platform/services/gateway/xenova_gateway/verified_runner.py)는 vehicle.control을 실제 발행한다.
- [GatewayProvider.context](../../platform/services/gateway/xenova_gateway/provider.py)는 메모리의 owner·generation·mode와 phase·output_permitted·storage_uncertain 등을 읽는 경로를 제공한다. [provider 계약](gateway-provider-v1.md)의 context는 Xen/Store 추가 조회 없이 제공된다.
- [Authority 요청서](../requests/2026-10-27-authority-recovery-ctrl.md)의 2026-10-29 담당 정정은 **박신영 / 플랫폼·Gateway**다. 이는 저장소상 담당 근거이며 표시 전용 토픽 발행 합의의 증거는 아니다.

따라서 ‘활성 VM·주행 모드를 누가 결정하는지 전혀 미정’으로 기록하지 않는다. 다만 Authority의 저장된 선호 owner나 mode가 물리 구동 허가·장치 적용 완료를 뜻하지는 않는다. 특히 재시작·disarmed·PENDING 상태를 표시용 정상 주행으로 단순 변환하지 않는다.

### 표시 수신 계약과 실제 공백

사용자 선택은 별도 표시 토픽 xenova/v1/display/drive-state, type display.drive_state, 1초 주기, QoS1, retain=false다. 수신기는 매 보고 새 seq를 요구하고 3초 미수신 시 mode=unknown·active_vm_id=null로 만료한다. 기존 vehicle/control의 변경 시 발행·retained 규칙은 유지한다.

| 항목 | 기준 platform에서 확인한 결과 |
| --- | --- |
| 제어 권위 원본·구현 주체 | Gateway에 있음 |
| 표시용 토픽 수신·역순/중복/만료 처리 | 표시 수집기에 구현, loopback 합성 발행 시험 있음 |
| 표시용 토픽을 1초마다 보내는 Gateway 송신 구현 | Gateway 소스에서 찾지 못함 |
| 표시용 토픽 write ACL | [gateway ACL 블록](../../platform/deploy/mosquitto/etc/acl)에 없음 |
| provider context의 표시 어댑터 | 이 정리에서 구현하지 않음. 읽기 경로 존재를 연결 완료로 처리하지 않음 |
| 실제 보드 발행·ACL·제어 상태 연동 | 이번 문서 정리에서 조회하지 않음 |

기존 retained 메시지를 어댑터가 무조건 1초마다 재발행하면 권위 프로세스의 현재 생존·상태를 증명하지 못한다. 표시용 보고는 기존 Gateway의 검증된 현재 상태를 재사용하고, phase·출력 허가·저장 미확정·관측 실패의 의미를 담당과 맞춰야 한다. source_id 문자열은 브로커 인증을 대신하지 않는다.

남은 결정은 [제어 담당 연결 규격](display-drive-state-v1.md)의 토픽·발행 경로·신선도·상태 의미·ACL 적용 주체를 확인하는 것이다. 주행 제어를 새로 구현하거나 이번 문서 정리에서 Gateway·ACL을 변경하지 않는다. 실제 제어 보고 만료/복귀는 255, VM별 앱 보고는 253에서 검증한다.

## 3. 보드·출력 실행 가능성: 확인 완료 범위

‘보드 미준비’는 2026-10-22 시점의 이력이며 현재 실행 가능성의 미해결 사유가 아니다.

- [256 자동 시작 시험](../verification/display-256-autostart-20261028.md): Pi4에서 KMS/Xorg·Chromium 두 화면, 서비스 배치·정상 재부팅 후 자동 표시 확인.
- [252 상태 렌더 시험](../verification/display-252-dual-state-20261028.md): 두 출력에서 합성 상태 13단계씩 26항목 확인. 실제 주행 장애를 발생시킨 결과는 아니다.
- [257 최종 시험](../verification/display-257-browser-acceptance-20261102.md): 실제 두 HDMI 캡처·원격 디코드, 양쪽 물리 분리·복귀 확인.

기존 보드의 실행 묶음으로 확인했으며 Yocto 이미지 편입·모든 새 이미지 호환·최신 수정 영구 배포를 보증하지 않는다. 메모리 기본 레시피 값을 실제 보드의 현재 할당량으로 인용하지 않는다.

## 4. 원격 전송 선정·목표·측정 결과

기본 backend는 FFmpeg이며 선택형 native backend는 X11/MIT-SHM→재사용 TurboJPEG 버퍼→MJPEG/HTTP→브라우저 canvas다. 같은 실제 출력 소스의 픽셀을 전송한다. 시연 키는 feed 키와 분리하고 HttpOnly 쿠키, loopback 바인딩과 인증된 접근 경계를 사용한다. 별도 웹 대시보드 재렌더를 HDMI 전송 성공으로 취급하지 않는다.

| 항목 | 1차 기준 | 실제 보드 최종 관측 |
| --- | --- | --- |
| 해상도·FPS | 출력별 1280×720, 설정 5 FPS | 실제 약 4.91~4.94 FPS. 엄격한 수신 하한 5.000 통과로 기록하지 않음 |
| JPEG 설정 | backend별 명시 | native quality85. FFmpeg q=7과 같은 척도가 아님 |
| 영상 지연 | p95 ≤500ms 목표 | 전경 Chrome 계측 p95 상한 약 456~464ms |
| JPEG payload 합계 | ≤8Mbps 목표 | 약 4.48Mbps. HTTP/SSH 헤더 포함 전체 망 사용량과 구분 |
| 송신기+인코더 CPU | 평균 ≤1코어 | 약 0.63코어. 로컬 Chromium·전체 시스템 CPU와 구분 |
| 연속 수신 | ≥10분 | 약 602.55초, 추가 재연결·1초 이상 draw 공백 각각 0회 |
| 분리·복귀 | 출력별 식별·새 영상 복귀 | 양쪽 실제 HDMI 확인. HDMI0 복귀 시 반대편 약 1.05초 draw 간격 1회 |

지연은 원격 명령 왕복을 단순히 영상 지연으로 사용하지 않는다. source draw bracket·clock echo·브라우저 draw/두 RAF 계측을 사용했고, 120초 이하 batch의 상대 시계 속도 0.99~1.01 공학 가정을 명시했다. 물리 LCD scanout·WAN·모든 망 조건의 보장이 아니다. 장비·망·시계 제약·실패 후보·원자료는 [최종 기록](../verification/display-257-browser-acceptance-20261102.md)에 있다.

시험 대상은 상태 패널과 작은 표식 변화다. 실제 지도·카메라·게스트 동영상 전체 부하, 물리 차량 동작 통합은 별도다. 6 FPS는 비교만 했고 기본값은 5다. 뒤이어 병합된 viewer의 5초 프레임 정지 재연결 보완은 위 10분 시험보다 나중 코드이며 그 시험으로 실보드 검증했다고 주장하지 않는다.

시험 후 native 라이브러리·키오스크 후보·SSH 포트 허용을 원복했다. **저장소 병합과 보드 영구 적용은 다르다.** 상시 native 배치·unit enable·정식 접속 경로는 [배포 안내](../../platform/services/display/router/deploy/board-kms/STREAM.md)를 따라 별도 적용한다.

## 5. 250 완료 조건 대조

| 완료 조건 | 근거·판정 |
| --- | --- |
| 외부 웹 라우팅/HDMI 담당 경계 | 본 문서 1절·설계 문서에 정리 |
| 두 출력·상태 결정표·전환 인터페이스 | 계기판/인포테인먼트로 갱신. 252·254·256 근거 존재 |
| HDMI와 원격 stream 소스 식별 | 실제 X11 영역 캡처·output_id·분리 복귀 증거 존재 |
| 프로토콜·자원/화질/지연 기준·독립성 | 목표와 측정·제약 분리. 원격 실패가 HDMI 실행을 소유하지 않는 경계 정의 |
| QEMU/보드/모니터/원격 수신 구분 | 검증 문서별 환경과 범위 구분 |
| 기존 미결정인 제어 상태 전달 합의 | 원본·담당 근거는 찾음. 표시용 발행·신선도·ACL 연결 확인은 남음 |

250은 새 화면 개발보다 **계약 마감 검토 단계**다. 보드 미준비나 이미 수행한 성능 시험을 다시 미해결로 세지 않는다. 제어 연결 방식이 아직 확정되지 않았다는 기존 의존성을 삭제하거나 합의 완료로 꾸미지 않는다. 250을 합의 대기 없이 완료하도록 범위를 바꾸려면 그 결정과 후속 담당·이슈를 명시해야 한다. 이번 정리에서는 Jira 완료·범위 변경을 하지 않는다.

- 2026-11-06 변경: 사용자 결정으로 표시 수집기의 주행 상태 입력을 [Gateway 읽기](display-gateway-read-v1.md) 하나로 정리하고 위 `xenova/v1/display/drive-state` 표시 토픽 경로는 폐기했다(발행자·ACL 합의 대기 항목도 종료).
