# 디자인 라이브러리

화면, 문서, 슬라이드와 이미지에 적용할 수 있는 설계 패턴, 선택적 레시피와
예시 자산을 모아 두었다. 현재 프로젝트에 디자인 시스템이 있으면 그것을 우선하며,
레시피는 전체 스타일을 복제하기보다 필요한 조형 원리와 재료를 고르는 데 사용한다.

등록된 디자인은 Design Library 화면에서 살펴보고 비교할 수 있다.

## 구조

```
designs/
├── README.md          # 라이브러리 규칙과 디자인 목록
├── library.json       # 라이브러리 이름, 버전과 라이선스
├── patterns.json      # 특정 시각 스타일에 묶이지 않는 설계 패턴
├── index.html         # 갤러리 (카드는 카탈로그에서 자동 생성)
├── shared/core.css    # 모든 프로필 앞에 자동 포함되는 공용 규칙
├── tools/bundle.py    # 설정과 HTML 스타일을 확인하는 도구
└── <디자인 id>/
    ├── design.json    # 패턴 연결, 선택 조건, 공개 범위와 출처
    ├── formats.json   # 웹·앱, 문서, 슬라이드, 이미지 규칙
    ├── *.css          # 역할별 CSS
    ├── styleguide.html# 브라우저 미리보기
    ├── templates/     # 필요한 경우에만 두는 최소 HTML 틀
    ├── README.md      # 색, 글자와 구성 규칙
    └── DESIGN.md      # 디자인 원칙
```

폴더에 유효한 schema v3 `design.json`이 있으면 번들러가 레시피로 등록한다.
프로그램이 읽는 목록은 `bundle.py --catalog`로 확인한다. 카탈로그는
`patterns`와 공개 `recipes`를 구분한다. `selection_ready: true`는 선택에 필요한
설정과 참조 파일이 준비되었다는 뜻이다.

갤러리 화면과 `index.html` 카드가 보여 주는 이름, 소개, 점검 문구, 색 견본과
라벨 번역은 전부 `design.json`의 `gallery` 블록에서 나온다. 이 블록이 없거나
번역이 빠지면 `--validate`가 실패한다.

## 설계 패턴

`patterns.json`은 탐색과 선택, 장문 읽기, 요약과 자료 설명, 구조와 절차,
한 가지 목적, 아카이브 읽기를 특정 색이나 레이아웃 이름과 분리해 정의한다.
각 레시피는 `pattern_refs`로 실제로 사용하는 패턴을 가리킨다.

## 등록된 레시피

| id | 이름 | 잘 맞는 내용 |
|---|---|---|
| [hanji](hanji/README.md) | 한지 | 요약, 자료와 시각 설명 |
| [seochaek](seochaek/README.md) | 서책 | 긴 글, 책과 안내 |
| [baekja](baekja/README.md) | 백자 | 탐색, 목록과 조작 |
| [formwork](formwork/README.md) | 거푸집 | 구성 요소, 연결 관계와 절차 |
| [yeobaek](yeobaek/README.md) | 여백 | 필수 내용과 한 가지 주요 행동 |
| [saegin](saegin/README.md) | 색인 | 디지털 아카이브와 긴 발간물 |

모든 레시피에는 웹·앱, 문서, 슬라이드와 이미지용 원칙이 있다. `format_support`는
형식별로 원칙만 있는지, 적용 자산과 확인된 예시까지 있는지 구분한다. HTML 틀의
내용 순서와 항목은 새 결과물의 목적에 맞게 바꾼다.

## 공용 번들러

HTML에는 조립한 CSS를 인라인으로 넣는다. 마커 이름에는 디자인 id를 쓴다.
`shared/core.css`는 여섯 디자인이 똑같이 쓰는 최소 규칙(박스 계산, 이미지
정규화, 숫자 셀, 스크린리더 전용, 움직임 최소화)을 담고, 번들러가 모든
프로필 맨 앞에 자동으로 포함한다. 디자인별 CSS가 언제든 덮어쓸 수 있다.

```html
<!-- hanji:styles profile=brief -->
<style>…</style>
<!-- /hanji:styles -->
```

```bash
python designs/tools/bundle.py --list
python designs/tools/bundle.py --catalog
python designs/tools/bundle.py --validate
python designs/tools/bundle.py --export-public <출력 폴더>
python designs/tools/bundle.py --inject <페이지.html>
python designs/tools/bundle.py --check <페이지.html>
python designs/tools/bundle.py --ready <완성본.html>
python designs/tools/bundle.py --design hanji --profile brief
```

CSS를 고친 뒤에는 해당 스타일가이드와 HTML 틀에 `--inject`를 다시 실행한다.
`--ready`는 자리표시자를 실제 내용으로 바꾼 완성본에만 사용한다.

`--export-public`은 `visibility: public`인 레시피와 패턴, 공용 CSS, 라이선스만
새 폴더에 내보낸다. 출력 묶음의 `catalog.json`에는 원본 파일의 콘텐츠 해시가
포함된다. 비공개 레시피와 갤러리 앱 코드는 내보내지 않는다.

## 새 디자인 등록

1. `patterns.json`에서 재사용할 패턴을 고르거나 실제로 필요한 새 패턴을 등록한다.
2. `designs/<id>/`에 schema v3 `design.json`을 만들고 `kind: recipe`, 공개 범위,
   `pattern_refs`, `format_support`와 출처 정보를 적는다.
3. `formats`, `format_fit`과 `format_guide`를 적고, `formats.json`에는 네 형식의
   판면, 글자와 배치 규칙을 적는다. `shared.palette` 키는
   background, surface, text, muted, accent에 필요하면 accent2, link, line을
   더하고, `shared.typography` 키는 heading, body에 필요하면 label, data,
   decorative를 더한다.
4. `gallery` 블록에 한글 이름, 목적, 소개, 점검 문구, 색 견본과
   용도 라벨 번역을 적는다. 갤러리 화면과 `index.html` 카드는 이 블록만 읽는다.
5. 색, 서체와 치수는 첫 CSS 파일의 토큰으로 분리한다. 공용 규칙은
   `shared/core.css`가 이미 제공하므로 다시 적지 않는다.
6. `styleguide.html`에서 실제 CSS와 구성요소를 확인할 수 있게 한다.
7. CSS나 참조 파일을 바꾼 뒤 `--validate`와 해당 HTML의 `--check`를 실행한다.
   등록 후 갤러리 반영은 `npm run sync:designs`가 처리한다.

## 공통 규칙

- CSS와 시스템 글꼴만 사용하며 CDN과 웹폰트는 사용하지 않는다.
- 이미 발행한 결과물은 바꾸지 않고 새 결과물부터 최신 규칙을 적용한다.
- 기존 클래스 이름은 유지하고 필요한 구성요소만 추가한다.
- 색에만 의미를 맡기지 않고 문구, 모양과 선을 함께 사용한다.
- 필요한 기능을 지원하지 않거나 `avoid_for`에 해당하면 다른 디자인을 고른다.
