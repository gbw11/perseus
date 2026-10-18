# dom0는 키 준비 뒤 일반 서비스로 시작한다. 게스트 템플릿에는 적용하지 않는다.
PACKAGECONFIG:remove = "systemd-sshd-socket-mode"
PACKAGECONFIG:append = " systemd-sshd-service-mode"
