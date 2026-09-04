# Personal Agent Toolkit

ChatGPT와 Codex에서 Sense, Corpus, Hypes, Journal, Library, Design과 문서 작업을 하나의 비공개
연결로 사용하는 OpenAI 배포 plugin입니다. 설치 항목의 이름만 **Personal Agent Toolkit**이며,
내부 제품과 Skill은 짧은 고유 이름을 유지합니다.

이 디렉터리의 `skills/`와 `runtime/document-files/`는 제품별 Skill과 Document Files Python 정본에서
생성합니다. 생성된 복사본을 직접 편집하지 않습니다.

```sh
python3 scripts/build_openai_plugin.py
```

제품별 구현과 Claude 배포 방식은 저장소 루트의 `DESIGN.md`와 각 제품의 `DESIGN.md`를 따릅니다.
