# PC Streamlit 관측기

별도 PC 프로세스에서 관측 파일을 읽는 진단 화면입니다. Jetson 제품 패키지를 import하지 않으며 제품의 requirements·pyproject와 별도 환경을 사용합니다. 조작은 기존 터미널에서 수행합니다. 이 화면에는 AUTO/MANUAL·주차·WASD 출력 기능이 없고, 화면을 닫아도 제품에 종료·STOP·재시작 신호를 보내지 않습니다.

네 패널은 전체 코스의 GT/추정/불확실성/경로, 원 RGB/YOLOP/native depth/grid, 운영 보고값, 장치·식별자·원 clock/seq/처리율입니다. 없는 값은 UNKNOWN입니다. 파일 검사 통과는 현재 READY·권한·장치 적용 성공이라는 뜻이 아닙니다.

## 별도 환경과 실행

저장소 루트에서 Python 3.11 이상 PC의 전용 venv를 사용합니다. Jetson 제품 환경에 설치하지 않습니다.

```powershell
python -m venv vehicle/Jetson/build/streamlit-viewer-env
& ".\vehicle\Jetson\build\streamlit-viewer-env\Scripts\python.exe" -m pip install -r vehicle/Jetson/tools/streamlit_viewer/requirements.txt
& ".\vehicle\Jetson\build\streamlit-viewer-env\Scripts\python.exe" -m streamlit run vehicle/Jetson/tools/streamlit_viewer/app.py --server.address 127.0.0.1 --server.port 18501
```

전용 requirements는 `streamlit==1.67.0` 하나입니다. Streamlit의 전이 의존성도 이 venv에만 설치합니다. 브라우저에서 `http://127.0.0.1:18501`을 열고 사이드바의 형식과 **PC 관측 폴더**를 선택합니다. 이미 같은 포트를 사용 중이면 별도 서버를 중복 실행하지 않습니다.

- `snapshot.json`: 지정 폴더의 `snapshot.json`과 그 파일이 가리키는 media를 읽습니다.
- `SyntheticDatasetWriter offline`: 기록 폴더와 ordinal을 선택합니다. `-1`은 마지막 기록입니다.
- `실제 OAK 대표 장면 기록 (HISTORICAL)`: 대표 장면 실행의 원 archive 폴더와 ordinal을 선택합니다. `-1`은 마지막 장면입니다.
- GT 층은 기본 표시 OFF이며 표시용으로만 사용합니다. RGB·YOLOP·Native depth·Grid는 탭으로 선택합니다.

[fragment](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment)는 0.5초마다 화면 관측을 갱신합니다. 이 2Hz는 주행 처리 FPS가 아닙니다. 자체 daemon·RX thread·무한 queue는 없습니다. 서버를 끝낼 때는 실행 터미널에서 `Ctrl+C`를 누릅니다.

## 새 실행의 코스 선택

사이드바 **새 실행 코스 준비**는 현재 저장소의 원 정의를 읽는 별도 PC 기능입니다. `Jetson native-depth (권장)`을 먼저 선택하고 코스·목표 바퀴 수를 지정합니다. 선택한 정의 경로·길이·SHA와 실제 지원 명령을 확인하고 `course-preparation.json`을 내려받을 수 있습니다. 표시 상태는 PREPARED/NOT_EXECUTED입니다. simulator 시작 IPC·현재 run 적용은 연결되어 있지 않습니다.

| 계열 | 현재 목록의 근거 | 지원 범위 |
| --- | --- | --- |
| Jetson native-depth (권장) | `simulation/fixtures/jetson-demo.json` + `experiment-profile.json` | 기본 코스1개. 실제 profile↔scene SHA·원 출처를 대조한다. 현재 `demo_scene()`이 이 fixture를 로드하며 범용 코스 CLI는 없다. 제공 명령은 기존 fixture/profile 검사시험이며 주행 실행이 아니다. |
| 기존 MJPEG simulator (별도 legacy) | `platform/apps/simulator/xenova_simulator/tracks.json` | 원 서킷10개와 기존 `--track` CLI. native depth/Feedback3 HIL 경로와 다르며 별도 legacy 환경·브로커 설정이 필요하다. 이 뷰어는 명령을 실행하지 않는다. |

파일이 없거나 source pin/길이·SHA가 바뀌면 해당 계열을 UNAVAILABLE/INVALID로 표시합니다. 저장소에 없는 코스·R 변형이나 과거 대표 장면을 지원 코스로 만들지 않습니다. catalog는 source 존재와 명시 관계를 확인하며 simulator 전체 runtime 검증이나 물리 주행 수락을 대신하지 않습니다. 명령을 추후 직접 실행하려면 해당 제품/시험 환경을 별도로 준비해야 합니다. PC viewer venv에서 제품 패키지를 자동 설치하거나 실행하지 않습니다.

다운로드 형식 `viewer-course-preparation-1`은 사람이 확인하는 PC 개발 계획이며 제품 wire/API나 기존 실행기가 읽는 설정이 아닙니다. native 계열의 scene/profile 경로와 legacy 계열의 track ID를 구분하고, 원 파일을 내려받기 직전 다시 검산합니다. 실행 연결을 추가할 때는 새 run 준비 단계에서 이 선택을 소비해야 합니다.

이 선택은 관측 폴더/과거 ordinal이나 Jetson 저장 지도를 바꾸지 않습니다. 운행 중 hot-swap, `track.select`, race reset, 지도·NN·source 초기화 명령을 보내지 않습니다. 같은 코스 두 바퀴는 같은 run/map 수명 안에서 이어가야 합니다. 목표 바퀴 수는 요청 기록일 뿐 자동 랩 수 실행·종료 기능이 아니며, 지원하지 않는 `--laps` 인자는 생성하지 않습니다.

## 입력1: viewer 전용 snapshot.json

이 파일은 **개발 도구용 계약**입니다. 제품 공개 wire나 HTTP endpoint가 아닙니다. producer는 PC 관측 폴더에 media/원 payload를 먼저 완성하고 snapshot을 마지막에 원자 교체해야 합니다. 제품 loop에 동기 파일 I/O hook을 넣지 않습니다. 원 RGB/상태가 없는 값은 null로 둡니다.

```json
{
  "schema": "jetson-viewer-snapshot-1",
  "source": {
    "id": "original-observer-id",
    "epoch": "original-observer-epoch",
    "sequence": "1",
    "kind": "DIAGNOSTIC",
    "stamp": null
  },
  "operation": null,
  "map": {"layers": []},
  "media": [],
  "identities": null,
  "metrics": null,
  "raw_payload": null
}
```

- source의 순번은 원 producer 순번이며 viewer 재조회 때 증가시키지 않습니다. kind는 DIAGNOSTIC 또는 OFFLINE_SYNTHETIC입니다. stamp가 있으면 `{domain, epoch, us}`이며 us/seq는 uint64 또는10진 문자열입니다. domain/epoch가 다르면 age는 UNMAPPED이고 임의 offset/현재시각으로 채우지 않습니다. 뷰어 최초 수신 후 경과와 원 입력 age는 따로 표시합니다.
- viewer-local clock은 독립 monotonic/session UUID이며 외부 source와 공유되지 않습니다. 외부 source의 입력 age는 UNMAPPED로 표시합니다. PC collector의 수신 시각과 원 source clock을 연결할 때에도 두 시각의 의미를 분리해야 합니다. 원 운영 보고값은 그대로 표시합니다.
- operation은 null 또는 mode/vehicle_phase/parking_enabled/active_vm/standby_vm/auto_ready/blocked_reason의 원 값입니다. 미제공 필드는 null/UNKNOWN입니다. DIAGNOSTIC 입력이 파일 검사와 clock mapping을 통과해도 현재 표시는 UNVERIFIED이며 주행권한·장치 적용 성공으로 승격하지 않습니다. OFFLINE_SYNTHETIC·STALE·UNMAPPED의 현재 운영 값은 UNKNOWN입니다. 옛 READY를 정상 상태로 표시하지 않습니다.
- map.layers는 최대8개, kind=GT/ESTIMATE/UNCERTAINTY/PATH, frame, points=`[[x,y],...]`, 선택 closed=bool입니다. 층당2048/합계8192점, 단위m입니다. 서로 다른 frame은 겹치지 않습니다. GT는 기본 표시OFF이며 viewer 밖으로 전달하지 않습니다.
- media는 rgb/yolop/depth/grid별 최대1개, 합계4개입니다. path(관측 폴더 내부 상대경로), sha256, byte_length, width/height, encoding=JPEG/PNG/NV12/RAW16_LE를 받습니다. stamp/source_sequence/original_stamp는 원 값입니다. RAW16의 단위가 알려진 경우에만 depth_unit="mm"를 씁니다.
- raw_payload는 선택된 원 JSON/bytes 파일의 path/sha256/byte_length입니다. 재직렬화하지 않은 bytes를 그대로 보존·다운로드합니다. 별도 원본이 없으면 snapshot 원 bytes만 제공하며 원 wire를 복원했다고 하지 않습니다.

snapshot 최대256KiB, media 각각1MiB/최대2097152픽셀, 원 payload256KiB입니다. 매 poll에서 파일 안정성/해시/길이를 재검사하며 절대경로·상위경로·symlink/reparse·비정규 파일·변하는 파일을 거절합니다. 부분 JSON/중복 키/비유한 수·동일seq 다른 bytes·같은source epoch의 seq 역전은 INVALID입니다. 같은 파일/mtime touch/오류 뒤 같은 원문 복구는 원 최초 수신 시각을 갱신하지 않습니다. 최신 snapshot1개/최신 오류 원문1개/identity HWM16개만 유지합니다. 최근 오류 metadata는 복구 후에도 남습니다.

NV12는 화면용 BT.601 limited 변환/축소만 합니다. 원 프레임을 고치거나 추론을 재실행하지 않습니다. RAW16의0은 UNKNOWN(검정)이며 FREE가 아닙니다. mm가 선언되면 표시 범위0~3m, 아니면 단위 UNKNOWN/원 RAW16 범위입니다. native depth와 RGB는 해상도·픽셀 좌표가 달라 자동 정렬/겹침을 하지 않습니다.

## 입력2: 실제 offline 합성 기록

UI에서 `SyntheticDatasetWriter offline`과 폴더/ordinal을 선택합니다. 실제 [writer](../../src/xenova_vehicle/simulation/dataset.py)의 manifest.json, snapshots.jsonl, result.json, frames/NNNNNN-rgb.bin/right.bin 형식을 직접 읽습니다. 기존 제품 decoder·SDK·inference를 import/실행하지 않습니다.

index 최대2MiB/256줄, 완료 receipt의 count/index SHA와 metadata 순서/원 paired stamp를 검사합니다. 선택한 RGB640×360 NV12와 RIGHT640×480 RAW16_LE의 길이·해시를 검사하고 index 원 한 줄을 그대로 보존합니다. 다른 ordinal의 payload bytes는 읽지 않으므로 전체 archive payload 검증이라고 하지 않습니다. right 이름은 실제 writer의 depth 저장 key입니다. mm 생성 근거는 simulation/capture.py의 `safe_z*1000`이며 물리 OAK 측정으로 위장하지 않습니다. calibration/effects ID는 원 기록 표시용이며 이 도구가 기하 보정을 재검산하지 않습니다.

기록 탐색은 HISTORICAL입니다. 원 시각/순번을 현재 값으로 바꾸지 않습니다. YOLOP·지도·운영 보고가 이 형식에 없으므로 해당 패널은 UNKNOWN입니다. oracle.jsonl은 GT checkbox와 무관하게 읽지 않습니다. GT가 필요하면 별도 표시 전용 snapshot layer를 명시해 공급해야 합니다.

## 입력3: 실제 OAK 대표 장면의 과거 추론 기록

`실제 OAK 대표 장면 기록 (HISTORICAL)`에서 `s23-device-results-01.manifest.json`이 있는 원 archive 폴더를 지정합니다. 형식은 schema_version=1과 files의 path/bytes/sha256이며, 유일한 nn-source-receipt.json의 trial 아래 result.json과 선택한 case/result.json을 읽습니다. ordinal은 trial의 원 cases 순서를 따르고 제목에 원 case 이름·상태를 표시합니다.

이 기록은 **합성 RGB·가상 native depth를 실제 OAK에 넣은 host-fed NN 실행**입니다. source receipt의 camera_nodes=0이며 카메라 실측 영상이 아닙니다. 이 입력의 source kind는 관측기 내부용 `HISTORICAL_OAK_REPRESENTATIVE`입니다. 제품 wire를 추가하지 않습니다. NN을 다시 실행하거나 제품 decoder·SDK·기하 처리 코드를 import하지 않습니다.

manifest와 선택 case의 파일 목록·길이·SHA, source/calibration/model/context 식별자, 원 capture 구간·순번·완료 시각, NN receipt·원 geometry/NN 기한의 결박을 검사합니다. RGB NV12 640×360과 실제 input-depth RAW16_LE 640×480을 원 bytes로 읽습니다. 같은 기록을 다시 보거나 ordinal을 바꿨다 돌아와도 원 순번·시각·뷰어 최초 수신 시각은 갱신하지 않습니다. 목록이 바뀌거나 선택 파일이 누락·변조되면 이전 정상 화면을 유지하지 않고 오류를 표시합니다.

`four-panels.png` 원본은 1280×720입니다. YOLOP는 좌상단640×360, FRAME_LOCAL grid는 좌하단640×360만 `DISPLAYCROP`으로 표시합니다. 우하단 GT와 우상단 depth 그림은 잘라낸 표시 이미지에 들어가지 않습니다. crop은 설치된 Pillow로 수행하는 PC 표시 변환이며 원 PNG bytes/SHA와 geometry는 그대로입니다. metadata의 display_crop은 `REPRESENTATIVE_YOLOP_TOP_LEFT`·`REPRESENTATIVE_GRID_BOTTOM_LEFT` 두 고정 role만 허용합니다. 대표 장면의 PNG에 이 role이 없거나 panel/해상도가 다르면 거절합니다. 미리보기 cache는 panel·원 SHA·crop role을 함께 구분합니다.

P06의 `NOT_EXECUTED`는 실행하지 않은 장면입니다. 시각·순번·NN·media를 만들지 않습니다. `depth=None`은 입력 깊이가 없는 상태이며 별도로 저장된 rendered-depth나 PNG의 depth 그림으로 대체하지 않습니다. RAW16의0도 UNKNOWN으로 유지합니다. 원 pipeline metrics, NN 도로 픽셀 수·차량 검출 수, FRAME_LOCAL occupied/free/unknown, 원 packet metadata를 표시하지만 현재 MAP 위치나 차체 주행 가능 판정을 만들지 않습니다.

원 capture receipt의 depth SHA는 fault 적용 전 rendered-depth의 선언입니다. NATIVE 입력은 그 SHA와 실제 input-depth SHA가 같아야 합니다. ZERO 입력은 별도 input-depth SHA·길이·원 paired stamp와 전부0인 payload를 검사하며 capture receipt를 ZERO의 SHA로 바꾸지 않습니다. rendered-depth bytes는 읽지 않으므로 그 파일까지 검산했다고 하지 않습니다.

검산 범위는 **선택한 아티팩트**입니다. raw NN buffer·모델 blob·oracle·evaluation-only 내용은 읽거나 검산하지 않습니다. 모델 blob SHA는 source receipt의 원 선언이며 blob readback 성공을 뜻하지 않습니다. 전체 archive 검산·FPS·HIL·폐루프·실물 주행 성공으로 해석하지 않습니다. 원 clock과 PC clock은 연결되어 있지 않으므로 입력 age는 UNMAPPED, 현재 운영/READY/A-B 상태는 UNKNOWN입니다. GT 표시 checkbox를 켜도 이 형식의 GT는 읽거나 표시하지 않습니다.

## LIVE 연결과 해석 경계

- LIVE observer는 연결되어 있지 않습니다. 화면은 지정한 파일만 읽으며 원 데이터가 없는 패널은 UNKNOWN입니다. `MapperMotionObservation.fields`의 최대256KiB 원 JSON, NV12/native RAW16 bytes, 원 receipt/stamp/SHA에는 자동 파일/JPEG export가 없습니다. 별도 PC observer가 불변 결과 사본을 이 입력 계약으로 공급해야 합니다.
- 기존 `VideoHttpServer`는 JPEG만 제공합니다. 이 도구는 `/status`·`/health` endpoint에 접속하지 않습니다. `RuntimeStatus`의2048B 원 JSON도 파일 입력에 자동 연결되어 있지 않습니다.
- OFFLINE_SYNTHETIC에는 HISTORICAL 안내가 표시됩니다. 예제·표시 fixture나 과거 기록을 현재 차량의 관측으로 해석하지 않습니다.
- 파일·화면의 정상 표시는 물리/HIL/폐루프, 실시간 처리 FPS, 원 READY/주행권한, A/B 실제역할, 장치 APPLIED의 증거가 아닙니다. 제품 네트워크·드라이버·프로세스를 제어하지 않습니다.

## 테스트 명령

codec/cache/순번/clock/media/화면과 제품 수명 분리/offline/표시 변환의 의미시험은 stdlib unittest로 실행합니다. 이 명령은 Streamlit 서버나 제품 주행 프로세스를 시작하지 않습니다.

```powershell
& ".\vehicle\Jetson\build\streamlit-viewer-env\Scripts\python.exe" -m unittest discover -s vehicle/Jetson/tools/streamlit_viewer/tests -v
```

실제 대표 장면 archive의 선택 아티팩트 검산 시험도 포함하려면 `OAK_REPRESENTATIVE_RECORDING_ROOT` 환경변수에 해당 archive 폴더를 지정합니다. 지정하지 않으면 원 archive 시험1개만 건너뛰며 나머지는 임시 폴더의 명시 fixture로 실행합니다. 원 archive에는 쓰지 않습니다.

## 실험 완료 사본의 파일 export

[SimObservationExporter](../../tests/integration/sim_observation_export.py)는 실제 `MapperMotionObservation.fields`를 한 번 고정한 JSON bytes와 같은 결과의 `capture.observation`을 받습니다. 실험이 `finish_capture()`를 완료한 뒤 별도 diagnostic owner에서 호출합니다. helper는 제품·SDK·renderer를 import하지 않고 thread·queue·네트워크 전송이나 제품 loop의 동기 I/O hook을 만들지 않습니다.

```python
import json
from sim_observation_export import SimObservationExporter

original = json.dumps(actual_result.fields, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")
with SimObservationExporter(explicit_dedicated_directory) as exporter:
    receipt = exporter.export(original, actual_result.capture.observation)
```

`sim_observation_export`는 실험 harness의 integration 모듈 경로에서 import합니다. 관측마다 원 JSON bytes를 한 번만 고정하고 retry에도 같은 bytes를 사용합니다. 이 JSON은 진단 projection이며 MCU/protobuf 원 wire가 아닙니다. 출력 폴더를 PC에 전달하고 viewer에서 선택합니다. 자동 원격 전송은 제공하지 않습니다.

원 NV12·RAW16·FrameStamp·capture 구간·완료/E·순번·SHA를 유지합니다. 두 고정 슬롯의 media/원 JSON을 먼저 완성하고 snapshot을 마지막에 원자 교체합니다. 같은 원 관측은 `UNCHANGED`이며 파일·시각·순번을 갱신하지 않습니다. 다른 source epoch는 새 전용 폴더를 사용합니다. 정상 종료 후 같은 폴더 재개는 원 게시 파일/HWM을 검사하며 crash lock이나 첫 게시 전 미완성 폴더를 자동 인수하지 않습니다. fsync·무손실 전송 보장은 없습니다.

source kind는 `OFFLINE_SYNTHETIC`이므로 HISTORICAL로 표시합니다. 원 odometry 위치만 해당 `ODOMETRY:<epoch>`의 ESTIMATE 한 점으로 보존하며 MAP 경로를 만들지 않습니다. 입력에 없는 NN·grid·운영 값은 UNKNOWN입니다. GT는 기본 OFF이며 `display_gt_layers`에 명시한 같은 frame의 표시 층만 받습니다. 원 clock과 PC 수신 시각을 섞어 지연을 계산하지 않습니다.

순수 export/reader 의미시험은 제품·SDK·Streamlit 서버를 시작하지 않습니다.

```powershell
& ".\vehicle\Jetson\build\streamlit-viewer-env\Scripts\python.exe" -m unittest discover -s vehicle/Jetson/tests/integration -p test_sim_observation_export.py -v
```
