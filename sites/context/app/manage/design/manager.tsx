"use client";
import { contextCall as call } from "../../../lib/management";
import { useCallback, useEffect, useRef, useState } from "react";
import { mutationFailureState } from "@personal-agent/site-runtime";
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
export default function Manager() {
  const [itemMore, setItemMore] = useState(false);
  const [items, setItems] = useState<Row[]>([]),
    [groups, setGroups] = useState<Row[]>([]),
    [next, setNext] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);
  const reviewTrigger = useRef<HTMLElement | null>(null);
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
    const data = await call("design_list_recipes", { limit: 100 });
    setItems(array(data.recipes));
    setItemMore(array(data.recipes).length === 100);
    const bin = await call(`${product}_trash_list`, {});
    setGroups(array(bin.items));
    setNext(bin.next_offset as number | null);
    setEnabled(bin.management_enabled === true);
    setMaintenance(bin.maintenance as Row | null);
    setLoaded(true);
  }, []);
  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active) void load().catch(() => setMessage("목록 로딩 실패. 새로고침하세요."));
    });
    return () => {
      active = false;
    };
  }, [load]);
  useEffect(() => {
    if (!impact) return;
    const previous = reviewTrigger.current,
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
          ? "자료가 변경되었습니다. 결과를 다시 확인하세요."
          : "결과 미확인. 새로고침 후 상태를 확인하세요.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function review(action: Impact["action"], value: Target = {}, id?: string) {
    reviewTrigger.current = document.activeElement as HTMLElement | null;
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
      const locator = { ...target };
      delete locator.kind;
      const result = await call(operation, {
        ...(impact.action === "trash" ? locator : { deletion_group_id: impact.deletion_group_id }),
        expected_version: impact.expected_version,
        impact_token: impact.impact_token,
        idempotency_key: key.current,
        ...(impact.action === "purge" ? { confirm_permanent_delete: true } : {}),
      });
      if (result.state === "blocked") {
        setMessage("삭제 보류: 외부 참조 또는 미완료 작업");
        return;
      }
      setImpact(null);
      setFiles([]);
      await load();
      setMessage(
        result.state === "purging"
          ? "영구 삭제 진행 중 · 복원 불가 · 남은 삭제 재시도 가능"
          : "처리 완료",
      );
    });
  }
  async function moreItems() {
    await work(async () => {
      const data = await call("design_list_recipes", { limit: 100, offset: items.length });
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
      const data = await call("design_read_recipe", { id });
      setRecipe(id);
      setFiles(array(data.files));
    });
  }
  return (
    <main className="management-page">
      <header className="management-page-header management-page-header-with-action">
        <div>
          <h1>Design</h1>
        </div>
        <button disabled={busy} onClick={() => void work(load)}>
          새로고침
        </button>
      </header>
      {loaded && !enabled && <output>관리 기능 비활성</output>}
      <output className="management-message" role="status">
        {message || (!loaded ? "불러오는 중…" : "")}
      </output>

      <section>
        <h2>레시피</h2>
        <ul className="management-list">
          {items.map((item) => (
            <li key={String(item.id)}>
              <div>
                <strong>{String(item.title ?? item.name ?? item.id)}</strong>
                <span>{String(item.id)}</span>
              </div>
              <div className="management-actions">
                <button disabled={busy} onClick={() => void showFiles(String(item.id))}>
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
        <p className="management-meta">30일 보관</p>
        {maintenance && (
          <p className="management-meta">
            마지막 점검 {date(maintenance.started_at)} ·{" "}
            {maintenance.state === "dry_run"
              ? "사전 점검 · 삭제 없음"
              : maintenance.state === "retry_pending"
                ? "재시도 필요"
                : "완료"}
          </p>
        )}
        {loaded && groups.length === 0 && <p className="management-empty">비어 있음</p>}
        <ul className="management-list">
          {groups.map((group) => (
            <li key={String(group.deletion_group_id)}>
              <div>
                <strong>
                  {String(group.issue_id ?? group.recipe_id)}
                  {group.path ? ` / ${String(group.path)}` : ""}
                </strong>
                <span>
                  보관 시작 {date(group.trashed_at)} · 삭제 예정 {date(group.purge_after)}
                </span>
                {array(group.blockers).map((reason) => (
                  <p key={String(reason.code)}>보류: {String(reason.message)}</p>
                ))}
                {group.state === "purging" && <p>영구 삭제 진행 중 · 복원 불가</p>}
              </div>
              <div className="management-actions">
                <button
                  disabled={busy || !enabled || !group.restorable}
                  onClick={() => void review("restore", {}, String(group.deletion_group_id))}
                >
                  복원
                </button>
                <button
                  disabled={busy || !enabled}
                  onClick={() => void review("purge", {}, String(group.deletion_group_id))}
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
          aria-labelledby="asset-impact-title"
          onCancel={(event) => {
            if (busy) event.preventDefault();
            else setImpact(null);
          }}
        >
          <h2 id="asset-impact-title">
            {impact.action === "trash"
              ? "휴지통으로 이동"
              : impact.action === "restore"
                ? "삭제 묶음 복원"
                : "영구 삭제 확인"}
          </h2>
          {impact.action === "trash" && (
            <p>30일 후 영구 삭제 대상입니다. 원본·공유 자산은 보존됩니다.</p>
          )}
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
                busy || (impact.action === "purge" && (!confirmed || impact.blockers.length > 0))
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
