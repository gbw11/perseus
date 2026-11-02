# Xenova XenStore G1 시험 앱

기존 Starter와 별도로 xen-store 모듈을 빌드한다. 기존 xenvm의 1 vCPU·16MiB·GICv2를 사용한다.

앱은 Store 연결, VIF 발견, backend watch를 수행하고 자기 도메인의 전용 노드에 한해
read/write/directory, transaction commit/abort, watch 후 재조회를 실행한다.
실제 생성 부모인 /local/domain/<self>/data의 쓰기 권한을 검사한다. 도메인 루트의 RO 권한은
확장하지 않는다. /data 부재는 test-parent-missing, 거절은 test-parent-denied로 기록하고 중단한다.
시험 노드는 transaction으로 존재 여부를 검사한 뒤 생성하며 기존 노드를 인수하지 않는다.
시험 중 watch-ready에 출력된 control 경로를 dom0에서 변경하면 재조회 값을 기록한다.
변경이 없으면 60초에 timeout을 기록하고 자기 watch·노드·매핑을 정리한다.

네트워크 packet ring이나 frontend state는 게시하지 않는다.
VIF가 없거나 backend가 준비되지 않은 경우는 Store 실패와 구별해 출력한다.
전체 실행 로그의 단계별 rc를 판정하며 마지막 결과만으로 앞선 오류를 무시하지 않는다.
빌드·인계물 준비와 기동·관측·회수 도구 사용은
[Xenova 빌드 가이드의 G1 절](../../../../docs/platform/build-guide.md#10-xenstore-g1-시험-인계물)을 따른다.
