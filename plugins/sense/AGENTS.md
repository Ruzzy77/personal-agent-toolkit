# Sense 개발

- Sense는 현재 프로필 하나와 `sense_read`, `sense_overview`, `sense_revise`로 구성한다.
- 각 기능은 하나의 원자적 도구로 제공한다.
- 테스트는 원자적 저장, 충돌, 권한과 삭제 동작에 집중한다.
- 실행과 저장은 local launcher와 private runtime이 맡고, marketplace package가 배포본이 된다.
