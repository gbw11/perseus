# 표시 소스 매핑과 수신 규칙 v1

253번 2단계. 앱 이름은 유지하고, 등록한 VM UUID로 화면의 역할을 구분한다. `manual`이라는 이름만으로 수동 주행이나 백업 활성화를 판정하지 않는다.

## 설정

[시험 후보 예제](../../platform/services/display/router/examples/integration-candidate-bindings.json)는 `schema_version: 1`, `scope: candidate`, `bindings` 배열로 구성한다. 세 UUID는 합성 값이며 실제 VM 등록값이 아니다. VM1→autonomous, VM2→manual은 사용자 승인 **시험 후보**이고 실제 주·백업 배치는 담당 확인이 필요하다. VM3는 `expected_os: debian`·`role: interaction`·`app_id: interaction` 후보이다. [MQTT 계약 4장](mqtt-contract-v1.md)에서 `interaction`은 특정 앱 이름이 아닌 인터랙션 VM의 역할 ID이며, 앱 events 토픽은 `xenova/v1/apps/interaction/events`를 사용한다. 실제 앱 송신 구현·배포·보고 수신은 별도 확인 대상이다.

각 항목은 `vm_id`, `role`, `expected_os`, `app_id`, `agent_supported`를 가진다. 정확히 세 항목이 필요하고 UUID·역할·앱 ID 중복, dom0 UUID, 역할과 기대 OS 불일치를 거부한다. 기존 배열 설정은 호환되며 `binding_scope=legacy`로 표시한다. 새 형식의 `deployment`도 작성자의 분류일 뿐 실제 등록 조회에 통과했다는 증거가 아니다.

후보 설정은 기본 거부한다. 시험에만 `XENOVA_DISPLAY_ALLOW_CANDIDATE_BINDINGS=1`을 명시하고 `XENOVA_DISPLAY_BINDINGS`로 파일을 지정한다. 기존 실행 설정을 자동 교체하지 않는다. 읽기 전용 점검은 router 디렉터리에서 실행한다.

```sh
python -m xenova_display.bindings examples/integration-candidate-bindings.json --allow-candidate
```

점검기는 네트워크·VM에 접속하지 않으며 `registry_verified: false`를 출력한다. 실제 배치 전에는 UUID와 발행 app_id를 등록 정보 및 담당자와 대조해야 한다.

## 수신과 표시

| 확인 대상 | 동작 |
| --- | --- |
| 구독 | 설정된 앱 events, 지원 Agent heartbeat/inventory, 별도 표시용 drive-state만 구독 |
| SUBACK | 토픽별 pending/granted/denied/error/unavailable을 추적. 브로커 접속 성공만으로 구독 성공을 선언하지 않음 |
| 식별 | 토픽·메시지 type·등록 UUID·app_id·발행 세대·증가 seq를 기존 계약과 대조. 게스트 보고에 source_id가 함께 있으면 거부 |
| 재접속 | 새 SUBACK과 해당 연결에서 수신한 새 보고가 있어야 fresh. 이전 캐시를 정상 보고로 되살리지 않음 |
| VM 상태 | Xen stopped/stopping/paused/crashed 및 관측 단절이 이전 앱 정상 보고보다 우선 |
| 구독 실패 | VM이 running이면 앱 수신 경로 미확인 화면. VM 장애나 앱 error로 바꾸지 않음 |
| 앱 보고 | 신선한 error는 앱 오류 화면, 신선한 정상 보고는 상태 패널. ready는 보고 생존이며 주행 준비·미러링 완료를 뜻하지 않음 |
| Debian | heartbeat는 Agent 생존 근거. inventory가 없으면 observed OS는 미수신, 앱 보고 없으면 앱 상태도 미확인 |

스냅샷에는 `binding_scope`, `mqtt_subscriptions`, 소스별 `subscription_status`를 추가한다. 실제 MQTT 어댑터는 구독을 추적하며 주입 시험 소스는 untracked, 지원하지 않는 Agent는 unsupported로 구분한다. 구독 거절을 앱 미수신이나 VM 정지와 혼동하지 않는다.

UUID·토픽 대조는 발행자 인증을 대신하지 않는다. 실제 계정/ACL, domU 네트워크, 배포된 송신기, 실행 세대와 실제 VM 부팅의 대응은 별도 확인 대상이다. C 클라이언트 boot_id를 검증된 OS 부팅 ID로 취급하지 않는다. [의존성 목록](../design/display-source-dependencies.md)을 따른다.

이 단계는 수신·표시 규칙을 보완한다. 주행 모드/제어권 변경, VM 조작, 송신 코드 복제, 외부 페이지 라우팅은 수행하지 않는다. [2단계 검증](../verification/display-253-step2.md)에 합성 시험과 실제 게스트 미검증 범위를 구분했다.
