# Design Service

Design Service는 개인 디자인 레시피의 메타데이터와 파일을 보관하는 원격 정본입니다. D1에는
레시피, 패턴, 버전과 파일 해시를 두고 R2에는 HTML, CSS, 이미지와 템플릿 파일을 둡니다.
Design Site와 MCP는 이 서비스를 함께 사용하며 plugin이나 Site 배포본에는 개인 자산을 넣지
않습니다.

## 공개 표면

- `/mcp`: 소유자 OAuth가 필요한 Design MCP
- `/api/v1/*`: Design Site의 서버 경로만 사용하는 비공개 API
- `/health`: 배포 상태 확인

레시피 메타데이터는 `revision`, 파일은 `file_revision`을 대조해 오래된 편집이 새 내용을
덮어쓰지 않게 합니다. R2 객체는 내용 해시가 포함된 키에 저장하고 D1이 현재 파일을 가리킵니다.

기존 폴더형 라이브러리를 처음 옮길 때에는 서비스 배포 후 다음 가져오기 도구를 사용합니다.
원본 폴더는 저장소 밖의 개인 작업 위치일 수 있으며, 가져온 레시피의 공개 표시는 `private`로
바뀝니다.

```bash
DESIGN_SITE_TOKEN=... npm run import:library -- \
  --source <개인 디자인 라이브러리 폴더> \
  --service-url https://personal-agent-design.example.workers.dev
```
