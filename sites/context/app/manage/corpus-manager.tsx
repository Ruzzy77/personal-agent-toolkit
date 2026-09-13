"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { mutationFailureState } from "@personal-agent/site-runtime";
import { contextCall, ContextFailure } from "../../lib/management";

type Row = Record<string, unknown>;
type Member = { kind: string; id: string; locator: string; version: string };
type Target = {
  kind: "document" | "context_item" | "context_skill" | "space";
  space_id: string;
  id: string;
};
type Impact = {
  action: "trash" | "restore" | "purge";
  target?: Target;
  impact_token: string;
  members: Member[];
  group?: { id: string; version: number };
  blockers: { code: string; message: string }[];
  parent: { version: number };
};
const rows = (value: unknown): Row[] => (Array.isArray(value) ? value : []);
const record = (value: unknown): Row => (value && typeof value === "object" ? (value as Row) : {});
const label = (kind: string) =>
  ({
    document: "문서",
    context_item: "Context 항목",
    context_skill: "Context Skill",
    space: "Space",
  })[kind] ?? kind;
const date = (value: unknown) =>
  new Date(String(value)).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
const errorText = (error: unknown) =>
  mutationFailureState(error) === "conflict"
    ? "자료가 변경되었거나 현재 상태에서 처리할 수 없습니다. 입력은 그대로 두었습니다. 새로고침 후 다시 확인해 주세요."
    : error instanceof ContextFailure && error.code === "management_not_enabled"
      ? "관리 기능을 준비 중입니다. 아직 변경은 저장되지 않았습니다."
      : "처리 결과를 확인하지 못했습니다. 새로고침하거나 같은 요청을 다시 확인해 주세요.";

export default function ManagementPage() {
  const [spaceNext, setSpaceNext] = useState<number | null>(null),
    [trashNext, setTrashNext] = useState<number | null>(null);
  const [registrations, setRegistrations] = useState<Row>({}),
    [detachTarget, setDetachTarget] = useState<Row | null>(null),
    [device, setDevice] = useState("");
  const [scopeText, setScopeText] = useState("");
  const [spaces, setSpaces] = useState<Row[]>([]),
    [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Row>({}),
    [trash, setTrash] = useState<Row[]>([]);
  const [maintenance, setMaintenance] = useState<Row>({});
  const [loaded, setLoaded] = useState(false);
  const reviewTrigger = useRef<HTMLElement | null>(null);
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const [impact, setImpact] = useState<Impact | null>(null),
    [confirmed, setConfirmed] = useState(false);
  const [destination, setDestination] = useState(""),
    [moveTarget, setMoveTarget] = useState<Target | null>(null);
  const [createKind, setCreateKind] = useState("document"),
    [newId, setNewId] = useState(""),
    [newTitle, setNewTitle] = useState(""),
    [newBody, setNewBody] = useState("");
  const [name, setName] = useState(""),
    [purpose, setPurpose] = useState("");
  const requestKey = useRef<string | null>(null),
    sequence = useRef(0),
    dialog = useRef<HTMLDialogElement>(null);

  const load = useCallback(async (spaceId: string) => {
    const current = ++sequence.current;
    const [index, bin] = await Promise.all([
      contextCall("corpus_space_list", { lifecycle: "all" }),
      contextCall("corpus_trash_list", {}),
    ]);
    if (current !== sequence.current) return;
    const found = rows(index.spaces);
    setSpaces(found);
    setTrash(rows(bin.groups));
    setMaintenance(record(bin.maintenance));
    setSpaceNext(index.next_offset as number | null);
    setTrashNext(bin.next_offset as number | null);
    const chosen =
      found.find((s) => s.space_id === spaceId) ?? found.find((s) => !s.deletion_group_id);
    if (!chosen) {
      setLoaded(true);
      setSelected("");
      setDetail({});
      return;
    }
    const id = String(chosen.space_id);
    setSelected(id);
    const data =
      chosen.state === "active" && !chosen.deletion_group_id
        ? record(
            (
              await contextCall("corpus_space_get", {
                space_id: id,
                include_context_skill: true,
              })
            ).space,
          )
        : chosen;
    if (current !== sequence.current) return;
    setDetail(data);
    setName(String(data.display_name));
    setPurpose(String(record(data.context).purpose ?? ""));
    setScopeText(String(record(record(data.context).scope).description ?? ""));
    setRegistrations(await contextCall("corpus_registrations_list", { space_id: id }));
    setLoaded(true);
  }, []);
  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active)
        void load("").catch((error) => {
          if (active) setMessage(errorText(error));
        });
    });
    return () => {
      active = false;
    };
  }, [load]);
  useEffect(() => {
    if (!impact) return;
    const previous = reviewTrigger.current;
    const element = dialog.current;
    element?.showModal();
    return () => {
      element?.close();
      previous?.focus();
    };
  }, [impact]);

  async function work(action: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
    } catch (error) {
      setMessage(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  async function review(action: Impact["action"], target?: Target, deletion_group_id?: string) {
    reviewTrigger.current = document.activeElement as HTMLElement | null;
    await work(async () => {
      const data = await contextCall("corpus_management_preview", {
        action,
        ...(target ? { target } : { deletion_group_id }),
      });
      requestKey.current = crypto.randomUUID();
      setConfirmed(false);
      setImpact(data as unknown as Impact);
    });
  }
  async function applyImpact() {
    if (!impact) return;
    await work(async () => {
      const common = {
        impact_token: impact.impact_token,
        idempotency_key: requestKey.current,
      };
      let result: Row;
      if (impact.action === "trash") {
        const target = impact.target!;
        const version = impact.members.find((m) => m.kind === target.kind)!.version;
        result = await contextCall(`corpus_${target.kind}_trash`, {
          ...common,
          space_id: target.space_id,
          ...(target.kind === "document"
            ? { document_id: target.id }
            : target.kind === "context_item"
              ? { item_id: target.id }
              : {}),
          expected_version: target.kind === "context_skill" ? version : Number(version),
        });
      } else {
        result = await contextCall(`corpus_trash_${impact.action}`, {
          ...common,
          deletion_group_id: impact.group!.id,
          expected_version: impact.group!.version,
          ...(impact.action === "purge" ? { confirm_permanent_delete: true } : {}),
        });
      }
      setImpact(null);
      await load(selected);
      setMessage(
        result.state === "blocked"
          ? "참조나 연결이 남아 삭제를 보류했습니다."
          : result.state === "purging"
            ? "영구 삭제를 시작했습니다. 남은 자료는 다음 정리에서 이어서 처리합니다."
            : "변경을 저장했습니다.",
      );
    });
  }
  const context = record(detail.context),
    documents = rows(record(detail.documents).items),
    items = rows(context.items);
  const targets: { target: Target; title: string }[] = [
    ...documents.map((d) => ({
      target: {
        kind: "document" as const,
        space_id: selected,
        id: String(d.document_id),
      },
      title: String(d.title),
    })),
    ...items.map((i) => ({
      target: {
        kind: "context_item" as const,
        space_id: selected,
        id: String(i.item_id),
      },
      title: String(i.body_text).slice(0, 120),
    })),
    ...(context.skill
      ? [
          {
            target: {
              kind: "context_skill" as const,
              space_id: selected,
              id: selected,
            },
            title: String(record(context.skill).name),
          },
        ]
      : []),
  ];

  return (
    <main className="management-page">
      <header className="management-page-header">
        <div>
          <h1>Corpus</h1>
          <p>자료를 이동하거나 휴지통에서 복원합니다. 원본 파일은 바뀌지 않습니다.</p>
        </div>
      </header>
      <div className="management-message" role="status" aria-live="polite">
        {message || (!loaded ? "목록을 불러오는 중입니다." : "")}
      </div>
      <section aria-labelledby="space-heading">
        <h2 id="space-heading">Space</h2>
        <div className="management-toolbar">
          <label>
            관리할 Space{" "}
            <select
              value={selected}
              disabled={busy}
              onChange={(e) => void work(() => load(e.target.value))}
            >
              <option value="">선택</option>
              {spaces.map((s) => (
                <option key={String(s.space_id)} value={String(s.space_id)}>
                  {String(s.display_name)}
                  {s.deletion_group_id ? " · 휴지통" : s.state === "archived" ? " · 보관됨" : ""}
                </option>
              ))}
            </select>
          </label>
          <button disabled={busy} onClick={() => void work(() => load(selected))}>
            새로고침
          </button>
        </div>
        {selected && (
          <details>
            <summary>Space 설정</summary>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void work(async () => {
                  await contextCall("corpus_space_revise", {
                    space_id: selected,
                    expected_version: context.version,
                    display_name: name,
                    purpose,
                    scope: { ...record(context.scope), description: scopeText },
                    state: detail.state,
                  });
                  await load(selected);
                  setMessage("Space 설정을 저장했습니다.");
                });
              }}
            >
              <label>
                이름
                <input required value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label>
                목적
                <textarea value={purpose} onChange={(e) => setPurpose(e.target.value)} />
              </label>
              <label>
                범위 설명
                <textarea
                  value={scopeText}
                  onChange={(event) => setScopeText(event.target.value)}
                />
              </label>
              <div className="management-form-actions">
                <button className="management-primary" disabled={busy}>
                  설정 저장
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void work(async () => {
                      await contextCall("corpus_space_revise", {
                        space_id: selected,
                        expected_version: context.version,
                        display_name: name,
                        purpose,
                        scope: {
                          ...record(context.scope),
                          description: scopeText,
                        },
                        state: detail.state === "archived" ? "active" : "archived",
                      });
                      await load(selected);
                      setMessage("Space 상태를 저장했습니다.");
                    })
                  }
                >
                  {detail.state === "archived" ? "보관 해제" : "Space 보관"}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void review("trash", {
                      kind: "space",
                      space_id: selected,
                      id: selected,
                    })
                  }
                >
                  Space를 휴지통으로
                </button>
              </div>
            </form>
          </details>
        )}
        <ul className="management-list">
          {targets.map(({ target, title }) => (
            <li key={target.kind + ":" + target.id}>
              <div>
                <span>{label(target.kind)}</span>
                <strong>{title}</strong>
              </div>
              <div className="management-actions">
                {(target.kind === "document" || target.kind === "context_item") && (
                  <button
                    disabled={busy}
                    onClick={() => {
                      setMoveTarget(target);
                      setDestination("");
                      requestKey.current = crypto.randomUUID();
                    }}
                  >
                    이동
                  </button>
                )}
                <button disabled={busy} onClick={() => void review("trash", target)}>
                  휴지통으로
                </button>
              </div>
            </li>
          ))}
        </ul>
        {Boolean(record(detail.documents).has_more) && (
          <button
            disabled={busy}
            onClick={() =>
              void work(async () => {
                const next = await contextCall("corpus_document_list", {
                  space_id: selected,
                  offset: documents.length,
                  limit: 100,
                });
                setDetail((previous) => ({
                  ...previous,
                  documents: {
                    ...next,
                    items: [...documents, ...rows(next.items)],
                  },
                }));
              })
            }
          >
            문서 더 보기
          </button>
        )}
        {Boolean(context.has_more) && (
          <button
            disabled={busy}
            onClick={() =>
              void work(async () => {
                const next = record(
                    (
                      await contextCall("corpus_space_get", {
                        space_id: selected,
                        context_offset: items.length,
                        include_context_skill: true,
                      })
                    ).space,
                  ),
                  nextContext = record(next.context);
                if (nextContext.version !== context.version) {
                  await load(selected);
                  setMessage("자료가 바뀌어 목록을 새로 불러왔습니다.");
                  return;
                }
                setDetail((previous) => ({
                  ...previous,
                  context: {
                    ...nextContext,
                    items: [...items, ...rows(nextContext.items)],
                  },
                }));
              })
            }
          >
            항목 더 보기
          </button>
        )}
        {moveTarget && (
          <form
            className="management-form management-inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              void work(async () => {
                const target = moveTarget,
                  dest = spaces.find((s) => s.space_id === destination)!;
                if (target.kind === "document") {
                  const doc = documents.find((d) => d.document_id === target.id)!;
                  await contextCall("corpus_document_move", {
                    space_id: selected,
                    document_id: target.id,
                    destination_space_id: destination,
                    expected_version: doc.version,
                    expected_source_version: context.version,
                    expected_destination_version: record(dest.context).version,
                    idempotency_key: requestKey.current,
                  });
                } else
                  await contextCall("corpus_context_item_move", {
                    space_id: selected,
                    item_id: target.id,
                    destination_space_id: destination,
                    expected_version: context.version,
                    expected_destination_version: record(dest.context).version,
                    idempotency_key: requestKey.current,
                  });
                setMoveTarget(null);
                await load(selected);
                setMessage("자료를 이동했습니다.");
              });
            }}
          >
            <h3>이동할 Space</h3>
            <p>{moveTarget.id}</p>
            <label>
              목적지
              <select required value={destination} onChange={(e) => setDestination(e.target.value)}>
                <option value="">선택</option>
                {spaces
                  .filter(
                    (s) => s.space_id !== selected && s.state === "active" && !s.deletion_group_id,
                  )
                  .map((s) => (
                    <option key={String(s.space_id)} value={String(s.space_id)}>
                      {String(s.display_name)}
                    </option>
                  ))}
              </select>
            </label>
            <div className="management-form-actions">
              <button className="management-primary" disabled={busy || !destination}>
                이동
              </button>
              <button type="button" onClick={() => setMoveTarget(null)}>
                취소
              </button>
            </div>
          </form>
        )}
        <details>
          <summary>새 자료 만들기</summary>
          <form
            className="management-form"
            onSubmit={(event) => {
              event.preventDefault();
              void work(async () => {
                if (createKind === "space")
                  await contextCall("corpus_space_create", {
                    space_id: newId,
                    display_name: newTitle,
                    purpose: newBody,
                  });
                else if (createKind === "document")
                  await contextCall("corpus_document_create", {
                    space_id: selected,
                    document_id: newId,
                    title: newTitle,
                    body_markdown: newBody,
                    kind: "context",
                    source_scope_version: 2,
                  });
                else
                  await contextCall("corpus_context_item_create", {
                    space_id: selected,
                    item_id: newId,
                    expected_version: context.version,
                    kind: "finding",
                    body_text: newBody,
                  });
                await load(createKind === "space" ? newId : selected);
                setNewId("");
                setNewTitle("");
                setNewBody("");
                setMessage("새 자료를 저장했습니다.");
              });
            }}
          >
            <label>
              종류
              <select value={createKind} onChange={(e) => setCreateKind(e.target.value)}>
                <option value="document">Context 문서</option>
                <option value="context_item">Context 항목</option>
                <option value="space">Space</option>
              </select>
            </label>
            <label>
              식별자
              <input
                required
                value={newId}
                pattern={
                  createKind === "space" ? "[a-z0-9][a-z0-9._-]*" : "[A-Za-z0-9][A-Za-z0-9._:@-]*"
                }
                onChange={(e) => setNewId(e.target.value)}
              />
            </label>
            {createKind !== "context_item" && (
              <label>
                이름
                <input required value={newTitle} onChange={(e) => setNewTitle(e.target.value)} />
              </label>
            )}
            <label>
              {createKind === "space" ? "목적" : "본문"}
              <textarea
                required={createKind !== "space"}
                value={newBody}
                onChange={(e) => setNewBody(e.target.value)}
              />
            </label>
            <button
              className="management-primary"
              disabled={busy || (!selected && createKind !== "space")}
            >
              새로 저장
            </button>
          </form>
        </details>
      </section>
      {selected && (
        <section>
          <h2>등록 연결</h2>
          <p>
            Sync가 실행 중인 작업의 종료와 로컬 연결 해제를 확인해야 완료됩니다. 원본 파일과 보존된
            Source는 삭제하지 않습니다.
          </p>
          <ul className="management-list">
            {rows(registrations.connections).map((connection) => (
              <li key={String(connection.connection_id)}>
                <div>
                  {String(connection.connection_id)} · {String(connection.configuration_state)} ·
                  버전 {String(connection.generation)}
                </div>
                <button
                  disabled={busy || connection.configuration_state !== "ready"}
                  onClick={() => {
                    setDetachTarget({ ...connection, kind: "connection" });
                    requestKey.current = crypto.randomUUID();
                  }}
                >
                  연결 해제 검토
                </button>
              </li>
            ))}
            {rows(registrations.workspaces).map((workspace) => (
              <li key={String(workspace.host_id) + String(workspace.workspace_id)}>
                <div>
                  {String(workspace.host_id)} / {String(workspace.workspace_id)}
                </div>
                <button
                  disabled={busy}
                  onClick={() => {
                    setDetachTarget({ ...workspace, kind: "workspace" });
                    setDevice("");
                    requestKey.current = crypto.randomUUID();
                  }}
                >
                  Workspace 연결 해제
                </button>
              </li>
            ))}
          </ul>
          {rows(registrations.detachments).map((row) => (
            <p key={String(row.registration_key)}>
              {String(row.registration_key)}:{" "}
              {row.state === "detached"
                ? "연결 해제 완료"
                : row.state === "blocked"
                  ? "확인 필요"
                  : "Sync 확인 대기"}
              {record(row.result).reason ? ` · ${String(record(row.result).reason)}` : ""}
            </p>
          ))}
          {detachTarget && (
            <form
              className="management-inline-form"
              onSubmit={(event) => {
                event.preventDefault();
                void work(async () => {
                  const target = detachTarget;
                  const result = await contextCall(
                    target.kind === "connection"
                      ? "corpus_connection_detach"
                      : "corpus_workspace_detach",
                    {
                      space_id: selected,
                      idempotency_key: requestKey.current,
                      ...(target.kind === "connection"
                        ? {
                            connection_id: target.connection_id,
                            expected_generation: target.generation,
                          }
                        : {
                            host_id: target.host_id,
                            workspace_id: target.workspace_id,
                            expected_version: target.version,
                            device_id: device,
                          }),
                    },
                  );
                  setDetachTarget(null);
                  await load(selected);
                  setMessage(
                    result.state === "detached"
                      ? "연결 해제를 완료했습니다."
                      : "Sync의 확인을 기다리고 있습니다. 아직 해제 완료가 아닙니다.",
                  );
                });
              }}
            >
              <p>이 등록 연결을 해제하시겠습니까? 파일은 그대로 둡니다.</p>
              {detachTarget.kind === "workspace" && (
                <label>
                  해당 Workspace가 등록된 Sync
                  <select
                    required
                    value={device}
                    onChange={(event) => setDevice(event.target.value)}
                  >
                    <option value="">Sync 선택</option>
                    {rows(registrations.devices).map((item) => (
                      <option key={String(item.device_id)} value={String(item.device_id)}>
                        {String(item.display_name)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <div className="management-form-actions">
                <button className="management-primary" disabled={busy}>
                  연결 해제 요청
                </button>
                <button type="button" disabled={busy} onClick={() => setDetachTarget(null)}>
                  취소
                </button>
              </div>
            </form>
          )}
        </section>
      )}
      <section aria-labelledby="trash-heading">
        <h2 id="trash-heading">휴지통</h2>
        <p>
          삭제한 시각부터 30일이 지나면 다음 정리 때 영구 삭제합니다. 참조나 연결이 남은 자료는
          보존합니다.
        </p>
        <p className="management-meta">
          {loaded && (maintenance.enabled ? "매일 오전 4시 자동 정리" : "자동 정리 비활성")}
          {record(maintenance.last_run).finished_at
            ? ` · 최근 확인 ${date(record(maintenance.last_run).finished_at)}`
            : ""}
        </p>
        {loaded && !trash.length && <p className="management-empty">휴지통이 비어 있습니다.</p>}
        <ul className="management-list">
          {trash.map((group) => (
            <li key={String(group.deletion_group_id)}>
              <div>
                <strong>{String(group.root_id)}</strong>
                <p>
                  {date(group.trashed_at)} 삭제 · {date(group.purge_after)} 이후 정리
                </p>
                <details>
                  <summary>묶음 자료 {rows(group.members).length}개</summary>
                  <ul>
                    {rows(group.members).map((m) => (
                      <li key={String(m.kind) + String(m.id)}>
                        {label(String(m.kind))} · {String(m.locator)}
                      </li>
                    ))}
                  </ul>
                </details>
                {rows(group.blockers).map((b) => (
                  <p key={String(b.code)}>삭제 보류: {String(b.message)}</p>
                ))}
              </div>
              <div className="management-actions">
                <button
                  disabled={busy || !group.restorable}
                  onClick={() => void review("restore", undefined, String(group.deletion_group_id))}
                >
                  묶음 복원
                </button>
                <button
                  disabled={busy}
                  onClick={() => void review("purge", undefined, String(group.deletion_group_id))}
                >
                  영구 삭제 검토
                </button>
              </div>
            </li>
          ))}
        </ul>
      </section>
      {trashNext !== null && (
        <button
          disabled={busy}
          onClick={() =>
            void work(async () => {
              const next = await contextCall("corpus_trash_list", {
                offset: trashNext,
              });
              setTrash((previous) => [...previous, ...rows(next.groups)]);
              setTrashNext(next.next_offset as number | null);
            })
          }
        >
          휴지통 더 보기
        </button>
      )}
      {spaceNext !== null && (
        <button
          disabled={busy}
          onClick={() =>
            void work(async () => {
              const next = await contextCall("corpus_space_list", {
                lifecycle: "all",
                offset: spaceNext,
              });
              setSpaces((previous) => [...previous, ...rows(next.spaces)]);
              setSpaceNext(next.next_offset as number | null);
            })
          }
        >
          Space 더 보기
        </button>
      )}
      {impact && (
        <dialog
          ref={dialog}
          className="management-dialog"
          onCancel={() => setImpact(null)}
          aria-labelledby="impact-title"
        >
          <h2 id="impact-title">
            {impact.action === "trash"
              ? "휴지통으로 옮길 자료"
              : impact.action === "restore"
                ? "복원할 자료"
                : "영구 삭제할 자료"}
          </h2>
          <ul>
            {impact.members.map((m) => (
              <li key={m.kind + m.id}>
                {label(m.kind)} · {m.locator}
              </li>
            ))}
          </ul>
          {impact.action === "trash" && (
            <p>30일 뒤 영구 삭제 대상이 됩니다. 원본 파일이나 연결 설정은 삭제하지 않습니다.</p>
          )}
          {impact.blockers.map((b) => (
            <p key={b.code}>삭제 보류: {b.message}</p>
          ))}
          {impact.action === "purge" && (
            <label>
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />{" "}
              위 자료를 영구 삭제하며 복원할 수 없음을 확인했습니다.
            </label>
          )}
          <div className="management-actions">
            <button
              disabled={
                busy ||
                (impact.action === "purge" && (!confirmed || Boolean(impact.blockers.length)))
              }
              onClick={() => void applyImpact()}
            >
              {impact.action === "trash"
                ? "휴지통으로"
                : impact.action === "restore"
                  ? "묶음 복원"
                  : "영구 삭제"}
            </button>
            <button disabled={busy} onClick={() => setImpact(null)}>
              취소
            </button>
          </div>
        </dialog>
      )}
    </main>
  );
}
