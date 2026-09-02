'use client';

import { Check, Pencil, X } from 'lucide-react';
import { type SyntheticEvent, useState } from 'react';

import type {
  PeriodKind,
  PeriodSummaryVersion,
} from '@/lib/journal';

type ApiEnvelope<T> =
  | { ok: true; result: T }
  | { ok: false; error: { code: string; message: string } };

export function PeriodSummary({
  kind,
  anchor,
  initialVersions,
}: {
  kind: PeriodKind;
  anchor: string;
  initialVersions: PeriodSummaryVersion[];
}) {
  const [versions, setVersions] = useState(initialVersions);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const current = versions.at(-1) ?? null;

  function save(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const value = data.get('body');
    const body = typeof value === 'string' ? value.trim() : '';
    if (!body) return;
    setSaving(true);
    setMessage('');
    void fetch('/api/journal/period-summary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        kind,
        anchor,
        body,
        expectedVersion: current?.version ?? null,
      }),
    })
      .then(async (response) => {
        const payload = (await response.json()) as ApiEnvelope<{
          summary: PeriodSummaryVersion;
        }>;
        if (!response.ok || !payload.ok) {
          throw new Error(
            payload.ok ? '요약을 저장하지 못했습니다.' : payload.error.message,
          );
        }
        setVersions((previous) => [...previous, payload.result.summary]);
        setEditing(false);
      })
      .catch((error) => {
        setMessage(
          error instanceof Error ? error.message : '요약을 저장하지 못했습니다.',
        );
      })
      .finally(() => setSaving(false));
  }

  return (
    <section className="period-summary" aria-labelledby="period-summary-title">
      <div className="period-summary-heading">
        <h3 id="period-summary-title">기간 요약</h3>
        {!editing && (
          <button
            type="button"
            className="icon-button"
            aria-label="기간 요약 편집"
            title="편집"
            onClick={() => setEditing(true)}
          >
            <Pencil aria-hidden="true" />
          </button>
        )}
      </div>
      {editing ? (
        <form onSubmit={save}>
          <textarea
            name="body"
            defaultValue={current?.body ?? ''}
            required
            maxLength={5000}
            rows={4}
            aria-label="기간 요약"
          />
          <div className="period-summary-actions">
            <button
              type="button"
              className="icon-button"
              aria-label="편집 취소"
              title="취소"
              onClick={() => setEditing(false)}
            >
              <X aria-hidden="true" />
            </button>
            <button
              type="submit"
              className="icon-button is-primary"
              aria-label="기간 요약 저장"
              title="저장"
              disabled={saving}
            >
              <Check aria-hidden="true" />
            </button>
          </div>
        </form>
      ) : (
        <p>{current?.body ?? '요약 없음'}</p>
      )}
      {message && (
        <output className="period-summary-message">{message}</output>
      )}
      {versions.length > 1 && (
        <details>
          <summary>이전 버전 {versions.length - 1}</summary>
          <ol>
            {versions.slice(0, -1).reverse().map((version) => (
              <li key={version.id}>
                <span>v{version.version}</span>
                <p>{version.body}</p>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}
