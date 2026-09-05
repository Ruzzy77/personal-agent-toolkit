---
name: reflect-journal-outcomes
description: Reflect confirmed Journal week-close outcomes into the relevant existing Corpus project context or indexed Work source without creating an aggregate Journal archive.
---

# Journal 결과를 Corpus에 반영

`journal_prepare_week_close`에서 `corpusCandidates`가 반환되었거나 사용자가 확정된 Journal 결과를 프로젝트 맥락에 반영해 달라고 할 때만 적용합니다.

1. 후보의 `targetSpace`, 프로젝트와 `durableOutcome`을 확인합니다. 대상이 없거나 임시 활동 기록이면 Journal에만 남깁니다.
2. 해당 Corpus Space의 현재 Context, 승인된 Context Skill과 연결된 Work/Source를 읽습니다. 별도 종합 Journal Space를 만들지 않습니다.
3. 현재 프로젝트 이해 자체가 달라진 경우에만 기존 Context 항목을 최신 상태로 교체합니다. 사용자가 채택하지 않은 해석을 확정 판단으로 쓰지 않습니다.
4. 프로젝트 기록 파일에 남아야 하는 결과는 연결된 `read_write` Work 파일을 최신 버전과 대조해 최소 범위로 수정합니다. Source 색인은 원문 파일이 바뀐 뒤 해당 등록 Source만 갱신합니다.
5. 저장과 재색인을 다시 읽어 확인한 뒤 후보의 `contentHash`를 그대로 사용해
   `journal_record_corpus_promotion`으로 대상 Space, 프로젝트 root 기준 상대 Source·Work 경로와
   적용 상태를 기록합니다. 로컬 절대 경로는 보내지 않습니다. 이미 같은 결과가 반영된 receipt가
   있으면 중복 작성하지 않습니다. 반영하지 않기로 결정한 후보도 짧은 비경로 표식과 `skipped`
   영수증을 남깁니다.
   실패한 시도는 원인과 `failed` 상태를 기록하며 완료로 취급하지 않습니다. 같은 영수증 요청의
   응답이 불확실하면 같은 `idempotencyKey`로 재전송하고, 실패 뒤 실제 새 시도에는 새 키를
   사용합니다. Corpus 저장 여부부터 대상에서 다시 읽어 확인하고 필요한 변경만 재시도합니다.
   새 키가 Corpus의 중복 적용을 막아 주지는 않습니다. 후보가 바뀌었거나 실패 원인을 해소할 수
   없으면 재시도를 멈추고 미반영 상태와 필요한 조치를 알립니다. 실패 이력은 보존되며 이미 완료한
   후보는 추가 영수증으로 덮어쓰지 않습니다.
6. 주간 마감도 요청받았다면 모든 후보의 영수증을 확인한 뒤 `manage-journal`의 마감 절차에 따라 현재 `preparationVersion`과 사용자의 명시적 확인으로 확정합니다. 결과 반영 요청만으로 주간 마감까지 승인됐다고 보지 않습니다.

Corpus는 프로젝트의 최신 재사용 가능 맥락을 맡고, Journal은 날짜순 상태 전이와 사용자 확인 이력을 맡습니다. 일일 행동 목록, 다른 프로젝트의 진행 내용과 원문 이메일·문서 본문을 Corpus에 모아 넣지 않습니다.
