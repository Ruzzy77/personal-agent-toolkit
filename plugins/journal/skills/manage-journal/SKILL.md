---
name: manage-journal
description: Use Journal to review daily or weekly progress, ingest concise monitoring updates, confirm item status, close a week, add a correction, or inspect month, quarter, and year records.
---

# Journal 관리

## 현재 상태 읽기

일간 브리핑이나 주간 진행 확인은 `journal_get_board`로 시작합니다. 이번 주가 아니면 KST 기준 월요일 날짜를 `weekId`로 사용합니다. 같은 항목의 분류와 처리 상태를 구분해 읽습니다.

- `lane`: 오늘, 직접 처리, 대기, 주의 중 지금 어디에서 봐야 하는가
- `resolution`: 진행 중, 보류, 완료, 취소 중 사용자가 어떻게 확정했는가

완료나 취소 항목도 포함해 주간 전체 흐름을 볼 때만 `includeResolved=true`를 사용합니다.

## 모니터링 결과 반영

새 자료에서 달라진 사실만 `journal_ingest_items`로 반영합니다. 원문 본문을 복사하지 않고, 사용자가 진행 상태를 판단하는 데 필요한 요약과 원본을 다시 찾을 수 있는 `sourceRef`만 둡니다.

- 같은 일을 계속 갱신할 수 있도록 `sourceKind`와 `sourceKey`를 안정적으로 유지합니다.
- 실행마다 고유한 `idempotencyKey`를 사용합니다.
- `sourceVersion`은 원본의 메시지, 파일 버전이나 확인 시점처럼 갱신 여부를 구분할 수 있을 때만 넣습니다.
- 한 항목은 하나의 lane으로 분류합니다. 상대방이나 시스템을 기다리는 일은 새 회신이 없다는 이유로 직접 처리로 되돌리지 않습니다.
- 프로젝트에 남길 확정된 결과가 있을 때만 `durableOutcome`과 `corpusTargetSpace`를 함께 넣습니다. 임시 진행 메모는 비워 둡니다.

자동화가 관찰 사실을 갱신해도 `resolution`은 바뀌지 않습니다.

## 사용자 확인

사용자가 직접 완료, 보류, 취소 또는 재개를 선택하거나 같은 뜻을 분명히 말했을 때만 `journal_set_resolution`을 호출합니다. 화면 버튼 선택은 명시적 확인입니다. 현재 항목의 `version`을 `expectedVersion`으로 보내 동시 변경을 감지하고, 재시도에는 같은 `idempotencyKey`를 사용합니다.

확정되지 않은 추론으로 완료나 취소를 대신 결정하지 않습니다.

## 주간 마감

주간 마감은 되돌릴 수 없는 일반 수정이므로 사용자의 명시적 확인 뒤 `journal_close_week`을 호출합니다. 진행 중과 보류 항목은 같은 `logicalItemId`를 가진 다음 주 인스턴스로 이월되고, 완료와 취소 항목은 마감 주에 남습니다. 마감된 주는 항목을 다시 쓰지 않고, 사후 사실 정정만 `journal_add_correction`으로 남깁니다.

마감 결과의 `corpusCandidates`는 Corpus에 자동 복제할 일반 기록이 아닙니다. 대상 프로젝트에 재사용할 확정 결과만 아래 Corpus 반영 절차로 넘깁니다.

## 기간 기록

`journal_get_period`는 `week`, `month`, `quarter`, `year` 중 요청된 단위와 기준 날짜를 사용합니다. 수치 전체를 나열하기보다 완료, 진행, 보류, 이월과 프로젝트별 변화가 다음 판단에 주는 의미를 먼저 보여 줍니다.

시각적 확인이나 빠른 버튼 처리가 필요하면 소유자 전용 [Journal Site](https://personal-journal.ruzzy.chatgpt.site)를 엽니다.
