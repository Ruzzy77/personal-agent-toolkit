# Corpus 개발

- Source는 읽기 전용, Work Connection은 명시적으로 연결한 폴더로 한정한다.
- Space는 등록 정보에서 계산하며 별도 registry에 저장하지 않는다.
- CLI와 MCP의 중복 표면, 임시 migration 계층과 평가 framework를 만들지 않는다.
- 테스트는 데이터 손실, 경로·권한 경계와 재현된 장애에 집중하고 기존 통합 사례를 우선한다.
- Context Skill은 private Corpus 저장소에 두며 marketplace package에 복사하지 않는다.
- 사용법은 `README.md`, 제품 경계는 `DESIGN.md`, 외부 규격은 `docs/`에 둔다.
