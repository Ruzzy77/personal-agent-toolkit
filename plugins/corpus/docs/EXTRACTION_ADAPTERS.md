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

- binary HWP: content router가 일반 문서는 HWP5 specification parser로 읽고, 배포용
  문서나 specification parser 실패 문서는 고정된 `rhwp` 보조 추출기로 페이지 본문을
  읽습니다. 암호화 문서는 계속 거부합니다. 두 경로 모두 현재 복원 범위 밖의 구조는
  issue로 기록합니다.
- PDF: native text와 on-device OCR observation을 함께 보존합니다. OCR text에는 page,
  geometry, confidence와 backend/runtime/config identity를 남깁니다.
새 backend는 실제 문서에서 현재 추출기가 부족할 때만 추가합니다. 비교용 framework나
상시 평가 계층을 먼저 만들지 않고, 같은 `ExtractionEnvelope`를 반환하는 adapter 하나로
연결합니다.

## 현재 사용 중인 추출기

로컬 기준 문서와 처리량을 제한한 색인으로 다음 adapter를 확인했습니다.

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
- `work-corpus.hwp5.content-router`: 일반 HWP5는 원문 레코드 기반 구조 추출기로 읽습니다.
  배포용 문서와 기본 추출 실패 문서는 고정된 `rhwp` 페이지 본문 추출기로 복구합니다.
  암호화 문서는 계속 거부합니다. 구조 추출 도입 이전의 문단 전용 projection은 새 결과와
  동등하게 취급하지 않으며, 변경된 HWP/HWPX만 제한된 단위로 재색인합니다.
- `work-corpus.hwp5.spec-partial`: 기존 persisted adapter ID를 유지합니다. Hancom의
  [HWP 5.0 revision 1.3 공개 규격](https://cdn.hancom.com/link/docs/%ED%95%9C%EA%B8%80%EB%AC%B8%EC%84%9C%ED%8C%8C%EC%9D%BC%ED%98%95%EC%8B%9D_5.0_revision1.3.pdf)을
  바탕으로 OLE section, 문단 헤더, 컨트롤, 문단 리스트와 표 레코드의 포함 관계를 읽습니다.
  본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
  `hwp_structure.py`는 문단 본문을 중복 생성하지 않고 표·셀의 행과 열, 병합 범위, 캡션,
  글상자, 머리말·꼬리말, 각주·미주와 삽입 개체의 원문 위치를 연결합니다. DocInfo의
  개요와 번호·글머리표 속성을 구분하며, 글자 크기나 문구로 제목을 추정하지 않습니다.
  문자 글머리표와 번호 정의를 보존하지만 실제 자동 번호 계산은 하지 않습니다.
  수식은 실행하지 않은 원문 수식 문자열로 보존합니다. 지원하지 않는 구조와 필드 의미,
  개체 내부 내용, 미계산 번호는 기능별 issue로 남깁니다.
- `work-corpus.hwpx.content-router`: ZIP HWPX의 XML 포함 관계를 직접 읽습니다. 표 안의
  문단을 바깥 문단에 다시 합치지 않으며, 같은 문단의 표 앞뒤 텍스트는 구간별로 보존합니다.
  패키지에 명시된 구역 순서를 우선하고, 순서 정보가 없으면 구역 번호로 정렬합니다.
  잘못된 패키지 순서에는 경고를 남깁니다. 표·셀, 제목·목록, 주석과 개체는 HWP와 같은
  관계 필드를 사용합니다. 확장자가 `.hwpx`이더라도 내용이 바이너리 HWP이면 HWP 추출기로
  위임합니다. 라우터는 위임 경로 전체의 unit type을 선언합니다.
- `work-corpus.hwp5.rhwp-page-text`: `rhwp` 0.8.2의 `export-text --json`에서 페이지 본문을
  취하는 복구 경로입니다. 원본 경로는 전달하지 않고 읽기 전용 file descriptor를 사용합니다.
  공유 bounded subprocess가 실행 중 stdout·stderr 크기와 실행 시간을 제한합니다.
  구조를 복원하지 못한 결과는 계속 `partial`입니다. 실행 파일은
  `scripts/provision_rhwp.py`가 공식 release archive의 고정된 SHA-256을 확인해 설치합니다.
  `rhwp`의 표 추출은 실제 표본의 비교 검증에 사용하며 운영 색인에 중복 본문을 추가하지 않습니다.

## HWP/HWPX 구조와 조회

기존 `ExtractionEnvelope` 안에서 `structure_path`를 확장하며 별도 문서 저장소나 과거
projection 보관 계층은 만들지 않습니다.

- HWP의 `section`과 `record`는 1부터 시작하며 원래 section stream 안의 위치입니다.
  `paragraph_record`는 문단 헤더, `owner_paragraph_record`는 개체를 포함한 문단을 가리킵니다.
- HWPX의 `section_file`과 `element`는 XML 파일과 자식 인덱스 경로입니다. 같은 문단을
  나눈 텍스트는 `paragraph_element`와 `segment`로 구분합니다. 이 주소는 파일 경로가 아닙니다.
- `table`, `cell`, `note`, `object`는 해당 section 안의 구조 위치입니다. Corpus의 unit ID나
  영구 식별자를 추출기가 대신 생성한 값이 아닙니다. 행·열은 0부터 시작하고 병합 범위는
  원문 값을 그대로 보존합니다. 범위 밖 셀이나 겹침을 발견하면 값을 조용히 보정하지 않습니다.
- 빈 표와 셀도 구조 unit으로 보존하되 텍스트 검색에는 넣지 않습니다. 같은 값이 다른 셀에
  반복되면 각각 보존합니다. 검색 대상은 본문과 상대 파일명이며 내부 좌표는 검색어로 취급하지 않습니다.
- `corpus_file_read`의 `include_structure_context=true`는 지정한 셀의 행, 원문에 제목 셀로
  표시된 셀, 표 구조와 캡션, 연결된 주석·개체를 기존 읽기 제한 안에서 함께 반환합니다.
  기본값은 `false`이며 `neighbor_span`의 앞뒤 순번 조회는 그대로 유지합니다. 구조 조회가
  제한을 넘으면 일부를 조용히 잘라내지 않고 더 좁은 셀이나 일반 조회를 요청하도록 오류를 반환합니다.

페이지의 시각 배치와 읽기 순서는 이 구조 추출만으로 보장하지 않습니다. 그림 OCR, 삽입된
외부 문서의 본문 추출, 필드 실행과 자동 번호 계산은 하지 않습니다. 실제로 존재하지만
복원하지 못한 내용은 기능별 경고와 `partial`로 남기며, 단순히 경고를 제거해 완료로 만들지 않습니다.

## 갱신 결과와 버전 전환

부분 추출, 원격 전용 파일, 크기 제한과 현재 추출 실패는 자동 갱신의 실행 오류와 구분합니다.
`sync.pending.outdated`는 오래된 projection의 실제 미처리 원인을 집계합니다. 갱신 스크립트는
이 집계와 최종 status가 정확히 일치할 때만 차단된 항목을 주의 사항으로 분류합니다. 명령 실패,
미완료 scan, 처리 가능한 문서의 잔여분과 설명되지 않은 구버전 결과는 계속 오류로 보고합니다.

추출기 전환 후 재색인이 끝나기 전까지 해당 형식의 검색 결과가 일시적으로 줄어들 수 있습니다.
새 추출 실패 시 기존 active projection은 보존하지만, 새 추출기에서 검색 가능한 상태라고
가정하지 않습니다. 성공한 교체 뒤 과거 projection과 unit은 보존하지 않으며, 이전 Context
출처를 새 unit에 추정으로 연결하지 않습니다. 복구가 필요하면 이전 추출기 버전을 복원한 뒤
영향 문서를 다시 추출합니다. 운영 반영은 실제 표본 검증 후 수행하고, 기본 갱신 제한과
원격 파일 미다운로드 정책을 변경하지 않습니다.

하위 프로세스 adapter는 임시 사본의 file descriptor를 입력으로 받고 로컬에서 실행됩니다. 추출기를
바꿀 때에는 변경된 형식의 실제 표본만 확인합니다. 전체 문서 조합을 위한 golden corpus나
별도 평가 framework는 유지하지 않습니다.

## 대용량 로컬 문서

자동 `sync`와 일반 `ingest`는 파일당 250 MiB, 실행당 500 MiB의 상한을 적용합니다. 문서
ID를 정확히 지정한 `ingest --document-id` 요청만 파일당·실행당 1 GiB까지 허용합니다.
대용량 문서는 한 번에 한 파일을 지정합니다. 이 경로도 원본을 직접 수정하지 않고 private
staging 사본을 사용하며 처리가 끝나면 사본을 삭제합니다. 대용량 projection이 현재 상태가
되면 이후 자동 갱신은 원본 관측값이나 adapter가 달라질 때까지 다시 복사하지 않습니다.
