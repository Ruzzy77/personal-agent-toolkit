# Sense 개발

- 현재 프로필 하나와 `sense_read`, `sense_overview`, `sense_revise`의 경계를 유지한다.
- 같은 기능의 단일·일괄·legacy 도구를 함께 두지 않는다.
- 테스트는 원자적 저장, 충돌, 권한과 삭제 동작에 집중한다.
- 원격 실행·이관 계층, 평가 harness와 배포 사본을 만들지 않는다.
