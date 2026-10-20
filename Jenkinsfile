// Xenova CI: GitLab push/MR → 백엔드 테스트 → GitLab 커밋 상태 + Mattermost 알림
//
// 필요한 Jenkins 플러그인: GitLab, Mattermost Notification, JUnit
// 에이전트 요구 사항: Java 21 (Maven은 mvnw가 고정된 버전을 자동으로 내려받는다), python3 + pip (dom0-manager·vm-agent 테스트)

pipeline {
    agent any

    options {
        gitLabConnection('gitlab')    // Jenkins 시스템 설정의 GitLab connection 이름과 같아야 한다
        timeout(time: 20, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30'))
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
    }

    post {
        always {
            junit testResults: 'backend/target/surefire-reports/*.xml,platform/services/dom0-manager/target/pytest.xml,platform/services/vm-agent/mqtt-client/target/pytest.xml,platform/services/vm-agent/agent/target/pytest.xml', allowEmptyResults: true
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

// Mattermost 웹훅 URL과 채널은 Jenkins 시스템 설정(Global Mattermost Notifier Settings)에 둔다.
def notifyMattermost(String color, String title) {
    def branch = env.gitlabSourceBranch ?: env.BRANCH_NAME ?: env.GIT_BRANCH ?: '-'
    def author = env.gitlabUserName ?: '-'
    def duration = currentBuild.durationString.replace(' and counting', '')

    mattermostSend color: color, message: """#### ${title} · ${env.JOB_NAME} #${env.BUILD_NUMBER}
브랜치: `${branch}` · 작성자: ${author} · 소요: ${duration}
[빌드 로그 보기](${env.BUILD_URL}console)"""
}
