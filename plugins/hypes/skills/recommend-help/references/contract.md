# 저장하지 않는 도움 추천 계약

이 계약은 현재 작업 문맥 안에서만 별도 도움 방식을 계산합니다. 원문 답변을 만들거나 고치지 않고, 개인별 저장소나 외부 서비스를 사용하지 않습니다.

실행 파일은 직접 실행하지 않고 `python3 <이 스킬의 절대 경로>/scripts/recommend_help.py`로 호출해 JSON을 표준 입력에 보냅니다.

## 요청

모든 객체는 아래에 적힌 key만 허용합니다. ID는 소문자 영문·숫자로 시작하고 소문자 영문·숫자·점·밑줄·콜론·하이픈만 사용합니다. 원문 대화, 답변, 사용자 이름, 자유문자열을 ID에 넣지 않습니다.

```json
{
  "schema_version": "0.1.0",
  "expected_policy_id": "recommend-help-fixed-v0.1.0",
  "request_id": "request-001",
  "conversation_id": "conversation-001",
  "relation_scope": {
    "project_id": "project-alpha",
    "task_relation": "decision-review",
    "responsibility": "ordinary"
  },
  "assistance_allowed": true,
  "baseline_delivery_plan": {
    "baseline_id": "baseline-001",
    "baseline_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "required_content_ids": ["answer", "limitations"],
    "optional_content_ids": ["example"],
    "assistance_mode": "direct_completion",
    "human_responsibility": {
      "release_owner": "human",
      "agent_execution_authority": false,
      "decision_class": "ordinary",
      "required_check_ids": ["scope-check", "release-check"]
    }
  },
  "current_conversation_overlay": {
    "conversation_id": "conversation-001",
    "revision": 0,
    "seen_events": [],
    "relation_states": [],
    "prior_delivered_baselines": []
  },
  "observations": []
}
```

- `conversation_id`는 이 작업에만 쓰는 임시 ID이며 사용자 ID가 아닙니다. 최상위 값과 overlay 값이 같아야 합니다.
- `relation_scope.responsibility`와 `human_responsibility.decision_class`는 `ordinary | approve_high_impact` 중 같은 값이어야 합니다.
- `assistance_mode`는 `none | brief | scaffold | direct_completion` 중 하나입니다. 이는 비교 기준이며 추천 정책 입력으로 사용하지 않습니다.
- `baseline_sha256`은 호출 전에 봉인한 기준 전달안의 외부 결속값입니다. 계산기는 본문을 받지 않으므로 그 외부 자료의 실제 존재를 증명하지 않고 값을 그대로 보존합니다.
- 이전 호출의 유효한 `next_overlay`가 현재 작업 문맥에 보일 때만 그대로 다시 씁니다. 보이지 않으면 revision 0의 빈 overlay로 시작하고 대화 원문에서 상태를 재구성하지 않습니다.
- overlay는 호출자가 직전 결과를 정확히 다시 넘긴다는 신뢰 경계입니다. 계산기는 `seen_events`에 event 원문을 보존하지 않으므로 임의로 고친 `relation_states`를 과거 event에서 복원하거나 독립 인증할 수 없습니다. `relation_states`와 `seen_events`를 손으로 고치지 않으며, 이 경계는 provenance 증명이 아닙니다.

## 허용 관찰

확인된 정정은 `effect: unknown | likely_gap`만 허용합니다. 사용자의 자기평가만으로 `demonstrated`를 만들 수 없습니다.

```json
{
  "event_id": "event-correction-001",
  "sequence": 1,
  "relation_scope": {
    "project_id": "project-alpha",
    "task_relation": "decision-review",
    "responsibility": "ordinary"
  },
  "kind": "confirmed_correction",
  "effect": "likely_gap",
  "confirmed_by_user": true
}
```

결과 관찰은 현재 기준안이 아니라 앞선 turn의 기준안에 결속합니다. 먼저 `current_conversation_overlay.prior_delivered_baselines`에 호출자가 확인한 앞선 전달 기록을 넣고, 결과의 `delivery_binding`이 그 기록과 정확히 같아야 합니다. 결과는 `effect: unknown | likely_gap | demonstrated`를 사용할 수 있습니다.

앞선 전달 기록은 다음 key를 가집니다.

```json
{
  "relation_scope": {
    "project_id": "project-alpha",
    "task_relation": "decision-review",
    "responsibility": "ordinary"
  },
  "baseline_id": "baseline-prior-001",
  "baseline_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "delivery_id": "delivery-001",
  "delivery_receipt_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "delivery_status": "delivered",
  "attribution_window_id": "window-001",
  "attribution_window_opened": true,
  "hypes_help_applied": false
}
```

결과 관찰의 `delivery_binding`은 위 여덟 결속 key에 더해 다음을 포함합니다.

```json
{
  "plan_kind": "baseline",
  "evaluation_criteria_complete": true,
  "outcome_id": "outcome-001",
  "outcome_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
}
```

실제 전체 `delivery_binding`에는 앞선 전달 기록의 `baseline_id`부터 `hypes_help_applied`까지도 함께 넣습니다. 이번 추천이 아니라 기준안의 결과만 비교하므로 `hypes_help_applied`는 `false`여야 합니다. `demonstrated` 결과는 `evaluation_criteria_complete: true`여야 하며, 불완전한 평가도 `likely_gap` 결과로는 받을 수 있습니다. 이 시제품은 두 구조의 해시와 ID가 맞는지 확인하지만 플랫폼에서 실제로 전달됐는지는 독립적으로 증명하지 못합니다.

일반 답변, 침묵, 단순 완료, 약한 신호, 겉으로 보이는 동의와 별도 Hypes 추천은 관찰이 아닙니다. 같은 event의 정확한 재전송은 멱등 처리하고, 같은 ID의 다른 내용이나 역순 sequence는 요청 전체를 거부합니다. 관계 범위가 다른 관찰은 현재 상태와 추천을 바꾸지 않습니다.

## 고정 추천 규칙

1. `assistance_allowed: false`이면 `none`
2. 같은 범위의 상태가 `likely_gap`이면 `scaffold`
3. 같은 범위의 상태가 `demonstrated`이면 `none`
4. 상태가 `unknown`이고 책임이 `approve_high_impact`이면 `scaffold`
5. 같은 범위의 `unknown` 상태가 있으면 `brief`와 `matching_unknown`
6. 같은 범위의 상태가 없으면 `brief`와 `no_matching_state`

정책 ID는 `recommend-help-fixed-v0.1.0`이며 호출 결과로 갱신하지 않습니다.

## 결과와 실패

성공 결과는 기준 전달안을 그대로 돌려주고 `shadow_recommendation`, `next_overlay`, `applied: false`, `delivery_window_opened: false`, `attribution_allowed: false`, `state_update_attributed_to_recommendation: false`, `persistent_write_count: 0`과 자체 hash를 포함합니다. 필수 내용 ID와 사람의 책임 경계는 바뀌지 않아야 합니다.

입력 오류는 exit code 2와 `status: invalid_input` 영수증을 냅니다. 스킬은 이때 추천을 적용하지 않고 이미 확정한 기준 전달안으로 계속합니다. Hypes 전용 UI나 오류 설명은 사용자가 진단을 요청했을 때만 보여줍니다.

이 계산기는 기준 전달안 보존과 `applied: false`를 검증합니다. 같은 Codex가 별도 추천을 본 뒤 최종 답변을 쓰므로 최종 문장의 완전한 비영향을 증명하지 않으며, 스킬 선택도 매 관련 답변마다 실행되는 플랫폼 실행 지점을 뜻하지 않습니다.
