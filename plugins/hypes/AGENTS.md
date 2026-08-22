# Hypes 개발

- 제품 모델은 Node, Predicate, Edge와 `hypes_read`, `hypes_rewrite`로 구성한다.
- 제품은 관계 모델과 원자적 읽기·갱신에 집중한다.
- 테스트는 원자적 rewrite, 참조 무결성, SQLite 권한과 재현된 장애에 집중한다.
- 각 기능은 하나의 API 표면으로 제공하고, marketplace package를 배포본으로 사용한다.
