# vehicle — 차량 탑재 소프트웨어

RC카에 탑재하는 Jetson과 ESP32의 코드를 장치별로 관리한다.

- [Jetson](Jetson/README.md): Jetson Nano B01과 OAK-D-Lite의 관측 수집, 인지, 지도, 주차 후보 생성 및 RPi 통신.
- `ESP32/`: 모터·서보·encoder 등 차량 장치 펌웨어.

RPi4의 Xen dom0·Zephyr domU 애플리케이션과 Gateway는 `platform/`에 둔다. 주행 목표 선택·경로 계획·추종은 Zephyr 자율주행 앱이 담당하며, Jetson은 모터 명령을 직접 생성하지 않는다.
