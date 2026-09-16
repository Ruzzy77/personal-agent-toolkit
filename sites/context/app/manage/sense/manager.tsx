"use client";
import { UiButton, UiInput, UiTextarea, Checkbox, IconButton } from "../../ui";
import { ArrowDown, ArrowUp, RotateCcw, Trash2 } from "lucide-react";
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
          ? "지침 또는 Skill 변경. 입력은 유지됩니다. 새로고침 후 확인하세요."
          : "저장 결과 미확인. 새로고침 후 상태를 확인하세요.",
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
      setMessage("저장 완료");
    });
  }
  return (
    <main className="management-page su-workspace su-stack" data-gap="section">
      <header className="management-page-header management-page-header-with-action su-toolbar">
        <div>
          <h1>Sense</h1>
        </div>
        <UiButton disabled={busy} onClick={() => void work(load)}>
          새로고침
        </UiButton>
      </header>
      {loaded && !enabled && <p>관리 기능 비활성</p>}
      <p className="management-message" role="status" aria-live="polite">
        {message || (!loaded ? "불러오는 중…" : "")}
      </p>

      <section className="su-section">
        <h2>일반 섹션</h2>
        <ol className="management-list">
          {sections.map((section, index) => (
            <li key={section.id} className="sense-section-row">
              <div className="sense-section-copy">
                <strong>{section.purpose}</strong>
                {section.skill && <div className="sense-skill su-row">
                  <span className="management-meta">Skill: {section.skill.name}</span>
                  <IconButton type="button" label="스킬 삭제" aria-label={`${section.skill.name} 삭제`}
                    disabled={busy || !enabled} onClick={() => void review("trash", section.id, "skill")}>
                    <Trash2 size="1em" aria-hidden="true"/>
                  </IconButton>
                </div>}
              </div>
              <div className="management-actions su-row" role="group" aria-label={`${section.purpose} 도구`}>
                <div className="management-reorder su-row">
                  <IconButton type="button" label="위로" aria-label={`${section.purpose} 위로`}
                    disabled={busy || !enabled || index === 0} onClick={() => void reorder(index, -1)}>
                    <ArrowUp size="1em" aria-hidden="true"/>
                  </IconButton>
                  <IconButton type="button" label="아래로" aria-label={`${section.purpose} 아래로`}
                    disabled={busy || !enabled || index === sections.length - 1} onClick={() => void reorder(index, 1)}>
                    <ArrowDown size="1em" aria-hidden="true"/>
                  </IconButton>
                </div>
                <IconButton type="button" label="섹션 삭제" aria-label={`${section.purpose} 삭제`}
                  disabled={busy || !enabled} onClick={() => void review("trash", section.id, "section")}>
                  <Trash2 size="1em" aria-hidden="true"/>
                </IconButton>
              </div>
            </li>
          ))}
        </ol>
      </section>
      <section className="su-section">
        <h2>일반 섹션 추가</h2>
        <form
          className="management-form su-stack"
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
          <label className="su-field">
            식별자
            <UiInput
              required
              pattern="[a-z0-9]+(-[a-z0-9]+)*"
              maxLength={64}
              value={id}
              onChange={(e) => setId(e.target.value)}
            />
          </label>
          <label className="su-field">
            목적
            <UiInput
              required
              maxLength={320}
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
            />
          </label>
          <label className="su-field">
            지침 본문
            <UiTextarea
              required
              maxLength={12000}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <UiButton className="management-primary" disabled={busy || !enabled || !digest}>
            새 섹션 저장
          </UiButton>
        </form>
      </section>
      <section className="su-section">
        <h2>휴지통</h2>
        <p className="management-meta">
          30일 보관{loaded && (sweepEnabled ? " · 자동 정리 켜짐" : " · 자동 정리 꺼짐")}
        </p>
        {loaded && !trash.length && <p className="management-empty">비어 있음</p>}
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
              <div className="management-actions su-row">
                <IconButton type="button" label="복원 검토"
                  disabled={busy || !enabled}
                  onClick={() =>
                    void review("restore", undefined, undefined, group.deletion_group_id)
                  }
                ><RotateCcw size="1em" aria-hidden="true"/></IconButton>
                <UiButton type="button"
                  disabled={busy || !enabled}
                  onClick={() =>
                    void review("purge", undefined, undefined, group.deletion_group_id)
                  }
                >영구 삭제 검토</UiButton>
              </div>
            </li>
          ))}
        </ul>
        {next !== null && (
          <UiButton
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
          </UiButton>
        )}
      </section>
      {impact && (
        <dialog
          className="su-dialog management-dialog"
          ref={dialog}
          aria-labelledby="sense-impact"
          onCancel={(event) => {
            if (busy) event.preventDefault();
            else setImpact(null);
          }}
        >
          <div className="su-section">
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
              <label className="su-row" data-align="start">
                <Checkbox
                  checked={confirmed}
                  onCheckedChange={setConfirmed}
                />{" "}
                위 자료를 영구 삭제하며 복원할 수 없음을 확인했습니다.
              </label>
            )}
            <div className="management-actions su-row">
              <UiButton
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
              </UiButton>
              <UiButton disabled={busy} onClick={() => setImpact(null)}>
                취소
              </UiButton>
            </div>
          </div>
        </dialog>
      )}
    </main>
  );
}
