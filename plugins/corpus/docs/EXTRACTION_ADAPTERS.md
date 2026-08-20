# 추출 adapter 연동 규격

Corpus core는 문서·revision·projection·source-unit 식별자와 원문 위치를 정합니다. 외부
parser는 refresh 중에 만든 임시 사본을 읽고 구조 추출 결과를 `ExtractionEnvelope`로
반환합니다.

## 공통 흐름

```text
registered source
  -> private temporary staging copy
  -> bounded adapter
  -> validated ExtractionEnvelope
  -> current extraction projection
  -> request-scoped agent context
```

내장 parser도 `run_builtin_extraction()`을 통해 같은 `ExtractionEnvelope`를 사용합니다.
외부 구현은 `ExternalJSONLAdapter`의 검증을 거쳐 projection으로 등록됩니다. 재추출이
완료되면 새 projection이 active 상태가 되며, 다시 추출하다 오류가 발생하면 직전 active
projection이 유지됩니다. `AdapterRegistry`는 source extension을 packaged 또는 built-in
adapter로 연결합니다. Document, revision, projection과 source-unit ID는 core가 관리합니다.

`complete`는 adapter 설명에 적힌 처리 범위에서 unit이 하나 이상 있고,
정보성 issue를 제외한 warning과 error가 0인 상태입니다. PDF adapter는 페이지별 본문
coverage가 확보돼도 reading order 상태를 unverified로 선언하며,
capability와 `reading_order_unverified` quality flag를 남깁니다. 실제 페이지 누락, OCR
실패, 처리 한도 도달이 있을 때만 issue와 `partial`을 기록합니다. 에이전트는 형식별 기본
문서 도구로 화면 배치와 읽기 순서를 확인합니다. DOCX·PPTX의 built-in adapter는 기존과
같이 읽기 순서 미확인을 warning과 `partial`로 기록합니다.

## Request

Adapter는 stdin에서 한 줄의 JSON request를 읽습니다.

```json
{
  "schema_version": "corpus.extraction-request.v1",
  "operation": "extract",
  "adapter": {
    "adapter_id": "local.hwp5.reader",
    "adapter_version": "1.0.0",
    "config_hash": "<sha256>"
  },
  "input": {
    "kind": "read_only_file_descriptor",
    "file_descriptor": 7,
    "path": "/dev/fd/7",
    "format_id": "hwp"
  },
  "config": {},
  "budgets": {}
}
```

`path`는 하위 프로세스가 넘겨받은 임시 사본의 file descriptor를 가리킵니다. Adapter는
stdout에 JSONL 결과 한 줄을 출력하고 진단문은 stderr에 기록합니다. 두 출력에는 각각
바이트 제한을 적용합니다.

## Result

```json
{
  "schema_version": "corpus.extraction-result.v1",
  "completeness": "partial",
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
      "details": {"page": 1}
    }
  ]
}
```

`derivation_method`의 현재 허용값은 `native_text`와 `ocr`입니다. `derivation_method`가
`ocr`이면 descriptor가 OCR capability를 선언해야 합니다. `ocr` quality flag도 같은
derivation method와 함께 기록합니다.

정보성 `unit_split`을 제외한 경고나 오류가 있거나 결과가 비어 있으면, adapter가
`complete`라고 적어도 core가 `partial`로 낮춥니다.
Adapter가 선언한 capability와 일치하는 format, unit type, geometry, confidence와 OCR
결과를 등록합니다. 일치하지 않는 result에는 validation error를 반환합니다.

다음 필드는 core가 배정하므로 result schema에서 제외합니다.

- document, revision, projection 또는 source-unit ID
- 원본 path, URI나 URL
- source anchor
- trust 또는 authority

## 추출기 구성 원칙

실제 추출기는 core schema와 분리된 하위 프로세스 adapter로 실행합니다. Plugin과 함께
배포되는 추출기도 자체 chunk와 heading 추정을 중립 envelope로 변환하며, 수명주기와
source-unit 식별자는 core가 정합니다.

- binary HWP: HWP5 parser가 section·record·paragraph locator를 반환하고, 현재 복원 범위
  밖의 구조는 issue로 기록합니다.
- PDF: native text와 on-device OCR observation을 함께 보존합니다. OCR text에는 page,
  geometry, confidence와 backend/runtime/config identity를 남깁니다.
새 backend는 실제 문서에서 현재 추출기가 부족할 때만 추가합니다. 비교용 framework나
상시 평가 계층을 먼저 만들지 않고, 같은 `ExtractionEnvelope`를 반환하는 adapter 하나로
연결합니다.

## 현재 사용 중인 추출기

로컬 기준 문서와 처리량을 제한한 색인으로 다음 두 adapter를 확인했습니다.

- `work-corpus.native.pdfkit-vision`: 기존 projection의 current 상태를 보존하기 위해
  유지하는 persisted adapter ID입니다. PDFKit native text와 Apple Vision OCR을 사용합니다.
  Swift 6.2 호환 SDK로 빌드한 macOS 26+ 환경에서는 on-device
  [`RecognizeDocumentsRequest`](https://developer.apple.com/documentation/vision/recognizedocumentsrequest)로
  paragraph와 table-cell/span을 얻습니다. 표 셀에 속한 line UUID를 추적하고, 해당
  line들로만 구성된 paragraph를 중복 결과에서 제외합니다. 이전 macOS 또는 structured
  request 실패 시 `VNRecognizeTextRequest`로 fallback합니다. 기본 `hybrid` 경로는 PDFKit
  native text를 먼저 보존하고, 본문이 32자 미만인 sparse page만 OCR합니다. 따라서
  searchable PDF를 모든 페이지에서 다시 OCR해 중복 unit을 만드는 비용을 피하면서
  image-only와 mixed PDF는 on-device OCR로 보완합니다. PDFKit이 열지 못하지만 유효한
  PDF는 `pypdf` page text fallback으로 복구합니다. `max_edge_pixels`, page count, OCR scope,
  native-text alphanumeric threshold, blank-page detection, fallback backend, language, OS
  runtime과 adapter source identity를 config와 version에 고정합니다. 렌더링 결과가 완전히
  흰 페이지는 관찰된 빈 페이지로 처리합니다. Reading order 미확인은 capability와 quality
  flag로 남기고, 페이지 누락·OCR 실패·budget 도달만 issue와 `partial`로 기록합니다.
- `work-corpus.hwp5.spec-partial`: 기존 projection 호환용 persisted adapter ID입니다.
  Hancom이 공개한
  [HWP 5.0 revision 1.3 specification](https://cdn.hancom.com/link/docs/%ED%95%9C%EA%B8%80%EB%AC%B8%EC%84%9C%ED%8C%8C%EC%9D%BC%ED%98%95%EC%8B%9D_5.0_revision1.3.pdf)에
  따라 OLE `BodyText/SectionN`, raw-deflate record와 `HWPTAG_PARA_TEXT` control boundary를
  직접 해석합니다. `olefile`은 Compound File을 여는 low-level dependency로 사용합니다.
  Table cell·heading·list·각주·embedded object 관계는 현재 projection 범위 밖의 구조로
  issue에 기록합니다.

두 adapter는 임시 사본의 file descriptor를 입력으로 받고 로컬에서 실행됩니다. 추출기를
바꿀 때에는 변경된 형식의 실제 표본만 확인합니다. 전체 문서 조합을 위한 golden corpus나
별도 평가 framework는 유지하지 않습니다.
