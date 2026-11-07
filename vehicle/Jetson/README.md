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

## YOLOP 선택 진단 표본 재생

`yolop-replay`는 `yolop-bench`가 저장한 선택 표본의 RGB·native depth·NN 원본을 카메라 없이 읽는다. `depthai`·NumPy·OpenCV·네트워크나 원 모델 파일이 필요하지 않다. 최대8개 선택 표본의 재생이며, 전체 주행 시간의 연속 NN 녹화를 뜻하지 않는다.

```bash
xenova-vehicle yolop-replay \
  --dataset "$HOME/xenova-data/yolop-bench/run-001" \
  --output "$HOME/xenova-data/yolop-replay/run-001" --speed 1 --json
```

모든 선택 표본의 파일·해시·원 문맥·실제 NN layout/FP16·파생 layer slice를 먼저 검사하고, 전달 직전 파일 수명을 다시 확인한다. 원 profile의 Linux 자산 경로는 기록 값으로 보존한다. 과거 실행의 자산 hash는 provenance이며 현재 모델 파일 검사나 장치 readback의 증거로 바꾸지 않는다. index에는 metadata 자체의 외부 서명·hash 결박이 없으므로 모든 metadata를 함께 고친 변조의 진위까지 인증하지 않는다.

기본값은 불완전 기록을 거절한다. `--allow-incomplete`는 온전한 index가 있는 선택 prefix를 허용한다. writer가 마지막에 쓰는 `sample-index.json`이 없는 마지막 꼬리만 제한적으로 제외하며, 이미 index가 있는 표본의 손상·유실·NaN을 건너뛰지 않는다. 실패 원자료와 writer drop도 따로 보고한다. 선택 후보의 drop을 연속 NN frame 손실률로 해석하지 않는다.

정상 꼬리는 RGB→depth→NN buffer→세 layer→source 순서의 prefix여야 하며 마지막 파일만 짧을 수 있다. 깊이 수신 시각은 저장 SDK/J의 단조 인과관계와 RGB bridge에 투영한 가능한 age 구간을 검사한다. 원 depth별 bridge는 저장돼 있지 않아 정확한 depth freshness를 재입증하지 않는다. API와 trace의 `depth_time_qualification`은 이 호환성 검사 한계를 별도로 표시하며 원 metadata는 그대로다.

`--speed 1`은 원 네 stream 수신 J의 최댓값인 bundle availability 간격을 따르고, `--speed 0`은 대기 없이 전달한다. 새 replay instance/timeline과 원 device·SDK·J 시각·순번은 별개다. 출력은 `REPLAY`, `live_authority=false`, `nn_ready=false`, `metric_ready=false`다.

```python
from pathlib import Path
from xenova_vehicle.recording.yolop_replay import run_yolop_replay

dataset = Path("/path/to/recorded-yolop-bench")

def consume(sample):
    rgb_bytes = sample.rgb.payload
    depth_bytes = sample.depth.payload
    nn_bytes = sample.nn_output.payload
    detection_bytes = sample.payloads["det_out.fp16"]
    original_descriptor = sample.metadata

result = run_yolop_replay(dataset, consumer=consume, speed=0)
```

API consumer는 소유 bytes와 원 descriptor를 받는다. 새 검사 비용은 재생 진단으로 구분하며 원 `validation_elapsed_us`를 덮어쓰지 않는다. CLI 출력 디렉터리의 `events.jsonl`에는 원 descriptor·payload 길이/hash·재생 timing을, `yolop-replay-result.json`에는 원 기록의 완료 여부와 재생 완료 여부를 따로 남긴다. 마지막 trace close 후에도 API 취소를 확인한다. 결과 저장의 최종 확정 뒤 신호는 API/stdout 전용 `finalization.too_late_signals`로 기록한다. 정상 재생0, 손상/미허용 불완전/실행 오류1, 잘못된 인수·기존 출력 경로2, 확정 전 SIGINT130/SIGTERM143이다.

## RGB·native depth·YOLOP 후보 해석 API

`YolopDecoder`는 위 선택 재생 표본을 BGR/native depth와 검출·road/lane 후보로 바꾼다. 기존에 승인한 NumPy와 OpenCV는 decode 호출에서만 import한다. 카메라·모델·네트워크를 열지 않으며 기본 CLI·doctor·수집 경로에서는 이 decoder를 호출하지 않는다.

```python
import json
from pathlib import Path
from xenova_vehicle.perception.observation import YolopDecoder
from xenova_vehicle.perception.yolop import YolopPostprocessConfig
from xenova_vehicle.recording.yolop_replay import run_yolop_replay

dataset = Path("/path/to/recorded-yolop-bench")
manifest = json.loads((dataset / "bench-manifest.json").read_text())
decoder = YolopDecoder(manifest, YolopPostprocessConfig(confidence=0.5, iou_threshold=0.3))

def consume(sample):
    observation = decoder.decode(sample)
    shared_bgr = observation.rgb.bgr             # uint8 HWC 360x640x3
    native_z_mm = observation.depth.depth_mm     # readonly <u2 480x640, RIGHT axial Z
    vehicle_candidates = observation.yolop.detections_source
    road_candidates = observation.yolop.road.source  # uint8 360x640
    lane_candidates = observation.yolop.lane.source
    provenance = observation.provenance

result = run_yolop_replay(dataset, consumer=consume, speed=0)
```

한 표본에서 NV12→BGR 변환을 한 번 수행하며 소비자들이 반환 결과를 공유한다. BGR은 새 소유 C-packed 배열, depth와 NN layer는 원 소유 bytes를 유지하는 readonly view다. 원 표본·frame·packet을 강하게 참조하므로 SDK 객체 수명에 의존하지 않으며 다음 decode가 이전 결과를 덮어쓰지 않는다. `decode_rgb`/`decode_depth`는 같은 packed RawFrame 계약의 독립 함수로도 쓴다. row padding·형식·길이가 다르면 거절한다. depth0은 invalid/unknown이며 `has_measurement`는 D!=0 표식이다. 이 값으로 RGB 정렬·metric 신뢰도·빈공간을 승인하지 않는다.

YOLOP는 실제 runtime tensor의 offset/stride를 읽고 FP16 det를 FP32로 바꾼 뒤 strict objectness 및 obj×class threshold를 적용한다. confidence0.5/IoU0.3은 임시 시험값이다. class0는 차량 후보이며 콘·박스 등 모든 장애물 class를 뜻하지 않는다. 프로젝트 NMS 정책은 FP32 내부 연산 후 binary64 threshold와 strict IoU 비교, score 내림차순·동점 원 row 오름차순이다. 모든6300행을 보며 pre-topK 없이 최대500개 NMS prefix에서 멈추고 미분류 잔여를 계수한다. 이 비교 상한은 실행시간 보장이 아니며 원 Torchvision kernel parity도 미확인이다. nonpositive 크기 또는 FP32 변환 뒤 접힌 box는 제외하고 센다. 양수의 작은 box에 임의2px 하한을 추가하지 않는다. full NN 좌표 NMS 뒤 content ROI clip/역변환하므로 padding 검출이 다른 후보를 억제한 뒤 source에서 사라질 수 있다.

det의 모든 obj/class와 두 segmentation score는 [0,1]이어야 한다. 범위 밖 유한 값도 전체 decode를 실패시키며 sigmoid/clamp로 수리하지 않는다. 이는 고정 모델의 score 정의역을 보호하는 프로젝트 정책이다. 원 packet은 호출자가 유지한다. NaN/Inf·layout 검사는 NNPacket 경계의 책임이다.

road/lane은 두 channel argmax이며 동점은 첫 channel이다. `argmax_nn`은320×320 원 label, `observed_nn`은 rows70..249만 관측인 별도 bool mask, `content`는180×320이다. padding label은 배경·FREE 관측이 아니다. source mask는 `source[y,x]=argmax_nn[70+y//2,x//2]`로 정확히2배 nearest 복원한다. 박스는 연속 image-edge 좌표로 x_source=2×x_nn, y_source=2×(y_nn−70)을 적용한다. 실제 ImageManip 입력 픽셀이 보존되지 않아 pixel equivalence는 미확인이다.

결과는 원 source/time·context와 model/preprocessing/parser/config identity를 연결한다. `source_metadata`와 provenance는 사본이며 새 `costs_us`가 원 검사 비용을 덮어쓰지 않는다. S03 표본의 결박을 다시 검사하지만 파일 hash는 reader 책임이다. `REPLAY`, `live_authority=false`, `nn_ready=false`, `metric_ready=false`와 원 depth 시간 한계를 유지한다. 후보 mask/box는 차선·주차칸·occupied/free 최종판정이나 차량 제어 권한이 아니다.

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

## 순수 준비·기한 정책 API

`runtime.lifecycle_policy`는 실행 adapter가 검증한 개별 사실과 정수 J 단조시각(us)을 받아 준비·원 기한을 판단한다. 내부에서 시계·파일·장치·프로세스·네트워크를 열지 않는다. 문서 폴더를 runtime config로 읽지 않으며 기존 벤치 CLI는 이 API를 호출하지 않는다.

- `AbsoluteDeadline.after(event_us, duration_us, now_us=..., cap_us=...)`는 원 사건과 상한으로 한 번 기한을 계산한다. `now >= expires_us`는 만료인 half-open 정책이다. bool/float/음수·u64 overflow·미래 사건은 거절한다.
- `AStageWatch`는 A의 명시 기동/정리 경로와 원 cold90초·stage15/30/30/15초를 검사한다. 같은 state 재전달은 새 시작이 아니다. `STREAMING`에는 현재 `CaptureWarmup`의 준비 근거가 필요하고, `CLOSED`에는 S가 실제 session 정리·process exit를 확인한 `SessionExitEvidence`가 필요하다. 이 API가 실제 Device/exit 증거를 생성하지 않는다.
- `LocalSetupWatch`는 M/D의 spawn→HELLO15초만 검사한다. `BindWatch`는 현재 worker·binding hash·원 request ID의 issue/send/ACK를 구분한다. EAGAIN 동안 `sent`를 호출하지 않으며 늦은 송신도 issue+5초를 넘길 수 없다. HELLO 뒤 정상 외부 대기는 cold/setup 기한의 연장이 아니다.
- `CaptureWarmup`은 queue 설정 뒤 현재 SDK clock barrier보다 새로운 서로 다른 RGB/depth 후보30개를 센다. 전달받은 원 stage 기한도 시작+15초를 넘길 수 없고 더 짧은 상한은 유지한다. A의 첫 STREAMING은 현재 WARMUP의 start/cap에 결박한 준비만 수락한다. 두 stream의 seq 숫자는 달라도 된다. `ParserWarmup`은 현재 capture 준비와 현재 M의 원 BIND_ACK가 모두 생긴 두 번째 원 사건부터15초 안에 정상 paired parser 결과5개를 연속 확인한다. 출력0개도 이 timer가 만료되며, current fresh parser 실패의 input key도 기억해 같은 입력 재시도를 새 성공으로 세지 않는다. empty detection은 정상 parser 결과일 수 있다.

`CaptureScope`의 RawBinding hash는 pipeline/config/calibration/stream/SDK bridge/clock 등을 포함한 검증된 전체 binding을 뜻한다. canonical hash와 개별 증거의 실제 SDK·형식·동기·원 age·lease 검증은 lower adapter 책임이다. 내부 `SourceStamp`와 개별 `CapturePairEvidence`/`ParserEvidence`는 payload나 wire schema가 아니다. kernel은 stream별 마지막 metadata와 유한 count만 보관한다. REPLAY·외국/과거 scope·중복 입력은 준비에 더하지 않고 원 시각을 현재 now로 바꾸지 않는다.

M-only 교체는 같은 capture tracker와 새 mapper/request 문맥의 parser tracker를 사용한다. 실제 source 교체에는 새 scope/tracker가 필요하며 이전 객체를 현재 권위로 사용하지 않는다. 원 clock lease의 정상 갱신·일반 관측은 source 교체가 아니다. parser 준비에는 Pi 임무·PublicationBinding·metric/world 준비가 필요하지 않다. 무작업 WAIT는500ms hang이 아니며, WAIT 상태에서도 실제 parser job이 활성이라면 `active_progress_expired(..., active_job=True)`가 그 활성 작업만 검사한다. NN의 추론률·post-warmup freshness는 이500ms 값으로 정하지 않는다.

REQUIRED의 초기 준비5개만 구현한다. ABSENT는 실제 NN stream이 없는 source scope이며 NN_READY를 발급하지 않는다. OPTIONAL 상세 parser는 `OPTIONAL_PARSER_NOT_IMPLEMENTED`로 표시하고 timer를 시작하지 않는다. `classical_fallback_allowed=True`일 때의 OPTIONAL 정책 허용은 실제 classical/metric 경로 수락이 아니다. warmup 완료는 지속 freshness·정확도·METRIC_READY/WORLD_VALID/DRIVE_PERMITTED가 아니며 해당 권한을 올리는 API는 제공하지 않는다. 실제 worker·IPC·resource guard·재시작 실행은 호출하는 adapter의 별도 책임이다.

## LocalEnvelope 구조와 유한 대기열

`runtime.local_envelope`의 `decode_structure(raw_bytes)`와 `encode_structure(fields)`는 `StructuralPacket`을 반환한다. 공개 constructor도 같은 검사를 수행한다. exact12 outer key·canonical UUID/u64string·kind/domain·UTF-8 JSON과 상한을 검사한다. payload는 bounded opaque JSON object다. kind별 unknown payload key/type/role·peer·sequence·원 기한·cleanup 허가는 별도 admission 책임이며, 구조 성공으로 작업을 실행하지 않는다.

packet은 최대4096B이고 root object를 depth1, payload object를 depth2로 세어 container depth6까지 허용한다. 모든 깊이의 decoded duplicate key, NaN/Inf·overflow float, lone surrogate, UTF16/BOM·잘못된UTF8·잘림·뒤 데이터·packet 앞뒤 whitespace를 거절한다. 내부 whitespace와 유효한 JSON 지수 표기는 허용하며 원 bytes를 보존한다. `packet.fields`/`packet.payload`는 새 사본이다. 이를 수정해도 큐·TX의 원 bytes는 바뀌지 않는다.

encoder는 exact builtin JSON tree를 먼저 검사한다. node/container length/string character work≤4096, integer bitlength≤13653, container depth≤6와 cycle 검사를 거친 뒤 실제 JSON 문자열 escaping/UTF8·숫자 표현 비용을4096B 예산에 누적한다. `sort_keys=True, ensure_ascii=False, separators=(',',':'), allow_nan=False`로 출력하고 최종 bytecap을 다시 검사한다. 한 호출 동안 입력 tree를 바꾸지 않는 단일 owner 계약이다. decoder는 실제 수신4096B를 기준으로 수락한다. compact 지수 표기의 유한 float가 re-encode 때 길어질 수 있으므로 decode→encode 성공이나 bytes 동일성을 원 packet 보존의 대체물로 쓰지 않는다.

`runtime.mailbox.RxMailbox`는 peer별 general8/latest HEARTBEAT1/terminal2를 보관한다. FAULT/STOP/STOPPED는 terminal이며 `pop()`은 terminal FIFO→general FIFO→heartbeat, 빈 경우None이다. `put(packet)`의 `PutResult`는 ACCEPTED/REPLACED_HEARTBEAT/BUSY_GENERAL/BUSY_TERMINAL을 구분하고 `.accepted`/bool은 접수만 뜻한다. 포화한 class를 덮어쓰거나 다른 class를 침범하지 않는다. heartbeat만 도착 최신 한 개로 교체한다. 실제 peer·sequence admission 뒤 사용하는 staging이며 논리 중복·원 순번 판정을 대신하지 않는다. RX 처리 우선순위는 이미 sequence가 붙은 TX wire 송신 순서와 별개다.

`PendingTx.accept(packet)`은 빈 TX slot에만 접수한다. True는 실제 송신 완료가 아니다. `peek()`은 같은 `PendingTransmission` handle과 원 bytes를 돌려주며 EAGAIN 가정에서 변하지 않는다. 실제 전체 송신 완료 뒤 `finish(expected_handle)`로 현재 접수만 해제한다. 같은 packet을 다시 접수해도 새 handle이므로 옛 finish가 새 pending을 지우지 못한다. terminal/heartbeat도 pending을 교체하지 않는다. wire ACK·원 job 완료·sequence 발급·재시도·만료 삭제 기능은 없다.

두 구조는 thread/lock/clock/sleep/I/O를 만들지 않는다. RX 최대11packet과 TX1packet의 encoded bytes 상한은49152B이며 Python 객체와 소비자가 따로 보유한 사본의 overhead는 별도다. local fence·기한 판정은 큐 상태와 관계없이 상위 actor가 수행해야 한다. 실제 socket/서비스/lease/메모리 회수와 full LocalEnvelope 의미 검증은 이 API가 제공하지 않는다.

## 초기 lifecycle 진술과 수신 정책

`runtime.lifecycle_messages`는 `LifecyclePeer(service_instance, channel_epoch, worker_instance, role, release_sha256)`로 caller가 등록한 현재 child 문맥을 고정한다. `parse_lifecycle(packet, peer, receiver_role)`와 공개 `LifecycleMessage` constructor는 네 kind의 정확 payload·방향·현재 문맥을 검사한다. `receiver_role`은 S 또는 등록 child role이다. S sender는 service UUID, child sender는 worker UUID다. 실제 OS peer 확인은 외부 adapter 책임이다.

이 초기 배포 profile은 LIFECYCLE·revision `"0"`만 사용한다. HELLO는 pinned role/worker/release와 capability_mask `"0"`이 일치해야 한다. HEARTBEAT는 role별 state와 둘 다 null 또는 canonical u64string인 원 progress tuple을 받는다. progress 시각은 원 issued 이후일 수 없다. STOP은 양수 revision·1..32 printable ASCII reason·issued≤drain≤expires를 검사한다. reason은 진단문이다. STOPPED는 exact bool leases_released, A의 bool device_closed 또는 M/D의 null, 전 role의 null checkpoint_job, STOPPED/FAILED status를 받는다. STOPPED 성공 진술에는 leases_released=true 및 A device_closed=true가 필요하며 FAILED는 부분 bool을 유지한다. BIND/BIND_ACK/PROGRESS/FAULT/SLOT/DB_JOB은 이 profile에서 거절한다.

`LifecycleReceiver(peer, receiver_role).receive(packet, now_us=..., cap_us=...)`는 typed/current·원 lifetime을 먼저 검사하고 last-seq를 판정한 뒤 새 사건을 수락한다. issued≤now<expires이며 cap이 있으면 원 expires가 이를 넘을 수 없다. 첫 정상 seq는1 이상 gap을 허용하고 invalid 높은 seq는 상태를 바꾸지 않는다. 마지막 동일 seq·동일 원 bytes는 `DUPLICATE_NO_EFFECT`와 `message=None`, 새 사건은 `ACCEPTED_NEW`와 진술을 반환한다. 동일 seq의 다른 bytes·과거 seq는 거절한다. canonical 재encode로 원 bytes를 교체하지 않는다. 수신 도착 순서로 이 검사를 마친 다음 mailbox 우선처리를 한다.

HELLO는 channel당 한 번이며 HELLO 전 HEARTBEAT는 거절한다. child는 HELLO 전에도 현재 S의 STOP을 받는다. S의 `register_stop_intent(packet, now_us=..., cap_us=...)`는 원 outbound STOP 하나를 고정하며 실제 전송을 뜻하지 않는다. 외부 adapter가 전체 송신 성공을 확인한 뒤 `record_first_sent(packet, event_us, now_us=...)`를 호출한다. event_us는 **성공한 그 송신 시도 직전**의 J lower bound이고 now_us는 성공 뒤 등록 시각이다. EAGAIN/short send에는 등록하지 않는다. 이 API가 실제 send 사실을 생성하거나 독립 검증하지 않는다. 두 등록 모두 S RX highwater를 바꾸지 않으며 원 수명 안의 정확 중복은 False, 다른 raw/first-send 사건은 충돌이다.

원 STOP은 worker/channel 수명 동안 교체·연장할 수 없다. active STOP 뒤 새 HELLO도 거절한다. STOPPED는 HELLO 전이라도 원 STOP의 exact request/revision·현재 문맥·first_sent를 확인하면 받는다. 원 STOP issued≤first_sent≤보고 issued≤now이고 보고 expires≤원 STOP expires가 자동 적용된다. 보고 하나만 수락하며 원 STOP은 보고 뒤에도 유지한다. now≥원 drain은 이미 소진된 drain이고 새 예산이 아니다. 만료 보고를 허용하거나 작업·메모리를 FREE로 바꾸는 예외는 제공하지 않는다. 새 worker/channel은 caller가 새 receiver로 등록한다.

`snapshot`은 frozen 현재 문맥/highwater/HELLO/last packet/원 STOP·first_sent/보고만 보관하며 무한 history가 없다. fields/payload는 사본이다. 단일 owner가 private state를 수정하지 않는 계약으로 쓴다. 내부 clock/sleep/I/O나 서비스 실행은 없다. heartbeat state는 실제 준비·progress 증명이 아니며 A device_closed=true도 producer의 자원 수명 정리 진술이다. 생성 미시도를 명시 확인한 경우를 포함하지만 변수None·constructor 불명으로 true를 추정하면 안 된다. typed STOPPED는 실제 Device.close/process exit/lease 반환·회수 권한을 발급하지 않는다.

## 순번 없는 lifecycle draft와 송신 준비

`runtime.tx_outbox.LifecycleDraft(peer, receiver_role, kind, request_id, issued_us, expires_us, payload, cap_us=None)`는 위 네 kind의 순번 없는 원 진술을 보관한다. receiver_role은 기존 typed profile의 수신 방향이다. caller가 가진 실제 peer/request UUID·원 시각·원 cap을 요구하고 sequence나 임시 UUID를 만들지 않는다. outgoing draft는 issued<expires, expires≤원 cap을 검사한다. 기존 수신 parser의 형식/lifetime 분리는 유지한다. native payload는 exact dict≤5, exact str key/각 문자열≤64, exact str/bool/null의 flat scalar를 검사하면서 작은 소유 사본을 만든 뒤 공유 typed 규칙을 적용한다. subclass/custom value/nested container는 set/hash/복사/serialize 전에 거절한다. 입력 dict의 호출 중 동시변경과 private state 수정은 지원하지 않는다. fields는 sequence 없는 실제11field 사본, payload도 새 사본이며 아직 encoded packet이 아니다.

`TxOutbox(peer, receiver_role)`는 고정 sender/channel/방향의 general8/latest heartbeat1/terminal2와 별도 pending1을 보관한다. 초기 general은 HELLO, terminal은 STOP/STOPPED다. `put(draft, now_us=..., cap_us=None)`은 현재 문맥·원 수명 뒤 현재 queue+pending의 request_id를 검사한다. 같은 ID의 전체 원 fingerprint(kind/peer/방향/request/body/issued/expires/원 cap)가 같으면 DUPLICATE_NO_EFFECT, 다르면 예외다. 새 HB snapshot에는 caller의 실제 새 request ID가 필요하다. 이는 초기 배포 선택이며 향후 여섯 kind의 원 operation 계약에 자동 적용하지 않는다. 중복 검사 뒤 새 HB만 coalesce하고 pending은 바꾸지 않는다. 포화는 BUSY_GENERAL/BUSY_TERMINAL이며 기존 상태를 유지한다. TxPutResult의 accepted/bool은 새 접수·HB 교체만 True이고 중복은 False다. 원 작업 재전송/완료 권한을 뜻하지 않는다.

`prepare(now_us=..., cap_us=None)`는 pending이 있으면 같은 handle/bytes를 PENDING_CURRENT/PENDING_EXPIRED/PENDING_CAP_EXCEEDED와 함께 반환한다. 다른 queue를 몰래 purge하지 않는다. pending이 없으면 최대11개를 읽어 만료/cap drop 목록과 terminal FIFO→general FIFO→HB의 첫 valid 후보를 계획한다. 그 후보의 실제 next sequence로만 기존 b1 encoder를 호출하고, 새 local PendingTx 접수와 결과 구성이 성공하면 purge·후보 제거·pending·counter를 함께 확정한다. 실패하면 계획한 purge까지 상태를 유지한다. 모두 만료/cap 초과이면 EMPTY와 bounded DraftDrop 목록을 반환하며 purge만 확정하고 번호를 소비하지 않는다. future issue는 예외다. 원 issued/expires/cap/body를 수리하거나 새 TTL을 붙이지 않는다.

새 channel의 counter는1부터 모든 kind를 합쳐 증가한다. MAX 발급 뒤 next_sequence=None이며 valid 후보는 SequenceExhausted로 거절하고 계획한 purge도 확정하지 않는다. 모두 만료면 drop만 처리할 수 있다. 이미 준비한 packet을 포기해도 번호를 환불하지 않는다. public seed/reset/resume/자동 channel UUID 발급은 없다. 같은 channel의 중복 outbox 생성과 은퇴한 객체 호출을 막는 전역 registry/routing은 실제 adapter 책임이다.

`finish(expected_handle)`은 외부에서 실제 full send 성공을 확인한 뒤의 bookkeeping이다. 늦은 실제 성공 결과 정리와 새 dispatch 허가는 다르다. `abandon(expected_handle, cause=DiscardCause...)`은 현재 exact handle만 별도 PendingDrop(handle,draft,cause)로 반환하고 내부 holder를 새 빈 객체로 바꾼다. cause는 EXPIRED/CAP_EXCEEDED/FENCED/CANCELLED인 local 포기 진술이며 실제 미송신을 증명하지 않는다. 성공 finish를 호출하지 않고 old/foreign handle은 새 pending을 지우지 못한다. EAGAIN은 같은 pending을 유지한다. 이 API는 transport attempt를 실행하거나 추적하지 않으며 pending handle·같은 μs timestamp를 attempt ID로 쓰지 않는다. 실제 send 결과 반환 전 다른 thread/signal callback의 outbox 변이·포기는 금지한다. short send/성공 불명은 후속 channel fault/retirement 수습 대상이다.

S는 queue 선택 전부터 즉시 local QUIESCING/fence를 적용해야 한다. prepared STOP bytes는 send 전에 b2 원 intent로 등록하고, 등록이 실패하면 송신하지 않는다. 실제 full-send 성공 뒤 성공 시도 직전 lower bound를 first_sent로 등록한다. 등록된 STOP을 local abandon해도 b2 원 latch·S fence·원 cleanup 기한은 유지하며 새 seq/request/expiry로 STOP을 재발급해 우회하지 않는다. full-send 성공 후 등록 시각이 만료됐다고 미송신 포기로 바꾸지 않는다. 이 actor/실제 송신/회수 경로는 현재 API 밖이다.

snapshot은 frozen queue tuple·원 draft·pending handle·counter이며 counts는 사본이다. 현재 held ID 최대12개만 보며 완료·교체·포기 뒤 과거 history는 남기지 않는다. queued encoded bytes는0이고 encoded_held_bytes는 실제 pending bytes만 센다. 고정 네 kind의 작은 scalar/JSON 상한과 별개로 최종 actual-sequence b1 encode는4096B 상한을 유지한다. native Python 객체·소비자 사본 overhead는 별도다. queue 용량·priority 통과는 lifecycle 출력 허가가 아니다. 미송신 HELLO 포기 뒤 HB는 실제 RX가 거절하며, prior HELLO가 있는 STOPPED 뒤 HB 진술의 기존 수신 정책은 유지한다. 실제 OS peer/send/exit/lease/READY·post-success 원 raw 재답 ledger는 제공하지 않는다.

## 한 raw arena의 소유권 원장

`runtime.raw_leases`는 S 단일 owner가 등록한 raw arena 두 slot의 내부 정책이다. 실제 메모리·token·Device·파일·clock을 열거나 UUID를 생성하지 않는다. `Endpoint(channel_epoch, worker_instance)`, `RawScope(service_instance, arena_id, writer, raw_binding_sha256, arena_name, arena_size_bytes=4194304, arena_role="RAW")`로 실제 caller 등록값을 고정한다. 이름은 유한 printable ASCII이고 정확한 OS 이름 profile·실 arena 존재/소유 검증은 별도 단계다. RawBinding SHA 형식 검사가 전체 binding 본문·SDK/보정을 승인하지 않으며 bench NN context hash와도 다르다.

`RawLeaseLedger(scope, reader, publication_sha256=None)`는 현재 A writer와 M reader의 서로 다른 역할 문맥을 보유한다. WriteKey에는 A channel을 포함한 RawScope·slot·generation·write_token, ReadKey에는 원 WriteKey·M endpoint·read_token이 있다. M 문맥을 writer key에 덮어쓰지 않는다. 모든 공개 record는 exact type/UUID/SHA/u64/상한을 검사하는 frozen 값이다. 외부 dict/배열을 보관하지 않고 snapshot도 frozen 두-slot tuple이다. private state 직접 수정이나 호출 중 동시 변이는 지원하지 않는다.

`LeaseRequest(request_id, issued_us, expires_us, cap_us=None)`의 원 수명은 issued≤now<expires이며 cap이 있으면 expires≤cap이다. `reserve_write(slot, request, write_token, now_us=...)`는 FREE에서 generation을 증가시켜 WriteGrant와 WRITING을 예약한다. 초기 generation은0, 첫 grant는1이다. A의 WRITING 또는 READY 교체 ticket은 전체 두 slot에 걸쳐 outstanding 하나다. dequeue·미송신 관측으로 없애지 않는다. `filled(FilledInfo(key, payload_length, descriptor_sha256, completed_us), now_us=...)`는 원 copy 기한 안의 정확한 완료 진술을 READY로 바꾼다. 길이/해시 형식·slot 상한만 검사하며 실제 descriptor/CRC/body·capture age는 검사하지 않는다.

`offer(slot, now_us=...)`는 현재 미claim READY의 FilledInfo를 가진 Offer를 반환한다. read token이나 읽기 권한이 없다. `ReadRequest(offer, reader, request, purpose="PARSER_WARMUP", publication_sha256=None)`와 caller의 실제 read_token을 `acquire(..., now_us=...)`에 주면 원장에 ReadLease와 IN_USE를 먼저 예약한다. ACQUIRED 송신 대기/EAGAIN이어도 새 WRITE·READY 교체를 허용하지 않는다. M의 active read도 하나다. parser는 pub null일 때 Pi/DB/odom/map을 요구하지 않는다. non-null pub는 등록된 SHA와 exact 대조하지만 전체 PublicationBinding 검증이 아니다. METRIC_MAP은 원 capture metadata의 사전 전달·완전 MetricJob 계약이 없어 명시 UnsupportedPurpose이며 상태를 바꾸지 않는다. parser 읽기를 metric 사전 조회로 승격하지 않는다.

정상 FILLED 뒤 독립한 새 parser read는 자기 원 request/time/cap을 사용한다. 이미 끝난 copy 작업의 기한을 상속하지 않는다. c1은 그 content의 sensor freshness를 증명하지 않는다. `write_is_current(grant, now_us=...)`와 `read_is_current(lease, now_us=...)`는 local full record/상태·fence와 실제 granted/reserved 하한부터 원 만료 전까지를 검사한다. 실제 publication token이나 메모리 접근 허가를 대신하지 않는다. 만료·취소·신호·STOPPED 보고만으로 WRITING/IN_USE를 FREE로 만들지 않는다.

`release(ViewReturnFact(lease, views_ended_us, writable_posted_us), now_us=...)`는 lower가 모든 view 종료와 writable post1을 확인했다는 원 lease 전체의 진술을 대조한다. reserved≤views_ended≤writable_posted≤now여야 하며, 정확히 보유한 lease는 원 read 기한 이후·fenced/retired여도 bookkeeping으로 FREE가 된다. constructor가 실제 view 종료/sem_post를 확인하는 것은 아니다. 같은 full key의 바뀐 원 request/완료 fact는 conflict다. 이미 반환한 정확 중복은 DUPLICATE_NO_EFFECT, 보유 밖/다른 key는 UNKNOWN_NO_EFFECT이며 새 FREE/token을 만들지 않는다. old 반환이 새 generation/read_token의 lease를 풀지 못한다.

현재 각 slot의 write/ticket와 최근 replacement 요약에 보유한 A request ID, 현재 read와 최근 반환 요약의 M request ID를 유한 scan한다. 같은 ID의 원 slot/time/cap/token/content가 같으면 원 기록만 돌려주거나 효과0이고, 바뀌면 conflict다. 원 필드를 현재 now로 바꾸지 않는다. 이미 수락한 FILLED의 늦은 정확 중복도 IN_USE/FREE를 READY로 되돌리지 않는다. slot별 현재 write/filled 기록은 FREE에서도 마지막 원본으로 남을 수 있으며 active grant와 다르다. 이전 모든 UUID/결과의 무한 history를 제공하지 않는다.

READY 교체는 `begin_replace(slot, expected_key, request, write_token, now_us=...)`가 미claim을 확인하고 ReplacementTicket을 예약하는 단계부터 시작한다. canonical READY는 유지하지만 ticket 동안 해당 slot의 ACQUIRE/다른 교체를 막는다. planned key는 아직 새 WriteGrant가 아니다. lower가 exact ticket에 대해 ready consume 성공→writable post 성공을 확인한 `ReplacementSyncFact(ticket, ready_consumed_us, writable_posted_us, finished_us)`를 `complete_replace(..., now_us=...)`에 주어야 새 generation/WRITING/grant를 확정한다. c1은 token 작업을 실행하지 않는다. MAX는 외부 작업 전에 거절하고 reset/자동 새 arena를 만들지 않는다.

부분 실패(None/None 또는 consume만 완료)·늦은 fact·source fence·외부 token 작업 뒤 local commit 실패는 ticket/fact를 유지한 sticky fault/fence로 남긴다. 정상 READY로 rollback하거나 writable post를 추가하지 않는다. 정확 중복 fact는 효과0이고 바뀐 terminal fact는 conflict다. 새 grant의 granted_us는 실제 local commit 시각이며 원 request 기한을 늘리지 않는다. `fence(now_us=..., retired=False)`는 사용권만 철회하고 원 소유 기록을 유지한다. fenced arena의 cleanup FREE가 새 write 사용권을 되살리지 않는다.

원장 하나는 현재2slot, slot당 현재 write/filled·최근 cleanup summary 하나, A outstanding write/ticket 하나와 M active read 하나만 보유한다. ticket은 이전 grant/history를 재귀적으로 붙이지 않는다. owner exit 회수·reader 교체·token 정규화·전역 active/최대1 quarantine set 관리자와 새 arena 생성은 미지원이다. 같은 arena에 원장을 새로 만들어 generation을 초기화하는 우회는 실제 adapter가 막아야 한다. SLOT/BIND wire 지원·owned bytes header/descriptor codec·SHM/SemLock·실 exit/lease 회수·metric/READY·이동 권위를 이 순수 API의 통과로 수락하지 않는다.

## 소유한 P0 raw slot의 바이트 검증

`runtime.raw_slot`은 소유한 `bytes`만 받는 순수 codec이다. SDK·파일·시계·UUID 생성·실제 shared memory/token을 호출하지 않는다. `P0Context(write_key, stream_ids, nn_configured)`는 원장의 전체 `WriteKey`와 caller가 이미 승인한 named stream UUID 표의 소유 사본을 보유한다. `stream_ids`는 NN configured면 rgb_video/depth_right/nn_input0/nn_output0 네 개, 아니면 앞 두 개이며 모두 서로 다르다. public 표는 readonly다. 이 subset 검증은 전체 RawBinding 승인·hash 계산이 아니다.

`SlotEncoding(context, native_descriptor, payload)` 또는 `encode_owned(...)`는 descriptor의 bounded 소유 사본을 검사하고 compact sorted ASCII JSON·header·zero metadata slack·원 body를 합친 새 used-slot bytes를 만든다. encoder에는 아직 없는 FILLED 완료 시각이 필요하지 않으며 fake 완료를 생성하지 않는다. 실제 A는 copy/post 뒤 원 완료 시각으로 `FilledInfo`를 별도 생성해야 한다. payload[0]은 slot offset4096이다.

`ExpectedSlot(context, original_filled_info)`는 원 full write key가 같은 FILLED를 고정한다. `PrefixHeader(header128bytes, context)` 또는 `prefix(...)`는 header만 검사해 `.used_length`를 반환한다. descriptor 길이1..3968, body 길이1..2093056, generation 양수 u64이고, prefix는 메모리 읽기 허가나 내용 검증이 아니다. `OwnedSlot(raw, expected, optional_prefix=None)` 또는 `decode_owned(...)`는 **동일 owned bytes의 header부터 다시** 검사한다. 정확4096+L 길이, 선택한 prefix 원128B, 원 FILLED의 length/descriptor SHA·원 received/bridge.after≤completed를 대조한다. 실제 lease/current·slot 선택·ready acquire와 안정된 copy는 caller의 별도 책임이다.

세 결과 constructor도 helper와 같은 검사를 수행하고 unchecked/validated flag는 없다. 결과 `.raw`, `.header`, `.descriptor_bytes`, `.descriptor_sha256`, `.payload_length`는 frozen 값이다. `.descriptor()`는 새 mutable dict/list 사본, `.payload_bytes()`는 gap/tail 포함 사용 body의 새 bytes 사본이다. decoder는 입력된 immutable owned bytes를 참조하고 작은 header/descriptor 사본·bounded JSON tree만 만들며, encoder는 최대2MiB 새 bytes를 만든다. zero-copy SDK/SHM view를 지원하지 않는다. 호출 중 입력 tree의 동시변경이나 private/frozen 상태의 강제 변이는 지원하지 않는다.

header는 `<8sHHI16sQ16s32sIII28s`, magic `XNVRAW1`+NUL/version1/128B/reserved0이다. 정수는 LE standard size, UUID는 RFC4122 `.bytes` 순서다. descriptor CRC/SHA는 slot `[128,128+D)` 원 bytes, payload CRC는 `[4096,4096+L)` 전체이며 `zlib.crc32(span,0)` unsigned32다. slack `[128+D,4096)`는0, 미사용 slot tail은 입력하지 않는다. header 전체를 덮는 CRC는 없고 descriptor SHA는 body를 덮지 않는다. body와 header payload CRC가 일관되게 바뀐 경우를 인증으로 검출하지 않는다.

exact root6은 `version,profile,raw_binding_sha256,streams,planes,nn`이다. version1/profile `p0-yolop-320-v1`이며 각 stream stamp는 `source_key,sdk_us,received_j_us,bridge_sample,sdk_host_j_interval_us,exposure_us,capture_j_interval_us`다. source key는 `[등록 stream UUID,canonical u64 seq string,canonical u64 device_us string]`이다. SDK/J 시각도 u64string이며 서로 다른 clock domain의 숫자를 직접 정렬하지 않는다. bridge가 있으면 원 `{j_before_us,sdk_us,j_after_us}`에서 ±1us 양자화 여유를 포함한 SDK-host J interval을 재계산한다. Jbefore≤Jafter, 수신−Jafter는0..1000000us, 최종 endpoint는 u64다. bridge/파생 interval은 함께 null일 수 있고 capture interval은 null-only다. 노출값은 sensor별 실제 int0..1000000 또는null, NN은 null-only다. 이 구조가 sensor capture·freshness를 증명하지 않는다.

각 plane exact6은 `offset,length,dtype,encoding,shape,strides_bytes`다. RGB는 NV12/u1/345600B/storage[540,640]/stride[640,1]이며 실제 영상640×360이다. depth는 RAW16_LE/<u2/614400B/[480,640]/stride[1280,2], native RIGHT Z mm·0unknown·RGB 미정렬이다. 선택 NN은 u1/NN_STORAGE/실제1..1048576B/[length]/[1]이다. plane offset은 slot-relative≥4096·64B 정렬·서로 비중첩이고 가장 늦은 plane 끝이 정확4096+L다. NN tensor offset은 NN storage-relative·even이며 plane 정렬과 다르다. 실제 arena 주소에는 외부 slot_base가 별도로 더해진다.

NN configured=true여도 개별 `nn=null`은 정상이며 이때 NN plane/input/output stamp는 모두 없다. present NN은 exact `rgb_parent_key,input_key,output_key,layers`이고 실제 각 stamp key 및 RGB/input/output의 원 seq/device/SDK stamp가 일치해야 한다. 네 stream UUID는 독립이고 depth 순번·시각은 강제로 같게 하지 않는다. stream 간 수신 역전도 허용한다. NN SDK stamp를 추론 종료 시각이나 sensor capture로 바꾸지 않는다.

layer는 실제 다차원 runtime det_out `[1,6300,6]`/order801/75600B와 drive_area_seg·lane_line_seg `[1,2,320,320]`/order17185/각409600B만 받는다. 각 exact7은 `name,data_type_code,order_code,dims,strides_bytes,offset,length`, FP16 code0·packed byte stride·고유3name·even/nonoverlap·실 storage 범위를 검사하며 원 배열 순서와 gap/tail을 보존한다. flat rank1은 이 codec에서 미지원이고 기존 NNPacket의 진단용 지원은 유지한다. 활성 FP16 finite 검사는 codec 밖의 기존 NNPacket/adapter 책임이다. CRC가 맞는 NaN의 형식 수락은 parser/NN_READY 성공이 아니다. `nn_output0`→기존 `nn_output` 이름 mapping과 NNPacket의 계측 clock도 이 API에 포함되지 않는다.

native descriptor는 exact dict/list/str/int/None을 한 번 복사하며 container depth(root1)≤5·node512·string/key64 ASCII·intbits64·list4·dict8을 검사한다. scalar leaf depth6은 허용하고 비순환 alias는 출현별로 복사·계수하며 cycle은 거절한다. 이 bound는 descriptor에만 적용하며 context arena_name128이나 nn_configured bool과 혼동하지 않는다. decoder는 parse 전 raw3968B/depth5를 제한하고 tree/typed bound를 추가 적용한다. parse 할당량을512nodes라고 보장하는 것은 아니다. decoded nested duplicate/unknown·BOM·trailing 다른 JSON·float/nonfinite·bool-int·surrogate/비ASCII semantic을 거절한다. 허용 ASCII unicode escape와 내부/앞뒤 JSON whitespace는 원 Dspan/CRC/SHA에 포함하며 strip/re-encode로 교체하지 않는다.

실제 RawBinding/센서·pixel equivalence·freshness·lease/메모리 발행·owner exit·METRIC_MAP 사전 metadata 전달·BIND/SLOT·15Hz/RSS·READY/주행 자격은 별도 계약이다. codec의 layout-only 결과를 이 권한으로 승격하지 않는다.

## RawBinding 값과 정적 RAW BIND/ACK

`runtime.raw_binding.RawBinding(fields)`는 카메라 세션의 exact13필드를 소유한 canonical 값으로 만든다. service_instance/acquisition_worker/pipeline_instance/capture_epoch/stream_instances/sdk_bridge_epoch/device_id/model_id/parser_id/graph_profile_sha256/calibration_bundle/format_table_sha256/clock_epoch만 허용하며 version을 추가하지 않는다. identity6개는 canonical UUID이고 서로 다른 named stream UUID만 유일해야 한다. `.canonical_bytes`/`.sha256`는 frozen 값, `.fields`는 독립 dict/stream map 사본이다. body hash는 canonical 내용에 적용하며 원 packet의 bytes hash와 다르다.

초기 P0는 rgb_video/depth_right 필수, nn_input0/nn_output0 쌍 선택, jpeg 선택이다. IMU는 별도 profile이며 이 타입에서 미지원이다. NN 쌍이 있으면 model/parser SHA 모두 필요하고 없으면 둘 다 null이다. no-video는 JPEG encoder 제외이며 host RGB는 유지한다. 개별 bundle의 NN 결과 부재는 source binding의 NN 설정을 바꾸지 않는다. device_id는 case를 보존한 `[A-Za-z0-9_-]{1,64}`이고 자동 대소문자 변환이나 첫 장치 대체를 하지 않는다.

model_id는 선택 compiled blob **원 bytes SHA256**이다. parser_id는 전처리·후처리·해석·필요 소스/상수·tensor/좌표 정책·실제 설정을 고정한 immutable parser manifest 원 bytes SHA다. graph/format/calibration도 각각 출판된 manifest 원 bytes SHA를 참조한다. 기존 ASSET_ID/PARSER_ID label, reference utils SHA, 단일 소스/config hash는 전체 parser release hash가 아니다. 현재 벤치의 경로/기록 budget 포함 profile hash를 운영 graph asset hash로 재사용하지 않는다. 이번 API는 없는 manifest를 생성하거나 S01의 과거 기록을 소급 변경하지 않는다.

calibration_bundle은 전체 차량 보정 manifest SHA 또는null이다. null은 보정 참조 미제공이며 metric 자격이 없다. nonnull 문법도 파일 존재·실물·leaf·보정 승인을 증명하지 않는다. actual parser/graph/format/calibration asset 출판과 실물 검증은 caller의 별도 gate다.

`RawBindingProfile(device_id, clock_epoch, model_id, parser_id, graph_profile_sha256, format_table_sha256, calibration_bundle, nn_enabled, jpeg_enabled)`는 body와 독립적으로 승인한 참조를 보유하는 frozen 값이다. `.stream_names`는 두 bool에서 유도한 tuple, `.refs`는 asset refs5개의 새 dict다. `.check(binding)`은 device/clock/refs/stream 선택을 exact 대조하고 성공 시None, 실패 시RawBindingError다. body에서 expected를 자동으로 추출하지 않는다.

`runtime.raw_binding_messages.RawBindContext(peer, acquisition, profile)`는 기존 LifecyclePeer(A/M만)와 등록 A Endpoint를 구분한다. A 대상은 worker/channel 모두 A와 같고 M 대상은 각각 A와 달라야 한다. body service는 peer service, acquisition_worker는 언제나 등록 A worker다. M identity는 body hash에 넣지 않아 M-only restart에도 건강한 A의 raw hash를 유지할 수 있다. peer.release_sha256는 BIND wire에 없고 HELLO/실제 bootstrap 등록은 별도다.

`RawBindMessage(packet, context, cap_us=None)`/`parse_raw_bind(...)`는 기존 b1 StructuralPacket의 S→등록 A/M RAW BIND를 검사한다. payload exact3은 binding_revision/binding_sha256/binding이다. outer/payload revision은 같은 positive canonical u64string, SHA는 canonical body SHA다. issued<expires, expires−issued≤5000000us이며 제공한 원 cap 이내여야 한다.

`RawBindAck(packet, original_bind)`/`parse_raw_bind_ack(...)`는 원 BIND를 필수로 받아 해당 A/M→S의 exact5 payload(binding_revision/binding_sha256/accepted/state/reason)를 검사한다. peer/request/revision/hash가 같고 BIND.issued≤ACK.issued<ACK.expires≤BIND.expires다. 다른 sender의 순번이므로 ACK seq가 BIND보다 작아도 된다. accepted는 exactbool이며 true이면 active role state와 null reason, false이면 기존 role 진단 state와 UNSUPPORTED_PROFILE/ASSET_UNAVAILABLE/LOCAL_SETUP_FAILED 중 하나다. positive에서 A의 DRAINING/CLOSING/CLOSED/FAILED 계열과 M의 QUIESCING/STOPPED/FAILED는 제외한다. WAIT/WARMUP/RECOVERING의 형식 수락은 READY나 원 fence 해제가 아니다. 부정 진술도 건강한 A/source를 삭제하지 않는다.

두 wrapper는 원 `.packet`과 `.raw`(packet.raw_bytes), immutable binding/context/원 ID·정수 시각·revision을 보유하며 `.fields`/`.payload`는 새 사본이다. constructor와 helper가 같은 검사를 수행하고 unchecked flag는 없다. RawBindingError(ValueError), RawBindMessageError(RawBindingError)로 오류를 구분한다. body는 exactdict13/streamdict5·container depth2·node64·ASCII string/key64·exactstr/None만 소유 복사하고 canonical2048B 이내로 만든다. scalar leaf depth3은 허용하며 bool/int/list/custom/subclass/cycle은 거절한다. 이 bound를 별도 Profile/Context나 전체 packet에 적용하지 않는다. outer는 기존 b1의 실제4096B/encoding/duplicate/구조 검사를 유지한다.

이 API는 순수 정적 진술 검사다. IO·clock·UUID 생성·actual send/등록 상태/HWM/outbox/BindWatch를 실행하지 않는다. HELLO/현재 revision/first-send·수신 now/공통 sequence·staging/lease 예약·timeout·SLOT/실 공유 메모리·자산 검증·준비/metric 자격은 별도 actor 계약이다. negative ACK를 실제 BindWatch.ack 성공으로 기록하지 않는다.

## 원 시각을 보존하는 정적 SLOT 6종

`runtime.slot_messages`는 REQUEST/GRANT/FILLED/OFFER/ACQUIRE/ACQUIRED만 지원한다. PARSER_WARMUP·null publication의 정적 진술이며 RELEASE/REVOKE/METRIC_MAP/non-null publication은 UnsupportedSlotOperation으로 거절한다. 실제 registry·예약·순번·token·송수신·READY를 만들지 않는다.

`SlotTimeLimits(copy_budget_us, offer_budget_us, read_budget_us)`는 caller가 명시 제공하는 positive u64의 frozen 값이며 read≤150000us다. copy/OFFER의 실제 숫자는 후속 pinned runtime profile이 정한다. 제품 API에는 암묵 예산 기본값이 없다. `WriterSlotContext(bind,scope)`는 A용 원 RawBindMessage와 등록 RawScope만 받고 M BIND를 기다리지 않는다. service/raw SHA/A worker·channel/name/size/role 원본을 보유한다. `ReaderSlotContext(writer,bind)`는 별도 M BIND의 canonical body/hash/RAW revision/service와 acquisition Endpoint 전체를 writer에 대조한다. `.reader`는 M peer에서 얻은 Endpoint다. BIND handshake5초는 안정 RawBinding의 상주 수명이 아니며 SLOT 시각을 그 전송 expiry에 제한하지 않는다. 현재성·fence는 실제 S registry가 소유한다.

`SlotMessage(packet,context,parent=None,*,limits,cap_us=None)`와 `parse_slot_message(...)`는 같은 검사로 원 packet/raw, context/limits/parent, op/request_id/정수 issued_us·expires_us·상속 cap_us와 c1 `.record`를 보유한다. fields/payload는 새 사본이다. parent가 있으면 문맥/limits 전체값을 대조하고 결과에는 원 parent의 context/limits/record를 보존한다. 공개 constructor에 unchecked flag는 없다. SlotMessageError(ValueError)와 그 하위 UnsupportedSlotOperation을 사용한다.

|op|필수 parent|원 ID·시각|c1 record|
|---|---|---|---|
|REQUEST|None|Qw/Iw/Ew|LeaseRequest|
|GRANT|REQUEST|같은Qw/예약G/같은Ew|WriteGrant|
|FILLED|GRANT|같은Qw/완료보고P/같은Ew|FilledInfo|
|OFFER|None|독립Qo/원P/정확P+offer budget|Offer|
|ACQUIRE|OFFER|새Qr/Ir/Er|ReadRequest|
|ACQUIRED|ACQUIRE|같은Qr/예약Gr/같은Er|ReadLease|

원 구간은 Iw≤G≤P<Ew, P≤Ir<Eo, Ir≤Gr<min(Er,Eo)다. copy/read 요청 폭은 각 원 예산 이내다. Qr는 아는 Qo와 달라야 한다. Qo와 Qw의 구별은 M이 Qw를 모르므로 S의 실제 생성 gate다. Eo 합산의 최종u64 초과는 거절하고 clamp/now 갱신을 하지 않는다. REQUEST/ACQUIRE만 optional exactu64 local cap_us를 c1 LeaseRequest에 보존한다. cap 자체는 wire에 없고 caller 독립 입력이며 다른4op에서 새 cap을 주어 바꿀 수 없다. 이미 끝난 COPY cap이나 BIND 전송 expiry를 OFFER/새 READ에 상속하지 않는다.

payload는 항상 exact13(op/arena_id/slot/generation/write_token/read_token/producer/consumer/raw_binding_sha256/publication_binding_sha256/purpose/payload_length/descriptor_sha256)이다. unused는 missing이 아닌 null이다. REQUEST는 arena/raw/producer만 지정하고 key는 null, GRANT는 slot0/1·positive u64string generation·write UUID, FILLED는 여기에 body length1..2093056·descSHA를 결합한다. OFFER는 원 full writer key/length/SHA와 M consumer를 안내하되 read token/purpose는 null이다. ACQUIRE는 같은 content/M에 PARSER_WARMUP, ACQUIRED만 read UUID를 추가한다. publication은 모두 null이다. slot/length는 exactint이며 bool을 받지 않는다. 원 packet4096B 구조/encoding/duplicate 규칙은 기존 b1을 따른다.

REQUEST/FILLED는 A→S, GRANT는 S→A, OFFER/ACQUIRED는 S→M, ACQUIRE는 M→S다. service/RAW revision/producer/raw SHA/arena와 채널을 등록값과 exact 대조한다. A writer channel을 M channel로 덮지 않으며 별도 S-worker UUID를 만들지 않는다. A/M BIND의 request/seq/발급시각은 달라도 같은 안정 body/hash/revision일 수 있다. M-only restart는 원 writer를 유지하고 새 M만 결합한다.

M은 OFFER에서 P/full key/length/SHA 진술을 처음 받으므로 OFFER에 외부 FI parent를 요구하지 않는다. 실제 S가 원 ledger FI/READY/current M/no replacement pending을 송신 직전 확인해야 한다. 이 wrapper는 과거G·실copy/post를 증명하지 않는다. ACQUIRED의 FI는 원 ACQUIRE 안의 Offer에서만 상속한다. parent chain은 고정 op 전이의 유한 길이이고 과거 작업의 history를 재귀 연결하지 않는다.

안내 Eo가 만료되기 전 Gr에 이미 예약한 read는 Er까지의 기록을 유지한다. Er>Eo도 정상이다. static wrapper는 수신 now를 받지 않는다. 실제 actor는 신규 ACQUIRE를 c1.acquire 전에 now<Eo로 검사하고, 뒤늦게 받은 ACQUIRED 때문에 기존 IN_USE/parent를 삭제하지 않는다. ACQUIRED 전송 포기·만료로 FREE를 만들지 않는다. 신규 FI의 늦은 최초 수신과 이미 수락한 exact duplicate도 실제 수신/원장이 구분한다.

참조 비용은 원 packet≤4096B와 작은 frozen context/limits/c1 값·정해진 parent뿐이며 fields/payload 조회는 작은 사본을 만든다. SDK·clock·UUID 생성·파일·시스템 자원은 호출하지 않는다. 실제 fullsend/BindWatch/공통 RX·TX순번·staging/예약 부분 성공·token·guard/arena/worker·remotecleanup·METRIC pre-read metadata·현재 lease/READY/주행 권한은 후속 gate다. c1 투영이 존재한다는 이유로 실제 허가를 발급하지 않는다.

## 원 ACQUIRED에 연결한 반환 정리

`runtime.slot_cleanup.CleanupTimeLimits(transport_budget_us)`는 caller가 명시한 exact positive u64 전송 예산이다. 제품 기본값은 없다. `ReleaseMessage(packet,original_acquired,*,limits)`와 `parse_release_message(...)`는 exact 원 SlotMessage/ACQUIRED를 parent로 받아 RELEASE 하나만 검사한다. `.packet`/`.raw`는 원 bytes를 보존하고 `.parent`/`.context`는 원 객체 참조, `.limits`/`.request_id`/`.issued_us`/`.expires_us`/`.record`는 정적 값이다. fields/payload는 새 사본이다. 오류는 SlotCleanupError(ValueError)다.

RELEASE payload는 원 ACQUIRED의 exact13 중 op만 RELEASE로 바뀐다. full writer/read key·producer/consumer·RAW hash·null publication·PARSER_WARMUP·length/descriptor SHA와 원 Qr를 유지한다. outer service/channel/revision/kind/domain도 원본과 같고 sender만 원 M worker다. 상대 sender의 ACQUIRED보다 sequence가 클 필요는 없다. 실제 채널 순번 수락은 별도다.

issued=C는 M의 모든 view 종료와 writable post 뒤 최초 완료보고 **진술**이며 expires=C+transport budget exact다. 최종 u64 overflow는 거절한다. C≥원 read 예약시각이며 C>원 Er·cleanup E>원 read cap도 정리 형식으로 정상이다. 원 parent 안의 read cap은 그대로 보유하고 wire 사실로 만들지 않는다. S는 S가 보유한 parent/cap으로 해석하며 M의 native cap과 같다고 가정하지 않는다. wrapper에는 now/context/cap 갱신 인자가 없다.

`runtime.raw_leases.RemoteReturnStatement(lease,completed_us)`는 원 exact ReadLease와 C를 보유하며 reserved≤C만 검사한다. local ViewReturnFact의 view-end/post 두 시각을 C/C로 만들지 않는다. `RawLeaseLedger.release_remote(statement,*,now_us)`는 C≤now를 duplicate/UNKNOWN보다 먼저 검사한다. 기존 `release(ViewReturnFact,*,now_us)`는 exact local 타입과 원 두 시각을 유지한다. 원 Er/fence/retired 뒤에도 정확한 반환 정리는 가능하며 새 읽기나 결과 채택 조건을 발급하지 않는다.

slot당 마지막 `CleanupSummary(fact,opposite=None)` 하나를 보유한다. 반환의 primary는 local/remote 중 먼저 받은 원본이고 optional opposite는 반대 종류 하나만 허용한다. full ReadLease가 같아야 하며 두 사실이 있으면 local writable_posted≤remote C다. post=C는 허용한다. replacement에는 opposite를 붙이지 않는다. history는 최대2facts이며 현재 summary보다 더 오래된 반환은 UNKNOWN일 수 있다.

현재 IN_USE의 첫 정확 반환은 APPLIED로 한 번 FREE가 된다. 첫 반대 종류 보충은 CLEANUP_CORROBORATED(value=None)로 cleanup metadata만 바뀐다. 이미 보유한 exact 재보고는 DUPLICATE_NO_EFFECT로 전체 snapshot이 같다. 같은 종류의 시각/원 lease 변경이나 full request/cap/purpose/pub/content 불일치는 거절한다. gen1의 늦은 보충이 gen2 WRITING/IN_USE/READY replacement pending의 현재 필드·fence를 바꾸지 않는다. acquire의 retained Qr 검사는 local/remote primary의 원 lease를 한 번만 사용한다.

참조 비용은 원 packet≤4096B와 고정 길이 parent·작은 frozen record이며 사본 조회만 작은 tree를 복사한다. SDK view나 큰 frame bytes를 보유하지 않는다. 실제 RSS/주기는 별도 계측 대상이다. sender는 C/E/첫 sequence/raw를 유지하고 E 뒤 새 dispatch를 하지 않는다. 전송 만료·abandon이 실제 반환 사실이나 미확인 ownership을 지우지 않는다. receiver의 exact known/current 또는 미회수 old cleanup은 원 Er와 전송 E 뒤에도 bookkeeping만 가능하다. 실제 peer/registry·공통 sequence/staging·전송·token post/owner exit·STOPPED/REVOKE·SHM 회수는 이 API가 실행하거나 증명하지 않는다.

## 자원 기록과 순수 guard 판정

`runtime.resource_manifest.ResourceManifest(raw_bytes)`는≤8192B ASCII JSON을 읽는 immutable 값이다. `.raw_bytes/.raw_sha256`와 key/Owner 정렬·compact·LF1의 `.canonical_bytes/.canonical_sha256`를 구별한다. `.fields`는 독립 사본이다. `.ref`는 실제 raw SHA/revision/service/resource epoch/runtime을 보유하고 `.runtime/.owners/.cgroup/.resources/.intents/.recovery_episode`는 frozen record와 tuple이다. Native 정수는 exactint이며 wire u64는 canonical decimal string이다. 오류는 ManifestError(ValueError)다.

root11/runtime7·owner≤9·resource12주소·미해결 creation1개를 검사한다. current/retired raw2·DB 물리 inventory≤4·sem 논리6이라는 상한이며 실제 DB segment 수/배치/할당은 이 값이 정하지 않는다. Resource의 KNOWN SHM size는 actual-known positiveu64 기록이고 DB 전체13,651,968B를 단일 arena나 raw2MiB로 강제하지 않는다. canonical kernel SHM 이름의 leading slash와 public SharedMemory name의 실제 mapping은 adapter 책임이다. native manifest tree container depth3/node512(키포함)/key32/semantic string192/list9/dict12/intbits64와 raw/canonical각8192B cap은 manifest에만 적용한다. JSON parser 할당 자체를512nodes라고 보장하는 것은 아니다.

NOT_ATTEMPTED/name null/intent 없음은 불완전 inventory가 아니다. INTENT/UNKNOWN과 null-birth owner는 미완전이며 일반 constructor 실패로 무생성이라고 바꾸지 않는다. `.complete_inventory`는 이 정적 완전성만 뜻한다. CURRENT/RETIRED는 실제 생존 상태가 아니다. child/첫 tracker 가능 경로 전 null-birth 행의 durable ACK·원 intent 기록·실제 결과 기록은 별도 단계다. settled history를 늘리거나 occupied retired 기록을 덮어쓰는 API를 제공하지 않는다. episode는 원 ID와0..5 카운터를 보존하며 `service_attempts`는 초기 group을 포함한 모든 회복 group의 전역 누적이다. 증가·건강 판정·reset은 수행하지 않는다.

`runtime.resource_guard`의 LoadOutcome은 VALID/MISSING/CORRUPT/IO_ERROR를 구별한다. CurrentGuardScope와 OwnerObservation/CgroupObservation/NameObservation, LiveIntentStatement/RuntimeResetStatement/FirstInstallStatement는 원 ref·정확 대상·현재 guard에 결박된 lower 진술이다. record는 frozen이고 집합은 exacttuple이며 cgroup members≤16은 manifest list9와 별도다. 실제 PID/cgroup/name/boot/reset/install을 여기서 조회하거나 검증하지 않는다. UUID/SHA는 실제 proof를 암호학적으로 증명하지 않는다.

`GuardDecision(load,guard,evidence)`와 `evaluate_guard(...)`는 같은 검증으로 kind/reason·원 load/guard/evidence·원 valid predecessor/episode를 보유한다. kind나 unchecked flag를 입력받지 않는다. 구조/타입 오류는 GuardInputError(ValueError), 구조가 맞는 다른 ref/대상·누락·중복·모순은 BLOCKED다. 제공된 진술은 모든 분기에서 결박을 대조하고 실제 필요한 집합은 분기별로 구별한다.

OLD_CLEAN_ELIGIBLE은 같은 full runtime·complete inventory·모든 known owner EXITED·현재 guard birth 외 cgroup 잔존 없음·모든 known name ABSENT를 요구한다. 자기 birth의 EXITED 주장은 거절한다. FRESH_RESET_ELIGIBLE은 old/current boot 불일치와 전용 reset 진술을 요구하며 old incomplete 원본·episode를 보존한다. FIRST_INSTALL_ELIGIBLE은 원 MISSING load_id와 설치 진술·명시 genesis에만 해당한다. CORRUPT/IO_ERROR는 자동 BLOCKED다. LIVE_INTENT_PENDING은 현재 같은 S의 명시 ALIVE/ref/birth/guard nonce와 원 DIRTY/target INTENT에만 해당하는 정보이며 UNKNOWN은 같은 S라도 BLOCKED다. LIVE도 일반 constructor 허가가 아니다.

파일 load/lock/CAS/fsync/S-log ACK·실제 관측/설치 근거 소비·SHM/고정6token/worker/서비스 기동·회복/종료·READY/주행 판단 조건은 별도 gate다. d1은 시계·UUID 생성·명시 파일/시스템 자원 호출을 하지 않고 canonical≤8KiB와 작은 frozen 값/진술만 보유한다. 조회 사본 비용·실제 RSS/주기·native 반환시간은 별도 계측 대상이다.

## 자원 기록의 Linux 동기 저장

`runtime.resource_store.ResourceStore(directory)`는 기존 보호 디렉터리의 `resource-set.json`을 읽고 별도 `resource-set.lock` 아래 교체하는 단일 writer다. Linux 전용이며 directory는 same-euid/group-other write 금지, file은 same-euid/regular/nlink1/exact0600을 요구한다. 상위 경로 신뢰는 호출자 전제다. context manager 또는 명시적 `close()`로 닫고, 취득한 process/thread에서만 사용한다. fork/fd 양도·복제·재진입은 지원하지 않는다. lock 파일은 삭제하거나 교체하지 않는다.

`load()`의 immutable StoreLoad는 원 LoadOutcome과 실제 lock nonce를 보유한다. 같은 fd의 누적8193B 읽기로8KiB 초과를 구분하고 원 raw/ref를 보존한다. manifest leaf ENOENT만 MISSING이며 손상/빈 body는 CORRUPT, 안전한 원본을 얻지 못하면 IO_ERROR다. directory/lock 오류는 StoreError이며 최초 설치 근거가 아니다. 실제 파일 I/O/fsync의 시간 상한은 보장하지 않는다.

`propose(receipt,manifest,request_id)`는 최신 실제 receipt에서 원 제안을 한 건 만든다. StoreProposal의 출처 raw SHA와 실제 저장할 canonical bytes/target ref를 구분한다. 외부 synthetic receipt·새 no-op·다른 pending 제안은 StoreUsageError다. revision/epoch/episode/request ID/임무 기한을 store가 발급하거나 증가시키지 않는다.

`publish(proposal)`와 `reconcile(proposal)`은 원 객체만 받는다. 원 raw CAS 뒤 temp 전체 write/file fsync→replace→directory fsync를 완료하면 DURABLE과 StoreAck를 반환한다. CAS_MISMATCH/UNCONFIRMED에는 ACK가 없다. ACK의 REPLACED와 원 replace 진입 뒤 실제 target 재동기화의 RECONFIRMED를 구분하며, target read-back만으로 ACK하지 않는다. 같은 완료 proposal의 cached ACK 재반환은 원 사실의 반복이며 새 완료나 현재 파일 보증이 아니다. 완료 뒤 다음 제안은 fresh load와 다른 request가 필요하다.

열기/guard 오류는 StoreError(RuntimeError), 경쟁 lock은 StoreBusyError(StoreError), 타입·수명·재진입·원 receipt/proposal 오용은 StoreUsageError(ValueError)다. 이 synchronous adapter는 운영 S-main/CLI/doctor/replay에 연결하지 않는다. 실제 OS owner/name/reset/install proof·S-log 비동기 ACK 수락·자원 생성과 주행 판단 조건은 별도 책임이다.

## 실제 runtime과 known owner 관측

`runtime.process_observation.ProcessObserver(store, service_instance)`는 살아 있는 exact ResourceStore를 빌려 고정 Linux `/proc`·`/dev/shm`을 동기로 관측한다. 생성자는 파일 I/O를 하지 않으며 service_instance는 시작 때 정한 canonical UUID다. 같은 실제 PID·Thread에서만 비재진입 사용하고 fork/복제/양도를 거절한다. borrowed store는 닫지 않는다.

`observe()`의 frozen ObservationBatch는 원 `original_load` L1과 마지막 실제 `latest_load` L2, 실제 nonce에 연결한 `guard`, 원 known owner 전체의 `owners`, monotonic_ns의 `started_ns/completed_ns`를 보유한다. VALID는 두 원 raw/ref가 같고 MISSING은 두 실제 조회가 모두 MISSING이어야 한다. L1/L2는 서로 다른 원 조회이며 후속 propose에는 최신 실제 L2를 사용한다. pending/null birth와 원 incomplete 상태는 보충하지 않는다.

고정 profile은 actual root FD의 fstat·fdinfo mnt_id·정확 mountinfo 행, namespace FD·boot·self birth의 전후 일치, native PID와 같은 Pid/Tgid 및 단일 NSpid/NStgid, hidepid 누락/명시0, 실제 tmpfs backing이다. kernel magic namespace link는 의도적으로 따라가고 일반 proc data에는 NOFOLLOW를 쓴다. 상위 경로·kernel 및 협력 실행을 신뢰하며 privileged 비협력 mount 변경을 제압하는 보안 경계는 아니다.

`runtime.procfs_formats`는 위 관측과 분리한 bounded bytes parser다. stat52필드의 opaque comm은 고정 Linux4.9 profile에서 최대15 raw bytes·내부NUL 없음이며 빈 값·개행·괄호·비UTF8은 허용한다. 그 뒤 suffix에서 원 start_ticks(u64 clock ticks)를 읽는다. stat4096B는 누적 I/O의 상한이며 그 길이의 raw가 유효한 schema라는 뜻은 아니다. boot64B·fdinfo8192B·status65536B·mountinfo1MiB/4096행/행65536B 상한을 검사하고 mount 경로 escape는 tokenization 뒤 한 번만 해제한다. proc st_size0을 빈 body로 보지 않는다.

같은 boot/PID-view에서 같은 원 PID/start_ticks는 Z/X도 ALIVE이며 종료·회수가 확인되지 않았다는 뜻이다. 처음 numeric PID directory open의 ENOENT 또는 온전한 같은PID/다른start_ticks만 원 birth의 EXITED다. 이미 연 directory의 도중 소멸·권한·형식 실패는 UNKNOWN을 빠뜨리지 않고 반환한다. 다른 boot/PID namespace도 UNKNOWN이다. ALIVE는 건강·READY나 다음 순간의 생존을 보장하지 않는다.

타입·PID/Thread·재진입 오용은 ObservationUsageError(ValueError), source/runtime/store 수명·지원 profile·owned FD 반환 불명은 ObservationUnavailable(RuntimeError)다. 관측 FD는 호출 안에서 한 번만 닫고 반납 실패에는 사용 가능한 묶음을 반환하지 않는다. 공개 값의 구조 검사는 실제 OS 출처 증명이 아니다. cgroup COMPLETE·이름 probe·설치/reset 근거·새 기한·정리/생성 허가·S-main 연결은 생산하지 않으며 동기 I/O의 반환시간 상한을 보장하지 않는다.

## 정확한 Linux 자원 이름 관측

`runtime.resource_name_observation.ResourceNameObserver(store, service_instance)`는 같은 borrowed ResourceStore와 startup UUID의 public ProcessObserver 하나를 보유한다. 생성자는 파일 I/O를 하지 않는다. 같은 실제 PID·Thread의 비재진입 호출만 허용하고 producer의 copy/deepcopy/pickle/fork 양도를 거절한다. `observe()`는 원 B0를 얻고 자기 proc/root/namespace FD로 중간 backing을 결박한 뒤, 그 FD를 모두 한 번 반환하고 원 B1을 얻는다. borrowed store를 닫거나 새 guard로 재시도하지 않는다.

frozen `NameObservationBatch(before, after, names)`는 두 원 ObservationBatch와 B0의 원 known_resources 순서대로 된 NameObservation tuple(≤12)을 보유한다. `.original_load/.guard/.started_ns`는 before의 원 객체/값, `.latest_load/.completed_ns`는 after의 원 객체/값이다. 네 실제 load는 모두 VALID·서로 다른 load ID·같은 원 raw/ref/nonce이며 두 guard 값 전체가 같고 B0.completed_ns≤B1.started_ns여야 한다. B1 뒤 추가 load가 없으므로 후속 propose에 `.latest_load` 원 객체를 사용한다. 두 owner outcome은 ALIVE→EXITED처럼 달라도 된다. 결과 생성자의 검사는 순수 값 관계이며 OS proof나 실제 관측 출처를 인증하지 않는다.

중간 FD는 d2b1의 고정 Linux profile과 같은 caps/public parser로 실제 root dev/inode·fdinfo mnt_id·정확 mountinfo 행·namespace/boot/current birth를 시작/끝에 확인한다. 현재 named root를 다시 연 FD의 mount ID도 대조한다. 원 full runtime이 current와 다르면 모든 원 known target은 UNKNOWN이고 leaf lookup은0회지만 중간 현재 profile은 확인한다. VALID+KNOWN0은 원 partial 상태를 유지한 빈 tuple이 가능하며 MISSING/CORRUPT/IO_ERROR는 전체 unavailable이다.

같은 runtime의 SHM leaf는 name[1:], semaphore leaf는 `sem.`+name[1:]다. 정확 leaf에 `os.stat(..., dir_fd=held_root, follow_symlinks=False)` 한 번만 수행한다. 실제 errno ENOENT만 ABSENT, regular는 size/mode/nlink가 달라도 PRESENT, symlink·directory·FIFO 등 다른 type이나 leaf 오류는 UNKNOWN이다. leaf open·payload read·생성·mmap·unlink·token 조작·tracker 등록·전역 scan은 없다. 입력·소유 수명·순수 값 오용은 NameObservationUsageError(ValueError), source/profile/store/owned FD 반환 불명은 NameObservationUnavailable(RuntimeError)다. 개별 UNKNOWN을 원 tuple에서 누락하지 않는다.

이 결과는 현재 Linux backing entry의 관측이며 generic POSIX ABI·creator 인증·남은 mapping/참조의 소멸·완전 inventory나 CLEAN/생성/READY 권한이 아니다. 실제 creator/backing과 owner/cgroup/tracker 장벽을 닫기 전 운영 guard 소비는 미완료다. 동기 I/O의 반환시간 상한을 보장하지 않고 S-main·프레임 loop에 연결하지 않는다.

## 테스트

`vehicle/Jetson`에서 표준 라이브러리 테스트를 실행한다.

```bash
PYTHONPATH=src python -m unittest discover -s tests/unit -v
PYTHONPATH=src python -m unittest discover -s tests/integration -v
PYTHONPATH=src python -m unittest discover -s tests/replay -v
```
