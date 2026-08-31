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

상태 응답의 `partial_extraction.by_category`는 읽기 순서 미확인, 읽지 못한 내용,
미복원 구조, 해석하지 않은 시각 정보, 처리 한도, 지원하지 않는 형식과 추출 실패를
구분합니다. 범주별 문서 수는 서로 중복될 수 있습니다. `verification_only_documents`는
읽기 순서 미확인만 남은 문서 수이며, 이 분류 때문에 기존 warning이나 `partial`을 지우지는 않습니다.

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
  image-only와 mixed PDF는 on-device OCR로 보완합니다. PDFKit과 기존 OCR 처리 뒤에도
  내용이 비어 있는 쪽만 `pypdf` 보조 프로세스로 읽습니다. 보조 추출 결과 때문에 기존
  OCR을 생략하거나, 이미 읽은 그림 속 본문을 교체하지 않습니다.
  보조 프로세스는 선택한 쪽만 읽으며 남은 실행 시간 중 최대 30초, 쪽별 해제된 내용
  스트림 32 MiB를 적용합니다. 메모리를 주기적으로 확인해 1 GiB를 넘으면 프로세스를
  종료합니다. 이는 운영체제가 강제하는 순간 메모리 상한이 아닙니다. 보조 추출 실패 시
  먼저 얻은 본문은 유지합니다. Structured OCR이 오류 없이 빈 결과를 반환해도 기존
  text-region OCR로 다시 확인합니다. PDFKit이 열지 못하지만 유효한
  PDF는 `pypdf` page text fallback으로 복구합니다. `max_edge_pixels`, page count, OCR scope,
  native-text alphanumeric threshold, blank-page detection, fallback backend, language, OS
  runtime과 adapter source identity를 config와 version에 고정합니다. 렌더링한 모든 RGB
  픽셀이 같은 색인 페이지는 관찰된 빈 페이지로 처리하며, 흰색 근처의 밝기만으로
  글자를 제거하지 않습니다. Reading order 미확인은 capability와 quality
  flag로 남기고, 페이지 누락·OCR 실패·budget 도달만 issue와 `partial`로 기록합니다.
  한 번에 최대 200쪽을 처리하며 `pdf_page_range_observed`에 원본의 연속 페이지 범위를,
  `pdf_page_range_pending`에 다음 쪽을 기록합니다. Core는 같은 revision과 adapter의 현재
  unit만 넘겨 이어 처리하며, 합친 결과를 원자적으로 교체합니다. 구간 실행 한도와 누적
  unit·문자 수 한도를 모두 적용합니다. 한 페이지가 결과 한도를 넘으면 그 페이지의 중간
  결과를 완료 범위에 넣지 않습니다. `pypdf` 복구 경로도 같은 페이지 구간을 적용합니다.
  등록된 기존 1.2.0·1.3.0의 정확한 source hash와 같은 host 설정은 성공한 내용의 호환성을
  유지합니다. 이전 버전의 페이지 누락·OCR 실패가 있는 문서는 같은 source revision의
  현재 unit을 그대로 보존하고, unit이 없던 원본 쪽만 다시 읽습니다. 기존에 읽은 쪽의
  OCR을 재실행하지 않습니다. 이전 1.2.0에 처리 범위 기록이 없으면 새로 확인한 전체
  쪽수가 당시의 200쪽 한도 안에 있고 기존 unit의 위치와 일치하는 경우에만 복구합니다.
  `pdf_existing_pages_retained`에 복구한 쪽, 보존한 unit 수와 이전 추출기 정보를 남깁니다.
  확인된 전체 처리 범위도 `pdf_page_range_observed`로 기록합니다.
  확인에 실패하면 기존 색인을 유지합니다. 현재 버전에도 남은 페이지 경고를 자동으로
  무한 재시도하지 않습니다.
  `pdf_page_limit_reached` 문서는 새 구간 추출로 전환합니다.
  로컬 실행파일은 같은 Swift 소스에 같은 ad-hoc 식별자를 사용합니다. 별도 인증서나
  추가 권한은 부여하지 않습니다. 새 소스의 첫 인식에는 운영체제의 모델 초기화 시간이
  필요할 수 있으므로, 배포 때 실제 인식과 기본 시간 한도를 함께 확인합니다.
- `work-corpus.native.office-vision.docx`, `.pptx`, `.hwp`, `.hwpx`: 원본 구조 추출 뒤 선택적으로
  on-device Vision을 사용합니다. 본문이 있는 문서의 그림도 대상으로 하되, 본문에 영숫자가
  32자 미만인 Word 문서 또는 PowerPoint 슬라이드를 먼저 처리합니다. 발표자 노트와
  대체 텍스트는 이 우선순위를 정하는 본문량에 포함하지 않습니다.
  한 번에 그림 16개, 그림당 16 MiB, 읽은 그림 합계 32 MiB와 60초의 OCR 한도를 적용합니다.
  같은 원본 그림과 같은 자르기 영역은 한 구간 안에서 인식 결과를 재사용합니다. 같은 그림의
  다른 자르기 영역은 별개로 인식하며, 원본 바이트는 중복해서 한도에 합산하지 않습니다.
  읽기 전용 패키지에서 꺼낸 임시 그림은 처리 후 삭제합니다. 그림의 가로·세로 곱은
  6,400만 픽셀, 인식용 긴 변은 3,000픽셀로 제한합니다. 원본 대응이 명확한 그림은 보이는
  픽셀만 인식한 뒤 좌표를 원본 그림의 좌표로 되돌립니다. OOXML의 음수 자르기가 만든
  여백은 인식하지 않고, 저장된 그림과 표시 영역이 겹치는 픽셀만 읽습니다. 이 경우
  `source_crop_outset`과 `recognized_scope`를 기록합니다. EXIF 방향과 자르기의 조합,
  원본 대응이 불분명한 복수 그림은 계속 경고로 남깁니다.
  EMF는 파일 전체가 하나의 RGB 비트맵을 그대로 그리는 `EMR_STRETCHDIBITS`로 구성되고,
  원본 비트맵과 전체 출력 영역이 정확히 대응하는 경우에만 비트맵을 꺼내 인식합니다.
  [Microsoft의 레코드 규격](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-emf/89c0d808-0dea-413f-be40-2e9e51fa36ac)에
  따라 위치·크기와 SRCCOPY를 확인하며, 비트맵의 레코드·바이트 위치도 남깁니다.
  별도의 변환 프로그램은 설치하거나 실행하지 않습니다. 여러 레코드의 합성, 벡터 글꼴,
  클리핑, 원본보다 큰 표시 영역과 WMF는 복원하지 않습니다.
  잘리지 않은 EMF의 `EMR_EXTTEXTOUTW`에 저장된 Unicode 문자열은 렌더링과 별개로
  읽습니다. [문자열 레코드 규격](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-emf/dd585d0a-5d7c-4034-963a-1141af836972)에
  따라 파일 길이·레코드 수·종료 레코드와 UTF-16LE 바이트 범위를 대조하며, 옵션이 없는
  문자열만 `image_native_text`로 남깁니다. glyph index나 ANSI 문자열을 Unicode로
  추측하지 않습니다. 각 문자열은 `native_text`이며 `source_metafile`에 원본 레코드와
  문자열의 바이트 위치를 기록합니다. 레코드들을 문장으로 합치거나 화면 좌표·OCR
  신뢰도를 부여하지 않으며, 읽기 순서와 표시 여부 미확인을 unit에 명시합니다.
  기존 그림 처리에서 이미 읽은 바이트만 사용하고, 파일당 그림·바이트·시간 한도를
  유지합니다. 문자열별 50,000 UTF-16 코드 단위, 그림별 문자열 20,000개·총 2,000,000
  코드 단위와 레코드 200,000개를 넘는 결과는 반영하지 않습니다. 이어 처리에서도
  앞서 확인한 문자열을 보존합니다. `office_metafile_text_observed`는 문자열을 읽은
  그림 수이며, 그림 형식 미지원과 시각 내용 미복원 경고는 그대로 남깁니다.
  형식 미지원, 디코딩 실패와 인식 실패를 구분하고 문제의 part·object 위치를 남깁니다.
  디코딩에 성공했으나 Vision이 `InvalidImage`로 거부한 그림은 짧은 변이 32픽셀 미만인
  경우에만 흰 여백을 더해 한 번 다시 인식합니다. 원본 픽셀은 확대하지 않습니다.
  인식된 글자의 영역이 추가 여백에 걸치면 결과를 버리고 실패 사유를 남깁니다.
  성공한 결과에는 `recognition_padding`과 `office_image_ocr_padding_observed`로 처리
  방법을 기록하며, 좌표는 여백을 제거한 원본 영역으로 되돌린 뒤 자르기 좌표와 합성합니다.
  얇다는 이유만으로 그림을 건너뛰거나 글자가 없다고 판정하지 않습니다.
  크기 제한 경고에는 그림 위치, 바이트·픽셀 중 적용한 한도와 관측값을 남깁니다.
  압축 해제 한도에 걸리면 확인한 최소 크기만 기록하고, 픽셀 크기는 읽을 수 있을 때만
  제공합니다. 기본 처리 한도는 늘리지 않습니다.
  `office_image_range_observed`와 `office_image_range_pending`은 현재 처리한 그림 순서와
  다음 위치를 기록합니다. 이어 처리할 때 같은 소스 digest와 adapter인지 확인하고,
  현재 projection의 인식 결과를 보존한 채 합친 결과를 원자적으로 교체합니다. 누적 결과
  한도 때문에 더 담을 수 없으면 미완료 경고를 남기고 반복 재시도하지 않습니다.
  원문 구조를 새로 추출할 때에도 같은 원본 digest, 그림 part와 자르기 영역의 현재 OCR은
  인식 구현·설정이 일치하는 경우 재사용합니다. 처리 범위에 인식 설정의 digest를 남기며,
  설정 기록이 없던 0.16.0은 검증한 정확한 구현·설정 조합만 허용합니다. 글자 없음은 이전
  전체 그림의 처리 내역이 빠짐없이 일치할 때만 재사용합니다. 실패나 미처리 결과를
  글자 없음으로 취급하지 않으며, 원문 구조는 항상 새로 읽고 누적 결과 한도를 적용합니다.
  여백 보완 직전의 정확한 구현에서는 성공했던 OCR 경로가 바뀌지 않았으므로 같은
  인식 설정의 결과를 보존합니다. 이 진단 갱신은 해당 이전 구현의 그림 인식 실패나
  크기 제한이 남은 문서만 한 번 재처리하고, 관련 없는 기존 색인과 출처 참조는 유지합니다.
  OCR은 기존 native unit을 대체하지 않으며,
  `image_text`에 원본 part·object·그림 digest, 그림 내부 좌표와 confidence를 남깁니다.
  그림의 시각적 의미와 배치 전체를 복원했다는 뜻으로 사용하지 않습니다.
  HWP는 그림 레코드의 참조 번호를 DocInfo의 BinData 항목과 대조한 뒤 유일한 내부 스트림만
  읽습니다. 압축 여부는 해당 항목의 원본 플래그를 따르고, 압축 해제에도 같은 바이트 한도를
  적용합니다. CRC와 원래 크기가 일치하는 압축 꼬리만 허용합니다. HWPX는 원본 패키지의
  유일한 내장 항목과 그림의 `binaryItemIDRef`를 대조합니다. 링크 항목은 따라가지 않습니다.
  `imgClip`과 `imgDim`으로 보이는 영역을 계산하고, HWP의 고정된 무효과 그림 레코드에서도
  대응하는 저장 값을 읽습니다. 중립적인 밝기·대비·투명도에서 회색조 효과만 지정한 그림은
  저장된 원본 픽셀을 읽고 효과를 재현하지 않았음을 기록합니다. 가변 효과, 밝기·투명도
  변경, 불명확한 영역은 인식하지 않습니다.
  기존 문단과 개체 unit을 보존하면서 그림 위치에 `image_text`를 추가합니다. 한글 문서도
  같은 `office_image_range_*` 기록과 제한값으로 이어 처리합니다. 이 코드명의 `office`는
  과거에 도입된 이름이며 한글 문서도 포함합니다.
  PowerPoint 표는 명시된 병합 범위와 `hMerge`·`vMerge`가 유일한 시작 셀을 가리킬 때만
  이어지는 셀의 텍스트를 `merged_into`로 연결합니다. 원래 텍스트는 각각 보존하고,
  확인한 연결은 `pptx_table_merge_content_observed`, 불명확한 병합은
  `pptx_table_merge_structure_partial`로 구분합니다.
  PowerPoint의 내장 OLE 개체는 직접 포함된 미리보기 그림을 읽습니다. `AlternateContent`의
  주 표현에 그림이 없을 때에는 주 표현과 대체 표현이 같은 유일한 내장 개체를 가리키는지
  관계 ID로 확인한 뒤 대체 미리보기만 읽습니다. 개체 위치, 관계 ID와 저장된 미리보기라는
  구분을 남기며, 개체의 내부 문서를 실행하거나 현재 내용으로 다시 렌더링하지 않습니다.
  미리보기에 글자가 없거나 지원하지 않는 그림 형식이면 해당 OCR 진단을 유지합니다.
  그림의 자체 표시 크기가 명시적으로 0이고 상속·애니메이션의 영향이 없으면 해당
  인스턴스를 `office_image_not_displayed_observed`로 기록하고 OCR을 생략합니다.
  그룹 안에서는 모든 상위 그룹의 유효한 좌표 변환과 그림의 가로·세로가 모두 0임을
  확인합니다. 같은 그림 part를 사용하는 다른 인스턴스는 각각 확인합니다.
  Word의 그룹 그림은 그룹과 각 그림의 원본 주소, 대체 텍스트, 자르기와 변환을 나누어
  보존합니다. 개별 그림의 관계가 확인되는 경우에만 해당 그림에 OCR을 연결합니다.
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
  공개 규격으로 해석이 확인되지 않은 번호 플래그는 원래 값으로 남기며 번호 형식으로
  단정하지 않습니다.
  수식은 실행하지 않은 원문 수식 문자열로 보존합니다. 지원하지 않는 구조와 필드 의미,
  개체 내부 내용, 미계산 번호는 기능별 issue로 남깁니다.
  `HWPTAG_MEMO_LIST`에 바로 이어진 문단 리스트는 메모 본문으로 구분합니다. 필드의 native
  ID·종류·command는 실행하지 않고 보존하며, `%unk` 중 저장된 명령이 `MEMO`인 필드는
  그 근거를 표시해 메모로 구분합니다. 문단 안의 필드 헤더가 유일하고 시작·끝 제어문자가
  같은 본문 흐름에서 대응할 때만 범위를 연결합니다. 원본 UTF-16 위치와 정규화한 문단의
  Unicode codepoint 위치를 함께 남깁니다. 같은 종류의 헤더가 여러 개이거나 제어문자가
  대응하지 않으면 순서로 추정하지 않고 `hwp_field_range_partial`로 남깁니다.
  메모는 양수인 끝 제어문자 토큰과 메모 헤더 값이 문서 안에서 각각 유일하게 일치할 때만
  연결하며, 이 관찰에 따른 연결 근거도 기록합니다. 나머지는
  `hwp_memo_attachment_unresolved`로 남깁니다. 번호의 공통·수준별 시작값도 보존합니다.
- `work-corpus.hwpx.content-router`: ZIP HWPX의 XML 포함 관계를 직접 읽습니다. 표 안의
  문단을 바깥 문단에 다시 합치지 않으며, 같은 문단의 표 앞뒤 텍스트는 구간별로 보존합니다.
  패키지에 명시된 구역 순서를 우선하고, 순서 정보가 없으면 구역 번호로 정렬합니다.
  잘못된 패키지 순서에는 경고를 남깁니다. 표·셀, 제목·목록, 주석과 개체는 HWP와 같은
  관계 필드를 사용합니다. 확장자가 `.hwpx`이더라도 내용이 바이너리 HWP이면 HWP 추출기로
  위임합니다. 라우터는 위임 경로 전체의 unit type을 선언합니다.
  HWPX 필드의 시작·끝은 `id`와 `beginIDRef`로 연결하고, MEMO의 내부 문단을 `comment`로
  구분합니다. 저장된 필드 결과와 매개변수를 실행하지 않고 읽습니다. 형광펜 표시는 본문을
  보존하는 서식 위치로 기록하며, 모르는 inline 요소는 계속 경고합니다.
  필드 종류가 없더라도 저장된 명령이 `MEMO`이면 그 근거와 함께 메모로 분류합니다.
- `work-corpus.hwp5.rhwp-page-text`: `rhwp` 0.8.2의 `export-text --json`에서 페이지 본문을
  취하는 복구 경로입니다. 원본 경로는 전달하지 않고 읽기 전용 file descriptor를 사용합니다.
  공유 bounded subprocess가 실행 중 stdout·stderr 크기와 실행 시간을 제한합니다.
  구조를 복원하지 못한 결과는 계속 `partial`입니다. 실행 파일은
  `scripts/provision_rhwp.py`가 공식 release archive의 고정된 SHA-256을 확인해 설치합니다.
  `rhwp`의 표 추출은 실제 표본의 비교 검증에 사용하며 운영 색인에 중복 본문을 추가하지 않습니다.

## 구조와 조회

기존 `ExtractionEnvelope` 안에서 `structure_path`를 확장하며 별도 문서 저장소나 과거
projection 보관 계층은 만들지 않습니다.

DOCX·PPTX·HWPX에서 번호 정의와 순서가 명확한 목록은 `computed_list_marker`에 번호·기호,
값, 수준과 계산 근거를 남깁니다. 본문은 고치지 않습니다. 숫자, 로마자와 알파벳 등 확인된
형식만 제한된 값 범위에서 계산하며, HWPX의 독립된 본문 흐름, PPTX의 명시된 텍스트 상자,
DOCX의 유일한 목록 정의를 벗어나 순서나 재시작을 추측하지 않습니다. DOCX에서 여러
목록 인스턴스가 같은 정의를 공유하면 연속 여부를 확정하지 않습니다. `w:start` 생략 시
시작값은 [OOXML 규격](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.startnumberingvalue)에
따라 0으로 처리합니다. 복원하지 못한 번호는 기존 부분 추출 경고로 남깁니다.

PPTX는 같은 텍스트 상자 안에서 번호 속성이 명확한 앞부분을 복원합니다. 상속된
글머리표 때문에 번호를 확정할 수 없는 문단을 만나면 그 문단부터 계산을 멈추고,
앞에서 확인한 번호는 유지합니다. 뒤쪽의 시작값만으로 불명확한 구간을 재연결하지는
않습니다. 번호는 [DrawingML의 자동 번호와 문단 수준](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.drawing.autonumberedbullet)에
따라 계산합니다. 이 변경은 이전의 정확한 구현·설정에서 `pptx_list_marker_partial`이
남은 문서만 한 번 다시 처리하며, 다른 문서의 출처 연결과 OCR 관측은 유지합니다.

- HWP의 `section`과 `record`는 1부터 시작하며 원래 section stream 안의 위치입니다.
  `paragraph_record`는 문단 헤더, `owner_paragraph_record`는 개체를 포함한 문단을 가리킵니다.
  구역 정의 `secd`에 속한 문단 리스트는 바탕쪽의 원본 소속을 기록합니다. 바탕쪽이 실제로
  표시되는 페이지는 추정하지 않습니다. 각주·미주 헤더의 예약 바이트를 주석 번호로 읽지 않고,
  번호가 필요하면 별도로 저장된 자동 번호 control의 값을 구분합니다.
- HWPX의 `section_file`과 `element`는 XML 파일과 자식 인덱스 경로입니다. 같은 문단을
  나눈 텍스트는 `paragraph_element`와 `segment`로 구분합니다. 이 주소는 파일 경로가 아닙니다.
- DOCX·PPTX의 `part`와 `element`는 패키지 내부 XML과 자식 인덱스 경로입니다. Word는
  문단·표·중첩 표를 본문 순서로 보존하고, 실제 셀을 한 번씩 읽어 병합 셀의 중복을 피합니다.
  연결된 머리말·꼬리말·각주·미주·댓글과 글상자도 원문 위치로 구분합니다. PPTX는 발표 순서의
  슬라이드 목록과 그룹 도형을 재귀적으로 읽고 native shape ID와 그룹 좌표 변환을 보존합니다.
  표 셀·병합 범위, 차트의 저장된 값과 SmartArt의 저장된 글자는 별도 unit으로 남깁니다.
  Word 수식은 기존 글자 본문과 함께 OMML 요소의 이름·속성·텍스트·원본 주소를
  `math_structure`에 보존합니다. 분자·분모, 위첨자·아래첨자 등의 원문 포함 관계를
  확인할 수 있지만 수식을 실행하거나 시각 배치를 재현하지는 않습니다. 수식 하나당
  요소 512개와 메타데이터 64 KiB를 넘으면 기존 본문을 유지하고 구조 한도를 알립니다.
  SmartArt의 `diagram_structure`는 글자가 없는 점도 포함하여 원본 `pt`와 `cxn`의 주소와
  속성을 저장합니다. [Microsoft의 연결 규격](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.drawing.diagrams.connection)에
  따른 시작·끝 점의 ID가 각각 유일할 때만 점의 주소를 연결합니다. 누락된 관계 종류나
  화면의 도형 배치는 추정하지 않습니다. 도표 하나당 점과 연결의 합계 512개, 메타데이터
  64 KiB 안에서 저장하며, 한도를 넘거나 대응이 불명확해도 기존 글자는 유지합니다.
  외부 링크, workbook과 필드 명령은 열거나 실행하지 않습니다. XML 순서와 화면의 읽기 순서는
  구분하며, 지원하지 않는 구조와 미계산 목록 번호는 경고합니다.
- `table`, `cell`, `note`, `object`는 해당 section 안의 구조 위치입니다. Corpus의 unit ID나
  영구 식별자를 추출기가 대신 생성한 값이 아닙니다. 행·열은 0부터 시작하고 병합 범위는
  원문 값을 그대로 보존합니다. 범위 밖 셀이나 겹침을 발견하면 값을 조용히 보정하지 않습니다.
- 빈 표와 셀도 구조 unit으로 보존하되 텍스트 검색에는 넣지 않습니다. 같은 값이 다른 셀에
  반복되면 각각 보존합니다. 검색 대상은 본문과 상대 파일명이며 내부 좌표는 검색어로 취급하지 않습니다.
- `corpus_file_read`의 `include_structure_context=true`는 지정한 셀의 행, 원문에 제목 셀로
  표시된 셀, 표 구조와 캡션, 연결된 주석·개체를 기존 읽기 제한 안에서 함께 반환합니다.
  기본값은 `false`이며 `neighbor_span`의 앞뒤 순번 조회는 그대로 유지합니다. 구조 조회가
  제한을 넘으면 일부를 조용히 잘라내지 않고 더 좁은 셀이나 일반 조회를 요청하도록 오류를 반환합니다.

HWP의 `atno`·`nwno`, HWPX의 `autoNum`·`newNum`에는 저장된 번호, 종류와 원본 위치를
기록합니다. HWPX 목록의 전체 시작값과 수준별 시작값도 구분합니다. 이 값은 원문에 저장된
속성이며, 문단 목록의 현재 순번이나 표시 문자열을 계산한 결과가 아닙니다. 목록 재시작과
수준별 증가 규칙을 확인할 수 없는 경우 번호를 만들어 본문에 붙이지 않습니다.

페이지의 시각 배치와 읽기 순서는 이 구조 추출만으로 보장하지 않습니다.
삽입된 외부 문서의 본문 추출과 필드 실행은 하지 않으며, 확인되지 않은 목록 번호를 추정하지 않습니다. 실제로 존재하지만
복원하지 못한 내용은 기능별 경고와 `partial`로 남기며, 단순히 경고를 제거해 완료로 만들지 않습니다.

## 갱신 결과와 버전 전환

부분 추출, 원격 전용 파일, 크기 제한과 현재 추출 실패는 자동 갱신의 실행 오류와 구분합니다.
로컬 `status.partial_extraction`과 갱신 스크립트의 최종 JSON은 형식별 문서 수와 문제 코드별
문서 수를 제공합니다. 한 문서에 여러 문제가 있으면 코드별 집계가 겹칩니다. OCR의 무검출·크기
제한·시간 제한·실패와 구조 미복원은 각각 다른 코드로 보존합니다.
`sync.pending.outdated`는 오래된 projection의 실제 미처리 원인을 집계합니다. 갱신 스크립트는
이 집계와 최종 status가 정확히 일치할 때만 차단된 항목을 주의 사항으로 분류합니다. 명령 실패,
미완료 scan, 처리 가능한 문서의 잔여분과 설명되지 않은 구버전 결과는 계속 오류로 보고합니다.

추출기 전환 후 재색인이 끝나기 전까지 해당 형식의 검색 결과가 일시적으로 줄어들 수 있습니다.
새 추출 실패 시 기존 active projection은 보존하지만, 새 추출기에서 검색 가능한 상태라고
가정하지 않습니다. 성공한 교체 뒤 과거 projection과 unit은 보존하지 않으며, 이전 Context
출처를 새 unit에 추정으로 연결하지 않습니다. 복구가 필요하면 이전 추출기 버전을 복원한 뒤
영향 문서를 다시 추출합니다. 운영 반영은 실제 표본 검증 후 수행하고, 기본 갱신 제한과
원격 파일 미다운로드 정책을 변경하지 않습니다.

같은 HWP 원본에서 이미 추출한 문단·표 구조가 있는데 주 추출기 오류로 페이지 텍스트만
반환되면 기존 구조 색인을 교체하지 않습니다. 주 추출기 오류 때문에 페이지 텍스트로
색인된 문서는 기본 제한 안에서 구조 추출을 다시 시도합니다. 다시 실패하면 현재 색인을
보존하고 현재 추출 실패로 분류하여 자동 재시도를 멈춥니다. 배포용 HWP의 정상적인
페이지 텍스트 경로는 이 복구 대상에 포함하지 않습니다.

하위 프로세스 adapter는 임시 사본의 file descriptor를 입력으로 받고 로컬에서 실행됩니다. 추출기를
바꿀 때에는 변경된 형식의 실제 표본만 확인합니다. 전체 문서 조합을 위한 golden corpus나
별도 평가 framework는 유지하지 않습니다.

## 대용량 로컬 문서

자동 `sync`와 일반 `ingest`는 파일당 250 MiB, 실행당 500 MiB의 상한을 적용합니다. 문서
ID를 정확히 지정한 `ingest --document-id` 요청만 파일당·실행당 1 GiB까지 허용합니다.
대용량 문서는 한 번에 한 파일을 지정합니다. 이 경로도 원본을 직접 수정하지 않고 private
staging 사본을 사용하며 처리가 끝나면 사본을 삭제합니다. 대용량 projection이 현재 상태가
되면 이후 자동 갱신은 원본 관측값이나 adapter가 달라질 때까지 다시 복사하지 않습니다.
