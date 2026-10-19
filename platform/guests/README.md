# Linux domU cfg templates (Jira 28)

Ubuntu/Debian의 `xen.cfg.in`을 기존 `xenova_vm.Platform.config()`에서 사용한다.
실행 경로는 `start → config → common/xen_config.py → OS별 xen.cfg.in → xl create`다.

- `common/xen_config.py`: 선택·준비된 인스턴스 메타데이터를 cfg로 변환.
- `ubuntu/xen.cfg.in`, `debian/xen.cfg.in`: OS별 Xen 설정 양식.
- `examples/*-cfg-spec.json`: 기존 인스턴스 구조를 따르는 오프라인 예제.

예제 JSON은 Spring/FastAPI 요청 본문이 아니다. UUID는 예제이며 기존 VM에 덮어쓰지 않는다.
템플릿 버전 선택, 이미지 준비, 자원 승인, Xen 실행은 기존 호출자의 책임이다.
Zephyr는 기존 별도 렌더러를 계속 사용한다.

```sh
python3 -B platform/guests/common/xen_config.py platform/guests/examples/ubuntu-cfg-spec.json
python3 -B platform/guests/common/xen_config.py platform/guests/examples/debian-cfg-spec.json
```

변수 명세, 패키지 설치, 검증 결과와 남은 작업은
[인계 문서](../../docs/platform/domu-xl-config-v1.md)를 참고한다.
