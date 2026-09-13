"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { mutationFailureState } from "@personal-agent/site-runtime";
import { contextCall } from "../../../lib/management";

type Section = {
  id: string;
  purpose: string;
  sensitivity: string;
  skill?: { name: string; version: string };
};
type Group = {
  deletion_group_id: string;
  section_id: string;
  title: string;
  purge_after: string;
  state: string;
  version: number;
  blockers: { code: string; message: string }[];
};
type Impact = {
  action: "trash" | "restore" | "purge";
  kind: "section" | "skill";
  section_id: string;
  profile_sha256: string;
  expected_skill_versions: Record<string, string>;
  impact_token: string;
  group?: { id: string; version: number };
  members: { kind: string; id: string; title: string }[];
  blockers: { code: string; message: string }[];
};
export default function SenseManagementPage() {
  const [sections, setSections] = useState<Section[]>([]),
    [digest, setDigest] = useState(""),
    [trash, setTrash] = useState<Group[]>([]);
  const [enabled, setEnabled] = useState(false),
    [next, setNext] = useState<number | null>(null),
    [sweepEnabled, setSweepEnabled] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const reviewTrigger = useRef<HTMLElement | null>(null);
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const [id, setId] = useState(""),
    [purpose, setPurpose] = useState(""),
    [text, setText] = useState("");
  const [impact, setImpact] = useState<Impact | null>(null),
    [confirmed, setConfirmed] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null),
    requestKey = useRef("");
  const load = useCallback(async () => {
    const [profile, bin] = await Promise.all([
      contextCall("sense_read", { view: "index", include_skill: false }),
      contextCall("sense_trash_list", {}),
    ]);
    setSections((profile.sections as Section[]).filter((s) => s.sensitivity === "ordinary"));
    setDigest(String(profile.profile_sha256));
    setTrash(bin.groups as Group[]);
    setEnabled(bin.management_enabled === true);
    setNext((bin.next_offset as number | null) ?? null);
    setSweepEnabled((bin.maintenance as { enabled?: boolean } | undefined)?.enabled === true);
    setLoaded(true);
  }, []);
  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active) void load().catch(() => setMessage("Sense 관리 화면을 불러오지 못했습니다."));
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
      setMessage(
        mutationFailureState(error) === "conflict"
          ? "지침이나 Skill이 변경되었습니다. 입력을 유지했으니 새로고침 후 다시 검토해 주세요."
          : "변경 결과를 확인하지 못했습니다. 저장 완료로 처리하지 않았습니다.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function review(
    action: Impact["action"],
    section_id?: string,
    kind?: Impact["kind"],
    deletion_group_id?: string,
  ) {
    reviewTrigger.current = document.activeElement as HTMLElement | null;
    await work(async () => {
      const data = await contextCall("sense_management_preview", {
        action,
        ...(action === "trash" ? { section_id, kind } : { deletion_group_id }),
      });
      requestKey.current = crypto.randomUUID();
      setConfirmed(false);
      setImpact(data as unknown as Impact);
    });
  }
  async function reorder(index: number, delta: number) {
    await work(async () => {
      const order = sections.map((s) => s.id);
      [order[index], order[index + delta]] = [order[index + delta], order[index]];
      await contextCall("sense_sections_reorder", {
        section_ids: order,
        expected_profile_sha256: digest,
        expected_skill_versions: Object.fromEntries(
          sections.map((s) => [s.id, s.skill?.version ?? "absent"]),
        ),
      });
      await load();
      setMessage("섹션 순서를 저장했습니다.");
    });
  }
  async function apply() {
    if (!impact) return;
    await work(async () => {
      const common = {
        impact_token: impact.impact_token,
        expected_profile_sha256: impact.profile_sha256,
        expected_skill_versions: impact.expected_skill_versions,
        idempotency_key: requestKey.current,
      };
      const result =
        impact.action === "trash"
          ? await contextCall(`sense_${impact.kind}_trash`, {
              ...common,
              section_id: impact.section_id,
            })
          : await contextCall(`sense_trash_${impact.action}`, {
              ...common,
              deletion_group_id: impact.group!.id,
              expected_version: impact.group!.version,
              ...(impact.action === "purge" ? { confirm_permanent_delete: true } : {}),
            });
      if (result.state === "blocked") {
        setMessage("연결된 자료가 있어 처리를 보류했습니다.");
        return;
      }
      setImpact(null);
      await load();
      setMessage("변경을 저장했습니다.");
    });
  }
  return (
    <main className="management-page">
      <header className="management-page-header management-page-header-with-action">
        <div>
          <h1>Sense</h1>
          <p>일반 섹션과 연결된 Skill을 관리합니다. 민감 섹션은 이 화면에서 변경하지 않습니다.</p>
        </div>
        <button disabled={busy} onClick={() => void work(load)}>
          새로고침
        </button>
      </header>
      {loaded && !enabled && <p>관리 기능을 준비 중입니다. 아직 변경 작업은 실행할 수 없습니다.</p>}
      <p className="management-meta">
        {loaded &&
          (sweepEnabled
            ? "자동 정리가 켜져 있습니다."
            : "자동 정리 검토 모드 — 아직 자동으로 삭제하지 않습니다.")}
      </p>
      <p className="management-message" role="status" aria-live="polite">
        {message || (!loaded ? "목록을 불러오는 중입니다." : "")}
      </p>

      <section>
        <h2>섹션 순서와 삭제</h2>
        <ol className="management-list">
          {sections.map((section, index) => (
            <li key={section.id}>
              <div>
                <strong>{section.purpose}</strong>
                {section.skill && <p className="management-meta">Skill: {section.skill.name}</p>}
              </div>
              <div className="management-actions">
                <div className="management-reorder">
                  <button
                    aria-label={`${section.purpose} 위로`}
                    disabled={busy || !enabled || index === 0}
                    onClick={() => void reorder(index, -1)}
                  >
                    위로
                  </button>
                  <button
                    aria-label={`${section.purpose} 아래로`}
                    disabled={busy || !enabled || index === sections.length - 1}
                    onClick={() => void reorder(index, 1)}
                  >
                    아래로
                  </button>
                </div>
                <div className="management-remove">
                  {section.skill && (
                    <button
                      disabled={busy || !enabled}
                      onClick={() => void review("trash", section.id, "skill")}
                    >
                      Skill 제거
                    </button>
                  )}
                  <button
                    disabled={busy || !enabled}
                    onClick={() => void review("trash", section.id, "section")}
                  >
                    섹션을 휴지통으로
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ol>
      </section>
      <section>
        <h2>일반 섹션 추가</h2>
        <form
          className="management-form"
          onSubmit={(event) => {
            event.preventDefault();
            void work(async () => {
              await contextCall("sense_section_create", {
                expected_profile_sha256: digest,
                expected_skill_versions: { [id]: "absent" },
                section: {
                  id,
                  purpose,
                  text,
                  origins: ["user_set"],
                  sensitivity: "ordinary",
                },
              });
              await load();
              setId("");
              setPurpose("");
              setText("");
              setMessage("새 섹션을 저장했습니다.");
            });
          }}
        >
          <label>
            식별자
            <input
              required
              pattern="[a-z0-9]+(-[a-z0-9]+)*"
              maxLength={64}
              value={id}
              onChange={(e) => setId(e.target.value)}
            />
          </label>
          <label>
            목적
            <input
              required
              maxLength={320}
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
            />
          </label>
          <label>
            지침 본문
            <textarea
              required
              maxLength={12000}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <button className="management-primary" disabled={busy || !enabled || !digest}>
            새 섹션 저장
          </button>
        </form>
      </section>
      <section>
        <h2>휴지통</h2>
        <p>삭제한 시각부터 30일이 지나면 다음 정리 때 영구 삭제합니다.</p>
        {loaded && !trash.length && <p className="management-empty">휴지통이 비어 있습니다.</p>}
        <ul className="management-list">
          {trash.map((group) => (
            <li key={group.deletion_group_id}>
              <div>
                <strong>{group.title}</strong>
                <p>
                  {new Date(group.purge_after).toLocaleString("ko-KR", {
                    timeZone: "Asia/Seoul",
                  })}{" "}
                  이후 정리
                </p>
                {group.blockers.map((b) => (
                  <p key={b.code}>삭제 보류: {b.message}</p>
                ))}
              </div>
              <div className="management-actions">
                <button
                  disabled={busy || !enabled}
                  onClick={() =>
                    void review("restore", undefined, undefined, group.deletion_group_id)
                  }
                >
                  복원 검토
                </button>
                <button
                  disabled={busy || !enabled}
                  onClick={() =>
                    void review("purge", undefined, undefined, group.deletion_group_id)
                  }
                >
                  영구 삭제 검토
                </button>
              </div>
            </li>
          ))}
        </ul>
        {next !== null && (
          <button
            disabled={busy}
            onClick={() =>
              void work(async () => {
                const bin = await contextCall("sense_trash_list", {
                  offset: next,
                });
                setTrash((previous) => [...previous, ...(bin.groups as Group[])]);
                setNext((bin.next_offset as number | null) ?? null);
              })
            }
          >
            휴지통 더 보기
          </button>
        )}
      </section>
      {impact && (
        <dialog
          className="management-dialog"
          ref={dialog}
          aria-labelledby="sense-impact"
          onCancel={(event) => {
            if (busy) event.preventDefault();
            else setImpact(null);
          }}
        >
          <h2 id="sense-impact">
            {impact.action === "trash"
              ? "휴지통으로 옮길 자료"
              : impact.action === "restore"
                ? "복원할 자료"
                : "영구 삭제할 자료"}
          </h2>
          <ul>
            {impact.members.map((m) => (
              <li key={m.kind + m.id}>
                {m.kind === "section" ? "섹션" : "Skill"} · {m.title}
              </li>
            ))}
          </ul>
          {impact.blockers.map((b) => (
            <p key={b.code}>처리 보류: {b.message}</p>
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
                busy || Boolean(impact.blockers.length) || (impact.action === "purge" && !confirmed)
              }
              onClick={() => void apply()}
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
