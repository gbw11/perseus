# Mapper checkpoint local profile

이 문서는 내부 Python/SQLite payload 계약이다. `DB_JOB`, CELL64, tile256KiB,
provenance256B, 기존 SQL schema와 기존7필드 manifest는 유지한다. 저장/import/encode
성공은 metric 준비·scene 승인·추진 권한이 아니다.

## 실제 셀 출처

`EvidenceGrid.add_hits/add_free(..., observer=None)`는 기존 정수 count를 반환한다.
observer에는 원 `FrameStamp`, shape와 불변 bool bytes 두 장을 가진 `GridMutation`을
동일 compute owner 안에서 전달한다. `affected_mask`는 실제 영향을 받은 셀,
`source_written_mask`는 원 source/time을 실제로 쓴 셀이다. old/untimed hit가 막힌 셀에
현재 RGB의 출처를 덮지 않는다. provenance와 occupancy가 어긋나면 CP는 NOT_READY다.

`FrameEvidence.from_capture`는 실제 owned `CaptureSample`과 `PerceptionInput`의
payload/seq/stream/device time, 원 ClockLease의 Pi/J 구간, SceneEvidenceScope와 parent
DAG를 대조한다. Pi interval은 caller의 원 clock 환산 결과이며 arrival/현재시각으로
만들지 않는다. `vehicle_parent`는 실제 수락 Vehicle의 원 session/source key·Pi interval을
같은 내구 예약 range에서 생성한다. 단일 ToF는 `from_tof/observe_tof`로 실제 sensor
instance/generation/leaf와 원 zone 결과를 결합한다. 합성 rear view나 누락 zone FREE는 없다.

운영 M 연결은 `MapperSensorState.original_pose_bracket`으로 PoseBuffer가 실제 사용한
두 endpoint의 원 `VehicleAdmission`을 읽는다. 원 wire·ClockLease·Pi/J interval·당시 수락과
callback 시각을 capacity256 이하로 보존한다. 옛 endpoint를 최신 Vehicle로 재수락하지
않는다. `vehicle_admission_parent`가 그 원 객체만 PI_ODOM으로 옮기고 `from_capture`의
선택 `pose_bracket/sensor_state` 쌍은 두 raw key·원 Pi interval·순번 proof가 모두 있는지
검사한다. 별도 현재 k가 있으면 k의 parent도 추가하며 두 보간 입력을 대신하지 않는다.

`frame_observer(frame, pipeline=actual_pipeline)`는 actual mutation/certificate/ray/source만
기록한다. `observe_result(frame,result,pipeline,raw_pose=...)`는 mutation0인 실제 RGB와
FrozenReference도 연결한다. registration은 원 Vehicle anchor·명시 valid_for_us와 실제
accepted alignment/reference가 있을 때만 기록하며, 동일 alignment의 원 timestamp와
revision을 재사용한다. 이전 reference와 원 Pi odom을 parent DAG에서 생략하지 않는다.
closure가16384 records/64 contexts를 넘으면 증거를 버려 cap에 맞추지 않고 NOT_READY다.

정합 수명은 `MapOdomAlignment`의 원4필드에 선택5필드를 모두 결합한다.
`observed_j_interval`은 수락한 raw pose의 전체 capture J 구간,
`registration_revision`은 그때 수락한 FrozenReference revision이다. `valid_for_us`,
`position_drift_mps`, `yaw_drift_rps`는 호출자가 명시한 configuration 가정이며 실측값을
추정해서 채우지 않는다. 5개가 모두 없는 원4필드 자료는 진단용 legacy로만 읽고,
일부만 있는 상태와 full9에 null을 넣은 상태는 거절한다. 최초 identity map을 BOUNDED
정합으로 위장하는 timestamp/revision은 발급하지 않는다.

`process`의 `localization_valid_for_us`, `localization_position_drift_mps`,
`localization_yaw_drift_rps`도 전부 명시하거나 모두 생략한다. `apply`는 원 관측 earliest부터
새 capture upper까지의 나이로 base alignment 오차를 늘린다. 이전 frame에서 늘어난 값을
다음 frame에 다시 더하지 않는다. `valid_at`의 끝은 원 earliest+valid_for이며 정상 frame,
clock, publication은 연장하지 않는다. 만료해도 전체 prior를 보존해 새 실제 BOUNDED 정합은
시도할 수 있다. 운영 FREE/품질1은 현재 metadata와 명시 configuration의 일치가 필요하다.

## META pages

기존 scene/survey 객체의 나머지 필드를 보존하며 다음 local member만 더한다.

```json
{"mapper_state":{"profile":"mapper-state-1","encoding":"ZLIB_CANONICAL_JSON_1",
 "state_sha256":"hex64","state_length":1,"compressed_length":1,
 "pages":[[0,"context_sha256"]]}}
```

각 page는 기존 `ContextRecord(kind=METRIC)`이며 body의 exact fields는
`profile=mapper-state-page-1,state_sha256,state_length,compressed_length,index,count,chunk_b64`다.
원 canonical JSON 상태는512KiB 이하, 압축 bytes는48×2304B 이하이고 마지막을 제외한
각 chunk는2304B다. 각 context payload는4KiB 이하이며 기존5MiB evidence/64-context
예산 안에 들어간다. META를 위한 별도 arena/SQL table/대형 inline 제어 메시지는 없다.

상태는 `mapper-pipeline-state-1`이며 원 storage context·clock·spec/profile hash·배열의
dtype/shape/bytes·source gate HWM·hit view·alignment·FrozenReference와 그 실제 proof
roots, tile revision highwater를 보존한다. `lane_catalog`의 `lane-catalog-state-1`과
`parking_catalog`의 `parking-catalog-state-1`은 명시 subtree로만 보존한다. 각 catalog
owner가 원 digest/ID/source를 별도로 검산하며 META tree가 allocation/use권은 아니다.

full9 alignment만 선택 `alignment_clock`을 가진다. exact fields는
`profile=alignment-clock-source-1,lease,pi_interval,accepted_j_us`다. `lease`에는 원
`ClockLease`의12필드를 그대로 보존하고 `trust_bytes/raw_probe_bytes`만 canonical base64로
직렬화한다. `pi_interval`은 원 REGISTRATION provenance header의 Pi 구간이며
`accepted_j_us`는 당시 frame 수락시각이다. LOAD는 그 원 clock을 원 수락시각에서 재구성해
J 구간·Pi host·mapper/geometry/calibration을 검산한다. 현재 lease/arrival로 바꾸지 않는다.
REGISTRATION union의 원 revision/validity와 META가 다르면 완전 LOAD 이후에도 semantic
restore는 NOT_READY다. 원4필드 legacy에 이 witness나 현재 drift를 덧붙이지 않는다.

D LOAD는 manifest가 exact SHA로 참조한 META pages와 실제 reference/registration
provenance roots만 tile/catalog closure에 더한다. unreferenced context를 임의 복원하지
않는다. 누락·과대 길이·손상·지원하지 않는 mapper shape는 legacy 성공으로 바꾸지 않는다.
원 META가 없는 legacy 자료는 storage COMPLETE일 수 있지만 pipeline restore는 NOT_READY다.

## Copy와 원 request

`snapshot(..., include_unchanged=True)`는 필요하면 active immutable tile도 복사한다.
`MapSnapshot.tiles`는 D에 보내는32개 이하 copy, `all_tiles`는 실제128개 이하 frozen
전체 bytes이며 `tile_references`와 exact 대응한다. `current_tile_references`는 현재
owner의 실제 generation/revision이 사본 이후 바뀌면 실패한다. hash-only 목록은 전체
freeze 검산을 대신하지 않는다. 작은 초기 grid는 최대4tile지만 full LOAD는128tile다.

일반 CP는 `capture`로 원 CheckpointRequest를 만든다. scene owner는 snapshot의 실제
tiles/evidence/META/full refs로 최종 원 manifest를 만든 뒤 `track_checkpoint`한다.
새 RAM 데이터는 D의 fsync를 기다리지 않는다. 실제 current `CommitReceipt` 전에는
durable refs를 바꾸지 않는다. 늦은 COMMITTED는 history일 수 있으며 `retire_checkpoint`
정리와 현재 성공을 구분한다. source/age/IDs를 새 job으로 다시 발급하지 않는다.

`track_checkpoint(..., catalog_extension=True)`는 명시 catalog 확장만 허용한다. 원 map의
job/tiles/full refs/META ref/cutoff와 proof/context bytes는 보존한다. 기존 catalog ID 삭제,
revision regression·동일 revision의 다른 bytes, 원 reservations 변경은 거절한다. 추가
proof/context는 실제 final catalog root의 parent closure에만 속할 수 있다. 실제 ID range,
geometry와 원 source의 검산은 기존 Catalog/Bridge/D가 담당하며 이 포트는 발급권이 아니다.
기본 `catalog_extension=False`는 원 evidence bytes 전체의 exact 비교를 유지한다.

## M compute 연결

`MappingContextPort.publish/read`는 M-main이 수락한 원 불변 StorageContext 참조만 Lock
안에서 교환한다. ACK나 새 time lease를 만들지 않는다. `MapperMapping.maintenance`만
전용 PROVENANCE 요청을 한 번 생성하고 실제 원 WireProvenanceGrant에서 collector를 만든다.
미수락 physical timing/localization/targets/permission은 D offer0이다. mode/manual/정상
clock 갱신은 초기화를 일으키지 않으며 실제 geometry 교체는 새 owner를 요구한다.

`prepare_frame`은 원 item·actual MetricInputs·원 두 Vehicle endpoint에서 frame/observer와
독립 collector 후보를 만들고 `finish_frame`은 실제 PerceptionResult와 catalog 후보를 연결한다.
`ready=False` 후보는 accepted metric map/reference/HIT/gates에 채택하지 않아야 한다.
`accept`는 참조만 교환하고 `cancel`은 후보를 버린다. 후보가 쓴 provenance cursor는 burn하여
다른 source에 같은 번호를 재발행하지 않는다. 원 pending request/snapshot/bytes는 clone해도
같은 객체를 공유하며 mailbox/Lock/grid를 deepcopy하지 않는다. 관측 중 D offer는 없다.

`queue_checkpoint`는 caller의 원 StorageJob으로 확정 상태만 고정한다. EMPTY에서는 current
catalog head를 만들지 않고 첫 map CP의 실제 COMMITTED receipt에서 원 ref13을 만든 뒤
실제 CURRENT_MAP LOAD로 head를 얻는다. head 이후에는 Catalog/Bridge의 실제 CP builder와
위 명시 확장을 사용한다. 실패/만료/UNKNOWN을 새 request/nonce/기한으로 자동 재발행하지
않는다. 명시 `queue_head_load(job)`만 원 내구 ref를 새 실제 read job에 연결한다.
초기 PROVENANCE의 실패 뒤에는 명시 `queue_provenance(request, now_us=...)`만 새 원
job+새 nonce를 수락한다. 옛 job UUID/nonce의 재포장이나 같은 range의 cursor 재설정은 없다.

현재 `MapperMapping`은 camera/Vehicle 증거와 catalog/CP를 연결한다. ToF는 실제 현재
instance/generation/leaf를 scope에 보존하지만 grid fusion은 아직 연결하지 않는다. ToF
시각의 자체 PoseBuffer bracket·map alignment와 실제 mutation/철회 검산 전까지 camera pose로
대체하거나 ToF FREE를 발급하지 않는다. ToF 역사 증거가 있는 COMPLETE의 운영 연결도 해당
실제 current sensor scope가 준비되지 않으면 NOT_READY다.

## 완전 LOAD와 SAFE_RESTORE

`CheckpointPages`는 실제 semantic decode된 `LoadedCheckpoint`만 받는다. 선택한 원 ref,
manifest/catalog bytes, mapper/storage 문맥, 페이지 순서와 전체 parent/context closure를
검사한다. 최대128tile의 네32-tile 페이지가 실제로 모여야 `SourceComplete`가 생성된다.
물리 RELEASE·tile_count·성공 bool로 만들 수 없다. BOOTSTRAP6의 원 selection만 각
페이지와 대조하고 나머지 stable identity를 exact 비교하므로 페이지별 원 context SHA를
보존한다. CURRENT_MAP은 full context를 그대로 비교한다. 각 page 원 기한과 외부 actor의
전체 bootstrap 기한은 갱신하지 않는다. `current_head`도 마지막 LOAD의 원 기한만 쓴다.

`restore_pipeline`은 승인 asset/Pi geometry origin과 명시 historical scopes를 검사한다.
원 live J 시각/배열은 META에 보존하고 현재 live grid는 UNKNOWN으로 시작한다. 원 source
gate는 실제 같은 A/stream/current clock에만 설치한다. 저장된 explicit identity mapframe과
동일 실제 odom epoch가 증명되면 그 좌표 정의를 유지한다. nonidentity map은 `_restore_pending`
으로 격리하여 실제 새 BOUNDED registration 전 raw odom HIT가 old map에 들어가지 않는다.
reference가 없어 정합할 수도 없고 identity도 증명되지 않으면 `MapRestoreNotReady`다.

`retain_complete`는 actor가 보존한 원 최신 COMPLETE bootstrap ref와 actual SourceComplete를
대조한다. old tile/catalog/proof와 full refs는 immutable retained 자료로 보존한다. 새 CP의
미재관측 active 셀은 원 source/Pi age와 STATIC|EXPIRED를 유지하며 live FREE가 아니다.
historical 임의 선택을 현재 tile head로 채택하거나 DB/HWM/옛 unused range를 rollback하지
않는다. Scene restore admission과 현재 relocation/use권은 이 모듈 밖의 기존 actor 책임이다.
