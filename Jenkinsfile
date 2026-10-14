// Perseus CI: GitLab push/MR → 백엔드 테스트 → GitLab 커밋 상태 + Mattermost 알림
//
// 필요한 Jenkins 플러그인: GitLab, Mattermost Notification, JUnit
// 에이전트 요구 사항: Java 21 (Maven은 mvnw가 고정된 버전을 자동으로 내려받는다)

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
    }

    post {
        always {
            junit testResults: 'backend/target/surefire-reports/*.xml', allowEmptyResults: true
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
