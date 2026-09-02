# Personal Agent Toolkit

## 소스와 실행 경계

- 저장소 루트와 변경 대상 제품의 `README.md`, `DESIGN.md`와 manifest를 파일과 구성의 역할을 정하는 기준으로 삼는다.
- `plugins/sense`, `plugins/corpus`와 `plugins/hypes`는 제품 계약과 배포 plugin의 소스, `services/remote-context`는 세 제품이 공유하는 상시 원격 MCP, `apps/sync`는 Finder 자료에 접근하는 로컬 실행 구성으로 다룬다. `gateway`는 이관 검증이 끝날 때까지만 유지하는 기존 터널 경계다. `plugins/journal`은 원격 Journal 연결과 운영 Skill, `services/journal`은 D1 기반 서비스, `auth`는 공통 소유자 인증 구성으로 다룬다.
- 제품 기능과 manifest는 해당 소스 폴더에서 수정한다. 설치본, plugin cache, 생성된 배포 복사본과 임시 환경을 원본처럼 수정하지 않는다.
- 사용자 데이터, 자격 증명, runtime database와 운영 설정은 private runtime이나 배포 환경에 두고 공개 저장소에 넣지 않는다.
- Python 소스, 스크립트와 테스트는 루트 `ruff.toml`을 따른다. 변경 뒤 `uvx ruff==0.16.5 format --check plugins/sense plugins/corpus plugins/hypes gateway`와 같은 범위의 `ruff check`를 통과시킨다.
- plugin base version을 바꿀 때에는 manifest, `pyproject.toml`, package `__version__`와 lockfile의 값을 함께 맞춘다.
- plugin base version을 바꾼 작업은 소스 커밋과 원격 저장소 반영 뒤 Codex, Claude Code, Claude Desktop과 ChatGPT 웹까지 같은 작업에서 갱신한다. Codex·Claude Code·Claude Desktop에서는 Sense, Corpus, Document Files, Hypes와 Journal을 업데이트하거나 다시 설치하고 새 세션에서 현재 공개 MCP 도구를 확인한다.
- ChatGPT 웹과 claude.ai는 Sense·Corpus·Hypes의 상시 원격 MCP를 각각 연결하고 소유자 인증, 현재 작업 목록과 권한을 확인한다. 기존 gateway와 Secure MCP Tunnel은 원격 이관 검증이 끝날 때까지 병행하며, 검증 뒤 활성 구성에서 제거한다.
- Document Files는 로컬 문서 작업용 plugin으로 유지하고 ChatGPT 웹에 별도 MCP로 노출하지 않는다. 로컬 Source 갱신은 Sync가 Connection 정책에 따라 설치된 Document Files를 호출하거나, 같은 분석 계약을 구현한 별도 원격 analyzer에 권한이 확인된 임시 자료를 보낸다.
- 배포 완료는 변경한 plugin이 시작되고 현재 공개 MCP 도구와 입력 구조가 실제 세션에 노출되며 위 실행 환경의 갱신을 모두 확인한 상태를 뜻한다.
- 로컬 launcher, Sync, marketplace 배포본, 원격 MCP, 기존 gateway와 원격 인증의 역할을 섞지 않는다.
