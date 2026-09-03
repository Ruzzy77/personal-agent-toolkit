# Document Analyzer service

`AnalysisJob v1`의 식별 헤더와 승인된 문서 바이트를 받아 `AnalysisResult v1`을 반환하는
비저장형 Cloudflare Worker입니다. 공개 MCP나 문서 저장소가 아니며
`services/remote-context`의 비공개 Service Binding에서만 호출합니다.

원격 실행에서는 텍스트, Markdown, HTML, PDF, DOCX, PPTX, XLSX, HWPX와 HWP의 저장된
텍스트를 추출합니다. 네이티브 앱 기반 렌더링·편집, OCR과 원격 구현이 보존하지 못하는 세부
구조는 로컬 Document Files가 맡습니다. 응답을 만든 뒤 입력 바이트와 추출 결과를 저장하지
않습니다.

```sh
npm ci
npm run check
cp wrangler.example.jsonc wrangler.jsonc
npm run deploy
```

