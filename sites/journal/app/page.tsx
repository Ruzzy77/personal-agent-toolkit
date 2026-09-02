import { ChevronLeft, ChevronRight } from 'lucide-react';
import Link from 'next/link';

import { JournalBoard } from '@/components/journal-board';
import { PeriodSummary } from '@/components/period-summary';
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

function addDays(date: string, days: number): string {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

function weekNumber(weekId: string): number {
  const date = new Date(`${weekId}T00:00:00Z`);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  return Math.ceil(
    ((date.getTime() - yearStart.getTime()) / 86_400_000 +
      yearStart.getUTCDay() +
      1) /
      7,
  );
}

function headerDate(date: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'long',
    day: 'numeric',
  }).format(new Date(`${date}T12:00:00+09:00`));
}

function weekday(date: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    weekday: 'long',
  }).format(new Date(`${date}T12:00:00+09:00`));
}

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

function safeWeek(value: string | string[] | undefined): string | undefined {
  const candidate = Array.isArray(value) ? value[0] : value;
  return candidate && /^\d{4}-\d{2}-\d{2}$/.test(candidate)
    ? candidate
    : undefined;
}

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const today = currentKstDate();
  const selectedWeek = safeWeek(params.week);
  const selectedPeriod = safePeriod(params.period);
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

  const previousWeek = addDays(board.week.id, -7);
  const nextWeek = addDays(board.week.id, 7);
  const isCurrentWeek =
    board.week.startsOn <= today && today <= board.week.endsOn;
  const boardTitle = isCurrentWeek
    ? `${headerDate(today)} 진행 보드`
    : `${headerDate(board.week.startsOn)}–${headerDate(board.week.endsOn)} 기록`;
  const periodTotal = period
    ? Object.values(period.totals).reduce((sum, count) => sum + count, 0)
    : 0;

  return (
    <main className="journal-shell">
      <section className="journal-sheet" aria-labelledby="journal-title">
        <header className="journal-header">
          <div>
            <p className="journal-kicker">
              Journal · {weekNumber(board.week.id)}주
            </p>
            <h1 id="journal-title">{boardTitle}</h1>
          </div>
          <div className="week-navigation">
            <Link
              href={`/?week=${previousWeek}&period=${selectedPeriod}`}
              aria-label="이전 주"
              title="이전 주"
            >
              <ChevronLeft aria-hidden="true" />
            </Link>
            <p className="journal-meta">
              {isCurrentWeek ? weekday(today) : '주간 기록'}
              <span>
                {board.week.status === 'closed' ? '마감' : '진행 중'} ·{' '}
                {Number(board.week.startsOn.slice(5, 7))}.
                {Number(board.week.startsOn.slice(8, 10))}–
                {Number(board.week.endsOn.slice(5, 7))}.
                {Number(board.week.endsOn.slice(8, 10))}
              </span>
            </p>
            <Link
              href={`/?week=${nextWeek}&period=${selectedPeriod}`}
              aria-label="다음 주"
              title="다음 주"
            >
              <ChevronRight aria-hidden="true" />
            </Link>
          </div>
        </header>

        {unavailable && (
          <p className="service-alert" role="alert">
            Journal에 연결하지 못했습니다.
          </p>
        )}

        <JournalBoard initialBoard={board} today={today} />

        <section className="period-section" aria-labelledby="period-title">
          <div className="section-heading period-heading">
            <div>
              <p className="section-kicker">기록</p>
              <h2 id="period-title">
                {period ? periodLabel(period) : '기간별 기록'}
              </h2>
            </div>
            <nav className="period-tabs" aria-label="기록 기간">
              {PERIODS.map(({ kind, label }) => (
                <Link
                  className={selectedPeriod === kind ? 'is-current' : ''}
                  href={`/?week=${board.week.id}&period=${kind}`}
                  aria-current={selectedPeriod === kind ? 'page' : undefined}
                  key={kind}
                >
                  {label}
                </Link>
              ))}
            </nav>
          </div>

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
                      href={`/?week=${week.id}&period=${selectedPeriod}`}
                      aria-current={week.id === board.week.id ? 'page' : undefined}
                      key={week.id}
                    >
                      <span>{week.id}</span>
                      <span>{week.status === 'closed' ? '마감' : '진행 중'}</span>
                    </Link>
                  ))}
                </nav>
              )}
            </div>
          ) : (
            <p className="period-empty">기간 기록을 불러오지 못했습니다.</p>
          )}
        </section>
      </section>
    </main>
  );
}
