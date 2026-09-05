# Personal Agent Toolkit

Sense, Corpus, Hypes, Journal, Library, Design과 문서 작업을 연결하는 OpenAI 배포 plugin입니다.
Codex에서는 하나의 **Personal Agent Toolkit** marketplace 항목으로 설치합니다. 비공개 개인
ChatGPT에서는 같은 이름의 등록 app을 설치하고 문서 기능은 같은 정본에서 만든 `Document Files`
Personal Skill 하나로 올립니다. 내부 제품과 Skill은 짧은 고유 이름을 유지합니다. DOCX, PDF,
XLSX·CSV, PPTX와 Google Docs·Sheets·Slides의 형식별 방법은 `document-files` 내부의 조건부
참고 자료이며 별도 Skill이나 별칭으로 노출하지 않습니다.

이 디렉터리의 `skills/`와 `runtime/document-files/`는 제품별 Skill과 Document Files Python 정본에서
생성합니다. 생성된 복사본을 직접 편집하지 않습니다.

```sh
python3 scripts/build_openai_plugin.py
```

개인 ChatGPT에 올릴 단일 `document-files` archive는 저장소 루트에서 임시 출력 디렉터리로 만듭니다.

```sh
python3 scripts/build_chatgpt_personal_skills.py /tmp/personal-agent-toolkit-skills
```

기존 `Document Files` Personal Skill을 갱신할 때에는 스킬 목록의 **만들기 → 컴퓨터에서 업로드**에서
archive를 올리고 같은 이름의 **기존 항목 교체**를 선택합니다. 편집기의 파일 업로드에 archive를
첨부하는 것은 전체 Skill 교체가 아닙니다. 교체 뒤 기존 등록과 설치 상태, 새 본문·실행 파일을
확인하며 삭제 후 재설치를 기본 절차로 삼지 않습니다.

기존 다섯 Skill 구성에서의 전환은 Codex 통합 plugin 정상 업데이트, 개인 ChatGPT의 `Document Files`
교체, 네 형식별 Personal Skill 제거와 새 세션 확인을 하나로 묶어 수행합니다. 통합 기능을 확인한 뒤
기존 `Documents`, `PDF`, `Spreadsheets`, `Presentations` 항목을 제거합니다. 이전 archive 식별자는
`word-documents`, `pdf-files`, `workbooks`, `slide-decks`일 수 있습니다. 형식별 실행 코드·호스트
라이브러리와 Excel live-control 같은 별도 live 앱 제어 기능은 제거하지 않습니다. 새 세션에서
`document-files` 하나의 노출과 실제 작업을 확인하기 전에는 단일 Skill 전환을 완료로 보지 않습니다.
세부 절차는 저장소 루트 [갱신 안내](../../README.md#chatgpt와-codex)를 따릅니다.

제품별 구현과 Claude 배포 방식은 저장소 루트의 `DESIGN.md`와 각 제품의 `DESIGN.md`를 따릅니다.
