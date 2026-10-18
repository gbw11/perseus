# Perseus Zephyr Starter App 1.0.0

Zephyr 4.4.0 / xenvm / SDK 1.0.0의 기존 고정 소스와 도구체인을 사용한다.
RAM 0x40000000부터 16MiB, 1 vCPU, GICv2 프로필은 변경하지 않는다.
PV 콘솔 안내를 한 번 출력하고 `k_sleep(K_FOREVER)`로 대기한다.
안내는 콘솔 접속 전에 지나갈 수 있다. 반복 출력·셸·SSH·Agent는 없다.
대기는 게스트 협조형 종료 지원이 아니다. 실보드 DTB/IRQ 검증은 별도다.

## 앱 수정과 빌드

`src/main.c`를 수정하고 기존 도구로 새 경로에 빌드한다. 아래 절대 경로를
자신의 검증된 Zephyr 소스·SDK·Python 도구·새 출력 경로로 바꾼다.

```bash
export ZEPHYR_BASE=/absolute/pinned-source/zephyr
export ZEPHYR_TOOLCHAIN_VARIANT=cross-compile
export CROSS_COMPILE=/absolute/sdk/aarch64-zephyr-elf/bin/aarch64-zephyr-elf-
export PYTHONPATH=/absolute/python-tools
export PATH=/absolute/python-tools/bin:$PATH
cmake -S /absolute/Perseus/platform/guests/zephyr/starter-app \
  -B /absolute/new-app-build -GNinja -DBOARD=xenvm \
  -DPython3_EXECUTABLE=/usr/bin/python3 -DWEST=/absolute/python-tools/bin/west
cmake --build /absolute/new-app-build -j 4
```

## 불변 번들과 명시적 교체

```bash
python3 -B scripts/package-perseus-firmware.py \
  --build-dir /absolute/new-app-build/zephyr --license "$ZEPHYR_BASE/LICENSE" \
  --version my-app-1 --app my-app --source-revision YOUR_SOURCE_REVISION \
  --build-id YOUR_BUILD_ID --app-source /absolute/your-app \
  --output /absolute/new-bundle --execute
sha256sum /absolute/new-bundle/firmware.json
```

새 번들만 dom0의 새 경로에 복사한 뒤 다음을 실행한다. SHA256은 위 실제
출력으로 바꾸고 각 명령이 실패하면 중단한다. UUID·자동 시작·보드 종료 정책은
교체가 바꾸지 않는다. 기존 활성 펌웨어와 원본 템플릿은 보존한다.

```bash
perseus-vm stop zephyr-dev --force
perseus-vm firmware-replace zephyr-dev --bundle /absolute/new-bundle --sha256 ACTUAL_HASH
perseus-vm firmware-replace zephyr-dev --bundle /absolute/new-bundle --sha256 ACTUAL_HASH --execute
perseus-vm start zephyr-dev
perseus-vm console zephyr-dev
```

팀원 앱의 강제 종료 안전성은 별도로 판단한다. 자동 시작을 켜는 것은 보드 종료
강제 제거 승인이 아니다. 기본 Starter의 `force-allowed`는 새 배포 인스턴스에만
설정하며 기존 인스턴스는 명시적인 정책 변경 전까지 보존한다.
