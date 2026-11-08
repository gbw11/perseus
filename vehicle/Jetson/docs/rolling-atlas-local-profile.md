# 이동 ROI와 내부 atlas의 local 계약

이 문서는 SQL·외부 protobuf wire를 바꾸지 않는 선택형 내부 확장이다. source/clock/Scene
승인이나 센서 자격을 만들지 않으며 기존 fixed mapper profile의 canonical bytes를 유지한다.

## 격자와 관측 창

`MapLattice(anchor_xy,resolution_m)`는 최초 `PipelineProfile.grid`의 원점을 고정 anchor로
삼는다. `GridWindow.offset_xy`는 signed global cell 좌표다. 현재 ROI origin은 매번
`fsum(anchor,offset*resolution)`으로 계산하며 누적 float 이동을 하지 않는다. tile은64cell,
global cell의 `divmod(value,64)`가 signed tile과0..63 local cell을 준다. 예를 들어 -1은
tile -1/cell63이고 -64는 tile -1/cell0이다. window는 최대64x64, 교집합 tile은 최대4개다.

MapperProfile의 선택형 `rolling_roi`는 exact `{margin_cells,lookahead_cells}`다. 두 값은
정수이며 margin은1 이상·창의 짧은 변 절반 미만, lookahead는0 이상·짧은 변 절반 이하다.
정합을 통과한 nominal MAP pose에서 heading 방향 lookahead cell만큼 앞선 점을 고른다.
그 점이 현재 창의 margin 안쪽이면 창을 유지하고, 밖이면 그 점을 창 중앙 cell로 옮긴다.
이 선택은 ROI 배치이며 pose 오차나 통행 자격을 줄이지 않는다. 격리/pose 미수락/시계 손실
입력은 창을 이동하지 않는다. 생략/null은 기존 fixed 동작이며 숨은 수치를 넣지 않는다.

GridMutation의 선택형 `window_offset_xy`는 실제 mutation 당시 불변 주소다. observer는
그 원 offset과 shape로 local index를 global cell에 대응한다. 이후 창의 origin으로 과거
mutation을 재해석하지 않는다. 원 sensor key·시각·HWM·map/frame/odom ID는 변하지 않는다.

## 유한 version pool

LIVE와 EVIDENCE를 별개 immutable block으로 두며 **둘 다** 총320version·80MiB에서 센다.
logical tile key는128개, 미저장 dirty version은32개, frozen 참조는128개까지다. 같은 원
block의 lease는 본문을 복제하지 않는다. 각 상한의 동시 최대를 모두 보장하지 않으며
합산 budget이 먼저 차면 RESOURCE/BUSY로 거절한다. 추가 pool이나 전체 atlas 사본은 없다.

현재 배치에서128개의 LIVE/EVIDENCE current pair는256block·64MiB다. 같은 EVIDENCE의
checkpoint/frozen lease를 공유하면 본문은 늘지 않는다. 독립된 옛 frozen128개까지 모두
유지하면384block·96MiB이고 dirty32개도 따로 있으면416block·104MiB이므로 보장하지 않는다.
mapper의 명시4MiB ROI/codec workspace까지 포함하면 version 수가320 미만이어도 data가
먼저 찰 수 있다. 실제 보장 조합과 highwater는 시험 receipt에서 block·dirty·lease·bytes를
함께 기록한다. EVIDENCE dirty32가 모두 미완료 CP에 고정됐을 때 새 EVIDENCE를 받아들이기
위해 그 CP를 clean으로 꾸미지 않으며, 기존 map을 유지하고 명시 RESOURCE를 보고한다.

각 block은4096x64B=262144B다. EVIDENCE는 기존 저장 `CELL64` 원문이다. LIVE는 little-endian
`<BQQdd31s>`다. flags의 bits0..1은 state(UNKNOWN0/FREE1/OCCUPIED2), bit2 support,
bit3 permission, bit4 time_known, bit5 conflict, bit6 unordered_hit, bit7 has_view다.
뒤 필드는 원 observed_j_us, hit_available_j_us, hit_origin_x/y와31B view key다. view key는
rolling에서 HIT/FREE 양쪽에 같은 SHA256(view_id UTF-8) 앞31B를 적용한다. hash 충돌은
두 view를 같다고 취급해 독립 view 인정을 막을 수 있으며, 같은 view를 다른 view로 바꿔
clearance를 승인하지 않는다. 원 sensor/provenance 식별자는 이 key로 대체하지 않는다.

후보 ROI 배열·pack 임시 buffer도 실제 peak budget에서 예약한다. pool prepare는 원
expected reference와 generation, old+new 실사용 bytes를 검사하고 변경 tile만 분리한다.
최종 accept는 준비한 참조를 교환하며 취소는 기존 본문·age·HWM을 바꾸지 않는다. 동일 key의
완전 replacement가 원 current 전체를 보존할 때 unleased 옛 dirty를 superseded로 닫는 것은
cache eviction과 다르다. frozen/submitted 원 version은 실제 terminal 전 그대로 남는다.
cache eviction은 clean+unleased만 허용하며 dirty/frozen을 공간 확보용으로 지우지 않는다.

최종 accept는 교환 전 state와 metadata를 retiring 참조에 보관한다. 마지막 소유권 해제로
큰 본문이 adoption lock 안에서 해제되지 않게 하며, 다음 compute 유지/prepare의
`drain_retired()`에서 해제한다. 실제 해제 전까지 current·retiring·staged의 고유 block
합집합을 version/data 상한에 센다. retiring에만 남은 block은 새 lease나 현재 관측의
근거가 아니며, CP 완료/coalesce도 아직 보관 중인 물리 bytes를 계수에서 빼지 않는다.

원 CheckpointRequest·SceneFrozen·ScheduleTerminal·SourceComplete가 lease 밖에서도 같은
tile bytes를 보존할 수 있다. 이 원 객체를 weak-owner pin으로 등록하고 실제 객체 수명까지
해당 block을 합산한다. owner는최대16개, owner별 원 참조128개 이하이며 같은 block은 data를
중복하지 않는다. callback은 유한 해제 token만 기록하고 실제 pin 정리는 compute owner가
유지 단계에서 한다. GC를 현재 COMMITTED/Scene 승인으로 바꾸지 않는다. 객체가 오래 남으면
실제 pool도 계속 차지하며 RESOURCE를 우회하지 않는다. weakref 등록 전 객체를 내보내지 않는다.

Scene의 원 full128 참조는 CP request의 copy32 pin과 별개 `SceneAtlasArchive` owner로
고정한다. carrier는 원 job/manifest/CP SHA·full wire refs·pool refs만 보유하고 본문 사본을
만들지 않는다. 활성 CP/FROZEN 또는 검산한 원 Complete의 bootstrap pin에서만 등록하며,
이미 준비한 frame 후보 뒤에 최초 등록하지 않는다. CP 완료는 Scene archive 해제가 아니다.

현재 World 계산은 원 archive pin의 scoped borrow로 현재 창 최대4tile만 읽는다. 실제
owner/pin/token/ref를 검사하고 범위 동안 owner를 강하게 보존한다. 소유권이 이미 고정되어
새 lease·generation·dirty 상태를 바꾸지 않는다. 일반 READ lease의 generation 규칙은
유지한다. 반환 reader/본문은 scope 밖에서 사용·보관하지 않는다. 이 구분으로 staged
AtlasDelta를 읽기 작업이 무효화하지 않으면서 실제 보관 bytes도 계속 계수한다.

## proof와 저장

전체 atlas를 cell별 Python dict로 펼치지 않는다. packed EVIDENCE를 보존하고 현재 ROI의
최대4096cell 작업 공간만 분리한다. unresolved는 global tile별512B bitset으로 보존하며
창 밖으로 이동한 이유로 지우지 않는다. 현재/dirty/frozen 원 EVIDENCE의 provenance root와
context closure를 보존하고 기존16384 provenance·metadata8MiB·index16MiB 상한을 유지한다.

CP는 변경한 signed global EVIDENCE tile32개 이하와 전체 selected 참조128개 이하를 기존
TileVersion/manifest/page 경로로 보낸다. 옛 CP A의 ACK는 A의 durable baseline만 바꾸며
그 뒤 관측한 B를 clean/current로 덮지 않는다. LIVE 본문은 mapper META에 넣지 않는다.

## 선택형 META와 복원

기존 `mapper-pipeline-state-1`에 선택 key `atlas`만 추가한다. exact 필드는 다음과 같다.

```json
{
  "profile": "atlas-rolling-1",
  "lattice_origin_xy_m": [0.0, -1.6],
  "roi_origin_cell": [-64, 0],
  "tile_cells": 64,
  "live_restore": "RETAINED_EVIDENCE_ONLY",
  "policy_sha256": "<canonical rolling_roi SHA256>"
}
```

숫자는 설치 예이며 기본값이 아니다. resolution/width/height는 기존 `state.spec`를 재사용하고
origin은 공유 격자 계산과 exact 대조한다. tile inventory·본문·새 source ID는 중복하지 않는다.
key 부재는 legacy fixed 문법이다. 생략한 옛 상태에 null을 추가해 hash를 바꾸지 않는다.

같은 process의 재방문은 LIVE의 원 J 시각·flags·view로 TTL을 평가한다. 재기동/eviction으로
LIVE가 없으면 원 Pi CELL·순번·proof·revision을 EVIDENCE로만 보존하고 LIVE는 UNKNOWN이다.
오래된 Pi를 현재 ClockLease로 환산해 새 J 나이를 만들지 않는다. 모든 tile/proof/원 asset과
실제 Scene 재검증 전에는 저장 FREE를 현재 FREE 또는 static 사용권으로 내보내지 않는다.

선택 디렉터리에 원 EVIDENCE ref가 있으나 cache 본문이 빠졌다면 `EVIDENCE_LOAD_REQUIRED`다.
기존 CURRENT_MAP의 exact TILE 선택(map_epoch,tile_x,tile_y,tile_revision)으로 원 checkpoint에서
읽고 원 hash·ref·CELL/provenance를 검산한 실제 LoadedCheckpoint만 다시 설치한다. 해당 LOAD의
원 객체도 weak owner pin에 등록하며 새 pool serial은 memory ID일 뿐 새 DB revision/관측이
아니다. LOAD가 끝나도 LIVE 나이는 만들지 않는다. 미확정/실패 원 요청을 새 기한으로 재발행하지 않는다.
