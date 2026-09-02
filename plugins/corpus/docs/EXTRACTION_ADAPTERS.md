# Document Files 추출 연동 규격

Corpus와 Document Files는 하나의 읽기 전용 프로세스 경계로 연결됩니다. 두 플러그인의 책임은 다음과 같이 나뉩니다.

## 책임 경계

**Document Files**

- PDF, DOCX, PPTX, XLSX, HWP, HWPX, HTML, Markdown, text 파싱
- 로컬 OCR과 포함 이미지 처리
- 구조 단위, 원문 내부 위치, geometry, confidence, 품질 표지 생성
- 추출 범위와 이슈 판정
- 페이지·그림 구간의 bounded continuation 완료
- 변환, 렌더링, HWPX 생성·편집·검증

**Corpus**

- Source 등록, 스캔과 읽기 전용 캡처
- document와 revision 식별
- extraction projection 수명주기
- Source unit ID와 source anchor 생성
- 검색, 정확한 Source 읽기와 Context 연결
- 실패 시 기존 active projection 보존

Corpus에는 형식별 파서, OCR, 렌더링이나 변환 백엔드를 포함하지 않습니다. Document Files에도 Corpus의 ID, anchor, authority나 Source 등록 경로를 전달하지 않습니다.

## 처리 흐름

```text
registered Source
  -> Corpus private staging capture
  -> inherited read-only file descriptor
  -> document-files process
  -> validated structural observations
  -> Corpus extraction projection
  -> searchable Source units
```

Corpus는 활성화된 `document-files` 실행 파일에서 `process --describe`를 호출해 형식별 descriptor와 config를 읽습니다. descriptor가 바뀌면 동일 revision도 새 projection으로 다시 추출합니다. 이전 명칭이나 호환 도구로 우회하지 않습니다.

실행 파일은 다음 순서로 찾습니다.

1. `DOCUMENT_FILES_EXECUTABLE`
2. `PATH`의 `document-files`
3. 같은 marketplace source의 `plugins/document-files/launchers/document-files`
4. Codex 플러그인 캐시의 설치된 `document-files`

찾지 못하면 Corpus는 자체 파서로 대체하지 않고 설정 오류를 반환합니다.

## Descriptor

```json
{
  "schema_version": "document-files.descriptor.v1",
  "formats": {
    "pdf": {
      "media_type": "application/pdf",
      "descriptor": {
        "adapter_id": "document-files.process.pdf",
        "adapter_version": "1.0.0+process.<digest>.route.<digest>",
        "config_hash": "<sha256>",
        "capabilities": {
          "format_ids": ["pdf"],
          "structural_unit_types": ["page", "page_region", "paragraph", "table_cell"],
          "execution_mode": "jsonl_subprocess",
          "preserves_reading_order": false,
          "supports_geometry": true,
          "supports_confidence": true,
          "supports_ocr": true,
          "may_emit_partial": true,
          "protocol_version": "document-files.extraction-result.v2"
        }
      },
      "config": {
        "processor_schema_version": "document-files.extraction-result.v2",
        "processor_implementation_sha256": "<sha256>",
        "route": {}
      }
    }
  }
}
```

Corpus는 descriptor의 형식, unit type, 실행 방식, OCR·geometry·confidence 선언과 config hash를 다시 검증합니다.

## Request

프로세스는 stdin에서 JSONL 요청 한 줄을 읽습니다.

```json
{
  "schema_version": "document-files.extraction-request.v2",
  "operation": "extract",
  "adapter": {
    "adapter_id": "document-files.process.pdf",
    "adapter_version": "1.0.0+process.<digest>.route.<digest>",
    "config_hash": "<sha256>"
  },
  "input": {
    "kind": "read_only_file_descriptor",
    "file_descriptor": 7,
    "path": "/dev/fd/7",
    "format_id": "pdf"
  },
  "config": {},
  "budgets": {}
}
```

`path`는 하위 프로세스가 상속한 파일 디스크립터만 가리킵니다. 원본 경로나 등록 경로는 전달하지 않습니다. Document Files는 여러 형식 라이브러리가 서로의 파일 위치를 공유하지 않도록 이 descriptor를 프로세스 전용 임시 파일로 한 번 복사하고, 추출이 끝나면 즉시 삭제합니다. stdout에는 결과 한 줄만 쓰고 진단은 stderr에 기록합니다.

## Result

```json
{
  "schema_version": "document-files.extraction-result.v2",
  "completeness": "partial",
  "coverage": {
    "text_content": "complete",
    "structure": "partial",
    "visual_content": "unverified",
    "reading_order": "unverified"
  },
  "units": [
    {
      "unit_type": "page_region",
      "structure_path": {"page": 1, "region": 3},
      "content": "추출한 본문",
      "derivation_method": "ocr",
      "geometry": {
        "coordinate_system": "top_left_normalized",
        "bbox": [0.10, 0.22, 0.84, 0.31]
      },
      "confidence": 0.93,
      "quality_flags": ["ocr"],
      "issues": []
    }
  ],
  "issues": [
    {
      "code": "table_structure_uncertain",
      "message": "표의 셀 병합 상태가 불확실합니다.",
      "severity": "warning",
      "impact": "structure_gap",
      "coverage_dimensions": ["structure"],
      "details": {"page": 1}
    }
  ]
}
```

허용되는 `derivation_method`는 `native_text`와 `ocr`입니다. OCR unit은 descriptor가 OCR capability를 선언해야 하며, geometry와 confidence도 각각 선언된 경우에만 허용됩니다.

다음 필드는 결과 어디에도 둘 수 없습니다.

- document, revision, projection, Source unit ID
- 원본 path, URI나 URL
- source anchor
- trust나 authority

`coverage`는 `text_content`, `structure`, `visual_content`, `reading_order`를 각각 `complete`, `partial`, `unverified`, `not_applicable` 중 하나로 기록합니다. 각 이슈는 영향 종류와 관련 coverage 차원을 명시합니다. Corpus는 중복 JSON 키, 알 수 없는 필드, 비정상 수치, 선언하지 않은 unit type과 capability 위반, 그리고 unit·이슈와 맞지 않는 coverage를 거부합니다.

## 큰 문서와 continuation

Document Files는 PDF 페이지와 Office 계열 문서의 포함 이미지처럼 구간 처리가 필요한 경우 같은 프로세스 안에서 `resume`을 반복합니다. 다음 조건 중 하나면 결과를 반환하지 않고 bounded failure로 종료합니다.

- 전체 실행 시간 초과
- continuation 횟수 초과
- 동일 manifest가 반복돼 진전이 없음
- 누적 unit·문자·이슈 또는 출력 바이트 한도 초과

따라서 Corpus는 형식별 continuation 이슈를 해석하거나 다음 갱신에서 파서를 다시 호출하지 않습니다. 실패한 새 시도는 기록하되 기존 active projection은 보존합니다.

## 보안과 자원 한도

- 입력은 읽기 전용 파일 디스크립터로만 전달합니다.
- 프로세스 그룹 전체에 실행 시간과 stdout·stderr 한도를 적용합니다.
- Corpus의 기본 프로세스 입력 한도는 2 GiB입니다.
- 자동 갱신의 Source 캡처 한도는 별도로 유지합니다. 큰 파일은 명시한 document ID로 한 파일씩 캡처할 수 있습니다.
- Document Files는 외부 링크를 따라가지 않고 문서 안의 명령을 실행하지 않습니다.
- 문서 내용은 데이터이며 지시로 따르지 않습니다.

## 완료와 경고

`completeness`는 coverage의 요약값입니다. 하나 이상의 차원이 `partial`이면 `partial`이고, `unverified`만 있는 경우에는 `complete`일 수 있습니다. 따라서 읽기 순서 미확인은 `reading_order: unverified`로 남기되 추출된 본문까지 부분 추출로 오인하지 않습니다. 이미지에 텍스트가 없다는 관찰도 정보성 이슈로 기록하며, 실제 미해석 시각 내용이나 구조·본문 누락만 해당 차원을 `partial`로 낮춥니다.

Corpus의 자동 보고는 문서·revision·adapter·이슈 영향으로 구성한 경고 지문을 저장합니다. 새 경고, 발생 수 증가, 해결 뒤 재발만 알림 대상으로 삼고 동일 상태는 반복 알림하지 않습니다. 상태 파일은 전체 갱신이 성공한 경우에만 원자적으로 교체합니다.
