# Xenova 명칭 통일 — 2026-10-19

현재 공식 표기는 `xenova` / `Xenova` / `XENOVA`다.
작업 브랜치는 `feat/platform/S15P31E110-28-domu-xl-template`이며
`platform`·`develop` 브랜치와 원격 저장소는 수정하지 않았다.

## 변경 범위

- 기존 표기가 있던 텍스트 파일 112개를 변경했다.
- 파일·디렉터리 이름 80곳을 변경했다.
- Yocto 레이어는 `platform/yocto/meta-xenova`다.
- 명령·모듈·서비스·환경변수·부트 설정·문서·테스트 참조를 함께 변경했다.
- 영구 저장소는 `/srv/xenova`, 런타임은 `/run/xenova`, 설정은 `/etc/xenova`다.
- Jira 28의 OS별 cfg 연결, 예제, 설치 레시피도 같은 표기를 사용한다.
- 수정된 Starter 소스의 app_files·app_source_sha256을 실제 바이트로 다시 계산했다.

기존 작업은 저장소 밖 ZIP으로 백업했다. Git 이력과 바이너리 fixture·Maven wrapper,
외부 OS/커널/SDK의 고정 버전·원본 다운로드 체크섬은 그대로 보존했다.

## 이번 변경의 검증

- 전체 플랫폼 unittest 344개 실행: 342개 통과, 2개는 WSL에 `cc`가 없어 오류.
  해당 항목은 Starter main 실행 검사와 고정 Xen reset 소스 컴파일 검사다.
  테스트를 건너뛰도록 소스를 수정하거나 결과를 통과로 처리하지 않았다.
- 부팅·init·네트워크 회귀 17개 통과.
- 실제 systemd 259.5 로더로 정상 의존성 그래프 통과 및 주입한 순환 검출 확인.
- 관련 bash/sh 문법 검사 통과.
- 소스의 기존 표기·파일명 잔여 검색과 공백 검사를 수행했다.

전체 실행 로그는 작업 공간의 `xenova-platform-check-20261019.log`에 있다.
별도 이미지 빌드·조립·SD 쓰기·실보드 부팅·배포·커밋·푸시는 수행하지 않았다.

## 산출물 재생성 경계

현재 카탈로그의 manifest_sha256은 기존에 게시된 payload를 식별한다.
명칭 변경만으로 새 payload의 해시를 알 수 없으므로 값을 임의로 바꾸지 않았다.
새 명칭의 이미지·Linux 템플릿·Starter 펌웨어를 준비·빌드·게시한 뒤,
실제 새 manifest로 기본 카탈로그 잠금을 갱신해야 한다.
기존 바이너리·WIC나 실행 중인 보드가 이 소스 변경으로 갱신되는 것은 아니다.

기존 빌드 문서의 과거 수치는 이번 변경의 빌드·부팅 증거가 아니다.
이번 변경의 검증 범위는 위 목록이다.

## 2차: 백엔드·dom0-manager·계약 — 2026-10-19 (S15P31E110-231)

플랫폼 밖 모듈에 현재 명칭으로 남아 있던 Perseus 표기를 Xenova로 통일했다.
작업 브랜치는 `refactor/common/S15P31E110-231-perseus-to-xenova`이며 `develop` 기준이다.

- backend: Java 패키지 `com.perseus.backend` → `com.xenova.backend`(파일 127개 이동),
  설정 접두사 `perseus.*` → `xenova.*`, Maven groupId `com.xenova`·name `xenova-backend`,
  `application.yml`(앱 이름, H2 파일 `./data/xenova`, JWT 기본 secret, MQTT client-id),
  refresh 쿠키 `xenova_refresh`, JWT issuer `xenova-backend`, OpenAPI 제목, README.
- services/dom0-manager: 환경변수 `PERSEUS_*` → `XENOVA_*`, 인스턴스 루트 `/srv/xenova/instances`,
  제목·설명, Mock 샘플의 uuid5 네임스페이스 `xenova/mock/<이름>`(샘플 vm_id 3개가 바뀜).
- `contracts/dom0-manager-openapi.yaml` 제목, `docs/contracts/vm-api-contract-v1.md`의 설정 접두사·`xenova_vm.py`,
  `Jenkinsfile` 주석.

검증: 백엔드 `mvnw test` 96개 통과(실패 0, 오류 0, 건너뜀 0), dom0-manager pytest 46개 통과.

유지한 것: MQTT 토픽 접두사 `perseus/v1`은 [MQTT 계약 v1](../contracts/mqtt-contract-v1.md)이 유지로 명시해 바꾸지 않았다.
바꾸려면 agent·gateway·ESP32 계약을 함께 바꿔야 한다. [리브랜딩 범위](xenova-rebranding.md)의 보존 목록
(역사 기록 문서, 결과 JSON, fixture provenance, 이전 명칭 부재를 검증하는 테스트, 패치 From, crypt salt)도 그대로다.

운영 영향: 배포 환경의 Spring 설정 `perseus.*`와 dom0-manager 환경변수 `PERSEUS_*`를 새 이름으로 바꿔야 한다.
기존 refresh 쿠키와 JWT(issuer)는 무효가 되어 다시 로그인해야 한다. 개발용 H2 파일은 `./data/xenova`로 새로 만들어진다.
