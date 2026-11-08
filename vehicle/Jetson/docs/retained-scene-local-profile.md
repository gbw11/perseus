# 원 체크포인트·hazard·주차 증거의 Scene 연결

이 문서는 Jetson 내부 저장·의미 소비 계약이다. 현재 Pi SceneGrant나 이동 권한을 발급하지
않는다. 원 저장 증거, 새 LOAD의 현재성, 새 카메라 관측과 Pi의 현재 승인은 별도 사실이다.

## 원 CP와 LOAD 페이지

`CheckpointPayload.sha256`는 manifest SHA, evidence SHA, **원 복사 tile의 순서**를 결합한다.
전체 selected 참조128개와 원 복사32개 이하가 다를 수 있고, LOAD는 선택 page의 tile과 필요한
provenance closure를 복사하므로 page SHA나 합친 evidence SHA는 원 CP SHA를 대신하지 않는다.

D는 실제 SQL의 원 payload/receipt를 검산한 뒤 `CheckpointCommitOrigin`을 LOAD에 추가한다.

```json
{
  "profile": "checkpoint-commit-origin-1",
  "receipt": "<원 CommitReceipt.fields 객체>",
  "evidence_sha256": "<원 evidence SHA256>",
  "copy_tiles": ["<원 ordered [tile_x,tile_y,revision,SHA256], 최대32개>"]
}
```

원 payload SHA는 다음의 canonical JSON SHA256이다.

```text
{manifest_sha256: SHA256(original_manifest),
 evidence_sha256: original_evidence_sha256,
 copy_tiles: original_ordered_copy_refs}
```

원 job의 fields와 이 payload SHA로 원 job digest를 계산하여 receipt와 대조한다. 원 DB/job/
context, selected ref의 manifest 길이·SHA·tile 수·map/source·durable revision, 원 commit stamp의
원 job 구간을 exact 비교한다. 원 copy를 정렬하거나 전체 selected 참조로 교체하지 않는다.
오래된 commit은 역사적 사실이며 새 LOAD의 issued/completed/expiry/head로 현재 commit처럼
갱신하지 않는다.

## 내부 복사 호환 범위

`LoadedCheckpoint`의 마지막 선택 필드 `original_commit`은 legacy에서 None이다. CHECKPOINT의
기존 DB_COPY metadata8개와 prefix/layout/READY/SQL schema는 유지한다. 새 LOAD는 metadata에
`original_commit`을 추가한 exact9 variant를 사용한다. 새 decoder는 old8 LOAD를 기하 자료로
읽지만 원 descriptor 없는 자료로 Scene 복원 양성을 만들지 않는다. new9의 null·손상·알 수
없는 필드는 legacy로 낮추지 않고 거절한다.

구형 strict reader는 new9를 읽지 못하므로 D/M을 같은 구현으로 배포한다. metadata16KiB와
evidence region5MiB의 기존 상한을 늘리지 않는다. prefix/metadata/evidence/READY의 기존
hash 검사에 descriptor도 포함한다. 공용 MQTT·protobuf나 DB_JOB 제어 필드는 바꾸지 않는다.

## 전체 material의 복원

`CheckpointPages`는 원 selected CP의 page0/32/64/96을 원 순서로 모으며 페이지별 원 commit
descriptor가 같아야 한다. `SourceComplete`는 원 descriptor 하나와 실제 tile/evidence/page
request·완료·수락·head를 보존한다. 원 closure를 확인하기 전 전체 source를 노출하지 않는다.

`MapperScene.restore_complete`는 현재 grant의 RESTORE_CHECK, 원 전체 CP SHA, 전체128tile 이하,
원 Scene/base 계보·sensor/provenance와 현재 새 관측·정지 창·hazard revision을 대조한다.
`RestoredSurvey`는 실제 complete와 원 descriptor를 보존하며 페이지를 새 CheckpointRequest로
재포장하지 않는다. status4 RESTORE_READY는 Pi 승인 전 보고일 뿐이다. 이후 현재 원 ADMITTED
grant의 exact SHA/revision/hazard가 맞아야 eligible이 된다.

## 보수 hazard 기억

선택 META `hazard`는 다음 exact4 형식이다.

```json
{
  "profile": "mapper-hazard-state-1",
  "revision": 7,
  "method_manifest_sha256": "<승인된 원 mapper method SHA256>",
  "tiles": [[-1,0,"<canonical base64의 512B occupied bitset>"]]
}
```

revision은 CP 순간 채택 revision과 이미 발급한 floor 중 큰 값이다. 취소된 후보가 소비한 번호를
재사용하지 않되, 취소된 occupied bit를 채택한 것처럼 저장하지 않는다. tile은 signed int32
좌표의 유일한 사전순 목록/최대128개이며 zero-only tile은 생략한다. fixed는 원 spec 안의(0,0)
tile 유효 cell만, rolling은 원 immutable lattice의 전역 tile을 쓴다. META512KiB·압축48page
상한은 그대로다.

CELL이 UNKNOWN/FREE가 된 뒤에도 보존 occupied는 남을 수 있어 CELL occupancy만으로 hazard를
재생성하지 않는다. 원 bitset을 CP에 직접 보존한다. 복원 직후 내부 retained baseline만 두며
current snapshot/source/J 시각은 없다. 같은 map/context/calibration/method/lattice에서 실제 새
RGB와 depth gate가 진행된 뒤 새 source에 결박한다. 오래된 원 snapshot의 시각을 복구하거나
새 revision1/빈 장애물 지도로 대체하지 않는다. hazard 없는 legacy CP는 명시 NOT_READY다.

정상 CP와 Scene freeze 둘 다 동일 compute owner의 채택 hazard를 포함한다. cached catalog
state에서 옛 hazard를 자동 재사용하지 않는다. 이후 새 FREE로 occupied를 제거할 때는 기존
원 depth/ToF proof·시각·순번/현재 source 검사를 유지한다. ToF 장착을 필수로 추가하지 않는다.

## 승인 ToF 자산과 현재 표본

`SceneEvidenceScope(..., approved_tof_assets=...)`는 역사 COMPLETE descriptor 검증에만
쓰는 로컬 `(external_leaf_name, TofLeaf)` 목록이며 최대 2개다. 실제 Mapping/Processor는
`MapperProfile.sensors.tof_sources`를 명시한다. 승인 bundle의 exact leaf/configuration/driver/
거리 convention을 대조하고, 과거 context의 원 instance/generation·raw key·Pi 시각을 보존한다.
현재 fresh 표본이 없어도 같은 승인 자산의 과거 기록을 검증할 수 있지만 현재 TOF context는
계속 실제 fresh instance/generation을 요구한다. 자료 복원이 새 Scene/센서 사용권은 아니다.

생성자 인자 `None`은 기존 호출을 위해 검증된 현재 `tof_sources`에서만 자산을 도출한다.
명시 `()`는 승인 자산 없음이며 과거 ToF를 수락하는 wildcard가 아니다. 등록된 선택적
unqualified leaf도 목록에는 보존하되, 실제 현재/과거 ToF proof를 소비할 때는 기존 네 가지
qualification을 계속 요구한다. RGB-only scope는 선택적 leaf의 미수락 때문에 막지 않는다.
자산 제거·변경이나 method SHA 변경을 표본 부재와 혼동하지 않고 기존 NOT_READY로 남긴다.
이 구분은 wire/SQL·원 proof·관측 기한·필수 sensor mask를 바꾸지 않는다.

## 실제 ParkingBridge 문맥

SceneEvidenceScope는 현재 Bridge의 실제 method 원문을 승인 비교 대상으로 등록한다.
`parking-method-1` exact10 정책·body·geometry/association profile·보정·전체 bundle·map frame·
좌표와 명시한 current/history full StorageContext를 대조한다. SHA만으로 옛 문맥을 재구성하지
않는다.

`parking-originals-1` exact6은 실제 method edge, typed SlotSource/FrameStamp와 원 source SHA,
catalog revision/entry, 원 provenance ID와 parent mask union·Pi 구간/순번을 검산한다. 두 프로필
소비에는 실제 EvidenceTable closure가 필요하며 일반 METRIC 이름만으로 수락하지 않는다.

직접 RGB/depth/ToF/odom raw key의 실제 sensor bit를 보존한다. 원 RGB+depth 기하/aggregate를
RGB-only로 바꾸지 않으며 depth 포함 과거 aggregate를 HISTORY 기하 예외로 수락하지 않는다.
현재 조사 창의 freshness와 양성 FREE/필수 sensor mask는 기존 Scene closure가 따로 검사한다.

## World 후보의 보존 FREE

실제 FrozenSurvey/RestoredSurvey의 원 receipt/full CP SHA와 현재 관측의 SceneDraft·HazardDraft를
같은 MappingFrameDraft에 결박한다. 현재 ADMITTED grant의 full SceneAdmissionRef는 publication의
표준 protobuf JSON과 exact 비교한다. 현재 source·원 ClockLease·hazard revision·Scene/관측/정합/
원 MapperInput 만료도 유지한다. 실제 운영 World의 LIVE/localization gate는 별도로 통과해야 한다.

rolling은 사전 등록된 원 SceneAtlasArchive의 현재 창 최대4tile만 scoped borrow한다. fixed는
원 전체 tile 사본4개 이하 또는 실제 restored Complete의 동일 작은 inventory를 사용한다.
원 META/spec/lattice/정책·map/Pi 문맥을 대조하고 실제 원 CELL의 valid FREE만 투영한다.

현재 RAW OCCUPIED·conflict·unordered hit 또는 현재 local/global hazard는 만료 뒤에도 보존
FREE로 덮지 않는다. 사용할 수 있는 fresh LIVE FREE는 그대로 둔다. 보존 FREE는 별도 readonly
후보 snapshot에서 state/support/permission과 static flag를 표현하며 실제 LIVE grid는 바꾸지
않는다. 통행 근거는 원 whole-cell FREE 증거와 현재 Scene 승인이다. 현재 카메라가 뒤를 새로
관측했다고 해석하지 않는다. UNKNOWN·기하 보간만으로 FREE를 추가하지 않는다.

보존 age는 원 CELL.pi_lower와 현재 승인 lease가 환산한 publication의 Pi 상한으로 보수 ceil-ms를
계산하고 uint32에서 포화한다. 실제 기존 J age가 있으면 낮추지 않는다. 미확인 J sentinel을
실제 나이라고 해석하지 않으며 과거 Pi를 새 J 관측 시각으로 만들지 않는다. 원 SceneRef는
World에 그대로 복사하고, 최종 채택은 원 Scene version/절대 만료를 다시 확인한다.

## 조사와 ADMITTED의 수명

원180초/RESTORE_CHECK 조사 deadline과 station24는 조사 상태0/1에 적용한다. 감독 아래 sampled
이동10m 상한은 purpose0 조사에만 누적한다. ADMITTED 상태2는 원 valid_until(승인된 최대4시간),
현재 clock·관측/정합·MapperInput 수명을 유지하며 조사 station을 추가하지 않는다. RGB/map
관측은 원 시각·순번 증가와 기존 freshness/pose bracket을 검사한다. 정지 창의 실제 Vehicle
sample max gap은 계속 별도 검산하며 RGB 처리 간격에 그 수치를 적용하지 않는다.

## ADMITTED 증분 CP의 실제 cutoff와 호환 범위

최초 frozen base의 `collection_end_pi_us` 의미와 원 manifest는 그대로다. 새 descendant CP는
실제 Collector snapshot 뒤 최초 payload를 만들기 전에 `collection_end_pi_us`를 그 사본의
`durable_cutoff_pi_us`와 정확히 같게 고정한다. 원 begin/base evidence SHA/station/Scene 계보와
grant의 최초 만료를 유지한다. 실제 원 capture와 등록된 보간 Vehicle 부모의 Pi hull, 현재
승인 clock의 Pi 현재 상한 안에서만 허용하며 현재 시각이나 `now+4h`로 수집 범위를 만들지 않는다.
원 CELL/관측의 시각·FREE age도 갱신하지 않는다.

이 경계는 compute-local `scene_at_snapshot` callback으로 연결하며 static scene descriptor와
동시에 지정하지 않는다. 선택 시점부터 eligible ADMITTED가 아니면 일반 geometry-only CP를
만들 수 있다. 원 frozen/archive 소유권은 별도로 보존한다. eligible일 때 선택한 callback이
snapshot 전에 stale이 되면 그 사본만 거절·해제하고 geometry-only로 몰래 낮추지 않는다.

`base_evidence_sha256`가 있는 descendant의 Scene 복원은 end와 durable cutoff의 exact 일치,
현재 원 grant/clock/위험·원 만료를 검사한다. **이 수정 전에 생성되어 nominal180초 end와
실제 durable cutoff가 다른 descendant도 Scene 복원에서는 거절한다.** 필드 모양과 SQL schema가
같다는 이유로 모든 과거 Scene 복원과 호환된다고 해석하지 않는다. 원 bytes와 geometry 자료는
계속 읽을 수 있지만 이전 CP의 end/시각/hash를 다시 쓰지 않는다. 새 증분 producer와 이 소비
검증을 함께 배포하며, 새 CP는 자기 실제 transaction/descriptor를 가진다.

## 원 key의 부정 사건

등록 source/Pi/Gateway와 이미 수락한 원 grant key가 정확한 terminal3/4만 current ClockLease
부재·revision 전진과 독립적으로 철회할 수 있다. 원 full SceneRef/nonce/source admission
revision/issued/valid_until/purpose/frozen SHA/나머지 필드는 같고 lifecycle state/revision만
변한다. 낮은 revision·다른 nonce·다른 원 key의 부정 사건은 새 복원을 취소하지 않는다.

JIO는 최종 lock에서 source token과 당시 실제 known head를 다시 대조하고 원 bytes를 기존
SOURCE_SCENE으로 전달한다. Mapper는 기존 Scene의 원 key만 철회하며 lease/기한을 새로
만들지 않는다. 최신 positive grant는 현재 실제 lease의 source admission revision과 반드시
같아야 한다. 정상 numerical clock 갱신과 의미상 source revision 전환은 구분한다.

실제 Pi/Gateway/VM 기기 사이의 grant 전달·계획 fence, 전체 Processor의 프레임 채택·발행과
장시간 OAK 상주 흐름, ToF 미장착 cold-start·회피·후진 주차 및 물리 성능은 별도 통합 대상이다.
구현 경과와 실행 결과는 저장소 README 대신 외부 making-reports에 남긴다.
