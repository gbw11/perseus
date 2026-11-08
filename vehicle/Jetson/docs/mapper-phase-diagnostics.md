# M 처리 상태와 상주 결과 진단

M worker state는 로컬 처리 단계다. `RUNNING`은 현재 원 입력에서 수락된 계산 결과가
있다는 뜻이며, metric 보정·현재 World·Pi 이동 권한을 승인하지 않는다. CPU 진전과
parser 준비, 원 관측의 현재성, 실제 World의 유효성은 각각 별도로 검사한다.

parser 준비는 기존 fresh NN 해석5회다. 그 전에는 `WARMUP`을 유지한다. 준비 직후
현재 수락 결과가 없으면 `WAIT_INPUT`이고, 결과가 있으면 다음 순서로 분류한다.

|원 수락 결과의 사실|M 상태|
|---|---|
|source 미결박|WAIT_INPUT|
|최종 `PI_J_CLOCK_UNAVAILABLE`|WAIT_CLOCK|
|현재 Vehicle 또는 전체 capture pose/bracket 부재|WAIT_ODOM|
|mapping 미준비이며 명시 provenance/DB/LOAD 대기|WAIT_STORAGE|
|quarantine, 복원 미준비 또는 명시 profile 부적격|DEGRADED|
|그 밖의 현재 처리 결과|RUNNING|

과거 Vehicle 행이나 선택 ToF 입력의 clock 경고만으로 `WAIT_CLOCK`을 만들지 않는다.
진행 중인 CP가 있어도 mapping이 현재 준비되어 있으면 저장 대기로 낮추지 않는다.
frame-local 진단 처리도 로컬 계산이므로 `RUNNING`일 수 있다. 이 분류는 core summary의
`metric_ready`·`world_valid`·`live_authority`를 읽거나 바꾸지 않는다.

M-main은 compute가 수락한 원 generation/result를 받고 JSON 해석 전후에 원 expiry를
검사한다. 결과가 만료되면 `WAIT_INPUT`으로 돌아간다. 마지막 summary는 과거 진단으로
보존하지만 원 기한을 연장하지 않는다. 폐기할 결과가 있어도 원 parser 첫 준비 기한과
NN 무진전 감시는 계속 수행한다. `QUIESCING`·`RECOVERING`·`FAILED`·`STOPPED`는 덮지 않는다.
heartbeat 송신/role watchdog/STOP/재시작 정책은 바꾸지 않는다.

S의 `runtime-result.json`은 검산된 마지막 heartbeat를 `workers.<role>.phase`와
`mapper_diagnostics.phase`에 기록한다. 원 issued/expiry/수신 시각과 `current`를 함께 남긴다.
종료·EOF·회수·복구 중이거나 원 기한/기존1초 heartbeat 공백을 넘으면 `current=false`다.
이는 heartbeat 진술의 현재성이고 현재 World의 증거가 아니다.

`external_waits`는 현재 M heartbeat가 보고한 WAIT 상태의 사유만 표시한다. 미관측·만료·
종료 뒤에는 null이고, `last_observed_waits`는 마지막 진술의 사유를 남긴다. 빈 배열은
현재 보고된 WAIT가 없다는 뜻으로, 모든 외부 의존성이나 World가 준비됐다는 뜻은 아니다.
`parser_warmup_observed`는 현재 M worker 세대가5회 준비를 마쳤다는 과거 사실이다. M 또는
A/M 복구로 이 세대가 바뀌면 초기화되며, 정상 종료 뒤에는 true로 남을 수 있다.
`cpu_active`와 `cpu_last_terminal`은 기존
원 MapperJobWatch 진도이며, `world_current=null`은 이 lifecycle 통로에서 현재 World를
판정하지 않았다는 뜻이다. 실제 World outbox와 Pi 소비자의 원 기한·source 검산은 별도다.
