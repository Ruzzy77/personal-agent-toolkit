"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { mutationFailureState } from "@personal-agent/site-runtime";
import "./management.css";
type Row = Record<string, unknown>;
type Target = {
  issue_id?: string;
  recipe_id?: string;
  kind?: string;
  path?: string;
};
type Impact = {
  action: "trash" | "restore" | "purge";
  expected_version: number;
  impact_token: string;
  deletion_group_id?: string;
  members: Row[];
  blockers: { code: string; message: string }[];
};
const product = "design";
const array = (value: unknown): Row[] => (Array.isArray(value) ? value : []);
const date = (value: unknown) =>
  new Date(String(value)).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
async function call(name: string, body: unknown): Promise<Row> {
  const response = await fetch(`/api/${product}/operations/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const result = (await response.json()) as Row;
  if (!response.ok)
    throw Object.assign(new Error(String(result.error ?? "request_failed")), {
      status: response.status,
      code: result.error,
    });
  return result;
}
export default function Manager() {
  const [itemMore, setItemMore] = useState(false);
  const [items, setItems] = useState<Row[]>([]),
    [groups, setGroups] = useState<Row[]>([]),
    [next, setNext] = useState<number | null>(null);
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [enabled, setEnabled] = useState(false),
    [maintenance, setMaintenance] = useState<Row | null>(null);
  const [impact, setImpact] = useState<Impact | null>(null),
    [target, setTarget] = useState<Target>({}),
    [confirmed, setConfirmed] = useState(false),
    [files, setFiles] = useState<Row[]>([]),
    [recipe, setRecipe] = useState("");
  const dialog = useRef<HTMLDialogElement>(null),
    key = useRef("");
  const load = useCallback(async () => {
    const response = await fetch("/api/design/recipes");
    if (!response.ok) throw new Error("list_failed");
    const data = (await response.json()) as Row;
    setItems(array(data.recipes));
    setItemMore(array(data.recipes).length === 100);
    const bin = await call(`${product}_trash_list`, {});
    setGroups(array(bin.items));
    setNext(bin.next_offset as number | null);
    setEnabled(bin.management_enabled === true);
    setMaintenance(bin.maintenance as Row | null);
  }, []);
  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active)
        void load().catch(() =>
          setMessage("목록을 불러오지 못했습니다. 새로고침해 주세요."),
        );
    });
    return () => {
      active = false;
    };
  }, [load]);
  useEffect(() => {
    if (!impact) return;
    const previous = document.activeElement as HTMLElement | null,
      element = dialog.current;
    element?.showModal();
    return () => {
      element?.close();
      previous?.focus();
    };
  }, [impact]);
  async function work(run: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await run();
    } catch (error) {
      setMessage(
        mutationFailureState(error) === "conflict"
          ? "자료가 변경되었습니다. 다시 확인해 주세요. 아직 완료로 처리하지 않았습니다."
          : "처리 결과를 확인하지 못했습니다. 같은 요청을 다시 확인하거나 새로고침해 주세요.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function review(
    action: Impact["action"],
    value: Target = {},
    id?: string,
  ) {
    await work(async () => {
      const preview = await call(`${product}_management_preview`, {
        action,
        ...(action === "trash" ? value : { deletion_group_id: id }),
      });
      setTarget(value);
      setImpact(preview as unknown as Impact);
      key.current = crypto.randomUUID();
      setConfirmed(false);
    });
  }
  async function apply() {
    if (!impact) return;
    await work(async () => {
      const operation =
        impact.action === "trash"
          ? `${product}_${target.kind}_trash`
          : `${product}_trash_${impact.action}`;
      const { kind: _kind, ...locator } = target;
      const result = await call(operation, {
        ...(impact.action === "trash"
          ? locator
          : { deletion_group_id: impact.deletion_group_id }),
        expected_version: impact.expected_version,
        impact_token: impact.impact_token,
        idempotency_key: key.current,
        ...(impact.action === "purge"
          ? { confirm_permanent_delete: true }
          : {}),
      });
      if (result.state === "blocked") {
        setMessage(
          "다른 자료의 참조나 미완료 작업 때문에 삭제를 보류했습니다.",
        );
        return;
      }
      setImpact(null);
      setFiles([]);
      await load();
      setMessage(
        result.state === "purging"
          ? "영구 삭제가 진행 중입니다. 남은 처리는 다시 이어갈 수 있으며, 복원은 불가능합니다."
          : "처리가 완료되었습니다.",
      );
    });
  }
  async function moreItems() {
    await work(async () => {
      const response = await fetch(
        `/api/design/recipes?limit=100&offset=${items.length}`,
      );
      if (!response.ok) throw new Error("list_failed");
      const data = (await response.json()) as Row;
      setItems((previous) => [...previous, ...array(data.recipes)]);
      setItemMore(array(data.recipes).length === 100);
    });
  }
  async function more() {
    await work(async () => {
      const bin = await call(`${product}_trash_list`, { offset: next });
      setGroups((previous) => [...previous, ...array(bin.items)]);
      setNext(bin.next_offset as number | null);
    });
  }
  async function showFiles(id: string) {
    await work(async () => {
      const response = await fetch(
        `/api/design/recipes/${encodeURIComponent(id)}`,
      );
      if (!response.ok) throw new Error("read_failed");
      const data = (await response.json()) as Row;
      setRecipe(id);
      setFiles(array((data.recipe as Row).files));
    });
  }
  return (
    <main className="management-page">
      <Link href="/">← Design로 돌아가기</Link>
      <h1>Design 자료 관리</h1>
      <p>
        휴지통에 넣은 자료는 30일 동안 보존합니다. 원본 파일과 다른 자료가 함께
        쓰는 자산은 삭제하지 않습니다.
      </p>
      {!enabled && (
        <output>
          관리 기능을 준비 중입니다. 아직 변경 작업은 실행할 수 없습니다.
        </output>
      )}
      {maintenance && (
        <p>
          최근 자동 정리: {date(maintenance.started_at)} ·{" "}
          {maintenance.state === "dry_run"
            ? "검토 모드 — 자동 삭제 꺼짐"
            : maintenance.state === "retry_pending"
              ? "일부 처리 재시도 필요"
              : "실행 완료"}
        </p>
      )}
      {message && <output>{message}</output>}
      <button disabled={busy} onClick={() => void work(load)}>
        새로고침
      </button>
      <section>
        <h2>현재 레시피</h2>
        <ul className="management-list">
          {items.map((item) => (
            <li key={String(item.id)}>
              <div>
                <strong>{String(item.title ?? item.name ?? item.id)}</strong>
                <span>{String(item.id)}</span>
              </div>
              <div className="management-actions">
                <button
                  disabled={busy}
                  onClick={() => void showFiles(String(item.id))}
                >
                  파일 관리
                </button>
                <button
                  disabled={busy || !enabled}
                  onClick={() =>
                    void review("trash", {
                      kind: "recipe",
                      recipe_id: String(item.id),
                    })
                  }
                >
                  휴지통으로
                </button>
              </div>
            </li>
          ))}
        </ul>
        {itemMore && (
          <button disabled={busy} onClick={() => void moreItems()}>
            자료 더 보기
          </button>
        )}
      </section>
      {recipe && (
        <section>
          <h2>{recipe} 파일</h2>
          <ul className="management-list">
            {files.map((file) => (
              <li key={String(file.path)}>
                <div>
                  {String(file.path)} · 버전 {String(file.revision)}
                </div>
                <button
                  disabled={busy || !enabled}
                  onClick={() =>
                    void review("trash", {
                      kind: "file",
                      recipe_id: recipe,
                      path: String(file.path),
                    })
                  }
                >
                  휴지통으로
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section>
        <h2>휴지통</h2>
        {groups.length === 0 && <p>휴지통이 비어 있습니다.</p>}
        <ul className="management-list">
          {groups.map((group) => (
            <li key={String(group.deletion_group_id)}>
              <div>
                <strong>
                  {String(group.issue_id ?? group.recipe_id)}
                  {group.path ? ` / ${String(group.path)}` : ""}
                </strong>
                <span>
                  보관 시작 {date(group.trashed_at)} · 삭제 예정{" "}
                  {date(group.purge_after)}
                </span>
                {array(group.blockers).map((reason) => (
                  <p key={String(reason.code)}>
                    보류: {String(reason.message)}
                  </p>
                ))}
                {group.state === "purging" && (
                  <p>영구 삭제 진행 중 · 복원 불가</p>
                )}
              </div>
              <div className="management-actions">
                <button
                  disabled={busy || !enabled || !group.restorable}
                  onClick={() =>
                    void review("restore", {}, String(group.deletion_group_id))
                  }
                >
                  복원
                </button>
                <button
                  disabled={busy || !enabled}
                  onClick={() =>
                    void review("purge", {}, String(group.deletion_group_id))
                  }
                >
                  {group.state === "purging" ? "삭제 재개" : "영구 삭제"}
                </button>
              </div>
            </li>
          ))}
        </ul>
        {next !== null && (
          <button disabled={busy} onClick={() => void more()}>
            휴지통 더 보기
          </button>
        )}
      </section>
      {impact && (
        <dialog
          ref={dialog}
          className="management-dialog"
          onCancel={(event) => {
            if (busy) event.preventDefault();
            else setImpact(null);
          }}
        >
          <h2>
            {impact.action === "trash"
              ? "휴지통으로 이동"
              : impact.action === "restore"
                ? "삭제 묶음 복원"
                : "영구 삭제 확인"}
          </h2>
          <p>
            다음 자료의 현재 버전을 확인했습니다. 다른 곳에서 바뀌면 실행하지
            않습니다.
          </p>
          <ul>
            {impact.members.map((member, index) => (
              <li key={index}>
                {String(member.id ?? member.path ?? member.kind)} · 버전{" "}
                {String(member.version ?? member.revision)}
              </li>
            ))}
          </ul>
          {impact.blockers.map((reason) => (
            <p key={reason.code}>보류: {reason.message}</p>
          ))}
          {impact.action === "purge" && (
            <label>
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />{" "}
              복원할 수 없는 영구 삭제를 요청합니다.
            </label>
          )}
          <div className="management-actions">
            <button disabled={busy} onClick={() => setImpact(null)}>
              취소
            </button>
            <button
              disabled={
                busy ||
                (impact.action === "purge" &&
                  (!confirmed || impact.blockers.length > 0))
              }
              onClick={() => void apply()}
            >
              확인 후 실행
            </button>
          </div>
        </dialog>
      )}
    </main>
  );
}
