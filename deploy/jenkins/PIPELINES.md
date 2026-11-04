# Xenova Jenkins pipelines

`vehicle`은 Jetson 없이 도는 호스트 시험(시뮬레이터·인지·수동 앱 pytest, Zephyr 앱 로직 C 시험)만 `xenova-vehicle-check`에서 검사한다.
Jetson·실보드가 필요한 시험과 Zephyr 이미지 빌드는 이 메인 서버 CI/CD에서 제외한다.

## Jobs

| Job | GitLab event | Allowed target branch | Script Path | Deployment |
| --- | --- | --- | --- | --- |
| `xenova-backend-check` | Merge Request | `backend` | `Jenkinsfile.backend` | No |
| `xenova-frontend-check` | Merge Request | `frontend` | `Jenkinsfile.frontend` | No |
| `xenova-platform-check` | Merge Request | `platform` | `Jenkinsfile.platform` | No |
| `xenova-vehicle-check` | Merge Request | `vehicle` | `Jenkinsfile.vehicle` | No |
| `xenova-develop-check` | Merge Request | `develop` | `Jenkinsfile.check` | No |
| `xenova-master-check` | Merge Request | `master` | `Jenkinsfile.check` | No |
| `xenova-pipeline` | Push | `master` | `Jenkinsfile` | EC2 production |

## Trigger settings

All check Jobs:

- Enable `Opened Merge Request Events`.
- Disable `Push Events`, `Accepted Merge Request Events`, and `Closed Merge Request Events`.
- Select `Filter branches by name` and enter only the target branch shown above.
- Use a distinct GitLab webhook endpoint: `/jenkins/project/<job-name>`.
- Enable only `Merge request events` on the matching GitLab webhook.

Production deployment Job:

- Enable `Push Events` only.
- Disable every Merge Request event.
- Filter the allowed branch to `master`.
- Use `/jenkins/project/xenova-pipeline` and enable only `Push events` on its GitLab webhook.

## Responsibilities

- Backend: Maven tests and backend runtime image build.
- Frontend: install, lint, typecheck, tests, both production builds, and both frontend image builds.
- Platform: dom0-manager and vm-agent Python tests.
- Vehicle: simulator, perception, and manual app Python tests; xenova-mqtt-c, manual, and autonomous C host tests (CMake, ASan/UBSan).
- Develop/Master check: all checks above (including vehicle), Compose validation, and all application image builds.
- Master deploy: all checks, production image build, EC2 deployment, Nginx restart, and public health checks.

The `master` Merge Request check never deploys. Production deployment begins only after the merge creates a Push event on `master`.
