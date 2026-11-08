# 차선의 공통 station 전송 표현

`lane_model_from_road()`는 원 Road 가설의 기하·오차·ID를 공용 `LaneModel`로 옮긴다.
source Road나 catalog를 다시 추정하지 않으며 시각·권한·기한도 발급하지 않는다.

공용 Python/C 계약은 `outer = 2*center - divider`로 차선 외곽을 복원한다. 외곽
양끝의 가능한 위치 오차보다 segment가 짧으면 방향을 한정할 수 없어 거절한다.
촘촘한 점의 수를 늘리는 것만으로 이 불확실성이 줄어들지는 않는다.

## 표현 선택

원 nominal 기하의 loop·순서·양쪽 위치를 먼저 검사한다. 모든 station을 유지할 때도
C 외곽의 재구성 반올림과 방향 차이를 검사하고, 선언한 오차가 부족하면 올린다.
원 오차가 이를 포함하고 모델이 유효하면 기존 표현을 반환한다. 복원 외곽 방향의 불확실성 때문에 거절된
경우에만, 동일 index를 사용하는 부분열을 최대30개 시도한다. 입력은 최대32점이며
처음과 마지막 station, 그 사이의 전체 구간을 보존한다.

각 후보는 원 common `s`에 따른 같은 보간 비율에서 원 점과 새 chord의 차이를 구한다.
원 piecewise-linear 표현과 chord의 차이는 각 구간에서 affine이므로 정점의 최대
편차가 구간 전체의 상한이다. 이 편차와 float32 양자화 오차를 원 위치 오차에 더해
상향 반올림한다. 원 outer도 복원 외곽의 오차 안에 들어야 한다. 방향 오차에는
생략한 subsegment와 chord의 각도 및 양자화 방향 차이를 더한다.

merged interval의 crossing은 원 interval들의 AND이며, 생략한 inferred 점은 해당
interval 양끝 상태에 OR로 전달한다. 일부 관측이나 교차 금지를 생략해 권한을 늘리지 않는다.
원 stable ID·revision·source Road는 유지한다.

## 거절과 검증 범위

각 후보에 공용 validator와 C의 float32 외곽·오차 연산 순서를 적용한다. C의 원 좌표
범위인 `abs(coordinate)<1e6`도 변환한 좌표와 복원 외곽에 적용한다. 보증할 수
없는 짧은 구간·급한 굴곡·잘못된 topology는 계속 거절한다. 일부 끝 구간을 버리거나
오차를 낮춰 통과시키지 않는다. C `hypotf`의 모든 입력에 대한 수치적 동등성을 주장하지
않으며, 실제 C decoder로 정상·곡선·큰 좌표와 경계 입력을 별도로 검사한다.

명목 source topology 검사는 같은 저장소의 고정된 공용 codec helper를 재사용한다.
공유 패키지를 갱신할 때는 해당 Python/C 쌍의 계약 시험도 함께 실행해야 한다.
이 변환은 별도 RoadBasis 기준선·지도 보정·실물 카메라 오차·주행 허가를 승인하지 않는다.
