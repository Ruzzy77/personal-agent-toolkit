# Personal Agent Toolkit

Sense, Corpus, Hypes, Journal, Library, Design과 문서 작업을 연결하는 OpenAI 배포 plugin입니다.
Codex에서는 하나의 **Personal Agent Toolkit** marketplace 항목으로 설치합니다. 비공개 개인
ChatGPT에서는 같은 이름의 등록 app을 설치하고 문서 기능은 같은 정본에서 만든 Personal Skills로
올립니다. 내부 제품과 Skill은 짧은 고유 이름을 유지합니다.

이 디렉터리의 `skills/`와 `runtime/document-files/`는 제품별 Skill과 Document Files Python 정본에서
생성합니다. 생성된 복사본을 직접 편집하지 않습니다.

```sh
python3 scripts/build_openai_plugin.py
```

개인 ChatGPT에 올릴 다섯 문서 Skill은 저장소 루트에서 임시 출력 디렉터리로 만듭니다.

```sh
python3 scripts/build_chatgpt_personal_skills.py /tmp/personal-agent-toolkit-skills
```

기존 Personal Skill을 갱신할 때에는 스킬 목록의 **만들기 → 컴퓨터에서 업로드**에서 archive를
하나씩 올리고 같은 이름의 **기존 항목 교체**를 선택합니다. 편집기의 파일 업로드에 archive를
첨부하는 것은 전체 Skill 교체가 아닙니다. 교체 뒤 기존 등록과 설치 상태, 새 본문·실행 파일을
확인하며 삭제 후 재설치를 기본 절차로 삼지 않습니다.

제품별 구현과 Claude 배포 방식은 저장소 루트의 `DESIGN.md`와 각 제품의 `DESIGN.md`를 따릅니다.
