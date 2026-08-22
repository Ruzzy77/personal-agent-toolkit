# Corpus 개발

- Source Connection은 읽기 전용이며, Work Connection은 사용자가 연결한 작업 폴더다.
- Space는 등록 정보에서 계산한다.
- CLI와 MCP는 공통 구현을 사용하며 현재 데이터 모델을 함께 발전시킨다.
- 테스트는 데이터 손실, 경로·권한 경계와 재현된 장애에 집중하고 기존 통합 사례를 우선한다.
- Context Skill은 private Corpus 저장소에서 관리한다.
- 사용법은 `README.md`, 제품 경계는 `DESIGN.md`, 외부 규격은 `docs/`에 둔다.
