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

## 테스트

`vehicle/Jetson`에서 표준 라이브러리 테스트를 실행한다.

```bash
PYTHONPATH=src python -m unittest discover -s tests/unit -v
PYTHONPATH=src python -m unittest discover -s tests/integration -v
```
