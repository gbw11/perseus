# 완료 플랫폼 산출물 인계 보완 — 2026-10-16

## 원인과 확정한 처리

정상 종료된 BitBake는 `bitbake.lock`을 삭제한다. 기존 `completed_input()`는 파일 존재를 전제로 열어 성공 WIC도 거부했다. 잠금 파일을 수동 생성하거나 부재 예외만 무시하는 방식으로 우회하지 않는다.

`publish-perseus-platform.py`가 성공 기록의 원본 이미지를 별도 경로에 복사·검증·게시한다. 실제 게시에서만 BitBake 호환 exclusive flock을 잡고 현재 경로의 inode가 같은지 확인한다. 직접 만든 동일 inode만 잠근 상태에서 정리하며 기존 잠금 파일은 보존한다. 신규 빌드는 복사/검증/원자적 게시가 끝날 때까지 같은 잠금을 얻을 수 없다. 게시 도구 자체는 BitBake를 실행하지 않는다.

조립기는 원래 빌드 경로에 접근하지 않고 게시 완료 사본만 읽는다. 독립 일반 파일·읽기 전용 권한·manifest/완료 기록/이미지/체크섬의 일치·임시 경로 여부를 검사한다. 원본 빌드 삭제나 다음 빌드의 실행은 이미 게시한 사본에 영향을 주지 않는다. 사용 중 변경도 실패로 처리한다. plan은 디렉터리·잠금·이미지를 생성하거나 수정하지 않는다.

게시 계획은 원본 이미지 bytes를 읽지 않고 완료 기록과 예상 복사량만 보고하며 `source_image_verified: false`다. 게시 실행은 `--execute`로 구분한다. 조립기의 계획은 게시된 이미지 해시까지 읽기 전용으로 검사한다. 실제 절차와 설정 형식은 [공통 SD 안내](perseus-first-install.md)를 따른다.

## 소스와 커밋 범위

기준 HEAD는 `70c85b90ef21e73b4d7b9daa97a65c0cacf3078c`, 브랜치는 `feat/platform/yocto-build-setup`이다. 작업 시작의 미커밋 파일 30개는 앞선 최초 설치 구현이며 직전 성공 빌드 입력 스냅샷과 모두 같았다. 이 기반과 이번 인계 수정 모두 사용자의 commit/push 승인 범위에 포함한다. 기존 외부 Layer·커널·Xen 버전은 변경하지 않았다.

이번 보완 파일:

- `platform/first-install/completed_artifacts.py`: 게시 계획/명시적 게시, 호환 잠금, 독립 입력 검증.
- `platform/first-install/artifact_io.py`: 기존 경로/해시/덮어쓰기 없는 원자적 게시 함수를 공유.
- `platform/first-install/assemble.py`: 기존 임시 잠금 의존을 제거하고 게시 사본만 허용. 소스 해시와 입력/출력 격리 연결.
- `scripts/publish-perseus-platform.py`: 기본 읽기 전용 게시 계획 CLI.
- `platform/first-install/example.config.json`: 게시 manifest와 같은 디렉터리의 이미지·완료 기록을 명시.
- `tests/platform/test_perseus_completed_artifacts.py`: 작은 비 OS fixture 26개. 기존 `test_perseus_first_install.py`의 raw 입력 검사와 원자적 게시 fixture도 새 구조에 맞췄다.
- `tests/platform/results/completed-artifacts-20261016.json`, 본 문서와 관련 안내/역사적 구현 보고 링크.

Yocto/runtime/게스트 소스 47개가 성공 빌드 입력과 그대로 일치함을 확인했다. 이번 호스트 수정 때문에 dom0를 다시 빌드할 필요는 없다. 성공 WIC를 실제로 게시하는 작업은 아직 실행하지 않았다.

## 실행한 검사와 근거

`bash scripts/check-perseus-platform.sh`: **exit 0, unittest 123개(기존 97+신규 26), 기존 boot/init/network 회귀 17개 통과**. 실제 systemd loader는 정상 그래프 exit 0, 의도적 순환 그래프 exit 1을 확인했다. 쉘 문법과 `git diff --check`도 통과했다.

새 회귀는 정상 종료 후 lock 부재, 활성 빌드 거부, 게시 중 새 BitBake 잠금 거부, lock inode 교체 재시도/기존 lock 보존/외부 symlink 방어, 원본 삭제·변경·다음 빌드와 독립된 소비, plan 무변경, 실패/미완료 기록, 해시/권한/hardlink/출처 변조, 기존 출력·게시 경합, 복사 중단·원본 변경, 미게시 staging 거부, WIC/rpi-sdimg 인계를 다룬다. 모두 수백 바이트 일반 파일이며 실제 OS 원본을 복사하거나 조립하지 않았다.

실제 성공 빌드의 `BUILD-RESULT.json`을 게시 계획에 읽기 전용으로 대입했다. exit 0, 예상 사본 200,729,167 bytes를 출력했고 원본 이미지 bytes를 읽거나 복사하지 않았다. 원래 `bitbake.lock`과 출력 경로가 계속 없음을 확인했다. 초기 신규 검사 1건은 fixture가 결과의 action 필드를 배포 설정에 섞은 오류였고 platform 블록만 사용하도록 고친 뒤 전체 검사가 통과했다. 실패 로그도 남겼다.

스테이징 후 전체 whitespace 검사에서는 앞선 구현의 `prepare_instance.py`에 EOF 빈 줄 1건이 확인됐다. 성공 이미지와의 바이트 계약을 유지하려고 이 기존 파일은 변경하지 않았다. 해당 경로만 blank-at-eof를 제외하고 나머지는 기본 whitespace 규칙으로 검사했다. 이번에 추가한 host IO 파일의 EOF 빈 줄은 정리했다. Yocto 패키징 QA는 변경하지 않았다.

원본 로그는 `/home/ssafy/perseus-artifact-handoff-fix-20261016-143944/`에 있다. `final-checks.command/log/exit-code`, `real-receipt-plan.command/log/exit-code`, `new-tests-first/second.*`, baseline Git 상태/diff를 보존했다. 재실행 가능한 코드와 요약 결과는 저장소에 남긴다.

## 빌드 상태와 미수행 범위

후속 dom0 빌드 `build-rpi4-xen-first-install-20261016-142155`는 이미 exit 0이었다. 실제 WIC FAT 28개, 일반 산출물 93개, 최초 설치 WIC 기능 47개 검사가 통과했으며 기존 산출물 보존도 확인했다. 원본 기록은 `/home/ssafy/perseus-first-install-build-20261016-142155/BUILD-RESULT.json`이다. 이 기록은 당시 미커밋 소스로 빌드한 실제 출처를 보존하며 새 커밋 SHA로 꾸며 바꾸지 않는다.

이번에는 소스·회귀 검사만 수행했다. 실제 이미지 게시·OS 다운로드·템플릿/인스턴스 생성·BitBake 빌드·최종 SD 이미지 조립·SD 접근/변경·실보드 검증은 실행하지 않았다. commit/push는 승인된 단계로 별도 수행·보고한다. 이후 실제 게시→게스트 준비→조립→64/128GB 실보드 확장/고유화/작업 보존 시험에는 별도 실행 승인이 필요하다. Alpine은 확장 구조만 남아 있다.
