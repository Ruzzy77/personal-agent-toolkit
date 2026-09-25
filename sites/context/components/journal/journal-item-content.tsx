import type { ItemDetailResult } from '@/lib/journal';
import { laneLabels, resolutionLabels, responsibilityLabels } from '@/lib/journal-labels';
import './journal-item-content.css';

function eventTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

export function JournalItemContent({detail}: {detail:ItemDetailResult}) {
  const {item,relatedItems,history}=detail;
  return <div className="journal-item-content">
    {item.summary&&<p className="journal-item-summary">{item.summary}</p>}
    {item.durableOutcome&&<section className="journal-item-outcome" aria-label="결과"><h3>결과</h3><p>{item.durableOutcome}</p></section>}
    <dl className="journal-item-facts">
      <div><dt>상태</dt><dd>{resolutionLabels[item.resolution]}</dd></div>
      <div><dt>구분</dt><dd>{laneLabels[item.lane]}</dd></div>
      <div><dt>담당</dt><dd>{responsibilityLabels[item.responsibility]}</dd></div>
      <div><dt>프로젝트</dt><dd>{item.projectKey??'미분류'}</dd></div>
      {item.sourceRef&&<div className="journal-item-source"><dt>출처</dt><dd>{item.sourceRef}</dd></div>}
    </dl>
    {relatedItems.length>0&&<section className="journal-item-section"><h3>관련 주차</h3><ol>{relatedItems.map(related=><li key={related.id}><span>{related.weekId}</span><span>{resolutionLabels[related.resolution]}</span></li>)}</ol></section>}
    {history.length>0&&<section className="journal-item-section"><h3>이력</h3><ol>{history.map(event=><li key={event.id}><time dateTime={event.occurredAt}>{eventTime(event.occurredAt)}</time><span>{event.label}</span></li>)}</ol></section>}
  </div>;
}
