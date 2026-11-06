# Jetson — 관측·인지·지도 애플리케이션

Jetson Nano B01과 OAK-D-Lite에서 수집한 관측을 처리해 차선·장애물·지도·주차 후보를 RPi에 제공한다. 구현 언어는 Python이다.

## 확인한 Jetson 환경

2026-11-06 전용 환경 설치 후 SSH 조회 기준이다. 설치·연결 상태가 바뀌면 다시 조회한 날짜와 값으로 갱신한다.

| 항목 | 확인 결과 |
|---|---|
| OS / L4T | Ubuntu 18.04.6 / 32.7.6 |
| 메모리 / 디스크 | 약 4GB / 루트 여유 42G |
| 시스템 Python·라이브러리 | Python 3.6.9, OpenCV 4.1.1, NumPy 1.13.3 유지 |
| 앱 Python | 별도 CPython 3.11.18 + 가상환경, pip 26.2.1 |
| 앱 라이브러리 | DepthAI 2.29.0.0, NumPy 1.26.4, opencv-python-headless 4.10.0.84 |
| OAK | 일반 사용자 SDK에서 1개 검색, 영상 수신·운영 USB 속도 미검증 |
| 네트워크 | Wi-Fi 접속 중, Ethernet 통신 미검증 |

glibc는2.27이다. L4T 일치는 JetPack 전체 패키지 설치 확인과 구분한다. 최초 시스템 조회와 별도 환경의 설치·검증 근거는 `Jetson-drive-docs/making-reports/0003-jetson-environment.md`와 `0005-jetson-python311-environment.md`에 보존한다.

## 앱 전용 환경

| 구성 | 경로·버전 |
|---|---|
| Python | `~/.local/opt/xenova/python-3.11.18/bin/python3.11` |
| 가상환경 | `~/.local/share/xenova-vehicle/venvs/jp466-py311` |
| 앱 TLS | 전용 OpenSSL 3.5.9, 시스템 OpenSSL과 분리 |
| 통신·직렬화 | paho-mqtt 2.1.0, protobuf 6.33.6 |

Jetson에서는 가상환경의 명령을 직접 실행할 수 있다. 기본 `/usr/bin/python3`는3.6.9를 유지한다.

```bash
~/.local/share/xenova-vehicle/venvs/jp466-py311/bin/xenova-vehicle doctor
```

OAK 접근은 `/etc/udev/rules.d/80-xenova-oak.rules`에서 USB VID03e7을 기존 `plugdev` 그룹의 `0660` 권한으로 허용한다. SDK 검색 성공은 장치 부팅·영상·깊이·NN 준비 확인과 구분한다.

## 디렉터리

```text
Jetson/
├── pyproject.toml                  # 패키지·명령 진입점
├── requirements.txt                # Python3.11 대상 런타임 설치 후보
├── requirements/                   # 검증된 설치 버전·해시
├── profiles/                       # 수집·재생·운영 설정
├── assets/
│   └── manifests/                  # 모델 출처·해시·입출력·라이선스
├── src/
│   └── xenova_vehicle/
│       ├── cli.py                  # 명령 분기
│       ├── doctor.py               # 설치·연결 진단
│       ├── _doctor_probe.py        # native 진단 격리
│       ├── runtime/                # 프로세스·스레드·IPC·기동·종료
│       ├── oak/                    # DepthAI 그래프·수신·원시 시각
│       ├── perception/             # 차선·구획·깊이·NN 출력 해석
│       ├── mapping/                # 점유 지도·위치 정합·후보 ID
│       ├── storage/                # SQLite·checkpoint·복원
│       ├── transport/              # Pi 통신·codec 어댑터·영상 제공
│       └── recording/              # 관측 기록·파일 입력·재생
├── tests/
│   ├── unit/                       # 순수 기하·시각·설정·상태
│   ├── replay/                     # 기록된 관측 기반 검증
│   ├── integration/                # 프로세스·IPC·통신·저장
│   ├── hardware/                   # 실제 OAK·Nano 검증
│   └── fixtures/                   # 작은 고정 시험 입력
├── deploy/
│   └── systemd/                    # 서비스 배포 파일
└── scripts/                        # 설치·진단·검증 보조
```

빈 디렉터리의 `.gitkeep`은 Git에서 구조를 보존하기 위한 파일이다.

## 책임과 실행 구조

- 운영 실행은 S(supervisor/I/O), A(acquisition), M(mapper), D(storage)의 네 프로세스를 기준으로 한다. 폴더별로 프로세스를 추가하는 구조가 아니다.
- `runtime/`은 실행·수명·취소를 관리하고, 인지·지도·저장의 실제 처리는 해당 패키지에 둔다.
- A만 DepthAI Device를 소유한다. OpenCV·NumPy 계산은 M이, SQLite 연결은 D가 담당한다. 프로세스 진입점은 가볍게 유지하고 장치 연결과 무거운 import는 담당 프로세스에서 수행한다.
- 실시간 수집과 파일 재생은 같은 인지·지도 함수를 사용한다. 재생 경로는 운영 broker로 결과를 발행하지 않는다.
- 통신 codec은 `docs/contracts/`의 정식 계약을 참조한다. 공용 메시지 규격을 이곳에서 독자적으로 재정의하지 않는다.
- 주차칸 선택·경로 계획·추종과 모드 권한은 RPi 측 책임이다. ESP32 펌웨어는 형제 디렉터리인 `../ESP32/`에서 관리한다.

실제 녹화 데이터·지도 DB·로그·모델 바이너리는 소스 외부의 실행 경로에서 관리한다. 저장소에는 코드·설정 예시·자산 명세와 작은 고정 시험 입력을 관리한다.

## 실행

Python 3.11 이상에서 사용한다. `setuptools>=68`이 준비된 환경에서는 저장소 루트에서 다음과 같이 패키지와 명령 진입점을 설치한다. 별도의 런타임 라이브러리는 자동 설치하지 않는다.

```bash
python -m pip install --no-deps --no-build-isolation -e vehicle/Jetson
xenova-vehicle --help
xenova-vehicle doctor
xenova-vehicle doctor --json --timeout 10
```

카메라·인지·통신용 라이브러리의 고정 설치 목록은 `requirements.txt`에서 관리한다. 이 파일은 별도로 준비한 CPython3.11.18 가상환경용이며 시스템 Python3.6의 패키지를 교체하는 데 사용하지 않는다. 해당 가상환경을 선택한 뒤 저장소 루트에서 설치한다.

```bash
python -m pip install --only-binary=:all: -r vehicle/Jetson/requirements.txt
```

현재 파일은 직접 의존성의 버전을 고정한 목록이며 전이 의존성과 파일 해시를 모두 고정한 배포 lock은 아니다. 실행 환경별 설치·import 검증과 실제 wheel 해시는 `making-reports`에서 별도로 관리한다. OpenCV 공급자는 headless 한 종류를 사용하며 CPU wheel을 기준으로 한다.

설치 없이 소스에서 실행할 때는 저장소 루트에서 다음 명령을 사용한다.

```bash
PYTHONPATH=vehicle/Jetson/src python -m xenova_vehicle doctor --json
```

`doctor`는 수동 설치·연결 진단 도구다. Python·Nano/L4T·라이브러리 배포판 버전·실제 import 위치와 SDK의 장치 검색 결과를 출력한다. 버전 기준은 상세설계 v1.3의 고정 설치 후보이며, 부족한 구성 요소를 자동 설치하지 않는다. `--timeout`은 native import와 검색 전체의 제한 시간이며 기본10초, 허용 범위0.1~30초다.

종료 코드는 검사 통과0, 실패·미확인1, CLI 사용 오류2다. SDK 장치 검색은 Device를 열지 않으며 OAK-D-Lite 모델·센서·USB 속도·영상 수신·보정·NN 준비를 확인한 것으로 해석하지 않는다. Nano 모델 정보로 carrier B01을 확정하지 않으며, L4T 일치도 JetPack 전체 패키지 설치 검증과 구분한다.

일반 패키지 import와 도움말은 진단이나 native import를 실행하지 않는다. 진단 결과는 런타임 준비·주행 허가가 아니며, 공용 장치·DB 락이나 상주 서비스도 만들지 않는다.

## RGB·native depth 벤치 수집

`capture`는 승인한 OAK 한 대의 RGB NV12 640×360과 RIGHT 기준 native depth 640×480을 원본으로 기록한다. 깊이는 little-endian uint16, mm 단위이며0은 invalid다. RGB 정렬·구멍 채우기·빈공간 판정을 하지 않는다. 고정15Hz는 요청값이며 실제 수신률과 누락 수는 결과에서 확인한다.

`profiles/oak-rgb-depth-bench.example.json`을 저장소 밖에 복사하고 `device_mxid`를 실제 승인 장치 ID로 바꾼다. 예시의 `REPLACE_WITH_OAK_MXID`는 실행 시 거절한다. 프로필은 `schema_version=1`, `profile_id=oak-rgb-depth-bench-v1`, `nn_requirement=ABSENT`, `jpeg_enabled=false`인 수집 벤치 전용이다. NN 모델이나 JPEG encoder를 적재하는 운영 그래프와 구분한다.

Jetson의 전용 환경에서 다음과 같이 실행한다. 출력 경로는 새 디렉터리여야 하며 기존 디렉터리는 비어 있어도 덮어쓰지 않는다.

```bash
~/.local/share/xenova-vehicle/venvs/jp466-py311/bin/xenova-vehicle capture \
  --profile "$HOME/xenova-config/capture.json" \
  --duration-s 30 \
  --output "$HOME/xenova-data/captures/bench-001" \
  --json
```

기록 시간은 장치·queue 설정과 초기 시계 표본 확보 뒤부터 센다. 기본 시작 기한90초, 실제 프레임 진전 공백500ms, 종료 기한15초를 벤치 초깃값으로 둔다. 정상 종료가 안 되면 TERM/KILL 각1초 안에서 실제 자식 종료를 확인하고 실패로 보고한다. native hang이나 OS의 미회수 상태에서 자동 재연결하지 않는다. Ctrl+C/SIGTERM도 같은 정리를 거치며 종료 코드는 각각130/143이다. 실제 Nano에서 이 기한을 수락한 것은 아니다.

부모는 가벼운 감시를, A 자식만 SDK·Device·raw bytes를 소유한다. A 안의 단일 writer thread에 최대8개 프레임을 비차단 제출하고 포화 시 drop을 센다. 원본 파일·보정 manifest·index는 writer가 쓰며, 출력 예약/결과 파일은 acquisition 전후의 짧은 별도 I/O 자식에서5초 제한으로 처리한다. 이 I/O 자식과 A는 동시에 실행하지 않는다. Linux 제어 통로는 최대4KiB nonblocking seqpacket이며 raw 이미지를 보내지 않는다. Windows 시험용 stream 대체 통로는 Linux 실기 수락을 대신하지 않는다.

```text
bench-001/
├── capture-result.json   # 원 실행 context·기한·종료·통계; 최초에는 complete=false
├── manifest.json         # 프로필·센서·USB·원 EEPROM/보정·host graph 선언
├── frames.jsonl          # stream별 원 순번·device/SDK/J 시각·형식·파일 SHA256
└── frames/
    └── ...bin            # 손실 없는 NV12/RAW16_LE bytes
```

각 stream의 ID와 원 시각을 유지한다. RGB/depth의 같은 숫자 순번을 같은 노출로 간주하지 않으며, timestamp 차이10ms 이내의 후보와 실제 양쪽이 저장된 후보 수를 구분한다. SDK host clock과 Python 단조시각의 비교 표본은 보존하지만 OAK 동기·노출/readout 오차의 실측 bound는 아직 없다. `metric_ready=false`와 `runtime_readiness=not_evaluated`를 유지한다. 원 EEPROM 저장도 차량 장착 보정·active K_rect 수락을 의미하지 않는다.

기본 기록 상한은1GiB, 종료 결과 여유64KiB, 디스크 여유 하한8GiB다. 공간 부족·파일 오류·필수 stream 누락·실제 저장된 동기 후보 없음·강제 종료는 성공이 아니다. 정상 제한 시간 기록 완료는0, 런타임 실패/불완전 기록은1, 인수·설정 오류/기존 출력 경로는2다. 중간에 남은 파일은 삭제하지 않는다. 원본·index의 hash 대조와 불완전 기록 처리는 후속 재생기의 책임이며, 이 기록 형식은 정전 내구성이나 주행 준비를 보증하지 않는다.

## 테스트

`vehicle/Jetson`에서 표준 라이브러리 테스트를 실행한다.

```bash
PYTHONPATH=src python -m unittest discover -s tests/unit -v
PYTHONPATH=src python -m unittest discover -s tests/integration -v
```
