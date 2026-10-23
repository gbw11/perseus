// Xenova CI: GitLab push/MR → 서버·프론트 테스트 → 컨테이너 빌드 → GitLab 상태 + Mattermost 알림
//
// 필요한 Jenkins 플러그인: GitLab, Mattermost Notification, JUnit, Credentials Binding
// 에이전트 요구 사항:
// - Java 21 (Maven은 mvnw가 고정된 버전을 자동으로 내려받는다)
// - Node.js 22 + npm
// - Docker Engine + Docker Compose v2
// - python3 + pip (dom0-manager·vm-agent 테스트)

pipeline {
    agent any

    options {
        gitLabConnection('gitlab')    // Jenkins 시스템 설정의 GitLab connection 이름과 같아야 한다
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30'))
        disableConcurrentBuilds(abortPrevious: true)
    }

    environment {
        DEPLOY_HOST = 'k15e110.p.ssafy.io'
        DEPLOY_USER = 'ubuntu'
        DEPLOY_DIR = '/opt/xenova'
        DEPLOY_SSH_CREDENTIALS_ID = 'xenova-ec2-ssh-key'
        DEPLOY_KNOWN_HOSTS_CREDENTIALS_ID = 'xenova-ec2-known-hosts'
        DEPLOY_ENV_CREDENTIALS_ID = 'xenova-production-env'
    }

    stages {
        stage('Backend Test') {
            steps {
                updateGitlabCommitStatus name: 'jenkins', state: 'running'
                dir('backend') {
                    sh './mvnw -B -ntp test'
                }
            }
        }
        stage('Frontend Quality') {
            steps {
                dir('frontend') {
                    sh 'npm ci'
                    sh 'npm run lint'
                    sh 'npm run typecheck'
                    sh 'mkdir -p target && npm test -- --reporter=junit --outputFile=target/vitest.xml'
                    withEnv([
                        'VITE_BASE_PATH=/screens/',
                        'VITE_SCREENS_ORIGIN=/screens',
                        'VITE_TELEMETRY=live'
                    ]) {
                        sh 'npm run build'
                    }
                }
            }
        }
        stage('Compose Validate') {
            environment {
                // config 검증용 임시 값이다. 운영 Credentials는 master 배포 단계에서만 사용한다.
                MYSQL_PASSWORD = 'ci-compose-validation-only'
                MYSQL_ROOT_PASSWORD = 'ci-compose-validation-root-only'
                REDIS_PASSWORD = 'ci-compose-validation-only'
                JWT_SECRET = 'ci-compose-validation-secret-at-least-32-bytes'
            }
            steps {
                sh 'docker compose -f compose.yaml config --quiet'
            }
        }
        stage('Docker Image Build') {
            steps {
                sh 'docker build --target runtime -t xenova-backend:ci-${BUILD_NUMBER} backend'
                sh 'docker build --target admin-console -t xenova-admin-console:ci-${BUILD_NUMBER} frontend'
                sh 'docker build --target vm-screens -t xenova-vm-screens:ci-${BUILD_NUMBER} frontend'
            }
        }
        stage('dom0-manager Test') {
            // Python 제어 서비스(platform/services/dom0-manager). 에이전트에 python3 가 없으면 이 단계만 실패로 표시하고
            // 빌드는 UNSTABLE 로 둔다. 에이전트에 python3 + pip 가 준비되면 catchError 를 걷어낸다.
            steps {
                dir('platform/services/dom0-manager') {
                    catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                        sh 'python3 -m pip install -q --user -e .[test] && python3 -m pytest -q --junitxml=target/pytest.xml'
                    }
                }
            }
        }
        stage('vm-agent Test') {
            // MQTT 클라이언트(platform/services/vm-agent/mqtt-client)와 VM 에이전트(platform/services/vm-agent/agent).
            // 브로커 통합 테스트는 XENOVA_MQTT_TEST_BROKER 가 없으면 건너뛴다. rootfs 설치 스크립트의 심볼릭 링크 테스트는
            // Windows 에서 건너뛰므로 Linux 인 여기서 실제로 검증된다. python3 가 없으면 dom0-manager 단계와 같이 UNSTABLE 로 둔다.
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    dir('platform/services/vm-agent/mqtt-client') {
                        sh 'python3 -m pip install -q --user "paho-mqtt>=1.6,<3" "pytest>=7" && python3 -m pytest -q --junitxml=target/pytest.xml'
                    }
                    dir('platform/services/vm-agent/agent') {
                        sh 'python3 -m pytest -q --junitxml=target/pytest.xml'
                    }
                }
            }
        }
        stage('Deploy to EC2') {
            when {
                beforeAgent true
                expression { isMasterPush() }
            }
            steps {
                withCredentials([
                    sshUserPrivateKey(
                        credentialsId: env.DEPLOY_SSH_CREDENTIALS_ID,
                        keyFileVariable: 'DEPLOY_SSH_KEY',
                        usernameVariable: 'DEPLOY_SSH_USERNAME'
                    ),
                    file(credentialsId: env.DEPLOY_KNOWN_HOSTS_CREDENTIALS_ID, variable: 'DEPLOY_KNOWN_HOSTS'),
                    file(credentialsId: env.DEPLOY_ENV_CREDENTIALS_ID, variable: 'DEPLOY_ENV_FILE')
                ]) {
                    sh 'bash ./deploy/scripts/deploy-ec2.sh'
                }
            }
        }
        stage('Production Health Check') {
            when {
                beforeAgent true
                expression { isMasterPush() }
            }
            steps {
                sh 'bash ./deploy/scripts/health-check.sh "https://${DEPLOY_HOST}"'
            }
        }
    }

    post {
        always {
            junit testResults: 'backend/target/surefire-reports/*.xml,frontend/target/vitest.xml,platform/services/dom0-manager/target/pytest.xml,platform/services/vm-agent/mqtt-client/target/pytest.xml,platform/services/vm-agent/agent/target/pytest.xml', allowEmptyResults: true
            sh '''
                if command -v docker >/dev/null 2>&1; then
                    docker image rm \
                      xenova-backend:ci-${BUILD_NUMBER} \
                      xenova-admin-console:ci-${BUILD_NUMBER} \
                      xenova-vm-screens:ci-${BUILD_NUMBER} >/dev/null 2>&1 || true
                fi
            '''
        }
        success {
            updateGitlabCommitStatus name: 'jenkins', state: 'success'
            notifyMattermost('good', '✅ 빌드 성공')
        }
        failure {
            updateGitlabCommitStatus name: 'jenkins', state: 'failed'
            notifyMattermost('danger', '❌ 빌드 실패')
        }
        aborted {
            updateGitlabCommitStatus name: 'jenkins', state: 'canceled'
            notifyMattermost('warning', '⚠️ 빌드 중단')
        }
    }
}

// Multibranch와 GitLab push 파이프라인을 모두 지원한다. MR의 대상 브랜치가 master인 것만으로는 배포하지 않는다.
def isMasterPush() {
    def branch = env.BRANCH_NAME ?: env.gitlabSourceBranch ?: env.GIT_BRANCH ?: ''
    def normalizedBranch = branch.replaceFirst(/^origin\//, '')
    def action = env.gitlabActionType ?: 'PUSH'
    return normalizedBranch == 'master' &&
        action.equalsIgnoreCase('PUSH') &&
        currentBuild.currentResult == 'SUCCESS'
}

// Mattermost 웹훅 URL과 채널은 Jenkins 시스템 설정(Global Mattermost Notifier Settings)에 둔다.
def notifyMattermost(String color, String title) {
    def branch = env.gitlabSourceBranch ?: env.BRANCH_NAME ?: env.GIT_BRANCH ?: '-'
    def author = env.gitlabUserName ?: '-'
    def duration = currentBuild.durationString.replace(' and counting', '')

    mattermostSend color: color, message: """#### ${title} · ${env.JOB_NAME} #${env.BUILD_NUMBER}
브랜치: `${branch}` · 작성자: ${author} · 소요: ${duration}
[빌드 로그 보기](${env.BUILD_URL}console)"""
}
