# 설계 검토 기록

## 결정한 쟁점

| 쟁점 | 결정 | 피한 오류 |
|---|---|---|
| 기존 backend unit 제한 | v2에서 필요한 단위·정밀도 정의, v1은 보존 | us/count를bytes로 위장 |
| 기존 vchan 요청서 | 새 Publisher는 이미 만든 netfront 직접 MQTT | 오래된 임시 요청서를 새로운 선행조건으로 강제 |
| 진단용 JSON 전체 broadcast | 5 streams·family·명시적 상세 snapshot | 큰 스택 분석을 매20ms 실행 |
| 동일 앱 A/B | 실제 VM/boot/publisher/task로 구분 | app_id/topic 하나로 latest 덮어쓰기 |
| 신원 | 고유성 확인, host binding 없으면null | 고정 PRNG UUID/가짜 Xen execution ID |
| 시간 | UTC null + 품질, uptime/cycle epoch 분리 | 1970 uptime를현재관측시각으로저장 |
| message ID | 세션·stream·seq의복합키 | A/B·재부팅 후 정수ID 충돌 |
| 부호/정밀도 | u64/i64 decimal string | JSON/JS double의 정수정밀도손실 |
| histogram | task_epoch 누적·버킷 상한·포화 | p99정확값가정·p99평균 |
| 오류 | 실제 오류와메시지규약위반구분 | 실패를0으로만드는정상화 |
| snapshot | source truncation과part누락을분리 | 전송완료를원본완전관측으로오인 |
| 스레드 | snapshot-local행ID와high-water | pointer노출·이름동일=같은thread단정 |
| 연결·ACK | 독립수신증거별도 | PUBACK로ACL/소비자/차량적용보장 |
| 생존 | publisher heartbeat와계산진행분리 | 보고thread만살았는데app정상 |
| consumer | 최소해석계약인계, 제품수신구현은담당자 | 전체백엔드를대신구현하거나대기 |

## 중요한 미보장

- README/RESULT의 API·의미를 근거로 설계했다. Diagnostics C 원본 전체 diff나 실보드 산출물은 이 작업에서 검증하지 않았다. 필드 매핑은 Codex가 실제 작업방과 대조해야 한다.
- 실제 timer frequency, thread 상태, heap 숫자는 Golden으로 증명하지 않는다.
- 이 패키지의 Python reference consumer는 작은 고정 시나리오용이다. 운영용 bounded broker·DB·device registry·clock-sync 구현이 아니다.
- UTC를모르는상태에서수신시각만으로end-to-end 지연을정확히계산할수없다. receipt lease를source freshness보장으로명명하지않는다.
- `host_provisioned`는 self-report 형식이다. 소비자의 실제 host 문맥 대조를 대체하지 않는다.
- 이벤트는 RAM의bounded best effort. crash 직전·네트워크단절·전원손실을넘는영구보존은별도다.
- 낮은우선순위Publisher도긴critical section·interrupt-disabled thread scan의지연을없애주지않는다.
- 실제주행알고리즘과safe handoff조건은이계약이새로만들지않는다. 신선한Telemetry라는이유만으로제어권을주지않는다.

## 후속 구현에서 반드시 다시 확인할 것

1. 실제 Kconfig/heap/stack/cycle counter와컴파일결과.
2. guest boot/Publisher ID의재부팅·다중VM고유성. test PRNG 배제.
3. credentials의개별주입·보안, 기존netfront own-domain정보와동일VM확인.
4. 패킷크기·parser token·socketblocking·정적scratch concurrency.
5. Source event sequence와lost-history개수의정확한추출.
6. currentcontext없는소비자의보수적표시및복구금지.
7. 실제broker/OS/네트워크환경의동작과성능을합성fixture결과와분리.

새로운관측요구를줄인것이아니라, **생산가능한사실/확장필요/미지원/다른계층의판정**을구별한설계다.
