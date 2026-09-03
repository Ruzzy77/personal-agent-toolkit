# Design Reference Library

제품과 콘텐츠의 맥락에 맞는 설계 패턴, 선택적 레시피와 예시 자산을 찾고 비교하는
라이브러리입니다. 특정 레시피를 정답처럼 복제하지 않고, 현재 프로젝트의 브랜드와
디자인 시스템을 우선한 뒤 필요한 원리와 재료만 골라 씁니다.

이 폴더는 Personal Agent Toolkit의 Design Site 정본입니다. `designs/`의 공개 라이브러리에서
사이트 카탈로그와 `plugins/design`의 오프라인 참고 묶음을 함께 만듭니다.

## 구조

```text
sites/design/
├── designs/          # 패턴, 공개 레시피와 예시 자산
├── app/              # 라이브러리 화면
├── public/           # 빌드 전에 자동으로 갱신되는 미리보기 파일
└── .openai/          # Sites 설정
```

## 실행

```bash
npm install
npm run dev
```

개발 서버와 프로덕션 빌드를 시작하기 전에 `designs/`의 내용으로 다음 파일을
자동 갱신합니다.

- `app/design-catalog.json`
- `public/previews/`
- `public/templates/`
- `designs/index.html`의 카드 목록

## 확인

```bash
npm run build
npm test
npm run lint
npm run export:plugin
```

원본은 `designs/`에서 수정합니다. 자동 생성되는 카탈로그와 `public/`의
미리보기 파일은 직접 고치지 않습니다. 공개 가능한 묶음은 다음처럼 내보냅니다.

```bash
python designs/tools/bundle.py --export-public <출력 폴더>
```

이 묶음에는 공개 패턴·레시피·자산과 라이선스만 들어가며, 사이트 코드는 제외됩니다.

`npm run export:plugin`은 같은 공개 묶음을 `plugins/design`의 배포 자산으로 갱신합니다.
프로젝트 전용 확장과 사용자 자료는 이 공개 저장소에 두지 않습니다.

## 에이전트 사용

화면의 주요 흐름은 같은 카탈로그를 사용하는 WebMCP 도구 세 가지로도 제공됩니다.

- `design_library_find`: 조건에 가까운 참고 후보 1–3개 찾기
- `design_library_compare`: 참고 후보 2–3개를 화면에서 비교하기
- `design_library_prepare_brief`: 선택한 후보로 요청문 준비하기

WebMCP가 없는 브라우저에서는 일반 화면으로 동일한 흐름을 사용할 수 있습니다.

배포된 소유자 전용 화면은 <https://personal-material-index.ruzzy.chatgpt.site>에서 확인합니다.
