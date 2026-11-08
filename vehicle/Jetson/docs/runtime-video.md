# 상주 JPEG 영상의 소유권과 수명

`runtime --http-host 127.0.0.1 --http-port 8080`으로 읽기 전용 영상을 선택한다.
HTTP를 선택하면 S/A/M/D의 직접 thread는8개다. S-http 한 개가 모든 client를
selector로 처리하며 client별 thread를 만들지 않는다. 사용자 RUN/STANDBY와 Pi의
출력 권위는 이 영상 API의 책임에 포함하지 않는다.

## 장치와 원 capture

A는 같은 ColorCamera.video를 기존 RGB·YOLOP와 선택 VideoEncoder에 연결한다.
encoder의 MJPEG graph 요청은 카메라 profile의15fps다. host/device JPEG queue는
각1/nonblocking이며, SDK message/view에서 최대256KiB를 소유 bytes로 한 번 복사한다.
graph 시작·모델 적재는 같은 A session에서 한 번 수행한다.

JPEG는 원 RGB의 sequence/device/SDK END 시각이 모두 맞는 경우만 사용한다.
A의 원 ClockEvidence와 RGB 노출로 capture interval을 계산하고 metadata8개만
보존한다. JPEG의 수신 시각이나 시청 요청 시각을 capture로 쓰지 않는다. 원 RGB가
없거나 원 bridge·노출을 확인할 수 없으면 JPEG를 버린다. 이 시간 결합은 영상 age를
제한하는 근거이며 OAK 동기·readout·장착 보정의 metric 수락을 뜻하지 않는다.

## 별도 전송

전체 RawBinding은 JPEG 선택까지 고정한다. raw P0에는 RGB/depth/NN의 기존 네
stream 표와 plane만 넣고 전체 binding hash를 그대로 대조한다. JPEG를 raw arena에서
빌리지 않으며 A의 소유 bytes를 별도 local stream socket으로 전달한다.

framing은 little-endian `header_length:u32, body_length:u32, header, body`다.
header≤1024B/body≤256KiB를 할당 전에 검사하고 원 header/길이/CRC32/현재 scope를
대조한다. header의 exact15필드는 다음과 같다.

```text
version, service_instance, acquisition_worker, pipeline_instance, capture_epoch,
frame_sequence, capture_j_lower_us, capture_j_upper_us, completed_j_us,
expires_j_us, width, height, encoding, payload_length, crc32
```

version1/640×360/MJPEG를 사용한다. 원 만료는 capture lower+500ms 이내이며 새 offer나
viewer에 배정할 때 연장하지 않는다. sender/receiver는 원 전송 시작+250ms를 제한한다.
receiver가 상류 시작을 모르는 경우 원 completed+250ms로 더 짧게 제한한다. partial의
CRC·길이·원 기한이 깨지면 영상 채널을 닫고 남은 JPEG만 회수한다.

## HTTP와 자원 한도

| 요청 | 응답 |
| --- | --- |
| `GET /camera/stream.mjpg` | 현재 원 JPEG의 multipart 연속 응답 |
| `GET /camera/snapshot.jpg` | 현재 원 JPEG 한 장과 연결 종료 |

현재 영상이 없거나 만료됐거나 자원이 차면503으로 응답한다. request header는4096B,
원 request 대기는250ms다. client2개와 송신 slot2개가 각각200ms 간격을 보존하여
5Hz를 제한한다. snapshot 재접속·client 종료·채널 교체가 이 간격을 환불하지 않는다.
각 원 frame의 partial write는 원 expiry와 배정 시작+250ms 중 이른 시각까지다.

S의 JPEG는 latest1+viewer별 in-flight1+partial ingress1만 보유하고 총 소유 bytes는
1MiB 이하다. immutable bytes의 참조를 공유하며 송신을 위해 큰 body를 이어붙이지
않는다. bytearray→bytes 변환 순간의 중복 소유도 같은 한도 안에서 검사한다.
HTTP5Hz 제한을 NN·장치 인코딩·USB 비용 절감으로 해석하지 않는다.

## 교체와 종료

M/D 대기·부분 재기동은 건강한 A 영상 통로를 유지한다. A의 알려진 철회·EOF·실제
종료 또는 새 A의 channel/scope 교체는 옛 JPEG와 viewer를 회수한다. 느린 viewer는
자기 연결과 frame 참조만 잃으며 Device·모델·RAW/지도 세대를 바꾸지 않는다.

STOP는 S-http를 깨워 client·partial·queued channel을 닫는다. S는 bounded join과
실제 thread 종료를 확인하며, 미종료 thread를 정상 회수로 기록하지 않는다.
최종 runtime receipt의 `http`, `http_error`, `http_exited`는 영상 진단이다.

실제 SDK의 bitstream 노출 metadata·원 RGB 대응, 이미지 decode, 대역폭·지연·FPS와
서비스 thread inventory는 target 검증 대상이다. 구현 경과와 원 실행 결과는
`Jetson-drive-docs/making-reports/0061-s22-runtime-status-video.md`에 별도 기록한다.
