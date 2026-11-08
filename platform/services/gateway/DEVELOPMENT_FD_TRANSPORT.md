# 명시 개발용 FD transport

`SerialSink`/`FeedbackSerialSink`는 다음 두 인자를 **함께** 제공할 때만 개발 FD를 사용한다. `Feedback3SerialSink`는 기존 `**kwargs`로 전달한다.

```python
sink = Feedback3SerialSink(
    'development-fd', development_fd=caller_fd,
    timeout_ms=approved_timeout_ms, odometry=odometry, profile=feedback3_profile,
)
owner = OutputOwner(sink, command_timeout_s=approved_command_timeout_s)
```

기본 `development_fd=None`과 일반 장치/URL은 기존 pyserial import·`serial_for_url` 경로다. 포트명만 `development-fd`로 주거나 일반 포트에 FD를 붙이는 입력은 거절한다. CLI·배포 설정·자동 fallback을 추가하지 않았다. pyserial 설치나 기존 pyserial/PTY 수락을 대신하지 않는다.

호출자는 POSIX의 full-duplex byte stream socket 또는 raw PTY를 먼저 준비한다. 구현은 socket/tty 종류·read/write access·이미 nonblocking인 상태를 검사한다. socket subtype와 tty raw 설정은 호출자 계약이며 자동 추정/설정하지 않는다. `os.dup` 한 개만 소유하며 caller FD는 닫거나 설정하지 않는다. dup은 같은 open-file description의 nonblocking 상태를 공유하므로 owner가 살아 있는 동안 caller가 blocking/tty 설정을 바꾸면 안 된다. blocking으로 바뀐 상태는 I/O 전에 거절한다.

개발 경로의 `_owned_read_fd`는 동일 `OutputOwner` selector에 연결한다. `serial.serialposix`를 import하지 않으며 새 RX thread/daemon/DB를 만들지 않는다. read≤4096B, write≤4096B의 한 syscall만 사용한다. partial/EAGAIN 쓰기는 재시도하지 않고 application-unknown 실패다. 직접 poll의 idle-EAGAIN은 빈 입력이며, readable-empty/PTY HUP는 기존 owner의 link failure 경계로 전달한다. close는 복제 FD만 한 번 닫고 종료 후 FD 번호 재사용으로 다른 연결에 쓰지 않는다.

Feedback3의 HELLO/session·원4시각·실제 latch·install ACK·command/report·STOP/원 E 및 `OutputOwner` FSM은 바꾸지 않았다. transport가 READY/APPLIED/clock/STOP witness를 넣지 않는다. write 실패 후 owner의 새 DRIVE 금지와 EOF 후 link/sensor 철회는 기존 FSM이 처리한다. clockless STOP·원 기한·physical 측정 조건도 유지한다.

`tests/test_development_fd_feedback.py`는 **새 development-FD scope**다. 실제 socketpair/raw PTY peer에서 같은 Feedback3/OutputOwner로 HELLO→PROBE/REPLY→INSTALL/ACK→Vehicle→STOP 측정→DRIVE 원 E/자동 STOP을 확인하도록 작성했다. partial/EAGAIN fault·EOF/HUP·단일 복제 소유·close·기본 dispatch도 포함한다. 외부 `VirtualFeedback3`는 명시 소프트웨어 device/plant이며 실물 ESP32/encoder/pyserial 보정 증거가 아니다.

cohort06 Nano 실행에서는 기본 FD 검사 11건이 통과했고, 실제 Feedback3 흐름 8건은 `clock_ready && vehicle_sample` 준비 단계에서 timeout했다. 원 로그에는 clock/odometry 내부 값이 없어 최초 거절 분기는 관찰되지 않았다. 코드 검토에서는 peer가 `quality=3`과 조향 오차 0을 함께 생성하고, 실제 `VehicleOdometry`는 이 조합을 `ODOM_STEERING_QUALITY`로 거절함을 확인했다. peer의 새 명시 인자 `steering_error_mrad=1`은 개발 양성 시험에서만 사용하는 가상 조향 센서의 양자화/오차 모델이다. 측정값·clock·시각·원 E를 사후 변경하지 않고 CLOCK3/SENSOR3 생산자가 해당 오차를 직접 생성한다. 기본값은 0이며, 그 원 표본이 계속 거절되는 socketpair/raw PTY 음성 시험도 따로 둔다. 원 payload 관찰은 최대 16개이며 수정하지 않는다.

이 수정은 AST 검증 후 Nano 재시험을 기다리는 상태다. 기존 pyserial5 시험과 원 예제의 기본값 0 경로는 미수락 상태 그대로이며, 다른 transport 결과로 대체하지 않는다.

실행 리포트는 선택한 transport/channel, 원 peer/source/session/profile 및 wire·clock/report/E를 남겨야 한다. 개발 FD 시험 PASS로 기존 pyserial5·실물 stationary/FREE·폐루프 수락을 완료 처리하지 않는다. owner 종료를 확인한 뒤 caller의 원 FD/peer를 정리한다.
