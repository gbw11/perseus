# 수동주행 앱 테스트를 CI에 추가 — 정수환(deploy) 요청

> 작성: 공병우, 2026-10-20 · 관련: S15P31E110-155·237 (브랜치 `feat/app/S15P31E110-155-manual-drive-app`)
>
> **2026-11-04 반영 (S15P31E110-365)**: 정수환 부재 주간에 팀 협의로 공병우가 처리. 루트 `Jenkinsfile` 대신 파트별 구조에 맞춰
> `Jenkinsfile.vehicle`(Job `xenova-vehicle-check`)에 수동 앱 pytest 와 C 호스트 시험(cmake + ctest)을 넣었다. Zephyr 빌드는 여전히 제외.

## 0. 요약

| 대상 | 해야 할 것 | 막혀 있나 |
| --- | --- | --- |
| 정수환 (deploy, `Jenkinsfile`) | `platform/apps/manual` 테스트 단계를 Jenkins에 추가 | 🟢 여유 있음 — 로컬에서는 돌고 있고, CI에서 안 돌 뿐 |

## 1. 무엇이 문제인가

수동주행 앱의 Python 참조 구현(`platform/apps/manual/`)에 테스트가 생겼는데 `Jenkinsfile`에 단계가 없어 MR마다 자동으로 돌지 않습니다.
Zephyr 판과 C MQTT 라이브러리(S15P31E110-236)의 호스트 단위 시험도 곧 생기므로, 앱 시험 단계를 하나 두고 거기에 이어 붙이는 형태를 제안합니다.

```bash
cd platform/apps/manual && python3 -m pytest -q     # 브로커 테스트는 XENOVA_MQTT_TEST_BROKER 없으면 건너뜀
```

## 2. 고쳐야 할 파일

| 파일 | 무엇을 |
| --- | --- |
| `Jenkinsfile` | `vm-agent Test` 다음에 아래 단계 추가, `post.always` 의 `junit` 목록에 `platform/apps/manual/target/pytest.xml` 추가 |

```groovy
stage('apps Test') {
    // RC카 VM 앱(platform/apps/*). paho·pytest 는 vm-agent 단계에서 설치한다. 브로커 테스트는 위와 같이 건너뛴다
    steps {
        catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
            dir('platform/apps/manual') {
                sh 'python3 -m pytest -q --junitxml=target/pytest.xml'
            }
        }
    }
}
```

- 의존성: `paho-mqtt>=1.6,<3`, `pytest>=7` (vm-agent 단계가 이미 설치). `xenova_mqtt`는 `pyproject.toml`의 `pythonpath`로 저장소 안 경로를 씁니다.
- 실측: Debian 13(python 3.13, paho 2.1)·Ubuntu 24.04(python 3.12, paho 1.6) 컨테이너에서 전부 통과 (2026-10-20).

## 3. 내가 이미 해 둔 것

| 내용 | 위치 | 병합됨? |
| --- | --- | --- |
| 앱·테스트 | `platform/apps/manual/` | ❌ 위 브랜치 MR 미병합 — 병합 뒤에 단계를 넣어야 CI가 빈 폴더에서 실패하지 않습니다 |
| `Jenkinsfile` 수정 | 하지 않음 (deploy 영역) | — |

## 4. 처리되면

C 라이브러리 호스트 시험(cmake + ctest, gcc)과 Zephyr `native_sim`/`xenvm` 빌드 확인이 다음 요청입니다. Zephyr 빌드는 공식 CI 이미지(`ghcr.io/zephyrproject-rtos/ci`)로 돌리는 안을 따로 정리해 드리겠습니다.
