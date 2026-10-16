---
description: Jira 이슈 키로 컨벤션에 맞는 작업 브랜치를 만들고 이슈를 '진행 중'으로 바꾼다
argument-hint: <Jira키 예: S15P31E110-56>
---

Jira 이슈 `$ARGUMENTS`로 작업 브랜치를 만든다. 규칙은 `CONTRIBUTING.md`의 3장(브랜치 네이밍)과 4장(작업 흐름)을 따른다.

1. 이슈 조회
   - Atlassian(Jira) 도구로 `ssafy.atlassian.net`의 이슈 `$ARGUMENTS`를 조회한다 (summary, description, issuetype, status, assignee).
   - 도구가 없거나 조회에 실패하면 사용자에게 이슈 제목을 물어본다.
   - 인자가 비어 있거나 `S15P31E110-숫자` 형식이 아니면 멈추고 키를 물어본다.
   - 이슈 유형이 에픽이나 스토리면 브랜치를 만들지 말고, 그 아래 작업 키로 다시 실행하라고 안내한다.

2. 브랜치 이름 `{type}/{part}/{키}-{설명}` 결정
   - part: 제목 접두어로 정한다.
     `[PLATFORM]`→platform, `[CTRL]`→ctrl, `[AGENT]`→agent, `[DISPLAY]`→display, `[BE]`→backend, `[FE]`→frontend, `[APP]`→app, `[DEPLOY]`→deploy, `[COMMON]`→common.
     `[TEST]`는 이슈 내용의 대상 폴더로 part를 정한다 (CONTRIBUTING 3장 표 참고).
   - type: `feat | fix | refactor | test | docs | chore` 중 이슈 내용에 맞는 것. `[TEST]`는 test, 문서 작성 이슈는 docs, 빌드·설정·CI는 chore, 그 외 기능 구현은 feat.
   - 설명: 이슈 제목을 영어 kebab-case 2~5단어로 줄인다 (소문자, 하이픈, 영숫자만).
   - 예: `[PLATFORM] RPi4 U-Boot → Xen → dom0 부팅` → `feat/platform/S15P31E110-56-rpi4-xen-boot`

3. 브랜치 생성
   - `git status --porcelain`이 비어 있지 않으면 멈추고, 변경 사항을 커밋하거나 stash할지 사용자에게 묻는다.
   - 같은 이름의 로컬·원격 브랜치가 있으면 새로 만들지 말고 그 브랜치로 전환할지 묻는다.
   - 기준 브랜치는 part의 기능별 develop 브랜치다 (CONTRIBUTING 1장 표).
     backend·ctrl→`backend`, frontend→`frontend`, platform·agent·display→`platform`, app→`vehicle`, common·deploy→`develop`.
     원격에 기준 브랜치가 없으면 멈추고 알려준다.
   - `git switch {기준}` → `git pull origin {기준}` → `git switch -c {브랜치}`

4. Jira 상태 변경
   - 이슈가 '해야 할 일'이면 '진행 중'으로 전환한다. 담당자가 비어 있으면 그대로 두고 알려준다.

5. 결과 보고
   - 만든 브랜치 이름, 기준 브랜치(= MR target), Jira 상태 변경 여부를 알려준다.
   - 기준이 기능별 develop 브랜치면: MR 설명에는 키 `$ARGUMENTS`만 적고, `Closes $ARGUMENTS`는 그 기능별 develop 브랜치 → develop MR에 적어야 Jira가 자동 완료된다고 알려준다.
   - 기준이 develop이면: MR 설명에 넣을 줄 `Closes $ARGUMENTS`를 알려준다 (develop 머지 시 Jira 자동 완료).
