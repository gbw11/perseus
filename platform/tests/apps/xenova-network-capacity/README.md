# 네트워크 연결 풀 용량 재현

고정 Zephyr `684c9e8f32e4373a21098559f748f06915f950c9`에서 실제 DHCP 초기화와
내부 `net_conn_register`·`net_conn_unregister`·`net_conn_foreach`를 시험한다.
SDK 내부 `subsys/net/ip/connection.h`를 쓰는 시험 전용 앱이며 제품 API가 아니다.
기존 SDK의 `tests/net/udp` 설정과 dummy 인터페이스 구성을 재사용했다.

기존 고정 빌드 도구가 있는 환경에서 저장소 루트 기준으로 실행한다. 새 패키지 설치나
네트워크 접근은 필요하지 않다. 아래 경로는 이번 로컬 검증에 사용한 SDK 캐시다.

```sh
export ZEPHYR_BASE=/home/ssafy/perseus-zephyr-template-20261016-153845/source/zephyr
toolroot=/home/ssafy/perseus-zephyr-template-20261016-153845/host-tools/python
export PYTHONPATH="$toolroot${PYTHONPATH:+:$PYTHONPATH}"
for pool in 4 5 7; do
  ZEPHYR_TOOLCHAIN_VARIANT=host "$toolroot/cmake/data/bin/cmake" \
    -S platform/tests/apps/xenova-network-capacity -B "out/network-capacity-$pool" -GNinja \
    -DCMAKE_MAKE_PROGRAM="$toolroot/bin/ninja" -DBOARD=native_sim/native/64 \
    -DEXTRA_CONF_FILE="pool$pool.conf" || exit 1
  "$toolroot/cmake/data/bin/cmake" --build "out/network-capacity-$pool" -j4 || exit 1
  "out/network-capacity-$pool/zephyr/zephyr.exe" || exit 1
done
```

DHCP가 UDP68 handler 하나를 예약한 상태에서 TCP 등록 용량은 각각 3·4·6이다.
각 구성은 소진 시 `-ENOENT`, 슬롯 해제 후 정상 재등록, 정리 후 DHCP 하나만 남는
상태를 20회 확인한다. `NET_MAX_CONTEXTS=6`은 세 구성 모두 같다. 4·5는 부족한
구성의 재현용이며 제품에 권하는 설정이 아니다.

실제 SDK의 등록·해제 경로와 DHCP 초기화를 사용한다. dummy 인터페이스로 외부
네트워크를 차단하며, TCP handshake·FIN/TIME_WAIT·패킷 callback 호출·MQTT 수신·
Xen netfront·ARM 펌웨어·실보드 수락은 이 시험에 포함하지 않는다.
