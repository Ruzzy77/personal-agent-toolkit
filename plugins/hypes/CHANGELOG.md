# Hypes 변경 기록

## 0.8.1

- 별도의 저장 확인 없이 대화 중 관계 모델을 다듬을 수 있게 했다.
- 사용자가 직접 밝힌 내용은 우선 적용하고, 필요한 범위만 읽어 갱신한다.

## 0.8.0

- 관계 모델을 Node·Predicate·Edge 구조로 정리했다.
- MCP 표면을 `hypes_read`와 `hypes_rewrite` 두 도구로 줄였다.
- 여러 put과 delete를 한 트랜잭션에서 처리하고 참조 무결성을 확인한다.
- 데이터 디렉터리와 SQLite 파일의 소유자·권한·파일 종류를 열기 전에 확인한다.
- 호출자가 선언하는 `read_purpose`, 파생 `read_state`, 복구 protocol과 model evaluation
  framework를 제거하고 직접적인 제한 조회 결과만 반환한다.
- 제품 소스와 marketplace 설치 경로를 하나의 plugin 폴더로 합쳤다.
