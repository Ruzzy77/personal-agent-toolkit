'use client';

import {
  Archive,
  Check,
  History,
  Pause,
  Plus,
  RotateCcw,
  Search,
  X,
} from 'lucide-react';
import {
  type ReactNode,
  type SyntheticEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';

import type {
  BoardResult,
  IngestResult,
  ItemDetailResult,
  ItemSearchResult,
  JournalItem,
  PeriodKind,
  PeriodResult,
  Resolution,
  Responsibility,
  WeekClosePreparation,
  WeekClosureResult,
} from '@/lib/journal';

const laneLabels = {
  today: '오늘',
  direct: '직접 처리',
  waiting: '대기',
  attention: '주의',
} as const;

const resolutionLabels = {
  active: '진행 중',
  held: '보류',
  completed: '완료',
  canceled: '취소',
} as const;

const responsibilityLabels = {
  user: '나',
  counterparty: '상대방',
  system: '시스템',
} as const;

type ApiEnvelope<T> =
  | { ok: true; result: T }
  | { ok: false; error: { code: string; message: string } };

type ResolutionResult = { item: JournalItem; duplicate: boolean };

type AddItemInput = {
  title: string;
  summary: string;
  lane: JournalItem['lane'];
  responsibility: Responsibility | null;
  projectKey: string | null;
};

type FindItemInput = { query: string; projectKey: string };

async function responseResult<T>(
  response: Response,
  fallback: string,
): Promise<T> {
  const payload = (await response.json()) as ApiEnvelope<T>;
  if (!response.ok || !payload.ok) {
    throw new Error(payload.ok ? fallback : payload.error.message);
  }
  return payload.result;
}

async function fetchBoard(weekId: string): Promise<BoardResult> {
  return responseResult<BoardResult>(
    await fetch(`/api/journal/board?week=${encodeURIComponent(weekId)}`, {
      cache: 'no-store',
    }),
    '진행 보드를 갱신하지 못했습니다.',
  );
}

function addDays(date: string, days: number): string {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

function shortDate(date: string, index: number): string {
  const [, month, day] = date.split('-');
  return `${'월화수목금토일'[index]} ${Number(month)}.${Number(day)}`;
}

function dueTime(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
}

function eventTime(value: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

function asResolutionToolInput(input: unknown): {
  itemId: string;
  resolution: Resolution;
} {
  if (!input || typeof input !== 'object') {
    throw new Error('itemId와 resolution이 필요합니다.');
  }
  const value = input as Record<string, unknown>;
  if (
    typeof value.itemId !== 'string' ||
    !['active', 'held', 'completed', 'canceled'].includes(
      String(value.resolution),
    )
  ) {
    throw new Error('itemId 또는 resolution이 올바르지 않습니다.');
  }
  return {
    itemId: value.itemId,
    resolution: value.resolution as Resolution,
  };
}

function asAddToolInput(input: unknown): AddItemInput {
  if (!input || typeof input !== 'object') {
    throw new Error('항목 내용이 필요합니다.');
  }
  const value = input as Record<string, unknown>;
  const title = typeof value.title === 'string' ? value.title.trim() : '';
  const summary = typeof value.summary === 'string' ? value.summary.trim() : '';
  const lane = String(value.lane);
  const responsibility =
    typeof value.responsibility === 'string' ? value.responsibility : null;
  if (
    !title ||
    title.length > 240 ||
    !summary ||
    summary.length > 1000 ||
    !['today', 'direct', 'waiting', 'attention'].includes(lane) ||
    (responsibility !== null &&
      !['user', 'counterparty', 'system'].includes(responsibility))
  ) {
    throw new Error('항목 내용이 올바르지 않습니다.');
  }
  return {
    title,
    summary,
    lane: lane as JournalItem['lane'],
    responsibility: responsibility as Responsibility | null,
    projectKey:
      typeof value.projectKey === 'string' && value.projectKey.trim()
        ? value.projectKey.trim()
        : null,
  };
}

function asFindToolInput(input: unknown): FindItemInput {
  if (!input || typeof input !== 'object') return { query: '', projectKey: '' };
  const value = input as Record<string, unknown>;
  const query = typeof value.query === 'string' ? value.query.trim() : '';
  const projectKey =
    typeof value.projectKey === 'string' ? value.projectKey.trim() : '';
  if (query.length > 240 || projectKey.length > 120) {
    throw new Error('검색 조건이 너무 깁니다.');
  }
  return { query, projectKey };
}

function asPeriodToolInput(input: unknown): {
  kind: PeriodKind;
  anchor: string;
} {
  if (!input || typeof input !== 'object') {
    throw new Error('kind와 anchor가 필요합니다.');
  }
  const value = input as Record<string, unknown>;
  if (
    !['week', 'month', 'quarter', 'year'].includes(String(value.kind)) ||
    typeof value.anchor !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}$/.test(value.anchor)
  ) {
    throw new Error('기간이 올바르지 않습니다.');
  }
  return { kind: value.kind as PeriodKind, anchor: value.anchor };
}

function Modal({
  open,
  titleId,
  onClose,
  children,
}: {
  open: boolean;
  titleId: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className="journal-dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      {children}
    </dialog>
  );
}

export function JournalBoard({
  initialBoard,
  today,
}: {
  initialBoard: BoardResult;
  today: string;
}) {
  const [board, setBoard] = useState(initialBoard);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [closingWeek, setClosingWeek] = useState(false);
  const [message, setMessage] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [closePreparation, setClosePreparation] =
    useState<WeekClosePreparation | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<ItemDetailResult | null>(null);
  const [detailError, setDetailError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchProject, setSearchProject] = useState('');
  const [searchResult, setSearchResult] = useState<ItemSearchResult | null>(null);
  const [searching, setSearching] = useState(false);
  const boardRef = useRef(board);

  useEffect(() => {
    boardRef.current = board;
  }, [board]);

  const refreshBoard = useCallback(async () => {
    const refreshed = await fetchBoard(boardRef.current.week.id);
    boardRef.current = refreshed;
    setBoard(refreshed);
    return refreshed;
  }, []);

  async function setResolution(itemId: string, resolution: Resolution) {
      const item = boardRef.current.items.find((entry) => entry.id === itemId);
      if (!item) throw new Error('항목을 찾지 못했습니다.');
      if (boardRef.current.week.status === 'closed') {
        throw new Error('마감된 주차는 상태를 바꿀 수 없습니다.');
      }
      if (item.resolution === resolution) {
        return { item, weekId: boardRef.current.week.id, unchanged: true };
      }

      setPendingId(itemId);
      setMessage('');
      try {
        await responseResult<ResolutionResult>(
          await fetch(
            `/api/journal/items/${encodeURIComponent(itemId)}/resolution`,
            {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                resolution,
                expectedVersion: item.version,
              }),
            },
          ),
          '상태를 바꾸지 못했습니다.',
        );
        const refreshed = await refreshBoard();
        const updated = refreshed.items.find((entry) => entry.id === itemId);
        if (!updated) throw new Error('변경한 항목을 다시 읽지 못했습니다.');
        setMessage(`${updated.title}: ${resolutionLabels[updated.resolution]}`);
        return {
          item: {
            id: updated.id,
            title: updated.title,
            resolution: updated.resolution,
            version: updated.version,
          },
          weekId: refreshed.week.id,
          unchanged: false,
        };
      } finally {
        setPendingId(null);
      }
  }

  const addItem = useCallback(
    async (input: AddItemInput) => {
      if (boardRef.current.week.status === 'closed') {
        throw new Error('마감된 주차에는 항목을 추가할 수 없습니다.');
      }
      const result = await responseResult<IngestResult>(
        await fetch('/api/journal/items', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...input,
            weekId: boardRef.current.week.id,
            clientRequestId: crypto.randomUUID(),
          }),
        }),
        '항목을 추가하지 못했습니다.',
      );
      await refreshBoard();
      setMessage(`${result.item.title}: 추가`);
      return {
        item: {
          id: result.item.id,
          title: result.item.title,
          lane: result.item.lane,
          resolution: result.item.resolution,
          version: result.item.version,
        },
        created: result.created,
      };
    },
    [refreshBoard],
  );

  const findVisibleItems = useCallback(async (input: FindItemInput) => {
    setSearching(true);
    try {
      const params = new URLSearchParams({ limit: '50' });
      if (input.query) params.set('query', input.query);
      if (input.projectKey) params.set('project', input.projectKey);
      const result = await responseResult<ItemSearchResult>(
        await fetch(`/api/journal/items?${params}`, { cache: 'no-store' }),
        '항목을 찾지 못했습니다.',
      );
      setSearchQuery(input.query);
      setSearchProject(input.projectKey);
      setSearchResult(result);
      return {
        count: result.count,
        items: result.items.map((item) => ({
          id: item.id,
          weekId: item.weekId,
          title: item.title,
          projectKey: item.projectKey,
          lane: item.lane,
          resolution: item.resolution,
        })),
      };
    } finally {
      setSearching(false);
    }
  }, []);

  const readPeriod = useCallback(async (kind: PeriodKind, anchor: string) => {
    const params = new URLSearchParams({ kind, anchor });
    const result = await responseResult<PeriodResult>(
      await fetch(`/api/journal/period?${params}`, { cache: 'no-store' }),
      '기간 기록을 불러오지 못했습니다.',
    );
    return {
      kind: result.kind,
      startsOn: result.startsOn,
      endsOn: result.endsOn,
      totals: result.totals,
      projects: result.projects,
      weeks: result.weeks.map((week) => ({
        id: week.id,
        status: week.status,
      })),
    };
  }, []);

  async function openItemDetail(itemId: string) {
    setDetailOpen(true);
    setDetail(null);
    setDetailError('');
    try {
      setDetail(
        await responseResult<ItemDetailResult>(
          await fetch(`/api/journal/items/${encodeURIComponent(itemId)}`, {
            cache: 'no-store',
          }),
          '항목을 불러오지 못했습니다.',
        ),
      );
    } catch (error) {
      setDetailError(
        error instanceof Error ? error.message : '항목을 불러오지 못했습니다.',
      );
    }
  }

  async function prepareClose() {
    setClosingWeek(true);
    setMessage('');
    try {
      const weekId = boardRef.current.week.id;
      const preparation = await responseResult<WeekClosePreparation>(
        await fetch(
          `/api/journal/weeks/${encodeURIComponent(weekId)}/prepare-close`,
          { method: 'POST' },
        ),
        '주간 마감을 준비하지 못했습니다.',
      );
      setClosePreparation(preparation);
    } finally {
      setClosingWeek(false);
    }
  }

  async function confirmClose() {
    if (!closePreparation) return;
    setClosingWeek(true);
    setMessage('');
    try {
      const weekId = boardRef.current.week.id;
      const result = await responseResult<WeekClosureResult>(
        await fetch(
          `/api/journal/weeks/${encodeURIComponent(weekId)}/confirm-close`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              preparationVersion: closePreparation.preparationVersion,
            }),
          },
        ),
        '주간 기록을 마감하지 못했습니다.',
      );
      await refreshBoard();
      setClosePreparation(null);
      setMessage(
        result.summary.rolloverCount > 0
          ? `주간 마감 · 다음 주 이월 ${result.summary.rolloverCount}개`
          : '주간 마감',
      );
    } finally {
      setClosingWeek(false);
    }
  }

  const actionRef = useRef(setResolution);
  const addRef = useRef(addItem);
  const findRef = useRef(findVisibleItems);
  const periodRef = useRef(readPeriod);
  useEffect(() => {
    actionRef.current = setResolution;
    addRef.current = addItem;
    findRef.current = findVisibleItems;
    periodRef.current = readPeriod;
  });

  useEffect(() => {
    const context = document.modelContext;
    if (!context?.registerTool) return;
    const lifecycle = new AbortController();
    const registrations = [
      context.registerTool(
        {
          name: 'journal_read_board',
          title: '진행 보드 읽기',
          description:
            '현재 페이지에 열린 주간 진행 보드의 항목과 처리 상태를 읽습니다.',
          inputSchema: {
            type: 'object',
            properties: {},
            additionalProperties: false,
          },
          annotations: { readOnlyHint: true, untrustedContentHint: false },
          execute() {
            const current = boardRef.current;
            return {
              week: { id: current.week.id, status: current.week.status },
              summary: current.summary,
              items: current.items.map((item) => ({
                id: item.id,
                logicalItemId: item.logicalItemId,
                title: item.title,
                projectKey: item.projectKey,
                lane: item.lane,
                resolution: item.resolution,
                responsibility: item.responsibility,
                summary: item.summary,
                version: item.version,
              })),
            };
          },
        },
        { signal: lifecycle.signal },
      ),
      context.registerTool(
        {
          name: 'journal_find_items',
          title: 'Journal 항목 찾기',
          description:
            'Journal 기록에서 문구와 프로젝트로 항목을 찾고 화면의 검색 결과를 갱신합니다.',
          inputSchema: {
            type: 'object',
            properties: {
              query: { type: 'string', maxLength: 240 },
              projectKey: { type: 'string', maxLength: 120 },
            },
            additionalProperties: false,
          },
          annotations: { readOnlyHint: true, untrustedContentHint: false },
          execute(input) {
            return findRef.current(asFindToolInput(input));
          },
        },
        { signal: lifecycle.signal },
      ),
      context.registerTool(
        {
          name: 'journal_add_item',
          title: 'Journal 항목 추가',
          description:
            '현재 페이지의 열린 주에 항목을 추가하고 진행 보드를 갱신합니다.',
          inputSchema: {
            type: 'object',
            properties: {
              title: { type: 'string', minLength: 1, maxLength: 240 },
              summary: { type: 'string', minLength: 1, maxLength: 1000 },
              lane: {
                type: 'string',
                enum: ['today', 'direct', 'waiting', 'attention'],
              },
              responsibility: {
                type: 'string',
                enum: ['user', 'counterparty', 'system'],
              },
              projectKey: { type: 'string', maxLength: 120 },
            },
            required: ['title', 'summary', 'lane'],
            additionalProperties: false,
          },
          annotations: { readOnlyHint: false, untrustedContentHint: false },
          execute(input) {
            return addRef.current(asAddToolInput(input));
          },
        },
        { signal: lifecycle.signal },
      ),
      context.registerTool(
        {
          name: 'journal_set_item_resolution',
          title: 'Journal 항목 처리',
          description:
            '현재 페이지의 Journal 항목을 진행 중, 보류, 완료 또는 취소로 확정하고 보드를 갱신합니다.',
          inputSchema: {
            type: 'object',
            properties: {
              itemId: { type: 'string', format: 'uuid' },
              resolution: {
                type: 'string',
                enum: ['active', 'held', 'completed', 'canceled'],
              },
            },
            required: ['itemId', 'resolution'],
            additionalProperties: false,
          },
          annotations: { readOnlyHint: false, untrustedContentHint: false },
          execute(input) {
            const value = asResolutionToolInput(input);
            return actionRef.current(value.itemId, value.resolution);
          },
        },
        { signal: lifecycle.signal },
      ),
      context.registerTool(
        {
          name: 'journal_read_period',
          title: 'Journal 기간 기록 읽기',
          description:
            '주, 월, 분기 또는 연 단위의 상태 합계와 프로젝트 기록을 읽습니다.',
          inputSchema: {
            type: 'object',
            properties: {
              kind: {
                type: 'string',
                enum: ['week', 'month', 'quarter', 'year'],
              },
              anchor: { type: 'string', format: 'date' },
            },
            required: ['kind', 'anchor'],
            additionalProperties: false,
          },
          annotations: { readOnlyHint: true, untrustedContentHint: false },
          execute(input) {
            const value = asPeriodToolInput(input);
            return periodRef.current(value.kind, value.anchor);
          },
        },
        { signal: lifecycle.signal },
      ),
    ];

    for (const registration of registrations) {
      void Promise.resolve(registration).catch((error) => {
        console.error('WebMCP tool registration failed', error);
      });
    }
    return () => lifecycle.abort();
  }, []);

  const isClosed = board.week.status === 'closed';
  const visibleItems = board.items.slice(0, 8);
  const focus = board.items.find(
    (item) => item.lane === 'today' && item.resolution === 'active',
  );
  const flowByDate = new Map(board.flow.map((entry) => [entry.date, entry]));
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = addDays(board.week.startsOn, index);
    return { date, index, events: flowByDate.get(date)?.events ?? [] };
  });
  const pendingCorpusCount = closePreparation
    ? closePreparation.corpusCandidates.filter(
        (candidate) =>
          !closePreparation.reflectedCandidateIds.includes(candidate.itemId),
      ).length
    : 0;

  function handleClick(itemId: string, resolution: Resolution) {
    void setResolution(itemId, resolution).catch((error) => {
      setMessage(
        error instanceof Error ? error.message : '상태를 바꾸지 못했습니다.',
      );
    });
  }

  function handleAdd(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const input = asAddToolInput({
      title: data.get('title'),
      summary: data.get('summary'),
      lane: data.get('lane'),
      responsibility: data.get('responsibility') || null,
      projectKey: data.get('projectKey'),
    });
    void addItem(input)
      .then(() => {
        form.reset();
        setAddOpen(false);
      })
      .catch((error) => {
        setMessage(
          error instanceof Error ? error.message : '항목을 추가하지 못했습니다.',
        );
      });
  }

  function handleSearch(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    void findVisibleItems({
      query: searchQuery.trim(),
      projectKey: searchProject.trim(),
    }).catch((error) => {
      setMessage(
        error instanceof Error ? error.message : '항목을 찾지 못했습니다.',
      );
    });
  }

  function handleCorrection(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const rawNote = data.get('note');
    const note = typeof rawNote === 'string' ? rawNote.trim() : '';
    if (!note) return;
    void fetch(
        `/api/journal/weeks/${encodeURIComponent(detail.item.weekId)}/corrections`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ itemId: detail.item.id, note }),
        },
      )
      .then((response) =>
        responseResult<{ eventId: string }>(
          response,
          '정정 기록을 추가하지 못했습니다.',
        ),
      )
      .then(() => {
        form.reset();
        return openItemDetail(detail.item.id);
      })
      .catch((error) => {
        setDetailError(
          error instanceof Error
            ? error.message
            : '정정 기록을 추가하지 못했습니다.',
        );
      });
  }

  return (
    <>
      <dl className="summary-strip" aria-label="진행 상태 요약">
        <div className="summary-cell is-today">
          <dt>오늘</dt>
          <dd>{board.summary.today}</dd>
        </div>
        <div className="summary-cell">
          <dt>직접 처리</dt>
          <dd>{board.summary.direct}</dd>
        </div>
        <div className="summary-cell is-waiting">
          <dt>대기</dt>
          <dd>{board.summary.waiting}</dd>
        </div>
        <div className="summary-cell is-complete">
          <dt>이번 주 완료</dt>
          <dd>{board.summary.completed}</dd>
        </div>
        <div className="summary-cell is-attention">
          <dt>주의</dt>
          <dd>{board.summary.attention}</dd>
        </div>
      </dl>

      {focus && (
        <section className="focus-line" aria-label="오늘 먼저 볼 항목">
          <p className="focus-time">{dueTime(focus.dueAt) ?? '오늘'}</p>
          <div>
            <h2>{focus.title}</h2>
            <p>{focus.summary}</p>
          </div>
        </section>
      )}

      <section className="board-section" aria-labelledby="board-title">
        <div className="section-heading">
          <h2 id="board-title">진행 보드</h2>
          <div className="board-heading-actions">
            <p>
              {board.items.length > 8
                ? `최근 8/${board.items.length}`
                : `${board.items.length}개`}
            </p>
            {!isClosed && (
              <button
                type="button"
                className="icon-button"
                aria-label="항목 추가"
                title="항목 추가"
                onClick={() => setAddOpen(true)}
              >
                <Plus aria-hidden="true" />
              </button>
            )}
          </div>
        </div>

        {visibleItems.length === 0 ? (
          <div className="empty-board">아직 기록된 항목이 없습니다.</div>
        ) : (
          <table className="board" aria-label="업무 진행 상태">
            <thead>
              <tr className="board-head">
                <th scope="col">구분</th>
                <th scope="col">항목</th>
                <th scope="col">현재 상태</th>
                <th className="sr-only" scope="col">
                  처리 상태
                </th>
              </tr>
            </thead>
            <tbody>
              {visibleItems.map((item) => (
                <tr className={`board-row is-${item.resolution}`} key={item.id}>
                  <td>
                    <span className={`lane-label is-${item.lane}`}>
                      <span aria-hidden="true" />
                      {laneLabels[item.lane]}
                    </span>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="item-title-button"
                      onClick={() => void openItemDetail(item.id)}
                    >
                      {item.title}
                    </button>
                    <p className="item-project">
                      {item.projectKey ?? item.sourceKind}
                      {item.resolution !== 'active' && (
                        <span className={`resolution-tag is-${item.resolution}`}>
                          {resolutionLabels[item.resolution]}
                        </span>
                      )}
                    </p>
                  </td>
                  <td className="item-state">{item.summary}</td>
                  <td
                    className="row-actions"
                    aria-label={`${item.title} 처리 상태`}
                  >
                    <button
                      type="button"
                      aria-label={`${item.title} 이력`}
                      title="이력"
                      onClick={() => void openItemDetail(item.id)}
                    >
                      <History aria-hidden="true" />
                    </button>
                    {item.resolution !== 'active' && (
                      <button
                        type="button"
                        aria-label={`${item.title} 다시 진행`}
                        title="다시 진행"
                        disabled={isClosed || pendingId === item.id}
                        onClick={() => handleClick(item.id, 'active')}
                      >
                        <RotateCcw aria-hidden="true" />
                      </button>
                    )}
                    <button
                      type="button"
                      className={
                        item.resolution === 'completed' ? 'is-selected' : ''
                      }
                      aria-label={`${item.title} 완료`}
                      aria-pressed={item.resolution === 'completed'}
                      title="완료"
                      disabled={isClosed || pendingId === item.id}
                      onClick={() => handleClick(item.id, 'completed')}
                    >
                      <Check aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className={item.resolution === 'held' ? 'is-selected' : ''}
                      aria-label={`${item.title} 보류`}
                      aria-pressed={item.resolution === 'held'}
                      title="보류"
                      disabled={isClosed || pendingId === item.id}
                      onClick={() => handleClick(item.id, 'held')}
                    >
                      <Pause aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className={
                        item.resolution === 'canceled' ? 'is-selected' : ''
                      }
                      aria-label={`${item.title} 취소`}
                      aria-pressed={item.resolution === 'canceled'}
                      title="취소"
                      disabled={isClosed || pendingId === item.id}
                      onClick={() => handleClick(item.id, 'canceled')}
                    >
                      <X aria-hidden="true" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <output className="board-message" aria-live="polite">
          {isClosed ? '마감된 주차' : message}
        </output>
      </section>

      <section className="week-section" aria-labelledby="week-title">
        <div className="section-heading">
          <h2 id="week-title">이번 주 흐름</h2>
          <div className="week-heading-meta">
            <p>
              {Number(board.week.startsOn.slice(5, 7))}월{' '}
              {Number(board.week.startsOn.slice(8, 10))}일–
              {Number(board.week.endsOn.slice(5, 7))}월{' '}
              {Number(board.week.endsOn.slice(8, 10))}일
            </p>
            {!isClosed && (
              <button
                type="button"
                className="week-close-button"
                aria-label="이번 주 마감 준비"
                title="주간 마감"
                disabled={closingWeek}
                onClick={() =>
                  void prepareClose().catch((error) => {
                    setMessage(
                      error instanceof Error
                        ? error.message
                        : '주간 마감을 준비하지 못했습니다.',
                    );
                  })
                }
              >
                <Archive aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
        <ol className="week-grid">
          {days.map((day) => (
            <li className={day.date === today ? 'is-current' : ''} key={day.date}>
              <p>
                {shortDate(day.date, day.index)}
                {day.date === today ? ' · 오늘' : ''}
              </p>
              {day.events.length > 0 ? (
                <ul>
                  {day.events.slice(0, 3).map((event) => (
                    <li key={`${event.occurredAt}:${event.eventType}`}>
                      <strong>{event.title ?? event.label}</strong>
                      {event.title && <span>{event.label}</span>}
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="no-event" aria-label="기록 없음">
                  —
                </span>
              )}
            </li>
          ))}
        </ol>
      </section>

      <section className="records-section" aria-labelledby="records-title">
        <div className="section-heading records-heading">
          <h2 id="records-title">항목 찾기</h2>
          {searchResult && <p>{searchResult.count}개</p>}
        </div>
        <form className="record-search" onSubmit={handleSearch}>
          <label>
            <span className="sr-only">검색어</span>
            <input
              type="search"
              value={searchQuery}
              placeholder="검색"
              onChange={(event) => setSearchQuery(event.target.value)}
            />
          </label>
          <label>
            <span className="sr-only">프로젝트</span>
            <input
              value={searchProject}
              placeholder="프로젝트"
              onChange={(event) => setSearchProject(event.target.value)}
            />
          </label>
          <button
            type="submit"
            className="icon-button"
            aria-label="항목 찾기"
            title="찾기"
            disabled={searching}
          >
            <Search aria-hidden="true" />
          </button>
        </form>
        {searchResult && (
          <ol className="record-results">
            {searchResult.items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => void openItemDetail(item.id)}
                >
                  <span>{item.title}</span>
                  <span>
                    {item.weekId} · {item.projectKey ?? '미분류'} ·{' '}
                    {resolutionLabels[item.resolution]}
                  </span>
                </button>
              </li>
            ))}
            {searchResult.items.length === 0 && (
              <li className="record-empty">기록 없음</li>
            )}
          </ol>
        )}
      </section>

      <Modal open={addOpen} titleId="add-item-title" onClose={() => setAddOpen(false)}>
        <div className="dialog-header">
          <h2 id="add-item-title">항목 추가</h2>
          <button
            type="button"
            className="icon-button"
            aria-label="닫기"
            title="닫기"
            onClick={() => setAddOpen(false)}
          >
            <X aria-hidden="true" />
          </button>
        </div>
        <form className="item-form" onSubmit={handleAdd}>
          <label>
            제목
            <input name="title" required maxLength={240} />
          </label>
          <label>
            현재 상태
            <textarea name="summary" required maxLength={1000} rows={3} />
          </label>
          <div className="form-grid">
            <label>
              구분
              <select name="lane" defaultValue="direct">
                <option value="today">오늘</option>
                <option value="direct">직접 처리</option>
                <option value="waiting">대기</option>
                <option value="attention">주의</option>
              </select>
            </label>
            <label>
              담당
              <select name="responsibility" defaultValue="user">
                <option value="user">나</option>
                <option value="counterparty">상대방</option>
                <option value="system">시스템</option>
              </select>
            </label>
          </div>
          <label>
            프로젝트
            <input name="projectKey" maxLength={120} />
          </label>
          <div className="dialog-actions">
            <button type="button" onClick={() => setAddOpen(false)}>
              취소
            </button>
            <button type="submit" className="primary-action">
              추가
            </button>
          </div>
        </form>
      </Modal>

      <Modal
        open={Boolean(closePreparation)}
        titleId="close-week-title"
        onClose={() => setClosePreparation(null)}
      >
        {closePreparation && (
          <>
            <div className="dialog-header">
              <h2 id="close-week-title">주간 마감</h2>
              <button
                type="button"
                className="icon-button"
                aria-label="닫기"
                title="닫기"
                onClick={() => setClosePreparation(null)}
              >
                <X aria-hidden="true" />
              </button>
            </div>
            <dl className="close-summary">
              <div>
                <dt>완료</dt>
                <dd>{closePreparation.summary.completedTitles.length}</dd>
              </div>
              <div>
                <dt>이월</dt>
                <dd>{closePreparation.summary.rolloverCount}</dd>
              </div>
              <div>
                <dt>Corpus</dt>
                <dd>{closePreparation.corpusCandidates.length}</dd>
              </div>
            </dl>
            {closePreparation.rolloverItems.length > 0 && (
              <ul className="close-list">
                {closePreparation.rolloverItems.map((item) => (
                  <li key={item.itemId}>{item.title}</li>
                ))}
              </ul>
            )}
            {pendingCorpusCount > 0 && (
              <output className="dialog-status">
                Corpus 반영 대기 {pendingCorpusCount}개
              </output>
            )}
            <div className="dialog-actions">
              <button type="button" onClick={() => setClosePreparation(null)}>
                취소
              </button>
              <button
                type="button"
                className="primary-action"
                disabled={closingWeek || pendingCorpusCount > 0}
                onClick={() =>
                  void confirmClose().catch((error) => {
                    setMessage(
                      error instanceof Error
                        ? error.message
                        : '주간 기록을 마감하지 못했습니다.',
                    );
                  })
                }
              >
                마감
              </button>
            </div>
          </>
        )}
      </Modal>

      <Modal
        open={detailOpen}
        titleId="item-detail-title"
        onClose={() => setDetailOpen(false)}
      >
        <div className="dialog-header">
          <h2 id="item-detail-title">{detail?.item.title ?? '항목'}</h2>
          <button
            type="button"
            className="icon-button"
            aria-label="닫기"
            title="닫기"
            onClick={() => setDetailOpen(false)}
          >
            <X aria-hidden="true" />
          </button>
        </div>
        {detailError && (
          <p className="dialog-status is-error" role="alert">
            {detailError}
          </p>
        )}
        {!detail && !detailError && <p className="dialog-status">불러오는 중</p>}
        {detail && (
          <>
            <p className="detail-summary">{detail.item.summary}</p>
            <dl className="detail-facts">
              <div>
                <dt>상태</dt>
                <dd>{resolutionLabels[detail.item.resolution]}</dd>
              </div>
              <div>
                <dt>구분</dt>
                <dd>{laneLabels[detail.item.lane]}</dd>
              </div>
              <div>
                <dt>담당</dt>
                <dd>{responsibilityLabels[detail.item.responsibility]}</dd>
              </div>
              <div>
                <dt>프로젝트</dt>
                <dd>{detail.item.projectKey ?? '미분류'}</dd>
              </div>
              {detail.item.sourceRef && (
                <div className="detail-source">
                  <dt>Source</dt>
                  <dd>{detail.item.sourceRef}</dd>
                </div>
              )}
            </dl>
            <section className="detail-section">
              <h3>관련 주차</h3>
              <ol className="related-weeks">
                {detail.relatedItems.map((item) => (
                  <li key={item.id}>
                    <span>{item.weekId}</span>
                    <span>{resolutionLabels[item.resolution]}</span>
                  </li>
                ))}
              </ol>
            </section>
            <section className="detail-section">
              <h3>이력</h3>
              <ol className="history-list">
                {detail.history.map((event) => (
                  <li key={event.id}>
                    <time dateTime={event.occurredAt}>
                      {eventTime(event.occurredAt)}
                    </time>
                    <span>{event.label}</span>
                  </li>
                ))}
              </ol>
            </section>
            {detail.item.weekId === board.week.id && isClosed && (
              <form className="correction-form" onSubmit={handleCorrection}>
                <label>
                  정정
                  <textarea name="note" required maxLength={2000} rows={2} />
                </label>
                <button type="submit">기록</button>
              </form>
            )}
          </>
        )}
      </Modal>
    </>
  );
}
