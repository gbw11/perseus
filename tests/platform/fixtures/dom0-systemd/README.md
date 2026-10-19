# dom0 SSH graph fixture

- `yocto-dom0-systemd-259.5.tar.gz`: 성공 WIC rootfs에서 읽기 전용 추출한 **전체** /usr/lib/systemd/system, /etc/systemd/system, preset 및 공개 OS 설정 336개. 파일/링크별 출처와 SHA-256은 provenance.json에 있다. SSH 개인 키·비밀번호 파일은 포함하지 않는다.
- `sshd.service.in`: 고정 OpenEmbedded OpenSSH 10.3p1 레시피의 일반 서비스 원본. 실제 WIC는 socket mode만 설치했으므로 이 파일은 WIC에서 온 것이 아니다. 테스트는 레시피와 같은 경로 치환을 적용한다.
- `xl-list-source.txt`: 고정 Xen 4.21 xl_info.c의 verbose 출력 및 옵션 처리 발췌. `xl list -v`는 libxl_list_domain의 domid/UUID와 조회한 이름을 출력하며 -l처럼 게스트 설정을 요구하지 않는다. provenance에 전체 소스의 해시도 기록했다. 실제 보드 출력 수집을 대체하지 않는다.

테스트는 실제 전체 유닛·enable 링크·drop-in·기본 의존성을 로더에 전달한다. 명령 존재 확인용 실행 파일 스텁만 만들며 서비스는 실행하지 않는다. 새 구성은 고정 OpenSSH service mode의 패키지 파일 선택을 재생하고, **현재 xenova-platform do_install 본문을 실제로 실행**해 격리 경로에 설치한다. 이는 새 rootfs의 패키징 모델이며 기존 설치에 제자리 적용하는 갱신 도구가 아니다.

`systemd-analyze verify --recursive-errors=yes --generators=no --man=no --root=...`를 사용한다. 의존성 무시 옵션은 없다. 기존 순환과 job 삭제, 수정 후 정상, 재주입한 순환을 모두 검사한다. rc=0이어도 순환/삭제/skip 진단을 실패로 처리한다. 키 생성 job과 서비스의 hard Requires/After가 실제로 로드됐는지도 확인한다.

`XENOVA_DOM0_SYSTEMD_ANALYZE`에는 systemd 259 이상 로더의 JSON argv를 지정한다. 이번 실행은 캐시 RPM의 타깃 systemd-analyze 259.5를 실제 WIC의 loader/shared libraries와 QEMU user emulation으로 실행했다. QEMU의 `-L /`와 명시적 loader/library path를 사용한다. `-L <WIC root>`로 호스트 절대 경로를 치환하면 fixture drop-in을 가릴 수 있으며 음성 대조가 이를 검출한다.

WSL 커널/가상화 정보는 Pi와 다르다. system unit generator 실행은 끄고 환경 generator는 빈 경로로 격리한다. 실제 systemd-ssh-generator의 고정 소스는 AF_UNIX/VSOCK/추가 socket 생성 경로가 있어 /etc override로 mask한다. generator 실행·PID1 부팅·키 생성 서비스의 런타임 실패 전파·리스너/로그인은 이 그래프 검사의 범위 밖이다. 후속 새 WIC/보드에서 확인한다.
