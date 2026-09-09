---
name: manage-journal
description: Use Journal to review daily or weekly progress, ingest concise monitoring updates, confirm item status, close a week, add a correction, or inspect month, quarter, and year records.
---

# Journal 관리

## 현재 상태 읽기

일간 브리핑이나 주간 진행 확인은 `journal_get_board`로 시작합니다. 이번 주가 아니면 KST 기준 월요일 날짜를 `weekId`로 사용합니다. 같은 항목의 분류와 처리 상태를 구분해 읽습니다.

- `lane`: 오늘, 직접 처리, 대기, 주의 중 지금 어디에서 봐야 하는가
- `resolution`: 진행 중, 보류, 완료, 취소 중 사용자가 어떻게 확정했는가
- `responsibility`: 사용자, 상대방, 시스템 중 다음 행동을 맡은 주체

완료나 취소 항목도 포함해 주간 전체 흐름을 볼 때만 `includeResolved=true`를 사용합니다.
여러 주의 특정 항목을 찾을 때는 `journal_find_items`, 출처와 상태 변경 경로를 확인할 때는 `journal_get_item_history`를 사용합니다.

## 미완료 업무의 연속성

현재 주 보드는 이전 주의 최신 진행 중·보류 항목을 함께 보여 줍니다. 여러 주가 지나도 같은 일을 한 번만 표시하며, 나중에 완료·취소된 항목의 과거 인스턴스를 되살리지 않습니다. 별도의 주간 마감이나 이월 요청은 필요하지 않습니다. 이전 주가 열려 있다는 이유로 마감을 독촉하지 않습니다.

보드 조회는 저장된 주·항목을 바꾸지 않습니다. 현재 보드에 이전 `weekId`의 항목이 있어도 정상입니다. 이후 관찰·처리 상태를 저장할 때 같은 `logicalItemId`의 이번 주 인스턴스를 만들고 과거 기록을 보존합니다. 쓰기 응답의 새 `id`로 저장 결과를 확인하며, 오래된 인스턴스의 충돌은 현재 보드를 다시 읽어 처리합니다. 대기 담당과 사용자가 정한 보류 상태를 그대로 유지합니다.

## 모니터링 결과 반영

새 자료에서 달라진 사실만 `journal_ingest_items`로 반영합니다. 원문 본문을 복사하지 않고, 사용자가 진행 상태를 판단하는 데 필요한 요약과 원본을 다시 찾을 수 있는 `sourceRef`만 둡니다.

장애·실행 결과 알림은 수신 시각과 실제 사건 시각을 구분하고 원 시스템의 최신 상태와 후속 결과를 대조합니다. 이미 해소된 과거 알림을 현재 장애나 사용자 할 일로 되돌리지 않으며, 최신 상태를 읽지 못하면 미확인으로 구분합니다.

- 같은 일을 계속 갱신할 수 있도록 `sourceKind`와 `sourceKey`를 안정적으로 유지합니다.
- 실제 관찰 변경마다 고유한 `idempotencyKey`를 사용하고 같은 변경의 재시도에는 같은 키를 재사용합니다.
- `sourceVersion`은 원본의 메시지, 파일 버전이나 확인 시점처럼 갱신 여부를 구분할 수 있을 때만 넣습니다.
- 한 항목은 하나의 lane으로 분류합니다. 상대방이나 시스템을 기다리는 일은 새 회신이 없다는 이유로 직접 처리로 되돌리지 않습니다.
- 프로젝트에 남길 확정된 결과가 있을 때만 `durableOutcome`과 `corpusTargetSpace`를 함께 넣습니다. 임시 진행 메모는 비워 둡니다.

자동화가 관찰 사실을 갱신해도 `resolution`은 바뀌지 않습니다.

이메일 모니터링에서는 마지막 성공 확인 이후의 받은 메일과 보낸 메일을 함께 대조합니다. 같은 스레드의 방향과 시각으로 실제 전달·회신·대기를 판단합니다. 최신 메시지가 발신이고 후속 회신이 없으면 상대방 대기를 유지하며, 최신 수신 메일에 사용자 조치가 필요할 때만 사용자 책임으로 바꿉니다. 출처에 없는 기관명·처리 주체·절차를 추정해 넣지 않습니다.

반영 뒤 보드를 다시 읽어 저장 결과와 중복을 확인합니다. 실패하면 마지막 성공 확인 시점을 유지하고 미완료 범위를 남깁니다. 실패한 구간을 확인 완료로 넘기거나 별도 HTML 기록으로 대체하지 않습니다.

## 사용자 확인

사용자가 직접 완료, 보류, 취소 또는 재개를 선택하거나 같은 뜻을 분명히 말했을 때만 `journal_set_resolution`을 호출합니다. 화면 버튼 선택은 명시적 확인입니다. 현재 항목의 `version`을 `expectedVersion`으로 보내 동시 변경을 감지하고, 재시도에는 같은 `idempotencyKey`를 사용합니다.

확정되지 않은 추론으로 완료나 취소를 대신 결정하지 않습니다.

## 주간 마감

먼저 `journal_prepare_week_close`로 요약, 이월 항목과 Corpus 후보를 읽습니다. 후보가 있으면 `reflect-journal-outcomes`에 따라 Journal의 확정 결과와 해당 Corpus 정본을 대조하고 확인된 불일치를 반영·개정합니다. 처리하지 못한 후보나 실패가 있어도 사용자가 마감을 명시적으로 요청했다면 준비 응답의 `preparationVersion`으로 `journal_confirm_week_close`를 호출할 수 있습니다. 마감을 위해 미처리를 `applied`나 `skipped`로 바꾸지 않습니다. 준비 뒤 항목이 달라졌다면 새 준비 결과를 다시 보여 줍니다.

명시적인 기록 동결을 원할 때만 이 절차를 사용합니다. 일상적인 업무 연속성과 주간 회고에는 마감이 필요하지 않으며, Corpus 반영도 자동 이월의 선행 조건이 아닙니다.

마감 시 아직 후속 인스턴스가 없는 진행 중·보류 항목은 같은 `logicalItemId`로 다음 주에 이어집니다. `held`는 유지하고 이미 존재하는 후속 상태를 덮어쓰지 않습니다. 완료와 취소 항목은 마감 주에 남습니다. 마감된 주는 항목을 다시
쓰지 않고, 사후 사실 정정만 `journal_add_correction`으로 남깁니다.

마감 뒤 남은 반영은 `journal_get_board`에 마감 주의 `weekId`를 지정해 `closure.corpusCandidates`의 `reflectionStatus`와 `closure.corrections`에서 이어갑니다. 실패 상세와 시도 이력은 해당 항목 이력에서 읽습니다.

마감 결과의 `corpusCandidates`는 Corpus에 자동 복제할 일반 기록이 아닙니다. 대상 프로젝트에 재사용할 확정 결과만 `reflect-journal-outcomes`로 넘깁니다. 결과 반영 요청만으로 주간 마감까지 승인됐다고 보지 않습니다.

## 기간 기록

`journal_get_period`는 `week`, `month`, `quarter`, `year` 중 요청된 단위와 기준 날짜를 사용합니다. 수치 전체를 나열하기보다 완료, 진행, 보류, 이월과 프로젝트별 변화가 다음 판단에 주는 의미를 먼저 보여 줍니다.

사용자가 기간 요약을 고쳐 채택하면 현재 `version`을 `expectedVersion`으로 보내 `journal_save_period_summary`로 새 버전을 저장합니다. 이전 요약 버전과 연결 Event는 그대로 둡니다.

시각적 확인이나 빠른 버튼 처리가 필요하면 소유자 전용 [Journal Site](https://personal-journal.ruzzy.chatgpt.site)를 엽니다.
