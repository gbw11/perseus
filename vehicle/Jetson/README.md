# Jetson — 관측·인지·지도 애플리케이션

Jetson Nano B01과 OAK-D-Lite에서 수집한 관측을 처리해 차선·장애물·지도·주차 후보를 RPi에 제공한다. 구현 언어는 Python이다.

## 확인한 Jetson 환경

2026-11-06 전용 환경 설치·실제 OAK 수신 확인 기준이다. 설치·연결 상태가 바뀌면 다시 조회한 날짜와 값으로 갱신한다.

| 항목 | 확인 결과 |
|---|---|
| OS / L4T | Ubuntu 18.04.6 / 32.7.6 |
| 메모리 / 디스크 | 약 4GB / 루트 여유 약41GiB |
| 시스템 Python·라이브러리 | Python 3.6.9, OpenCV 4.1.1, NumPy 1.13.3 유지 |
| 앱 Python | 별도 CPython 3.11.18 + 가상환경, pip 26.2.1 |
| 앱 라이브러리 | DepthAI 2.29.0.0, NumPy 1.26.4, opencv-python-headless 4.10.0.84 |
| OAK | RGB IMX214·mono OV7251×2, NV12 640×360/native depth 640×480 수신·종료 확인 |
| OAK USB | 실제 동작 HIGH(USB 2.0), USB 3 연결·동시 NN 부하 미검증 |
| 네트워크 | Wi-Fi 접속 중, Ethernet 통신 미검증 |

glibc는2.27이다. L4T 일치는 JetPack 전체 패키지 설치 확인과 구분한다. 최초 시스템 조회·전용 환경·실제 수신의 근거는 `Jetson-drive-docs/making-reports/0003-jetson-environment.md`, `0005-jetson-python311-environment.md`, `0008-jetson-oak-capture-validation.md`에 보존한다.

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

YOLOP 320×320의 고정 blob·출처·라이선스·입출력 계약과 수동 검사 방법은 [자산 명세](assets/yolop-320x320.md)를 따른다. 대용량 모델 파일은 외부 자산 경로에 두고 Git에는 manifest와 저작권·허가문을 보존한다.

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

기록 시간은 장치·queue 설정과 초기 시계 표본 확보 뒤부터 센다. 기본 시작 기한90초이며, 각 stream의 첫 입력은 원 시작 기한과 수집 시작+15초 중 빠른 때까지만 기다린다. 입력을 받은 뒤에는 필수 stream별 실제 진전 공백500ms, 종료 기한15초를 벤치 초깃값으로 둔다. RGB만 계속 들어오는 동안 depth가 멈춰도 실패로 처리한다. 저장 drain과 Device close는 같은 전체 종료 예산 안에서 병행한다. 정상 종료가 안 되면 TERM/KILL 각1초 안에서 실제 자식 종료를 확인하고 실패로 보고한다. native hang이나 OS의 미회수 상태에서 자동 재연결하지 않는다. Ctrl+C/SIGTERM도 같은 정리를 거치며 종료 코드는 각각130/143이다. 위 기한은 다른 부하·장면·OS 정체에서의 반환 보장을 뜻하지 않는다.

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

기본 기록 상한은1GiB, 종료 결과 여유64KiB, 디스크 여유 하한8GiB다. 공간 부족·파일 오류·필수 stream 누락·실제 저장된 동기 후보 없음·강제 종료는 성공이 아니다. 정상 제한 시간 기록 완료는0, 런타임 실패/불완전 기록은1, 인수·설정 오류/기존 출력 경로는2다. 중간에 남은 파일은 삭제하지 않는다. 원본·index의 hash 대조와 불완전 기록 처리는 아래 재생기가 담당한다. 이 기록 형식은 정전 내구성이나 주행 준비를 보증하지 않는다.

## YOLOP·RGB·native depth 장치 벤치

`yolop-bench`는 같은 CAM_A video에서 RGB NV12와 BGR letterbox→YOLOP를 만들고, native RIGHT depth도 함께 수신한다. 모델은 [고정 자산 명세](assets/yolop-320x320.md)의 로컬 blob만 사용한다. 자산 검사·원 기동 기한·Device owner·단일 graph 시작을 유지하며 부팅 중 다운로드하지 않는다.

`profiles/oak-yolop-rgb-depth-bench.example.json`을 저장소 밖에 복사하고 승인 MXID와 자산 디렉터리·manifest 경로를 채운다. 상대 경로는 profile 파일 기준이다. no-JPEG/REQUIRED 벤치 전용이며 기존 ABSENT 수집 profile과 섞지 않는다.

```bash
xenova-vehicle yolop-bench \
  --profile "$HOME/xenova-config/yolop-bench.json" \
  --duration-s 30 --output "$HOME/xenova-data/yolop-bench/run-001" --json
```

부모는 SDK를 import하지 않고 기존 capture와 공통인 spawn 감시·4KiB 제어 통로·실제 자식 종료를 사용한다. A에서 검증 bytes→Blob 등록은 한 session에 한 번이다. RGB/depth host queue8, NN output2, metadata-only passthrough8 및 device 입력1은 nonblocking이다. collector는 stream당 최대1개, 회차16개/4MiB 이하로 복사한다. NN 출력은 원 SDK 객체 수명 안에서 소유 bytes로 만들고, 실제 runtime tensor 이름·dtype·rank/dims·stride·offset·범위·겹침을 검증한다. compiler layout을 대신 넣지 않는다. 활성 FP16 값의 NaN/Inf도 검사한다.

첫 NN layout과 SDK `getLayerFp16` 대조는 별도 `NN_LAYOUT_PROBE`의 원15초 이내에 한 번만 수행하고 추가 복사 비용을 기록한다. 완료 뒤 SDK/J clock barrier를 세워 그전에 소모한 프레임을 벤치 관측으로 재사용하지 않는다. cold startup90초, ASSET_CHECK15초·DEVICE_BOOT30초·PIPELINE_START30초·초기 probe15초는 원 기동 기한과 min이다. 늦게 돌아온 단계도 원 기한을 다시 받지 않는다.

원 RGB와 NN passthrough/output은 동일 pipeline/capture binding, 원 sequence/device/SDK 시각으로 연결한다. ring은 stream별8개/500ms이며 늦은 결과·다른 context·순번 역전·입력 부재를 보고한다. depth는 같은 sequence로 묶지 않고 SDK 시각 차이10ms 이내의 후보만 표시한다. 이 값은 벤치 초깃값이며 metric 동기 오차 수락값이 아니다. RGB/depth 진전 공백500ms와 NN 기한을 분리한다. 기본 NN 첫 결과15초, 이후 수신 및 **검증된 원 입력 pairing 진전** 공백5초다. 다른 stream·heartbeat·미결합 NN 수신은 pairing 기한을 연장하지 않는다.

전체 NN 연속 녹화가 아니라 최대3개의 독립 표본을 저장한다. 정상 대기2개와 실패 예약1개를 가진 A 내부 writer1개가 비차단 제출을 받는다. 기본 저장 상한64MiB(결과 여유64KiB 포함), 여유 공간 하한8GiB다. 결과에 실제 수신·연결/거절 분모, 원 시각, 관측된 수신률 계산용 경과시간, 검사 비용·수집 회차 시간·RSS를 남긴다. NN을 카메라15Hz와 같다고 가정하지 않는다.

```text
run-001/
├── yolop-bench-result.json   # 원 기한·종료·관측/NN/pairing·writer 통계
├── bench-manifest.json      # 자산/graph 선언·원 context·첫 NN layout/golden
├── samples/0001/
│   ├── source.json          # 원 RGB/depth/NN 시각·key·실제 runtime tensor metadata
│   ├── sample-index.json    # 파일별 길이·SHA256
│   ├── rgb.nv12
│   ├── depth.raw16le
│   ├── nn-buffer.bin
│   └── *.fp16               # 검증된 runtime offset에서 분리한 세 layer
└── failure/                 # 발생 시 최초 실패의 제한된 미검증 원자료
```

이 출력은 capture/replay v1 dataset이 아니며 기존 `replay`에 입력하지 않는다. NN 연속 기록·재생은 별도 형식/작업으로 확장한다. 실패 원자료는 `raw_unvalidated=true`이며 정상 tensor나 관측으로 승인하지 않는다. ImageManip pixel equivalence·실제 NN semantic parser·metric 보정은 별도 검증이다. 성공한 장치 벤치도 `nn_ready=false`, `metric_ready=false`, `runtime_readiness=not_evaluated`를 유지한다.

정상 종료0, 런타임/검증/필수 입력 또는 pairing 정체1, 인수·profile·기존 출력 경로 오류2, SIGINT130/SIGTERM143이다. 저장 drain과 Device close를 동일15초 종료 예산에서 병행하고 실제 A 종료 전 새 owner를 시작하지 않는다. 정상적인 기한 종료는 무손실15Hz·트랙 인식 정확도·주행 준비의 수락을 뜻하지 않는다.

최종 결과 파일은 별도 I/O 자식이 임시 파일을 준비한 뒤 부모의 COMMIT/ABORT 결정을 받아 게시한다. 그 결정 전 SIGINT/TERM은 중단 결과를 저장하고, 결정 뒤 도착한 신호는 `final_receipt.too_late_signals`에 기록하며 확정 결과를 뒤집지 않는다. 부모는 I/O 자식의 실제 종료까지 신호와 소유권을 유지한다. 최종화는 I/O5초+회수2초의 원7초 예산에서 한 번만 시도한다. 저장 실패·ACK/실종료 불명은 성공이 아니며, 정전·SIGKILL에 대한 완전 내구성 보장은 아니다.

## 카메라 없는 원본 재생

`replay`는 위 version1 기록을 읽어 RGB NV12·native depth RAW16_LE 원본 bytes를 index 순서대로 재생한다. DepthAI·NumPy·OpenCV 설치나 카메라 연결은 필요 없다. 원 device/SDK/J 시각, stream별 순번, source key와 capture context를 유지하며, 저장 누락을 보간하거나 RGB/depth를 같은 노출로 재정의하지 않는다.

```bash
xenova-vehicle replay \
  --dataset "$HOME/xenova-data/captures/bench-001" \
  --output "$HOME/xenova-data/replays/bench-001-run-001" \
  --speed 1 --json
```

`--speed 1`은 원 `received_j_us`의 상대 수신 간격으로 대기한다. 검증 시간은 재생 간격에 포함하지 않는다. `--speed 0`은 대기 없이 읽고, 양수는 최대16배속까지 허용한다. 처리 지연이 생겨도 프레임을 버리거나 다음 목표 시각을 밀지 않으며 지연량을 결과에 남긴다. 원 관측 시각과 재생 경과 시각은 별도 필드다. `--profile FILE`을 추가하면 내장된 수집 프로필과 정확히 일치하는지도 검사한다. 생략 시 manifest의 프로필을 사용한다.

재생 전 모든 indexed payload의 SHA256·길이·형식, 프로필 hash·context, stream별 순번/시각 증가, 원 종료 결과와 기록 통계를 검사한다. 재생 중에도 payload hash와 파일 변경을 다시 확인한다. 첫 사전 검증에서 하나라도 손상되면 프레임을 전달하지 않는다. 입력은 읽기만 하며 출력은 입력과 분리된 새 디렉터리여야 한다. SHA256 검사는 파일 무결성 대조이며 촬영 출처를 인증하는 서명은 아니다.

기본값은 완전한 정상 종료 기록만 허용한다. 중단·종료 결과 부재·마지막 index 줄 잘림·index에 없는 payload는 `--allow-incomplete`를 명시해야 한다. 이때도 newline으로 끝난 검증 가능한 index 행만 사용하고, 버린 꼬리 bytes와 미참조 payload를 결과에 남긴다. 참조 파일 부재·hash 오류·잘못된 행/경로/문맥은 이 옵션으로 허용하지 않는다. 정상 수집에서 명시적으로 센 저장 drop이나 stream 순번 간격은 불완전 종료와 구분해 보고한다. 정전으로 manifest 자체가 손상된 기록을 복원하는 도구는 아니다.

```text
bench-001-run-001/
├── replay-result.json   # 원 기록 완전성·hash 검사·재생 완료·통계·오류
└── events.jsonl         # 전달 순번·원 index metadata·별도 재생 시각
```

CLI는 영상창 없이 원본을 읽고 진단 trace를 남긴다. 알고리즘에는 아래 API의 `consumer`로 실제 `RawFrame.payload`와 원 metadata를 전달한다. callback은 동기 실행되며 예외가 나면 중단한다. 원본은 NV12/RAW16_LE로 유지하고 화면 표시·색 변환은 consumer의 책임이다.

```python
from pathlib import Path
from xenova_vehicle.recording.replay import run_replay

def consume(sample):
    frame = sample.frame  # stream, payload: bytes, sequence, device_us, sdk_us, received_j_us
    source = sample.metadata  # 원 index 행 사본; source_key와 clock evidence 포함
    # 인지 함수에 frame과 source를 전달한다.

report = run_replay(Path("/data/captures/bench-001"), speed=0, consumer=consume)
```

`replay_completed`는 선택한 입력의 재생 완료, `source_complete`는 원 기록의 완전성이다. 부분 기록 재생을 완료해도 원 기록을 완전하다고 바꾸지 않는다. replay마다 새 `replay_instance`를 발급하고 `input_kind=REPLAY`, `live_authority=false`, `metric_ready=false`를 유지한다. 운영 broker·차량 제어는 열지 않는다.

종료 코드는 재생 완료0, 검증/재생 실패1, 인수·프로필·기존 출력 경로 오류2, Ctrl+C130, SIGTERM143이다. 대기 중에는 최대50ms마다 취소를 확인한다. 이는 동기 consumer나 저장 장치 I/O의 강제중단 기한을 뜻하지 않는다. version1 입력 상한은 payload 합계4GiB, 10,000프레임, index64MiB·행32KiB, manifest2MiB, 원 결과64KiB다. 진단 trace는96MiB로 제한한다. 전체 영상은 메모리에 적재하지 않으며 index metadata는 상한 내에서 보관한다.

## 테스트

`vehicle/Jetson`에서 표준 라이브러리 테스트를 실행한다.

```bash
PYTHONPATH=src python -m unittest discover -s tests/unit -v
PYTHONPATH=src python -m unittest discover -s tests/integration -v
PYTHONPATH=src python -m unittest discover -s tests/replay -v
```
