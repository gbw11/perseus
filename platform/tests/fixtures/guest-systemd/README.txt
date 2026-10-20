기존 불변 Ubuntu/Debian 템플릿에서 debugfs 읽기 전용 rdump로 추출했다.
유닛/링크는 원본 바이트다. provenance.json에 원본 템플릿과 패키지 버전·파일 해시를 기록했다.
실제 PID1 부팅이 아닌 systemd-analyze 그래프 검사 전용이다. 실행 파일 존재 검사에는 스텁을 쓴다.
원본 unit 파일의 SPDX/copyright는 각 파일을 따른다. SSH generator는 해당 배포판 패키지의 GPL/BSD 고지에 따른다.

REV-01 그래프 검사:
- systemd-analyze verify --recursive-errors=yes로 모든 연관 의존성을 검사한다.
- Ubuntu의 기존 순환/해당 순환의 job 삭제와 같은 root의 수정 후 통과를 대조한다.
- rc=0이어도 Found ordering cycle / deleted to break ordering cycle 진단은 실패다.
- drop-in Requires와 SSH/identity job enqueue도 확인해 링크/디렉터리 누락을 허용하지 않는다.
- Debian 257 유닛은 systemd 257 이상 로더가 필요하다. native 255로 의존성 오류를 무시하지 않는다.
- 기본 검증기는 PATH의 systemd-analyze다. 별도 실제 로더는
  PERSEUS_SYSTEMD_ANALYZE='["/path/to/systemd-analyze"]' 같은 JSON argv로 지정한다.
- 이번 검사는 기존 Debian root.raw에서 debugfs 읽기 전용으로 추출한 257.13 검증기와
  기존 qemu-aarch64-static을 사용했다. -L / 및 명시적 ELF loader --library-path를 사용했다.
  QEMU -L로 축소된 라이브러리 사본을 root처럼 지정하면 단위 디렉터리 탐색이 누락될 수 있다.
- --generators=no는 게스트 generator/서비스를 호스트에서 실행하지 않기 위한 경계다.
  배포판 기본 의존성은 유지한다. 실제 PID1/SSH listener 시험은 별도 실보드 단계다.
- 의존성 부모 링크는 guest namespace로만 해석한다. 절대 링크는 guest root 기준,
  root 밖 상대 링크는 거부한다. 동일 절대 경로의 guest 사본만 정리하고 외부 자산은 보존한다.
