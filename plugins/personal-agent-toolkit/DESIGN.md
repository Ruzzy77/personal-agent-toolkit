# OpenAI 통합 배포 설계

이 디렉터리는 Sense, Corpus, Hypes, Journal, Library와 Design을 ChatGPT와 Codex에 하나의 설치 항목으로
제공하는 배포 묶음이다. 제품 자체의 계약과 구현 정본은 각 `plugins/<product>`와 서비스에 남는다.

통합 범위는 설치와 소유자 인증이다. 제품 이름, Skill 이름, 도구 이름, 업무 규칙과 데이터 저장소는
합치거나 `Personal Agent Toolkit` 접두사를 붙이지 않는다. 통합 app은 각 제품 서비스를 직접 등록한
하나의 MCP 표면으로 묶으며, 중간 gateway나 로컬 tunnel을 만들지 않는다.

`skills/`는 `scripts/build_openai_plugin.py`가 여섯 제품의 현재 Skill에서 만든 배포본이다. Skill을
고칠 때에는 제품 소스를 먼저 수정하고 통합 묶음을 다시 만든다. Claude용 제품별 plugin과 OpenAI
통합 묶음이 같은 Skill 내용을 사용하되, OpenAI 전용 `agents/openai.yaml`은 통합 app과 맞지 않는
제품별 MCP 의존성 선언을 피하기 위해 복사하지 않는다.
