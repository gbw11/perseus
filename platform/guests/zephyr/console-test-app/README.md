# MO-04 Zephyr 콘솔 시험 앱

고정 Zephyr 4.4.0의 xenvm/Xen PV 콘솔에서 `uart_poll_in()`으로 입력을 읽고
`printk()`로 응답하는 별도 시험 펌웨어다. 기존 Starter와 source lock은 변경하지 않는다.
주행·미러링·모드 전환·네트워크 기능이나 범용 셸은 없다.

| 입력 | 응답 |
| --- | --- |
| `ping` | `pong` |
| `ping <고유 문자열>` | `pong <고유 문자열>` |
| `echo <문자열>` | `echo: <문자열>` (UTF-8 원시 바이트 유지) |
| `info` | 시험 펌웨어 이름과 버전 |
| `help` | 지원 명령 |

Enter는 CR/LF/CRLF를 지원한다. ASCII 편집은 Backspace/Delete, 입력 취소는 Ctrl+C다.
한 줄은 종료 문자를 제외하고 최대 127바이트다. 초과 시 해당 줄 전체를 버리고
`ERR line-too-long`으로 응답한다. 알 수 없는 명령은 `ERR unknown-command`다.
자동 입력 에코가 없으므로 xterm에서 타이핑 자체는 보이지 않아도 Enter 후 응답으로 확인한다.

Starter README와 같은 고정 소스·SDK·도구를 사용하되 `-S`는 이 앱, `-B`는 새 경로로
지정한다. `platform/scripts/package-xenova-firmware.py`에 별도 앱 이름/버전을 지정해
새 번들을 생성하고 `--template-id`는 지정하지 않는다. Starter 카탈로그에 덮어쓰지 않는다.

QEMU에서만 기존 펌웨어 revision/해시를 기록한 뒤 명시적으로 정지·교체·시작한다.
시험 후 보존한 이전 revision을 같은 관리 API로 다시 적용하고 시작해 복구한다.
검증 방법과 결과는 `docs/verification/mo-04-zephyr-console.md`에 기록한다.
