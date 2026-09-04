# OpenAI 통합 배포 설계

이 디렉터리는 Sense, Corpus, Hypes, Journal, Library, Design과 문서 Skill을 ChatGPT와 Codex에 하나의
설치 항목으로 제공하는 배포 묶음이다. 제품 자체의 계약과 구현 정본은 각 `plugins/<product>`와
서비스에 남는다.

통합 범위는 설치와 소유자 인증이다. 제품 이름, Skill 이름, 도구 이름, 업무 규칙과 데이터 저장소는
합치거나 `Personal Agent Toolkit` 접두사를 붙이지 않는다. 통합 app은 각 제품 서비스를 직접 등록한
하나의 MCP 표면으로 묶으며, 중간 gateway나 로컬 tunnel을 만들지 않는다.

`skills/`는 `scripts/build_openai_plugin.py`가 일곱 제품의 현재 Skill에서 만든 배포본이다.
`document-files`, `documents`, `pdf`, `spreadsheets`, `presentations`는 짧은 이름으로 들어가고,
`runtime/document-files/`는 같은 빌드가 Document Files Python 정본과 host launcher에서 만든다.
Skill이나 실행기를 고칠 때에는 제품 소스를 먼저 수정하고 통합 묶음을 다시 만든다. Claude용 제품별
plugin과 OpenAI 통합 묶음이 같은 Skill 내용을 사용하되, 제품별 `agents/openai.yaml`은 복사하지 않는다.

Document Files host launcher는 OpenAI가 제공하는 Python을 사용하며 실행 중 package를 내려받거나
Cloudflare 분석기로 폴백하지 않는다. 필요한 실행 기능이 없으면 `runtime_unavailable`을 반환한다.
원격 제품용 등록 app은 문서 바이트를 받거나 저장하지 않는다.
