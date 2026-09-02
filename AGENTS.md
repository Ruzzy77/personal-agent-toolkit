# Personal Agent Toolkit

## 소스와 실행 경계

- 저장소 루트와 변경 대상 제품의 `README.md`, `DESIGN.md`와 manifest를 파일과 구성의 역할을 정하는 기준으로 삼는다.
- `plugins/sense`, `plugins/corpus`, `plugins/hypes`와 `gateway`는 로컬 제품의 소스이자 설치·실행 경로로 다룬다. `plugins/journal`은 원격 Journal 연결과 운영 Skill, `services/journal`은 D1 기반 서비스, `sites/journal`은 소유자 전용 화면, `auth`는 공통 소유자 인증 구성으로 다룬다.
- 제품 기능과 manifest는 해당 소스 폴더에서 수정한다. 설치본, plugin cache, 생성된 배포 복사본과 임시 환경을 원본처럼 수정하지 않는다.
- 사용자 데이터, 자격 증명, runtime database와 운영 설정은 private runtime이나 배포 환경에 두고 공개 저장소에 넣지 않는다.
- Python 소스, 스크립트와 테스트는 루트 `ruff.toml`을 따른다. 변경 뒤 `uvx ruff==0.16.5 format --check plugins/sense plugins/corpus plugins/hypes gateway`와 같은 범위의 `ruff check`를 통과시킨다.
- plugin base version을 바꿀 때에는 manifest, `pyproject.toml`, package `__version__`와 lockfile의 값을 함께 맞춘다.
- plugin base version을 바꾼 작업은 소스 커밋과 원격 저장소 반영 뒤 Codex, Claude Code, Claude Desktop과 ChatGPT 웹까지 같은 작업에서 갱신한다. Codex·Claude Code·Claude Desktop에서는 Sense, Corpus, Document Files, Hypes와 Journal을 업데이트하거나 다시 설치하고 새 세션에서 현재 공개 MCP 도구를 확인한다.
- ChatGPT 웹은 gateway LaunchAgent를 다시 설치한 뒤 세 developer plugin을 새로 고침하고, 현재 액션 목록과 `모든 액션 허용` 권한을 확인한다.
- Document Files는 로컬 전용 plugin으로 유지하고 ChatGPT 웹에 별도 developer plugin으로 노출하지 않는다. Corpus gateway는 로컬에 설치된 Document Files를 문서 처리 경계로 사용한다.
- 배포 완료는 변경한 plugin이 시작되고 현재 공개 MCP 도구와 입력 구조가 실제 세션에 노출되며 위 실행 환경의 갱신을 모두 확인한 상태를 뜻한다.
- 로컬 launcher, marketplace 배포본, gateway와 원격 인증의 역할을 섞지 않는다.
