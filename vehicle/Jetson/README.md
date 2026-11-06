# Jetson — 관측·인지·지도 애플리케이션

Jetson Nano B01과 OAK-D-Lite에서 수집한 관측을 처리해 차선·장애물·지도·주차 후보를 RPi에 제공한다. 구현 언어는 Python이다.

## 디렉터리

```text
Jetson/
├── pyproject.toml                  # 패키지·명령 진입점
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
