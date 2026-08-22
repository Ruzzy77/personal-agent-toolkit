# Personal Agent Toolkit

`plugins/sense`, `plugins/corpus`, `plugins/hypes`와 `gateway`가 각각의 소스이자 실행·설치 경로다.

- 제품 기능과 manifest는 해당 폴더에서 수정한다.
- plugin base version은 manifest, `pyproject.toml`, package `__version__`와 lockfile에 함께 반영한다.
- 사용자 데이터, 자격 증명과 runtime 파일은 private runtime에서 관리한다.
- 배포에는 수정한 plugin의 시작과 MCP 도구 노출 확인이 포함된다.
