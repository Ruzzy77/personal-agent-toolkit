# 세션 연결 Source

Corpus는 완료된 Codex·Claude 작업을 provider record로 관찰할 수 있습니다. 이 기능은 선택한 Context item이 어느 작업 기록에서 나왔는지 연결하며, 대화 archive나 자동 학습 계층을 만들지 않습니다.

## 저장 범위

Corpus에는 provider 종류, 안정적인 session·turn ID, 완료 시각, workspace 범위, 상대 locator와 visible message의 SHA-256만 저장합니다. 메시지 본문, reasoning, tool 입출력, 첨부물과 자격 증명은 저장하지 않습니다.

정확한 사용자·assistant 메시지는 `source fetch` 또는 대응 MCP 도구를 호출할 때 provider JSONL에서 읽습니다. 반환한 내용은 현재 요청에서만 쓰며 Corpus에 복사하지 않습니다.

## 연결과 읽기

Binding에는 provider와 관찰할 workspace 범위를 지정합니다.

```json
{
  "provider_kind": "codex",
  "selector": {
    "cwd_prefix": "/absolute/path/to/workspace",
    "actor": "user_task",
    "lookback_days": 30,
    "include_archived": true
  }
}
```

```sh
./bin/corpus source bind --corpus completed-work --id codex --payload-file binding.json
./bin/corpus source refresh --corpus completed-work --id codex
./bin/corpus source list --corpus completed-work --binding codex
./bin/corpus source fetch --corpus completed-work --id codex --external-id turn_...
```

Claude는 `provider_kind`를 `claude`로 바꿉니다. Subagent 기록이 실제로 필요할 때만 `actor`가 `subagent_task`인 별도 binding을 만듭니다.

## 변경 상태

Context item은 사용한 provider record의 identity를 보관합니다.

- `valid`: 현재 record와 사용한 observation이 같음
- `source_changed`: visible message 또는 observation이 달라짐
- `source_removed`: 완료된 refresh에서 record가 사라짐
- `source_unavailable`: provider 파일을 열 수 없음
- `record_not_found`: 파일에는 접근했지만 해당 turn이 없음

Provider 연결 item은 restricted 상태로 유지합니다. 필요한 결론만 사용자가 선택한 Context에 기록하고 `external_sources`로 record를 연결합니다. 자동 요약, semantic queue, 별도 평가 framework나 provider 본문 사본은 만들지 않습니다.
