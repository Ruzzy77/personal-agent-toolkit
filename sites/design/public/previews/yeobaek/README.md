# 여백(Yeobaek)

필수 내용과 주요 행동만 남기는 디자인이다. 제목, 본문, 구분선과 버튼을
필요한 만큼만 사용한다.

- [미리보기](styleguide.html)
- [형식별 규칙](formats.json)
- [HTML 틀](/templates/yeobaek/templates/artifact.html)

## 원칙

1. 제목은 한 번만 쓰고 같은 뜻의 부제와 첫 문장을 덧붙이지 않는다.
2. 설명은 사용자가 다음 행동을 이해하는 데 필요할 때만 사용한다.
3. 주요 행동은 한 개를 기본으로 하고 보조 행동은 필요한 경우에만 추가한다.
4. 카드, 배지, 아이콘, 구분선과 배경은 실제 경계나 상태를 나타낼 때만 사용한다.
5. 정보 순서는 글자 크기, 정렬과 간격으로 만든다.
6. 접근성 이름, 오류 해결 방법과 꼭 필요한 조건은 줄이지 않는다.

## 형식

웹·앱, 문서, 슬라이드와 이미지에 같은 중립 색상과 산세리프 활자를 사용한다.
형식마다 허용하는 요소 수, 판면과 글자 크기는 [formats.json](formats.json)을
따른다.

## 파일

| 파일 | 내용 |
|---|---|
| `tokens.css` | 중립 색상, 글자, 간격과 판면 |
| `base.css` | 본문, 표, 코드, 포커스와 인쇄 |
| `components.css` | 필요한 구획, 입력, 버튼과 상태 |
| `formats.json` | 웹·앱, 문서, 슬라이드와 이미지 규칙 |

## 주요 클래스

- 판면: `.minimal-page`, `.minimal-head`, `.minimal-section`, `.minimal-summary`
- 내용: `.compact-list`, `.format-list`, `.notice`, `.callout`
- 조작: `.action-row`, `.button`, `.button-primary`, `.field`, `.field-group`, `.inline-form`
- 표와 그림: `.table-scroll`, `.visual-scroll`, `.num`

## 참고한 방향

- [Vercel Geist Typography](https://vercel.com/geist/typography) — 역할별 글자 체계
- [Vercel Web Interface Guidelines](https://vercel.com/design/guidelines) — 정렬, 직접적인 도움말과 의미 구조
- [Linear UI redesign](https://linear.app/now/how-we-redesigned-the-linear-ui) — 시각적 잡음 축소와 정렬
- [GOV.UK spacing](https://design-system.service.gov.uk/styles/spacing/) — 작은 화면과 큰 화면의 간격 체계

표현을 복제하지 않고 글자 역할, 정렬, 내용 제한과 반응형 간격만 가져왔다.

## 개정 기록

- **v1.1 (2026-08-26)** — 인쇄 규격을 보강하고 공용 콜아웃 별칭과 대비 검증 기록을 추가했다.
- **v1.0 (2026-08-19)** — 필수 내용과 주요 행동만 남기는 최소 구성을 등록했다.
