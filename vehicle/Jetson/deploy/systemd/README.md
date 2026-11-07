# systemd237 실행 예시

이 unit은 JetPack 4.6.6의 systemd237·UID1000 `ssafy`·legacy systemd cgroup 전용이다. 실제 설치 경로와 REQUIRED YOLOP profile 경로를 명시적으로 바꾼 뒤 root 소유이며 일반 사용자가 쓸 수 없는 `/etc/systemd/system`에 설치한다. runtime은 실제 unit fragment/drop-in 권한과 InvocationID, PID1 birth, MainPID, 전용 slice와 하위 tree를 검사한다.

`/var/lib/xenova-vehicle`과 `diagnostics`는 미리 `ssafy` 소유·0700으로 만든다. 최초 한 번만 `ssafy`(UID1000)로 전용 Python의 `-m xenova_vehicle runtime-init --state-dir /var/lib/xenova-vehicle/state`를 실행한다. root로 초기화하면 이후 `ssafy` 서비스가 소유권 검증에서 거절한다. state 경로는 그때 존재하지 않아야 한다. 시작할 때 state를 지우거나 초기화를 반복하지 않는다. 기존 state를 옮길 때도 원 catalogue와 설치·실행 기록을 보존한다.

DB 기본 정책은 `EXISTING_ONLY`다. 최초 DB가 없으면 운영자가 명시적으로 `CREATE_IF_ABSENT`를 선택하여 처음 실행한 뒤 기본 정책으로 되돌린다. 기존 잘못된 DB를 덮지 않는다. slice는 먼저 enable/start하고 다른 unit을 이 slice에 배치하지 않는다. `ProtectControlGroups`처럼 invocation마다 mount namespace를 바꾸는 옵션은 이 full-runtime 비교 profile에 추가하지 않는다.

`COOPERATIVE_UID_EXECUTION`은 동일 UID의 협력 실행을 전제한다. 전용 slice와 service 하위 `cgroup.procs`·`tasks`는 root만 쓸 수 있어야 하며 Delegate는 꺼져 있어야 한다. 다른 user manager의 권한은 바꾸지 않는다. 비협력 same-UID process의 다른 writable cgroup을 통한 이동이나 privileged root의 변경까지 차단하는 보안 경계는 아니다. live Store의 평생 exclusive lock과 생성 전 S-only 실제 관측을 함께 사용한다.

정상 stop 신호는 `SIGINT`다. S가 원 STOP/STOPPED와 실제 reap를 진행하는 동안 worker는 SIGINT를 무시한다. `KillMode=control-group`의 기본 SIGTERM으로 worker를 먼저 죽이지 않도록 실제 `KillSignal=2`도 검사한다. 제한 시간을 넘긴 강제 회수는 별도다.

`execution-record.json`의 PUBLISHED는 같은 service/resource epoch·S birth·runtime·cgroup·invocation에 대한 불변 admission anchor다. inventory와 복구 episode·counter의 단일 정본은 보호된 `resource-set.json`이다. anchor의 최초 revision 이후 정본이 진행해도 anchor를 매번 복제하지 않는다. 최초 PREPARED 확정 때만 실제 manager 확인과 sidecar fsync를 수행하며, 후속 WRITE는 원 Store/ACK/post-load와 불변 incarnation 관계를 검사한다. 이로써 정상 stop 신호가 부가 systemctl helper를 끊거나 두 파일의 counter 사본이 갈라지는 일을 주행 상태 변경과 결합하지 않는다. PREPARED의 미확정 초기 시도와 다른 boot reset의 보수적 시도 보존은 별도다.

이전 같은 boot의 SSH catalogue는 원 session cgroup이 여전히 살아 있으면 차단된다. 관리된 새 S가 살아 있는 원 directory/basefile FD를 먼저 pin하고 `legacy-scope-handover.json`에 원 ref와 기한을 기록한 경우에만, 해당 SSH의 정상 종료를 원 기한 안에서 관측할 수 있다. 진단 파일은 허가가 아니다. 원 basefile의 실제 ENODEV와 mount·incarnation 결박, 이름·owner의 전후 관측이 필요하다. path 부재나 directory nlink만으로 성공을 판정하지 않는다. [Linux4.9 cgroup 소스](https://raw.githubusercontent.com/torvalds/linux/v4.9/kernel/cgroup.c), [kernfs file 소스](https://raw.githubusercontent.com/torvalds/linux/v4.9/fs/kernfs/file.c), [kernfs inode 소스](https://raw.githubusercontent.com/torvalds/linux/v4.9/fs/kernfs/inode.c)

설치 예시는 배포 형식이며 실제 설치·재시작 검증 결과는 `Jetson-drive-docs/making-reports`에 기록한다.
