# Corpus native helpers

macOS의 File Provider 자료와 PDF 추출을 맡는 native subprocess입니다. 빌드 결과는 로컬 runtime에 두며 Git에 포함하지 않습니다.

## File Provider capture

```sh
corpus-file-provider probe --source /absolute/source/file

corpus-file-provider copy \
  --source /absolute/source/file \
  --source-fd 8 \
  --source-root /absolute/read-only/corpus \
  --destination /absolute/private/staging/file \
  --max-bytes 1048576
```

성공 결과는 stdout, 오류는 stderr에 JSON 객체 하나로 기록합니다.

`probe`는 `NSFileCoordinator.ReadingOptions.immediatelyAvailableMetadataOnly`와 `lstat`으로 File Provider 상태와 파일 metadata를 읽습니다. 본문은 열지 않습니다.

`copy`는 Python이 `openat`과 `O_NOFOLLOW`로 연 파일 descriptor에서 private staging 파일로 복사합니다. 복사 한도는 `--max-bytes`이며, source identity와 version, byte count가 달라지면 staging 파일을 지웁니다.

Python runtime은 staging directory descriptor와 destination name을 함께 전달할 수 있습니다. 이 경우 helper는 `openat`과 `unlinkat`으로 staging 파일을 다룹니다.

Caller가 맡는 범위는 다음과 같습니다.

- 파일별 timeout, 전체 byte budget과 concurrency
- private staging directory 생성
- staged copy의 hash와 parsing
- timeout 뒤 hydration 상태 처리

Helper 반환 뒤 Python은 Source root와 대상 파일을 다시 열어 처음 관찰한 객체와 같은지 대조합니다. 현재 지원 범위는 resident file capture와 실패 시 중단입니다.

### Build

```sh
swiftc -O \
  src/corpus/native/corpus_file_provider.swift \
  -o runtime/native/corpus-file-provider
```

## PDFKit + Vision extraction

`src/corpus/native/corpus_pdf_vision.swift`는 `work-corpus.native.pdfkit-vision` adapter의 JSONL subprocess입니다. 전달받은 `/dev/fd/N` staged copy만 열며, PDFKit으로 본문과 페이지 이미지를 만들고 Vision으로 문단과 표 셀을 추출합니다.

macOS 26 이상과 호환 SDK에서는 `RecognizeDocumentsRequest`, 그 밖에는 `VNRecognizeTextRequest`를 사용합니다. Python adapter는 source hash를 반영한 실행 파일을 private Corpus runtime에 빌드합니다.

Swift 코드를 바꾼 경우 다음 명령을 사용합니다.

```sh
swiftc -parse-as-library -typecheck \
  src/corpus/native/corpus_pdf_vision.swift
```
