import type { JournalItem } from './journal';

export const laneLabels: Record<JournalItem['lane'], string> = {
  today: '오늘', direct: '직접 처리', waiting: '대기', attention: '주의',
};
export const resolutionLabels: Record<JournalItem['resolution'], string> = {
  active: '진행 중', held: '보류', completed: '완료', canceled: '취소',
};
export const responsibilityLabels: Record<JournalItem['responsibility'], string> = {
  user: '나', counterparty: '상대방', system: '시스템',
};
