# Work Surface

Flow 작업 화면에 쓰는 공통 콘텐츠·배치 모듈입니다. UI Kit의 기본 스타일을 사용하며, Flow 앱과 Toolkit 웹에서 같은 구성 요소를 씁니다. 배치는 에이전트가 작업물의 목적과 내용에 맞춰 구성합니다. 공통 모듈을 제공하는 목적은 사용자용 배치 편집기를 만드는 것이 아닙니다. HTML과 기존 문서·이미지·도식·콘텐츠 작업물의 읽기 화면은 `ArtifactPreview`를 함께 사용합니다.

`ReviewComparison`은 현재 작업물과 수정안을 넓은 화면에서 나란히, 좁은 화면에서 위아래로 보여줍니다.

## 공통 구성 요소

| 구분 | 구성 요소 | 적용 범위 |
| --- | --- | --- |
| 기본 스타일·조작 | UI Kit 토큰·레이아웃, Apps SDK UI 버튼·입력·메뉴, UI Kit `FieldSelect`·`Dialog` | 색상·글꼴·간격·컨트롤은 설치된 UI Kit 2.2.2을 사용합니다. Flow 전용 스타일은 콘텐츠 배치에 한정합니다. |
| 화면 구조·탐색 | AppHeader, WorkCanvas, LibraryBrowser, ReferencePanel, BrowseToolbar, ThemeSetting, Disclosure, ListItemAction | 로컬판과 웹판의 작업공간, 라이브러리, 파일 탐색을 공유합니다. 주 작업물만 표시하고 참고 자료는 따로 엽니다. ReferencePanel은 현재 작업의 연결만 표시하며 라이브러리 전체를 섞지 않습니다. |
| 작업물 구성 | `SurfaceHeader`, `WorkSurface` | 에이전트가 정한 제목 위계와 콘텐츠 구성을 표시합니다. 폭과 순서를 바꾸는 메뉴는 기본 작업 화면에 두지 않습니다. |
| 콘텐츠 표시 | `contentRenderers`, `ArtifactPreview`, `FilePreview`, `TextContentView`, `HtmlContentView`, `MediaPlayer`, `PdfPreview` | 작업 화면, 자료 읽기, 보관함, 미리보기에서 같은 표시 요소를 사용합니다. |
| 기존 편집기 호환 | EditorLayout, EditorActions, CompositionEditor, ContentFields, ImageRegionEditor, DiagramFields | 기존 형식 및 제작 예시에 필요한 호환 구성입니다. 기본 작업 화면에는 노출하지 않습니다. |
| 검토 | `ReviewComparison`, `ImageDialog` | 수정 전후 비교와 원본 확대에 사용합니다. |

### 화면 배치

- 화면 전체 이동과 현재 작업의 도구는 하나의 상단바 안에서 구분합니다. 좁은 화면에서는 주요 화면 메뉴와 추가 작업 메뉴로 접습니다. 제목과 본문 사이에는 절 간격을, 제목과 부가 정보 사이에는 항목 간격을 적용합니다.
- 검색·필터·보기 전환은 목록 바로 위에 둡니다. 검색과 필터의 컨트롤 크기를 맞추고, 자료 목록과 파일 목록은 같은 UI Kit 표 형식을 사용합니다.
- 같은 자료에 적용하는 실행 버튼은 하나의 `su-row`에 모으고, 폭이 부족할 때만 다음 줄로 넘깁니다. 수정 중에는 저장과 취소만 표시합니다. `LibraryBrowser`는 별도 실행 행을 붙이지 않으며, 자료 표시 컴포넌트가 수정 상태와 버튼 배치를 함께 맡습니다. `ReferencePanel`의 `actions`는 원본 보기의 실행 영역에 전달해 긴 본문 뒤로 떨어지지 않게 합니다.
- `SurfaceHeader`는 제목, 선택적인 보조 정보와 도구를 받습니다. 제목의 단계와 문구는 해당 화면에서 정합니다.
- `EditorLayout`은 입력 영역과 미리보기를 분리합니다. 좁은 화면이나 좁은 작업 영역에서는 입력 다음에 미리보기를 표시합니다.
- 본문 배치는 `composition.rows`에 저장합니다. 내용 편집기는 배치 조작을 제공하지 않습니다. 편집기의 펼침 상태는 저장된 콘텐츠를 바꾸지 않습니다.
- 유형별 생성 메뉴를 늘리지 않습니다. 새 콘텐츠의 표시는 렌더러로 확장하고, 편집 도구는 해당 내용을 선택했을 때 제공합니다.

## 작업물

작업은 자료를 참조하며 결과를 만드는 공간입니다. 자료를 열거나 연결해도 작업물 본문은 바뀌지 않습니다. Flow 앱에서는 자료를 별도 패널에서 읽고, 파일 연결은 원본 경로를 보존합니다. 자료로 새 작업을 시작하면 참고 관계와 빈 작업물을 만듭니다. 보관한 작업물의 명시적 복사는 별도 동작입니다.

### HTML 작업물

에이전트는 UIKit의 토큰과 공통 요소를 사용해 내용에 맞는 HTML을 직접 구성합니다. 문서마다 적절한 제목 위계, 여백, 콘텐츠 비중과 반응형 배치를 정합니다. 기존 HTML을 아래의 구조화 블록으로 다시 작성할 필요는 없습니다.

HTML 작업물의 저장 계약은 다음과 같습니다.

```json
{
  "kind": "html",
  "title": "검사 결과",
  "html": "<!doctype html><h1>검사 결과</h1><img src='images/part.png' alt='검사 대상'>",
  "assets": [
    {"name": "images/part.png", "src": "/api/flow/assets/<sha256>.png"}
  ]
}
```

- `html`은 UTF-8 기준 2 MiB 이하, `assets`는 최대 24개입니다. 자산 이름은 상대경로이며 중복이나 상위 경로를 허용하지 않습니다. `src`는 `flow_asset_import`가 반환한 콘텐츠 해시 주소를 사용합니다. 예시의 `<sha256>`는 실제 반환값으로 바꿉니다.
- 이미지, 오디오, 영상, PDF를 불변 자산으로 연결할 수 있습니다. `src`, `poster`, `href`, `srcset`과 CSS `url()`에 사용한 자산 이름을 해석합니다. JavaScript 안의 자산 경로 문자열은 자동 변환하지 않습니다.
- CSS와 스크립트는 HTML 안에 포함합니다. 구조화 블록으로 변환할 필요가 없습니다. 외부 스크립트·스타일·프레임은 실행하지 않습니다.
- `HtmlArtifact`는 중첩 샌드박스로 표시합니다. 안쪽 프레임은 인라인 스크립트와 문서에 묶인 자산만 사용하고, 바깥 프레임은 네트워크 주소로의 이동을 제한합니다. 계정·파일 접근, 외부 요청과 폼 전송을 허용하지 않습니다.
- 화면 테마는 `html[data-theme="light"]`와 `html[data-theme="dark"]`로 전달합니다. 테마가 바뀌어도 문서를 다시 열거나 입력값을 초기화하지 않습니다.
- `flow_change_submit`의 `replace`는 요청한 수정을 같은 ID와 기준 버전에 적용합니다. `proposal`은 아직 선택하지 않은 대안을 검토할 때 사용합니다.
- `exportHtmlArtifact`는 자산을 포함한 독립 HTML을 반환합니다. 원자료를 이동하거나 파일을 자동 저장하지 않습니다.
- 파일 탐색의 `HtmlContentView`는 원본을 읽는 제한된 미리보기입니다. 에이전트가 제출한 상호작용 HTML 작업물과 구분합니다.

### 기존 구조화 작업물

기존 content, document, image, diagram, blank 작업물은 내용과 ID를 유지해 읽습니다. content의 blocks와 rows 계약 및 아래 렌더러는 호환과 에이전트 제작에 사용할 수 있습니다. 새 작업물에 이 형식을 강요하지 않습니다. 기존 편집기는 기본 화면에서 제외하며, 작업물 자체의 계산 입력·확대·재생 같은 조작은 유지합니다.

## 파일 추가

파일은 종류별 생성 메뉴 없이 탐색하고 미리봅니다. 참고 자료로 연결해도 주 작업물은 바뀌지 않습니다. 에이전트는 요청된 내용을 작성할 때 원본을 참조합니다. 새로운 파일 표시 방식은 렌더러로 확장하되 기존 작업물과 파일 경로는 보존합니다.

`workspaceFilePresentation(path)`는 표시 방식, 응답 형식과 크기 제한을 반환합니다. 파일 선택기와 표시 화면은 같은 정의를 사용합니다. `FilePreview`는 `filePreviewRenderers`에 등록된 보기로 파일을 표시합니다. 새로운 파일 보기는 `WorkSurface`의 `context.resolveFilePresentation`과 `context.filePreviewRenderers`로 연결할 수 있으며, 파일 추가 메뉴나 저장된 `file` 블록 형식을 바꿀 필요가 없습니다. 파일 읽기 권한과 서버의 형식 확인은 그대로 적용됩니다. 보기 기능이 없는 연결은 원본 링크로 남습니다.

## 블록

각 블록은 `{id, kind, content}` 형식입니다. `id`는 한 화면에서 고유해야 합니다. 기본 렌더러는 다음 블록을 지원합니다.

| kind | content의 주요 필드 |
| --- | --- |
| `heading` | `title`, `description?` |
| `text` | `heading?`, `paragraphs[]` |
| `image` | `src`, `alt`, `caption?`, `selection?`, `width?`, `height?`, `crop?` |
| `comparison` | `heading`, `items[]` — 각 항목의 `label`, `src`, `alt`, `caption?`, `view?` |
| `diagram` | `heading?`, `nodes[]`, `edges[]` — 항목과 연결, 위치 |
| `media` | `heading?`, `src?`, `poster?`, `alt?`, `caption?` |
| `table` | `heading?`, `columns[]`, `rows[][]` |
| `metrics` | `heading?`, `items[]` — `label`, `value`, `unit?`, `detail?` |
| `chart` | `heading?`, `items[]` — `label`, 0 이상의 숫자 `value`, `unit?`; 블록의 `unit?`, `caption?` |
| `gallery` | `heading?`, `images[]` — `src`, `alt`, `caption?` |
| `steps` | `heading?`, `steps[]` — `title`, `text?` |
| `references` | `heading?`, `items[]` — `title`, `detail?`, `href?` |
| `file` | `name`, `type?`, `size?`, `description?`, `href?` |
| `code` | `heading?`, `language?`, `code` |
| `audio` | `heading?`, `src`, `caption?`, `transcript?` |
| `resource` | `reference`, `title`, `detail?` — Toolkit 자료의 원본 식별자와 표시 이름 |

`resource`는 Journal·Library·Sense·Corpus 문서와 원자료·Design·Host 파일을 원본 식별자로 연결합니다. Corpus 원자료의 본문은 작업물에 복사하지 않고 읽기 전용으로 표시합니다. Flow 앱에서는 자료 이름을 표시하고, Toolkit 웹에서는 현재 원본 내용을 작업 화면에 표시합니다. 일반 콘텐츠 추가 목록이 아닌 연결 자료에서 선택합니다.

이미지·영상·오디오·파일을 교체해도 다른 콘텐츠와 배치는 유지됩니다. 이미지를 바꾸면 이전 자르기 범위와 이미지 비교 항목의 확대 위치는 초기화됩니다.

`file`은 연결된 작업공간의 이미지와 영상·오디오를 해당 보기로, PDF를 쪽별로, Markdown 파일은 문서와 원문으로, CSV·TSV 파일은 표와 원문으로, 다른 텍스트 파일은 원문으로 화면 안에 보여줍니다. Markdown 문서와 같은 작업공간에 있는 PNG·JPEG·GIF·WebP·AVIF 이미지는 상대경로로 표시할 수 있습니다. Flow의 자료 읽기 화면도 같은 `MarkdownContent`를 사용합니다. 편집 화면의 파일 추가에서 지원하는 파일을 골라 넣을 수 있습니다. HTML은 스크립트를 실행하지 않는 미리보기와 원문으로 표시합니다. Flow의 파일 화면에서는 원본을 미리보고 참고 자료로 연결합니다. 파일을 선택했다는 이유로 작업물 본문에 넣지 않습니다. 지원하지 않는 파일 주소는 열기 링크로 표시합니다. `media`는 영상 `src`가 있으면 재생기를, `poster`만 있으면 이미지를 표시합니다. `image`의 `selection`과 `crop`은 원본 크기에 대한 비율 좌표 `{x,y,width,height}`입니다. `crop`을 사용하려면 이미지 너비·높이도 저장합니다. `comparison`의 `view`는 확대 비율 `scale`과 이미지 위치 `x,y`를 지정합니다. `references`와 `file`의 링크에는 웹 주소, `/`·`./`·`../`로 시작하는 경로 또는 화면 내 앵커를 사용합니다.

선택, 확대 등 화면 상태는 `WorkSurface`의 `context`로 전달합니다. 이미지·이미지 모음·영상 포스터는 기본적으로 `ImageDialog`에서 원본을 확대합니다. 화면이 자체 확대 동작을 제공한다면 `onOpen(block)` 또는 `onOpenMedia(image)`로 대신할 수 있습니다. 선택 영역 전환에는 `onSelectionToggle(block)`을 사용합니다.

## 배치

`rows`의 행마다 `columns`를 둡니다. 열의 `span`은 12칸 기준 1–12이고, `ids`는 열 안에 세로로 놓을 블록을 가리킵니다. 한 행의 열 너비 합계는 12 이하이며, 모든 블록은 정확히 한 번 배치해야 합니다. 좁은 화면에서는 열이 읽기 순서대로 쌓입니다. 도식 항목의 가로·세로 재배치에는 `layoutDiagramNodes`를 사용합니다.

- `defineComposition({blocks,rows})`: 행과 열을 직접 정합니다.
- `stackComposition(blocks)`: 블록을 한 줄씩 놓습니다.
- `packComposition(blocks,spanOf)`: 각 블록의 너비에 따라 행을 채웁니다.
- `composition-editor.js`: 콘텐츠 추가·삭제·이동, 열 너비 변경, 윗줄에 새 열 추가·기존 열 아래에 이어 놓기·줄 분리를 기존 콘텐츠를 보존하면서 처리합니다. 이 함수들은 에이전트의 구성과 기존 데이터 호환에 사용하며, 기본 UI의 배치 조작으로 노출하지 않습니다.

```jsx
import {WorkSurface,contentRenderers,defineComposition} from '@personal-agent/flow-surface';

const blocks=[
 {id:'lead',kind:'heading',content:{title:'검사 이미지'}},
 {id:'photo',kind:'image',content:{src:'/examples/metal.png',alt:'금속 부품 상단'}},
 {id:'notes',kind:'text',content:{heading:'관찰',paragraphs:['오른쪽 표면에 흠집이 보인다.']}}
];
const composition=defineComposition({blocks,rows:[
 {id:'intro',columns:[{span:12,ids:['lead']}]},
 {id:'body',columns:[{span:8,ids:['photo']},{span:4,ids:['notes']}]}
]});

<WorkSurface composition={composition} renderers={contentRenderers} label="검사 이미지" />;
```

지도, 전용 편집기처럼 형식별 동작이 필요한 콘텐츠는 고유한 `kind`와 렌더러를 등록합니다. 기존 `contentRenderers`를 펼쳐 추가하면 기본 블록과 함께 사용할 수 있습니다. 콘텐츠 유형 목록을 작업 생성 메뉴로 사용하지 않습니다.

기존 예시 데이터는 `src/examples`에 보존합니다. 이전 예시 URL은 현재 Flow 화면을 엽니다.

## 기존 문서·도식 전환

기존 문서·도식·이미지는 `contentFromArtifact`로 콘텐츠 작업 화면에 옮길 수 있습니다. 작업물 ID·제목·버전과 문서 본문, 도식의 항목·연결, 이미지의 원본·자르기 범위를 유지합니다. 전환한 뒤 같은 화면에 다른 콘텐츠를 넣고 배치할 수 있습니다. `appendBlocksToArtifact`는 빈 작업면과 기존 콘텐츠뿐 아니라 전환 가능한 문서·이미지·도식에도 새 콘텐츠를 추가합니다. 기존 작업물의 ID와 제목, 이미지 원본과 자르기 범위, 도식의 항목과 연결은 유지됩니다. `contentBlockCapacity`는 화면에 더 넣을 수 있는 블록 수를 알려줍니다. 발표 형식의 문서는 현재 전환 대상이 아닙니다.

## 이미지 영역

`ImageDialog`는 이미지 블록, 독립 이미지 작업물과 파일 미리보기에서 원본을 크게 확인하는 공통 보기입니다. 기본 읽기 화면에서는 UI Kit 대화상자를 사용하며, 화면이 자체 확대 동작을 제공하면 그 동작을 유지합니다.

`ImageRegionEditor`는 이미지에서 영역을 끌어 선택하거나 백분율로 입력하는 공통 편집 요소입니다. Flow 앱과 Toolkit 웹의 이미지 작업물에 같은 영역 좌표를 적용합니다. `imageRegionBetween`은 포인터 좌표를 0–1 범위로 제한하고, `validImageRegion`은 선택 영역이 이미지 안에 있는지 확인합니다.

## 도식

`DiagramCanvas`는 도식의 항목·연결을 표시합니다. 읽기 화면에서는 `readOnly`를 사용하고, 편집 화면에서는 선택 항목과 위치 변경을 `onSelect`, `onMoveNode`로 받습니다. 항목은 끌어서 옮기거나 방향키로 이동할 수 있습니다. `layoutDiagramNodes`는 항목의 내용과 연결을 바꾸지 않고 가로·세로 위치만 다시 잡습니다. Flow 앱과 Toolkit 웹은 같은 도식 요소를 사용하며, 저장 방식은 각 화면에서 처리합니다.

## 영상·소리

`MediaPlayer`는 영상과 소리의 재생기를 함께 다룹니다. 콘텐츠 블록과 작업에 연결한 파일 미리보기에서 같은 재생기를 사용합니다. MP3·WAV·OGG·M4A·AAC 오디오와 MP4·WebM 영상을 선택할 수 있습니다. 재생이 실패하면 파일을 열 수 없다는 메시지를 표시합니다.

로컬에서 정본 자료를 열 때에는 `TOOLKIT_FLOW_TOOLKIT_URL`에 등록된 Toolkit 웹의 동일한 Flow 자료 경로를 사용합니다. HTML 샌드박스에 계정 정보를 넘기거나 다른 등록 폴더의 경로를 현재 작업공간 경로로 해석하지 않습니다.

`workReferences`는 작업의 자료 ID와 원본 연결을 하나의 목록으로 구성합니다. 같은 자료를 라이브러리와 원본에서 중복 연결했다면 한 항목으로 표시하되, 별도로 정리한 내용은 합치거나 지우지 않습니다. 연결을 해제해도 자료 원본과 라이브러리, 작업물은 보존합니다. 원본 연결 오류는 해당 항목에서 다시 열 수 있게 하며 목록에서 숨기지 않습니다.

상호작용 HTML은 UIKit의 `ui_kit.py screen App.jsx output.html --title "제목"` 명령으로 생성합니다. `UIKitRoot colorScheme="inherit"`는 Flow의 밝기 설정을 따릅니다. 테마 변경은 메시지로 전달하며 HTML을 다시 탑재하지 않습니다.
