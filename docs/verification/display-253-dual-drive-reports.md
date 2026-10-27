# 253: VM별 두 앱 보고 수집 모의 검증

2026-10-27. 작업 브랜치 `feat/platform/S15P31E110-253-display-source-collection`.

## 결과

display router 전체 Python 시험 **127 passed**. 기존 Starlette TestClient/httpx 사용 중단 예정 경고 1건이 있다. 새 `tests/test_dual_drive_reports.py`는 39개 사례를 추가한다.

```sh
# 저장소 루트에서 의존성이 설치된 가상환경 사용
python -m pytest platform/services/display/router/tests
```

합성 UUID 후보 설정과 fake clock, Xen 드라이버 대역, MQTT 구독 클라이언트 대역을 사용한다. 실제 `MqttSource.on_message`와 정규화 저장소, 표시 정책, FastAPI 인증 REST/WS 경로로 연결해 확인했다. MQTT 패킷 브로커 통합 시험은 아니며 QoS/retain/SUBACK은 콜백 입력으로 재현했다.

## 확인한 동작

- VM1·VM2 각각 autonomous/manual 네 보고를 별도 저장. 같은 앱 이름·순번·송신 인스턴스 ID가 다른 VM이나 앱을 덮지 않는다.
- 새 토픽의 UUID/앱 ID 불일치, 이전 전역 토픽 우회, 설정 혼합을 거부한다.
- heartbeat 3초·readiness 300ms 만료, 앱 하나 미수신/오류/재시작, MQTT 연결 해제·재구독 거부를 반영한다.
- 보고 타입 사이에서도 앱 토픽의 공통 seq를 검사하고 늦은 heartbeat/readiness를 거부한다.
- 동일 progress READY 재전송은 신선도를 연장하지 않는다. NOT_READY 취소, 역행 거부, uint64 최대 progress 보존을 확인했다.
- readiness의 필드·정수/문자열 범위·크기·QoS·retain 제약을 검증했다.
- Xen pause/실행 교체 시 보고를 정상으로 표시하지 않으며 퇴역 boot/송신 세션을 거부한다.
- 인증된 REST/WS에서 앱별 자료를 제공하고 비인증 REST를 거부한다. 준비 보고로 활성 VM·주행 모드가 생성되지 않으며 실행/권한 검증 플래그는 false다.
- 이전 단일 앱 설정과 기존 수집기·정책 시험도 통과했다.

## 이번에 하지 않은 작업

실보드 접속·배치, 펌웨어 빌드/교체, 브로커 설치/설정, Bootstrap·root 바인딩 적용, VM 재시작은 하지 않았다. 기존 실보드 검증 자료와 별개의 모의 수신 결과다. 새 상세 UI와 HDMI 디자인도 변경하지 않았다.

다음은 확정 소스의 펌웨어 해시와 인증된 브로커·Bootstrap·실행 바인딩을 준비하는 배포 준비 단계다. 현재 수집 결과만으로 실제 주행 준비·승계 권한 검증이나 253번 전체 실보드 완료를 주장하지 않는다.
