# 한지(Hanji)

요약, 자료와 설명을 빠르게 읽게 하는 디자인이다. 따뜻한 바탕, 산세리프
활자와 가는 구분선을 사용한다.

- [미리보기](styleguide.html)
- [형식별 규칙](formats.json)
- HTML 틀: [간단한 보고서](templates/brief.html) · [표를 포함한 보고서](templates/artifact.html)
- [디자인 원칙](DESIGN.md)

## 원칙

1. 제목 다음에 요약을 두고 자료와 설명을 이어 배치한다.
2. 제목과 본문은 산세리프를 사용하며 굵기와 크기로 순서를 구분한다.
3. 종이 질감, 이중선, 낙관과 장식용 꼬리표는 사용하지 않는다.
4. 표와 그래프는 자료 가까이에 두고 색 외에 라벨과 모양으로도 구분한다.
5. 구성요소는 내용에 실제 구분이나 관계가 있을 때만 사용한다.

## 형식

웹·앱, 문서, 슬라이드와 이미지에서 같은 색과 글자 역할을 사용한다. 판면,
글자 크기와 한 화면에 담는 정보량은 [formats.json](formats.json)에 적힌
형식별 규칙을 따른다.

## 파일

| 파일 | 내용 |
|---|---|
| `tokens.css` | 색, 글자 크기와 문서 폭 |
| `base.css` | 제목, 본문, 표, 코드와 인쇄 |
| `components.css` | 문서 머리, 구획, 요약 값과 보조 문구 |
| `diagram.css` | 이미지 관계 그림 |
| `charts.css` | 이미지 그래프 배치와 배경 전환 |
| `assets/` | 밝은 배경과 어두운 배경용 그래프 이미지 |
| `formats.json` | 웹·앱, 문서, 슬라이드와 이미지 규칙 |

`brief`는 기본 문서와 관계 그림을 포함한다. `artifact`는 그래프까지 포함한다.

## 주요 클래스

- 문서: `main`, `.brief-header`, `.lead`, `.block`, `.prose-block`
- 요약 값: `.stat-row`, `.stat`, `.stat-label`, `.stat-value`, `.stat-note`
- 표와 그림: `.visual`, `.table-scroll`, `.diagram-scroll`
- 보조 문구: `.callout`, `.sources`, `.colophon`
- 관계 그림: `.diagram-image`, `.diagram-scroll`, `.diagram-legend`
- 그래프: `.chart-asset`, `.chart-scroll`, `.chart-legend`, `.chart-note`

`table-scroll`, `diagram-scroll`, `chart-scroll`에는 `tabindex="0"`,
`role="region"`과 내용을 설명하는 `aria-label`을 함께 쓴다.

## 개정 기록

- **v3.3 (2026-08-26)** — 디자인 원칙 문서를 추가하고 어두운 배경을 시스템 설정에 자동으로 맞추게 했다. `.status-warn` 별칭을 더했다.
- **v3.2 (2026-08-19)** — 관계 그림과 그래프를 이미지 애셋으로 바꿨다.
- **v3.1 (2026-08-19)** — 웹·앱, 문서, 슬라이드와 이미지 규칙을 추가했다.
- **v3.0 (2026-08-19)** — 장식 요소를 덜고 요약·자료·설명 순서에 집중했다.
