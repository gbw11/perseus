# 조사 결과를 동결하는 저장 작업의 수명

`ResidentMapperProcessor`는 원 PENDING grant의 nonce로 동결 작업을 준비한다.
작업의 J 시계 만료는 다음 중 가장 이른 값이다. Pi 기한은 원 clock trust의
`upper_offset_us`를 빼서 보수적으로 환산한다.

- 준비 시각 + 기존 저장 작업 상한
- 원 survey deadline
- 원 grant의 valid-until
- 현재 Scene이 수락한 원 clock lease의 expiry

이 값은 원 `StorageJob`의 ID·issued·expiry·nonce·context와 함께 고정한다.
S나 D가 받아들였다는 증거가 없는 게시 전 작업도 새 시각이나 새 lease로 재포장하지 않는다.

## 게시 전 한 칸

현재 M 프로세스는 미게시 작업 한 개만 보유한다. snapshot 준비가 실패하면 원 snapshot을
반납하고, 같은 grant·지도 revision·hazard·context가 유지될 때 같은 작업으로만 다시 준비한다.
입력이 바뀌거나 원 기한이 지나면 기존 작업으로 진행하지 않는다. 정상 clock 갱신으로
같은 grant의 작업 ID나 만료를 다시 만들지 않는다.

원 descriptor가 만들어진 뒤 queue 준비가 실패한 경우에는 기존의 명시 terminal 정책을
따른다. 자동으로 새 작업을 발급하지 않으며, 정상적인 새 grant가 필요하다.
이 한 칸은 M 메모리의 준비 상태이며 재기동 복구 기록이나 D의 처리 결과를 대신하지 않는다.

## 소유권 인계

`MapperMapping.queue_checkpoint()`가 원 request를 수락하면 게시 전 칸을 비우고 기존
pending/terminal 경로로 넘긴다. 이는 로컬 요청 수락이다. 실제 D 전송·COMMIT·사본 회수의
완료를 의미하지 않는다. 이후 원 receipt와 원 request의 동일성·기한 검사는 계속 적용한다.

원 SQL 완료 뒤에도 FROZEN과 사용 가능 상태를 구분한다. 실제 ADMITTED grant와 현재
source·시계·기하 검증을 충족해야 retained 근거를 사용할 수 있다. 작업 기한을 줄였다는
사실로 셀 나이·관측 시각·grant 기한이나 주행 권한이 바뀌지는 않는다.
