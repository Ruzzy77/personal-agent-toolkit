# Hypes 개발 원칙

Hypes의 관계 모델보다 큰 평가·검증·배포 구조를 만들지 않는다.

- Node·Predicate·Edge의 저장, 제한된 읽기와 원자적 rewrite라는 두 도구의 경계를 우선한다.
- 모델 응답 품질을 고정하려는 대규모 scenario, 평가 runner, golden 결과와 판정 framework를 제품 저장소에 두지 않는다.
- TDD와 전체 회귀를 기본 절차로 사용하지 않는다. 변경한 경로와 직접 관련된 핵심 사례 한두 개만 확인한다.
- 데이터 손실, SQLite 권한, 원자적 rewrite 또는 재현된 핵심 장애에 필요한 테스트만 남기고 중복 사례는 지운다.
- 별도 read 상태, 복구 protocol, legacy 입력과 같은 안내 계층을 실제 제품 기능보다 크게 만들지 않는다.
- 이 plugin 폴더가 소스이자 설치 경로다. 별도 배포 사본, package builder와 생성 version 파일을 두지 않는다.
