# 351 휴대폰 표시 통합 배포 구성 점검

- 날짜: 2026-11-04
- 최초 기준: `origin/platform` `cd571b9`, 최종 반영 기준: `5dc0c3b`
- 작업 브랜치: `feat/platform/S15P31E110-351-mobile-deployment-check`
- 범위: 로컬 설치 구성·운영 절차·정적 자산 검사. 보드·외부 서버·실제 휴대폰에는 접속하지 않았다.

## 변경

`platform/scripts/check-mobile-display-rootfs.py`를 추가했다. 빌드된 fresh rootfs에서
Python 모듈, native systemd unit, 공유 loopback feed, HDMI 휴대폰 우선 guard,
운영 문서와 컴파일된 `/display/mobile` 자산을 읽기 전용으로 점검한다.
현재 Vite 산출물의 HTML 진입점과 문자열로 참조된 로컬 자산을 검사하며,
일반적인 모든 JavaScript 의존성을 해석하는 도구는 아니다.

서비스를 실행하거나 환경 파일의 비밀 값을 읽지 않는다. 이미지에 사설 설정이나
휴대폰 우선 marker가 미리 들어가거나 mobile 서비스가 자동 활성화된 경우 거부한다.
결과의 `runtime_ready`, `usb_verified`, `physical_phone_verified`는 항상 false다.

`deploy/mobile/mobile-deployment.README.md`를 설치하도록 351 append에 추가했다.
기존 recipe와 349 append의 동작은 수정하지 않았다. 안내는 기존 HDMI 실행을 유지한 채
349 휴대폰 접속부터 확인하고, 이후 351 marker를 만들고 native HDMI 서비스만
중지하는 순서다. 전체 display target이나 공유 feed를 중지하지 않는다.

새 수집기·MQTT 연결·Xen 조회·렌더링 프로세스를 추가하지 않았다.
검사기는 빌드 담당이 필요할 때만 실행하는 일회성 도구이며 기존 runtime
`mobile_preflight`와 구분된다.

## 검증 결과

WSL Linux에서 다음 관련 테스트가 **45 passed, 1 warning**으로 통과했다.

```sh
python -m pytest tests/test_mobile_deployment_bundle.py tests/test_mobile_runtime.py tests/test_mobile_access_deploy.py -q -o addopts=
```

- 실제 기본 recipe, 351 append, 349 append의 SRC_URI 파일을 하나의 임시 UNPACKDIR에
  모으고 실제 세 설치 함수의 shell 본문을 함께 실행했다. BitBake 변수 해석을 대신한
  시험 환경이며 `chown`만 임시 root 경계 검사 후 생략했다. 제품 계정·소유권 검증은 아니다.
- 설치된 unit을 실제 `systemd-analyze verify --root`로 확인했다. 시험용 기본 target과
  Python 실행 파일을 넣어 의존 관계를 검증했고 서비스는 시작하지 않았다.
- 누락된 lazy chunk·백틱 참조 자산, 모바일 경로가 없는 이전 bundle, root 밖 자산 참조,
  외부 unit 심볼릭 링크, HDMI 종속, 사설 설정 사전 포함, mobile 자동 활성화를 거부했다.
- CLI 실행 전후 파일 내용을 비교해 읽기 전용 동작을 확인했다.
- 경고 1건은 기존 FastAPI TestClient의 httpx 사용 중단 예정 안내다.

실제 빌드된 vm-screens `dist`도 세 설치 함수와 함께 임시 rootfs에 넣어 검사했다.
349 작업의 `c509b73`와 이 브랜치 사이 `frontend` 소스 차이가 없음을
`git diff --quiet c509b73 HEAD -- frontend`로 확인한 후 해당 빌드 산출물을 재사용했다.
**13개 구성 검사 통과, 참조 자산 6개 확인, 모바일 경로 확인** 결과는
[JSON 증거](evidence/display-351-deployment-20261104/built-web-rootfs-check.json)에 보관했다.
이는 전체 앱의 브라우저 실행 시험을 추가 수행했다는 의미는 아니다.

## 배포 전·실장비에서 남은 확인

1. 최초 기준 `cd571b9`에는 349 연결 폼 출처 정책 수정 MR !391 (`c509b73`)이 없었으나,
   최종 확인한 `5dc0c3b`에는 머지되어 있다. 최종 이미지도 해당 수정이 포함된 커밋으로
   빌드해야 한다. 이 브랜치에서 수정을 복제하지 않았다.
2. 실제 빌드 환경에서 두 append 적용, FILESPATH, BitBake·패키지 QA 및 ARM 이미지 빌드를 확인한다.
   이번 점검에서는 해당 빌드를 실행하지 않았다.
3. S24 USB host 드라이버, 안정적 연결과 실제 인터페이스·IP를 확인한다.
   저장소 구성 검사만으로 USB 지원을 확정하지 않는다.
4. 기존 private 설정을 보존하고 실제 USB 값·권한으로 접속 설정을 준비한 뒤,
   번호 인증 → 화면 표시 → 만료·재접속 → HDMI 대체 → 재부팅을 확인한다.
   실제 주소·토큰·VM UUID를 예제 값으로 덮지 않는다.
5. S24 가독성·물리 분리 복귀·장시간 성능·두 HDMI 대체 시연은 352 실증으로 남는다.

351의 로컬 통합 배포 구성 점검은 통과했다. 실보드 배포·재부팅 성공이나
실제 휴대폰 연결 완료를 의미하지 않는다.
