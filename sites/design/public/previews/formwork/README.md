# 거푸집(Formwork)

회색 패널, 청사진 파랑과 주황 강조색을 사용하는 디자인이다. 구성 요소,
연결 관계와 절차를 보여 주는 데 어울린다.

- [미리보기](index.html)
- [형식별 규칙](formats.json)
- [HTML 틀](/templates/formwork/templates/report.html)
- [문서 사이트 HTML 틀](/templates/formwork/templates/document-site.html)
- [디자인 원칙](DESIGN.md)

## 원칙

1. 제목, 구성, 절차가 구획만 보아도 드러나게 배치한다.
2. 파랑은 구조와 연결에, 주황은 주의가 필요한 한 곳에 사용한다.
3. 표와 도식에는 짧은 제목과 설명을 붙인다.
4. 수치와 짧은 라벨은 가지런히 정렬한다.
5. 장식용 격자나 배지는 넣지 않고 실제 관계와 상태를 보여 줄 때만 사용한다.
6. 문서 사이트는 상위 메뉴, 같은 영역의 문서와 현재 문서 목차를 분리한다.
7. 구조물에는 단색 오프셋 그림자, A4 문서지에는 확산 그림자를 사용한다.

## 형식

문서는 제목, 구성과 절차 순서로 배치한다. 슬라이드는 한 개의 도식이나 표를
중심으로 구성한다. 이미지는 회색 또는 짙은 파랑 바탕 가운데 하나를 고르고,
설명 문장은 이미지 밖의 캡션으로 분리한다. 웹·앱에서는 넓은 표와 도식만 해당
영역 안에서 가로로 움직이게 한다. 여러 기술 문서를 제공하는 사이트는 고정된
세 구획의 상단 셸과 A4 페이지 묶음을 사용한다. 자세한 값은
[formats.json](formats.json)을 따른다.

## 파일

| 파일 | 내용 |
|---|---|
| `tokens.css` | 회색 표면, 파랑과 주황, 서체와 크기 |
| `base.css` | 본문, 표, 코드와 인쇄 |
| `doc.css` | 제목 정보, 구획, 패널, 도식, 목록과 상태 |
| `site.css` | 상위 셸, 문서 전환·목차, A4 페이지 묶음과 반응형 규칙 |
| `assets/` | 흐름도 이미지 |
| `formats.json` | 웹·앱, 문서, 슬라이드와 이미지 규칙 |

## 주요 클래스

- 머리: `.titleblock`, `.tb-cell`, `.doc-head`, `.lead`
- 구획: `.sec`, `.sec-head`, `.sec-no`, `.sec-lede`, `.dim`, `.docfoot`
- 면: `.panel`, `.panel-grid`, `.stat-row`, `.stat`
- 그림: `.fig`, `.fig-body`, `.fig--blueprint`, `.fig-caption`
- 표와 목록: `.table-wrap`, `td.num`, `.spec`
- 상태: `.status-good`, `.status-warn`, `.status-bad`, `.badge`, `.callout-safety`, `.callout`
- 문서 사이트: `.fw-site-header`, `.fw-global-bar`, `.fw-global-nav`, `.fw-local-nav`
- 문서 이동: `.fw-sibling-nav`, `.fw-document-toc`, `.fw-local-actions`
- 페이지 묶음: `.fw-sheet-stack`, `.fw-sheet`, `.fw-sheet-footer`
- 연결 모듈: `.fw-module-grid`, `.fw-structure-panel`

## 개정 기록

- **v2.3 (2026-08-27)** — 문서 사이트 셸, 문서 전환과 목차의 분리, A4 페이지 묶음, 구조물·종이 그림자 역할과 390px 상위 메뉴 규칙을 추가했다.
- **v2.2 (2026-08-26)** — 참고 콜아웃(`.callout`)을 추가하고 검증 어휘를 공용 사전에 맞췄다.
- **v2.1 (2026-08-19)** — 질감용 벡터를 제거하고 흐름도를 이미지 애셋으로 바꿨다.
- **v2.0 (2026-08-19)** — 특정 대상에 묶인 표현을 없애고 네 형식의 규칙을 추가했다.
- **v1.0 (2026-07-29)** — 회색 패널, 청사진 도식과 주황 강조색을 등록했다.
