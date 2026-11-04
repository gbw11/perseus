# 선택형 native 송신기 프로파일

`RuntimeConfig.encoder`의 기본값은 `ffmpeg`다. `native_jpeg`는 Linux 64-bit X11/MIT-SHM, little-endian BGRX32, 출력과 전송 해상도가 동일한 경우만 지원한다. 지원하지 않는 환경에서 자동 전환하지 않으며 시작 검사가 실패한다. 축소 전송이 필요하면 FFmpeg를 선택한다.

한 출력당 비특권 worker 하나를 사용한다. 공유 캡처 버퍼와 JPEG 최악 크기 버퍼를 한 번 할당하고 재사용한다. 서비스 경로에는 프레임 개수 제한·진단 배열·과거 영상 큐가 없다. JPEG는 최대 2MiB, native 할당은 최대 16MiB다. MIT-SHM은 0600으로 만들고 attach 직후 제거 예약한다. 정상 종료·수신 pipe 종료 시 자원을 해제한다. Xlib 오류 처리기는 worker 프로세스 전용이며 한 프로세스에 캡처 객체 하나를 둔다.

`stream-host.py`는 기존 canonical 출력 설정과 키오스크 상태 파일을 읽는다. 별도 VM 조회·MQTT 구독·화면 렌더러를 만들지 않는다. 기존 Xorg의 mount namespace를 확인한 뒤 UID/GID1000 worker를 실행하며 HTTP 인증 토큰은 worker 환경에 넘기지 않는다. HTTP 송신기는 loopback 8083에서 기존 인증 규격을 유지한다.

실제 앱의 두 Capture는 `RuntimeStatus` 하나를 공유한다. 파일 상태는 최대 100ms 간격으로 확인하고 inode/mtime/크기가 달라질 때만 JSON을 다시 파싱한다. producer의 `updated_monotonic`을 cache 조회 시각으로 갱신하지 않으며, 출력별 상태와 원본의 6초 신선도 제한은 매번 검사한다. 파일 삭제·파싱 실패는 fail-closed로 처리한다. 상태 전파에는 최대 100ms의 추가 관측 지연이 있을 수 있다.

`XENOVA_STREAM_FPS`는 송신기 전용 선택적 override다. 설정하지 않으면 기존 canonical 값을 유지하며 `RuntimeConfig`의 1~15 제한으로 검증한다. 6 FPS는 사용자가 허용한 성능 비교 후보이며 비교 결과 없이 기본 설정이나 성능 목표를 바꾸지 않는다.

배포 파일은 `xenova-display-stream.service`와 `stream.env.example`이다. unit의 `@STREAM_RELEASE@`를 실제 호스트 release로 치환하고, host-lib에 제품 패키지를 준비한다. rootfs 안에도 같은 소스의 worker 패키지와 검증된 ARM64 `libturbojpeg`·라이선스를 별도 release에 준비한다. worker에는 FastAPI/Pydantic이 필요 없다. root 전용 0600 환경 파일에 별도 stream 토큰과 경로를 넣는다. 토큰이나 native binary는 Git에 추가하지 않는다.

서비스는 시작 전 `--check`로 실제 두 출력의 native 라이브러리·X11 자원 생성/해제를 확인한다. FFmpeg 선택 시에는 실행 파일 버전을 확인한다. 기존 graphics에 의존하며 송신기만 중지해도 HDMI 서비스는 유지한다. 이 템플릿을 추가하는 것만으로 enable/설치하지 않는다. 실보드 비교 시험은 임시 디렉터리·transient unit으로 수행하고 종료 시 삭제한다. 상시 라이브러리 배치·unit enable·원격 접근 경로의 영구 허용은 별도 적용 작업이다.

Kiosk supervisor는 geometry 관측·출력별 브라우저 검증·1초 상태 기록을 독립 작업으로 진행한다. 한쪽 Chromium 복귀가 느려도 다른 출력의 검증을 막지 않는다. renderer의 최근 검증 시각을 따로 확인하므로 파일 heartbeat만 갱신해 오래된 출력을 정상 처리하지 않는다. [최종 실보드 수신·지연·분리 검증과 제한](../../../../../../docs/verification/display-257-browser-acceptance-20261102.md)을 참고한다.

라이브러리 출처와 해시는 [비교 검증 기록](../../../../../../docs/verification/display-257-native-jpeg-20261102.md)을 참조한다. 공식 Debian ARM64 패키지에서 추출한 라이브러리를 격리된 경로로 로드하며 시스템 패키지·ldconfig 설정을 교체하지 않는다. FFmpeg q=7과 TurboJPEG quality=85는 동일 척도가 아니며 동적 앱 영상은 별도 화질/부하 검증이 필요하다.
