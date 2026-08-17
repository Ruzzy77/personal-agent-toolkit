# Personal Agent Toolkit 개발 원칙

`plugins/sense`, `plugins/hypes`, `plugins/corpus`가 각 제품의 소스이자 marketplace 설치 경로다.

- 제품 기능과 manifest는 해당 plugin 폴더에서 직접 수정한다. 별도 owner 사본이나 package builder를 두지 않는다.
- 전체 validator, package inventory와 회귀 matrix를 다시 만들지 않는다.
- 수정한 실행 경로와 직접 관련된 기본 확인만 하고, 실제 release에서는 각 plugin의 시작과 기본 도구 노출을 확인한다.
- plugin에 사용자 데이터, 자격 증명과 로컬 runtime 파일을 넣지 않는다.
- `gateway/`는 선택 기능의 배포 폴더다. gateway 변경은 `owners/remote-runtime` 정본에서 함께 반영한다.
