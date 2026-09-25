import { ChevronDown } from 'lucide-react';
import Link from 'next/link';

import { requireOwnerUser } from '@/lib/owner-service';
import { JournalBoard } from '@/components/journal/journal-board';
import { PeriodSummary } from '@/components/journal/period-summary';
import {
  currentKstDate,
  emptyBoard,
  getBoard,
  getPeriod,
  type PeriodKind,
  type PeriodResult,
  weekIdForDate,
} from '@/lib/journal';

const PERIODS: Array<{ kind: PeriodKind; label: string }> = [
  { kind: 'week', label: '주' },
  { kind: 'month', label: '월' },
  { kind: 'quarter', label: '분기' },
  { kind: 'year', label: '연' },
];

export const dynamic = 'force-dynamic';

function periodLabel(period: PeriodResult): string {
  if (period.kind === 'week') return '선택한 주';
  if (period.kind === 'month') return `${Number(period.anchor.slice(5, 7))}월`;
  if (period.kind === 'quarter') {
    return `${Math.floor((Number(period.anchor.slice(5, 7)) - 1) / 3) + 1}분기`;
  }
  return `${period.anchor.slice(0, 4)}년`;
}

function safePeriod(value: string | string[] | undefined): PeriodKind {
  const candidate = Array.isArray(value) ? value[0] : value;
  return PERIODS.some((entry) => entry.kind === candidate)
    ? (candidate as PeriodKind)
    : 'week';
}

function safeItem(value: string | string[] | undefined): string | undefined {
  const candidate = Array.isArray(value) ? value[0] : value;
  return candidate && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(candidate)
    ? candidate
    : undefined;
}

function safeWeek(value: string | string[] | undefined): string | undefined {
  const candidate = Array.isArray(value) ? value[0] : value;
  return candidate && /^\d{4}-\d{2}-\d{2}$/.test(candidate)
    ? candidate
    : undefined;
}

export default function Home(props: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <AuthenticatedHome {...props} />;
}

async function AuthenticatedHome({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const today = currentKstDate();
  const selectedWeek = safeWeek(params.week);
  const selectedItem = safeItem(params.item);
  const selectedPeriod = safePeriod(params.period);
  const returnTo = new URLSearchParams();
  if (selectedWeek) returnTo.set('week', selectedWeek);
  if (selectedItem) returnTo.set('item', selectedItem);
  if (params.period !== undefined) returnTo.set('period', selectedPeriod);
  await requireOwnerUser(returnTo.size > 0 ? `/journal?${returnTo}` : '/journal');
  let unavailable = false;
  let board;
  try {
    board = await getBoard(selectedWeek);
  } catch {
    unavailable = true;
    board = emptyBoard(selectedWeek ?? weekIdForDate(today));
  }

  let period: PeriodResult | null = null;
  if (!unavailable) {
    try {
      period = await getPeriod(selectedPeriod, board.week.id);
    } catch {
      period = null;
    }
  }

  const periodTotal = period
    ? Object.values(period.totals).reduce((sum, count) => sum + count, 0)
    : 0;

  return (
    <main className="journal-shell su-workspace">
      <section className="journal-sheet" aria-labelledby="journal-title">
        {unavailable && (
          <p className="service-alert" role="alert">
            Journal에 연결하지 못했습니다.
          </p>
        )}

        <JournalBoard
          key={board.week.id}
          initialBoard={board}
          initialItemId={selectedItem}
          today={today}
          selectedPeriod={selectedPeriod}
        />

        <details
          className="period-section secondary-details"
          open={selectedPeriod !== 'week'}
        >
          <summary className="secondary-summary">
            <div>
              <h2 id="period-title">
                {period ? periodLabel(period) : '기간별 기록'}
              </h2>
            </div>
            <div className="secondary-summary-meta">
              {period && <span>{periodTotal}개</span>}
              <ChevronDown aria-hidden="true" />
            </div>
          </summary>

          <div className="period-details-body su-stack">
            <nav className="period-tabs su-row" aria-label="기록 기간">
              {PERIODS.map(({ kind, label }) => (
                <Link
                  className={selectedPeriod === kind ? 'is-current' : ''}
                  href={`/journal?week=${board.week.id}&period=${kind}`}
                  aria-current={selectedPeriod === kind ? 'page' : undefined}
                  key={kind}
                >
                  {label}
                </Link>
              ))}
            </nav>

            {period ? (
              <div className="period-overview">
                <PeriodSummary
                  kind={period.kind}
                  anchor={period.anchor}
                  initialVersions={period.summaryVersions}
                />
                <dl className="period-totals">
                  <div>
                    <dt>전체</dt>
                    <dd>{periodTotal}</dd>
                  </div>
                  <div>
                    <dt>진행</dt>
                    <dd>{period.totals.active}</dd>
                  </div>
                  <div>
                    <dt>보류</dt>
                    <dd>{period.totals.held}</dd>
                  </div>
                  <div className="is-complete">
                    <dt>완료</dt>
                    <dd>{period.totals.completed}</dd>
                  </div>
                  <div>
                    <dt>취소</dt>
                    <dd>{period.totals.canceled}</dd>
                  </div>
                </dl>
                <div className="project-rollup">
                  <h3>프로젝트</h3>
                  {period.projects.length > 0 ? (
                    <ol>
                      {period.projects.slice(0, 8).map((project) => (
                        <li key={project.projectKey}>
                          <span>{project.projectKey}</span>
                          <span>
                            {project.completed}/{project.total}
                          </span>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p>기록 없음</p>
                  )}
                </div>
                {(period.highlights.length > 0 ||
                  period.longRunning.length > 0) && (
                  <div className="period-flows">
                    {period.highlights.length > 0 && (
                      <section>
                        <h3>주요 결과</h3>
                        <ol>
                          {period.highlights.slice(0, 8).map((item) => (
                            <li key={item.itemId}>
                              <span>{item.title}</span>
                              <span>{item.projectKey ?? '미분류'}</span>
                            </li>
                          ))}
                        </ol>
                      </section>
                    )}
                    {period.longRunning.length > 0 && (
                      <section>
                        <h3>장기 이월</h3>
                        <ol>
                          {period.longRunning.slice(0, 8).map((item) => (
                            <li key={item.logicalItemId}>
                              <span>{item.title}</span>
                              <span>{item.weekCount}주</span>
                            </li>
                          ))}
                        </ol>
                      </section>
                    )}
                  </div>
                )}
                {period.weeks.length > 0 && (
                  <nav className="period-weeks" aria-label="기간 내 주차">
                    {period.weeks.map((week) => (
                      <Link
                        href={`/journal?week=${week.id}&period=${selectedPeriod}`}
                        aria-current={
                          week.id === board.week.id ? 'page' : undefined
                        }
                        key={week.id}
                      >
                        <span>{week.id}</span>
                        <span>주간 기록</span>
                      </Link>
                    ))}
                  </nav>
                )}
              </div>
            ) : (
              <p className="period-empty">기간 기록을 불러오지 못했습니다.</p>
            )}
          </div>
        </details>
      </section>
    </main>
  );
}
