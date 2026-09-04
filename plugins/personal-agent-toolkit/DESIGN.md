# OpenAI 통합 배포 설계

이 디렉터리는 Sense, Corpus, Hypes, Journal, Library, Design과 문서 Skill을 Codex에 하나의 설치
항목으로 제공하는 OpenAI 배포 묶음이다. ChatGPT 개인 계정은 같은 등록 app을 쓰고 문서 Skill은
`scripts/build_chatgpt_personal_skills.py`가 같은 정본에서 만든 Personal Skill archive로 설치한다.
제품 자체의 계약과 구현 정본은 각 `plugins/<product>`와 서비스에 남는다.

통합 범위는 설치와 소유자 인증이다. 제품 이름, Skill 이름, 도구 이름, 업무 규칙과 데이터 저장소는
합치거나 `Personal Agent Toolkit` 접두사를 붙이지 않는다. 통합 app은 각 제품 서비스를 직접 등록한
하나의 MCP 표면으로 묶으며, plugin에서 필수 app 의존성으로 선언한다. 중간 gateway나 로컬 tunnel은
만들지 않는다.

`skills/`는 `scripts/build_openai_plugin.py`가 일곱 제품의 현재 Skill에서 만든 배포본이다.
`document-files`, `documents`, `pdf`, `spreadsheets`, `presentations`는 짧은 이름으로 들어가고,
`runtime/document-files/`는 같은 빌드가 Document Files Python 정본과 host launcher에서 만든다.
Skill이나 실행기를 고칠 때에는 제품 소스를 먼저 수정하고 통합 묶음을 다시 만든다. Claude용 제품별
plugin과 OpenAI 통합 묶음이 같은 Skill 내용을 사용하되, 제품별 `agents/openai.yaml`은 복사하지 않는다.

개인 ChatGPT용 `.skill` 파일은 설치할 때 임시 디렉터리에만 만들고 저장소나 plugin 소스로 보관하지
않는다. Personal Skill 화면에는 `Document Files`, `Documents`, `PDF`, `Spreadsheets`,
`Presentations`의 짧은 이름과 통합 아이콘을 사용한다.
ChatGPT에서 기본 문서 Skill과 겹치거나 예약된 식별자는 upload archive에서만 `word-documents`,
`pdf-files`, `workbooks`, `slide-decks`로 바꾸고 화면 이름은 짧게 유지한다. 같은 기능의 시스템
plugin을 제거할 수 없는 계정에서도 개인 Skill을 명시적으로 선택한 작업은 시스템 plugin이나
Library를 함께 호출하지 않도록 개인 archive에 실행 경계를 추가한다. Codex plugin의 Skill 식별자는
바꾸지 않는다.

Document Files host launcher는 OpenAI가 제공하는 Python을 사용하며 실행 중 package를 내려받거나
Cloudflare 분석기로 폴백하지 않는다. 필요한 실행 기능이 없으면 `runtime_unavailable`을 반환한다.
원격 제품용 등록 app은 문서 바이트를 받거나 저장하지 않는다.
