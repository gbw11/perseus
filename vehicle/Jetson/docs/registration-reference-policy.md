# 영상 정합 reference의 끝점 정책

`FrozenReference.feature_policy`는 유한 segment를 생산한 규칙이다. 현재 pipeline은
`PHYSICAL_STRIP_ENDPOINTS_V1`을 생산한다. 일반 `register()`나 LaneCatalog의
독립 feature 계약에 공통으로 적용하는 승인 표시는 아니다.

## 현재 영상에서 사용하는 근거

`PerceptionPipeline`의 current feature와 다음 frozen reference는 같은 조건을 쓴다.
실제 관측한 열린 strip이며, 양끝이 `physical_junction` 또는 `observed_paint_end`일 때만
유한 segment로 사용한다. `FOV_clip`, `occluded`, `predicted`, 닫힌 strip은 제외한다.

화면 경계에서 잘린 끝은 도색의 실제 끝이 아니다. 두 영상에 같은 화면 경계가
나타나더라도 이를 같은 물리 끝으로 정합하면 전진 이동을 부당하게 배제할 수 있다.
반대로 물리 끝의 관측이 유지되면 기존 유한 feature 정합을 계속 사용한다.

이번 영상에 해당 feature가 없다는 사실만으로 기존 유효 alignment를 폐기하지 않는다.
원 수명·오차·문맥 검사는 그대로 적용하며, 새 근거가 없으면 정합 수명을 갱신하지 않는다.
잔차·오차 상한·작업량·시계·원 만료 시각을 이 정책으로 바꾸지 않는다.

## 저장과 이전 기록

mapper state는 reference와 함께 정책을 저장한다. 복원 시 네 필드로 된 이전 reference는
`UNSPECIFIED`로 읽는다. 다섯 필드의 새 형식은 명시한 정책을 보존하며, 알 수 없는 정책이나
추가 reference 필드는 거절한다. 좌표나 component 이름으로 과거 끝점 의미를 추정하지 않는다.

원 지도와 odometry 사이의 변환이 필요한 nonidentity 기록은, 정책이 없는 reference로
재정합을 시작하지 않는다. 완전한 원 기록은 보존하고 새 pipeline 설치 전에 NOT_READY를
반환한다. 원 좌표계가 identity인 이전 기록은 UNSPECIFIED를 유지해 읽되, 그 reference가
남아 있는 metric 처리 경로를 격리한다. reference를 지우고 같은 map epoch를 다시 seed하지 않는다.

## 남는 범위

이 정책은 잘린 끝을 유한 물리 끝으로 쓰지 않는 보수적인 제한이다. 화면 밖으로 이어지는
긴 도로의 모든 정보를 사용하는 방법까지 구현한 것은 아니다. 그런 구간은 실제 공통 관측
범위와 선의 normal 제약을 별도로 다뤄야 한다. 유한 끝이 부족하다는 이유로 UNKNOWN을 FREE로
바꾸거나 위치 오차를 임의로 줄여서는 안 된다. 실물 보정·odometry·주행 수락은 별도 근거가 필요하다.
