# Corpus native helpers

## File Provider capture

This macOS-only helper is the narrow boundary between a File Provider-backed
source tree and the Corpus staging area.

It has two operations:

```sh
corpus-file-provider probe --source /absolute/source/file

corpus-file-provider copy \
  --source /absolute/source/file \
  --source-fd 8 \
  --source-root /absolute/read-only/corpus \
  --destination /absolute/private/staging/file \
  --max-bytes 1048576
```

Each successful invocation writes one JSON object to standard output. A
failure writes one JSON object to standard error and exits nonzero.

`probe` coordinates the URL with
`NSFileCoordinator.ReadingOptions.immediatelyAvailableMetadataOnly` and only
uses `lstat`; it never opens the file body. Its result reports the dataless
flag, logical and allocated sizes, timestamps, device, inode, file type, and
whether metadata changed during coordination.

`copy` is the explicit materialization boundary. Python first traverses the
registered source root component by component with `openat` and `O_NOFOLLOW`,
then passes that exact securely opened regular file as `--source-fd`. The
helper never reopens the source path and copies only from the inherited
descriptor. It creates a new destination with mode `0600`, copies no more
than the required `--max-bytes` ceiling,
sequentially, flushes it, and verifies source identity/version
(device, inode, logical size, and nanosecond modification time) and the exact
source/destination byte count. A failed or unstable copy removes the
incomplete destination.

The Python runtime also passes an already verified private staging
directory as `--destination-dir-fd` with one `--destination-name`. In that
mode the helper creates and removes the staged file with `openat`/`unlinkat`;
the absolute `--destination` remains only the independently checked boundary
and diagnostic path.

The caller remains responsible for:

- enforcing per-file timeout, run byte budget, and concurrency;
- passing the approved per-file byte ceiling to `--max-bytes`;
- creating a private staging directory outside the synchronized source tree;
- treating a timeout as an unknown hydration state, because the provider may
  continue work after the helper is terminated;
- hashing and parsing only the verified staged copy.

After the helper returns, Python securely reopens the registered source-root
path component by component, requires its original directory identity, and
opens the relative file from that freshly pinned root. The capture is discarded
unless the registered root and file still name the observed objects. Remote-only
File Provider fault-in through the inherited descriptor remains unverified
after this boundary change; resident capture and fail-closed outcomes are the
current supported claim.

Build:

```sh
swiftc -O \
  src/corpus/native/corpus_file_provider.swift \
  -o runtime/native/corpus-file-provider
```

The compiled binary is a local build artifact and should not be committed.

## PDFKit + Vision extraction

`src/corpus/native/corpus_pdf_vision.swift` is the packaged JSONL subprocess behind the
`work-corpus.native.pdfkit-vision` persisted adapter identity. It only opens the inherited
`/dev/fd/N` staged copy. PDFKit emits native page text and renders page images;
Vision emits OCR paragraphs and table cells with normalized geometry and
line-derived confidence.

On macOS 26+ and a compatible SDK it uses `RecognizeDocumentsRequest`.
`VNRecognizeTextRequest` remains the compiled fallback. The Python adapter
builds a source-hashed executable in the private Corpus runtime, not in
the registered source tree.

PDF 또는 native helper를 실제로 바꾼 경우에만 type-check합니다.

```sh
swiftc -parse-as-library -typecheck \
  src/corpus/native/corpus_pdf_vision.swift
```
