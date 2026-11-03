# 349 휴대폰 접속·인증·상태 전달 — 로컬 구현/검증

- 기준: `origin/platform` 84a0f8f, 350·351이 병합된 상태.
- 브랜치: `feat/platform/S15P31E110-349-mobile-access`.
- 사용자 확정: 임시 6자리 연결번호로 첫 접속 확인.
- 보드·실제 USB·외부 서버에 접속하지 않았다. 기존 추적 파일은 수정하지 않고 새 파일만 추가했다.

## 구현

- `mobile_network`: 명시한 USB 인터페이스와 폰 IPv4를 검증하고 보드 주소를 조회한다.
  listener는 `SO_BINDTODEVICE`로 USB에 묶는다. IP/라우트/DHCP 설정을 바꾸지 않는다.
- `mobile_upstream`: 기존 loopback feed의 모바일 HTML·허용 자산·presentation GET만 전달한다.
  토큰은 보드에 남으며, 임의 URL/제어 API/WS/HDMI ACK·redirect·proxy는 허용하지 않는다.
- `mobile_access`: 120초·1회용·최대 5회 시도의 연결번호, 단일 폰의 최대 8시간 HttpOnly 세션,
  peer/Host/Origin 확인, 초기 미연결·인증 만료·feed 오류 안내와 복귀를 구현했다.
- root 전용 runtime 파일, 중복 실행 lock, 일회성 SIGUSR1 번호 재발급과 종료 정리를 제공한다.
- 선택형 systemd 서비스·비활성 설정 예시·별도 bbappend·운영 인계 안내를 추가했다.
  351 파일/recipe·공통 frontend·기존 feed/Gateway/VM/MQTT·네트워크는 변경하지 않았다.

접속 서비스는 하나의 worker로 실행하며 중복 수집·추가 WebSocket·영상 인코더가 없다.
실제 데이터는 기존 수집기를 재사용한다. 추가 프로세스의 실보드 메모리/CPU는 아직 측정하지 않았다.

[접속 계약](../contracts/display-mobile-access-v1.md),
[실행·번호 발급·복귀·복원 절차](../../platform/services/display/router/deploy/mobile-access/mobile-access.README.md).

## 검증

환경: 로컬 WSL/Python 3.14 및 Windows headless Chrome. 실제 ARM 보드·USB·S24 시험이 아니다.

```sh
# platform/services/display/router에서
python -m pytest tests/test_mobile_access_deploy.py tests/test_mobile_access.py \
  tests/test_mobile_network.py tests/test_mobile_upstream.py tests/test_mobile_runtime.py \
  tests/test_feed.py tests/test_policy.py tests/test_gateway_drive.py tests/test_native_session.py
```

- 총 **181개 통과**, 실패·오류·건너뜀 없음. 기존 Starlette/httpx 테스트 어댑터 경고 1건.
- 신규 349 검사 78개: 접속·세션 22, USB 설정/장치 bind 21, upstream 전달 29, 배포/실행 지원 6.
- 기존 351·feed·정책·Gateway·native HDMI 회귀 103개.
- 실제 로컬 HTTP 서버로 200/404/401/403/302/503·응답 크기·proxy/redirect 미사용을 검사했다.
- USB sysfs/주소/소켓 device bind는 로컬 fixture/대역으로 검증했다. 실제 USB 네트워크로 검증한 것은 아니다.
- real asyncio SIGUSR1 callback을 전용 시험 프로세스에 보내 번호 재발급 시 기존 세션 무효화를 확인했다.
- 설치 조각을 임시 root에서 실행해 기존 351 파일 보존과 자동 설정/enable 미생성을 확인했다.
- 실제 `systemd-analyze verify --root=<임시 root>`로 새 서비스 설정을 검사했다. 시스템 서비스를 실행하지 않았다.
- 실제 Chrome에서 연결번호 HTML을 360×780·780×360으로 렌더링해 가로 잘림과 입력 칸 범위를 확인했다.
  가로 높이가 부족하면 세로 스크롤을 허용한다. 이 시각 검사는 인증 서버와 연결한 브라우저 E2E 시험은 아니다.

[세로 연결 화면](evidence/display-349-mobile-access-20261103/pairing-portrait.png),
[가로 연결 화면](evidence/display-349-mobile-access-20261103/pairing-landscape.png),
[UI 검사](evidence/display-349-mobile-access-20261103/pairing-ui-summary.json),
[테스트 요약](evidence/display-349-mobile-access-20261103/local-test-summary.json).

## 실제 적용 전에 남은 것

1. **348**: 드라이버를 포함한 이미지와 실제 케이블/포트/전원으로 USB 링크 안정성·인터페이스·폰/보드 IP 확인.
2. **349**: 확인한 값으로 root 전용 설정을 준비하고 새 서비스 기동, S24에서 실제 번호 입력·상태 수신·만료/재접속 확인.
3. **350/351**: 실제 화면 가독성과 휴대폰 우선 실행·재부팅·HDMI 대체 시연 확인.
4. **352**: USB 물리 분리/복귀·주소 변경·화면 잠금/복귀·장시간 사용·CPU/RAM·전원/발열 측정.
5. BitBake 전체 빌드·패키지 QA·ARM 이미지 부팅·실제 소켓 device binding도 아직 미수행이다.

이 작업만으로 348~352를 완료 처리하지 않는다. Jira 상태는 변경하지 않았으며 이 기록은 로컬 검증 결과다.
