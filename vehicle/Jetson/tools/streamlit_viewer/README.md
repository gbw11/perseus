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
- GT 층은 기본 표시 OFF이며 표시용으로만 사용합니다. RGB·YOLOP·Native depth·Grid는 탭으로 선택합니다.

[fragment](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment)는 0.5초마다 화면 관측을 갱신합니다. 이 2Hz는 주행 처리 FPS가 아닙니다. 자체 daemon·RX thread·무한 queue는 없습니다. 서버를 끝낼 때는 실행 터미널에서 `Ctrl+C`를 누릅니다.

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
