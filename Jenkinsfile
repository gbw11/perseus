// Xenova CI: GitLab push/MR → 백엔드 테스트 → GitLab 커밋 상태 + Mattermost 알림
//
// 필요한 Jenkins 플러그인: GitLab, Mattermost Notification, JUnit
// 에이전트 요구 사항: Java 21 (Maven은 mvnw가 고정된 버전을 자동으로 내려받는다), python3 + pip (dom0-manager 테스트)

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
            // Python 제어 서비스(services/dom0-manager). 에이전트에 python3 가 없으면 이 단계만 실패로 표시하고
            // 빌드는 UNSTABLE 로 둔다. 에이전트에 python3 + pip 가 준비되면 catchError 를 걷어낸다.
            steps {
                dir('services/dom0-manager') {
                    catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                        sh 'python3 -m pip install -q --user -e .[test] && python3 -m pytest -q --junitxml=target/pytest.xml'
                    }
                }
            }
        }
    }

    post {
        always {
            junit testResults: 'backend/target/surefire-reports/*.xml,services/dom0-manager/target/pytest.xml', allowEmptyResults: true
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
