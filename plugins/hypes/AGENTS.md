# Hypes 개발

- 제품 모델은 Node, Predicate, Edge와 `hypes_read`, `hypes_rewrite`의 경계를 유지한다.
- 응답 평가용 scenario, golden 결과와 별도 판정 framework를 제품에 넣지 않는다.
- 테스트는 원자적 rewrite, 참조 무결성, SQLite 권한과 재현된 장애에 집중한다.
- 기능의 중복 표면, 복구 protocol과 배포 사본을 만들지 않는다.
