export type DecisionState = 'applied' | 'shadowed' | 'out_of_scope' | 'conflict';
export type ResolutionStatus = 'ready' | 'needs_input' | 'degraded' | 'blocked';

export type GuidanceDecision = {
  id: string;
  state: DecisionState;
  source: string;
  sourceKind: string;
  title: string;
  excerpt: string;
  reason: string;
  authority: string;
  scope: string;
  version: string;
  replaces?: string;
};

export type Scenario = {
  id: string;
  label: string;
  workspace: string;
  taskType: string;
  revision: number;
  status: ResolutionStatus;
  statusText: string;
  statusDetail: string;
  request: string;
  requestNote: string;
  objective: string;
  targets: string[];
  boundary: string;
  completion: string;
  assumption?: string;
  question?: string;
  guidance: GuidanceDecision[];
};

const sharedGuidance: GuidanceDecision[] = [
  {
    id: 'sense-korean-generation',
    state: 'applied',
    source: 'Sense / conversation-and-writing',
    sourceKind: 'Sense',
    title: '한국어 생성 Skill을 사용한다',
    excerpt: '한국어가 포함된 산출물의 생성과 개작 방법은 연결된 Section Skill이 맡습니다.',
    reason: '한국어 화면 문구와 설계 문서가 함께 생성되는 작업입니다.',
    authority: '사용자',
    scope: '한국어 산출물',
    version: 'a3720876',
  },
  {
    id: 'workspace-project-boundary',
    state: 'applied',
    source: 'Agent-Workspace/AGENTS.md',
    sourceKind: 'AGENTS.md',
    title: '최상위 작업 폴더를 독립 프로젝트로 다룬다',
    excerpt: '최상위 작업 폴더마다 독립된 프로젝트로 다룬다.',
    reason: '새 제품을 독립된 작업 폴더에서 설계하고 있습니다.',
    authority: '프로젝트',
    scope: '/Agent-Workspace/**',
    version: '4e820fb1',
  },
  {
    id: 'global-test-default',
    state: 'out_of_scope',
    source: '~/.codex/AGENTS.md',
    sourceKind: 'AGENTS.md',
    title: 'JavaScript 변경 뒤 npm test를 실행한다',
    excerpt: 'Always run npm test after modifying JavaScript files.',
    reason: '현재 프로젝트에는 test script가 선언되어 있지 않습니다.',
    authority: '사용자',
    scope: '전역',
    version: '1d30ce4a',
  },
  {
    id: 'sense-evidence-state',
    state: 'applied',
    source: 'Sense / evidence-and-judgment',
    sourceKind: 'Sense',
    title: '제작·검증·배포 상태를 구분한다',
    excerpt: '화면 열람, 요청 제출, 진행 중, 저장·전송·승인·배포 완료를 같은 상태로 다루지 않습니다.',
    reason: '프로토타입 제작과 게시 완료를 따로 기록해야 합니다.',
    authority: '사용자',
    scope: '상태 보고',
    version: '3d003ac6',
  },
];

export const scenarios: Scenario[] = [
  {
    id: 'continuation',
    label: '설계 이어가기',
    workspace: 'context-control-plane',
    taskType: 'product-design',
    revision: 4,
    status: 'ready',
    statusText: '실행 준비됨',
    statusDetail: '확인 질문 없이 진행할 수 있습니다.',
    request: '다음 설계도 이어서 해보자.',
    requestNote: '앞서 작성한 v0.2 설계에서 제안한 다음 단계를 이어갑니다.',
    objective: 'Resolver의 데이터 계약과 핵심 화면을 구현 가능한 수준으로 만든다.',
    targets: ['JSON Schema', 'MCP 계약', '화면 프로토타입'],
    boundary: '대표 데이터로 작동하는 프로토타입까지 제작합니다. 실제 개인 지침은 변경하지 않습니다.',
    completion: '현재 요청과 이 작업에 필요한 기준을 한 화면에서 확인할 수 있습니다.',
    assumption: '이번 단계의 “진행”은 배포 전 검토용 프로토타입 제작을 뜻합니다.',
    guidance: sharedGuidance,
  },
  {
    id: 'path-override',
    label: '하위 AGENTS 재정의',
    workspace: 'storefront/services/payments',
    taskType: 'code-change',
    revision: 8,
    status: 'ready',
    statusText: '적용 경로 확인됨',
    statusDetail: '하위 디렉터리의 검사 명령이 전역 기본값을 대체합니다.',
    request: '결제 서비스 수정한 뒤 테스트까지 해줘.',
    requestNote: '현재 작업 경로는 services/payments입니다.',
    objective: '결제 서비스 변경을 적용하고 이 경로에 맞는 검사를 실행한다.',
    targets: ['services/payments', 'make test-payments'],
    boundary: 'API 키 교체와 외부 배포는 수행하지 않습니다.',
    completion: '하위 경로 전용 검사가 성공하고 변경 파일이 확인됩니다.',
    guidance: [
      {
        id: 'payments-test',
        state: 'applied',
        source: 'services/payments/AGENTS.override.md',
        sourceKind: 'AGENTS.override.md',
        title: '결제 서비스는 make test-payments로 검사한다',
        excerpt: 'Use make test-payments instead of npm test.',
        reason: '현재 경로와 가장 가까운 명시적 재정의입니다.',
        authority: '프로젝트',
        scope: '/services/payments/**',
        version: 'cc82d410',
        replaces: 'global-test-default',
      },
      {
        ...sharedGuidance[2],
        state: 'shadowed',
        reason: 'payments 하위 경로의 override가 이 기본값을 대체합니다.',
      },
      {
        id: 'payments-key-approval',
        state: 'applied',
        source: 'services/payments/AGENTS.override.md',
        sourceKind: 'AGENTS.override.md',
        title: 'API 키 교체 전 보안 담당자에게 알린다',
        excerpt: 'Never rotate API keys without notifying the security channel.',
        reason: '현재 작업이 키 교체를 요구할 경우 승인 경계가 됩니다.',
        authority: '프로젝트',
        scope: '/services/payments/**',
        version: 'cc82d410',
      },
    ],
  },
  {
    id: 'publish-ambiguity',
    label: '발행 대상 모호함',
    workspace: 'personal-library',
    taskType: 'publish',
    revision: 11,
    status: 'needs_input',
    statusText: '사용자 확인 필요',
    statusDetail: '서로 다른 두 발행 대상 중 하나를 선택해야 합니다.',
    request: '이 버전으로 올려줘.',
    requestNote: '대화에는 Daily 초안과 Digest 초안이 함께 열려 있습니다.',
    objective: '채택된 원고를 Personal Library에 발행한다.',
    targets: ['Daily 초안?', 'Digest 초안?'],
    boundary: '발행은 되돌릴 수 있지만 외부 독자에게 즉시 노출됩니다.',
    completion: '선택한 원고가 발행되고 실제 페이지에서 확인됩니다.',
    question: 'Daily와 Digest 가운데 어느 원고를 발행할까요?',
    guidance: [
      {
        id: 'publish-verify',
        state: 'applied',
        source: 'Sense / evidence-and-judgment',
        sourceKind: 'Sense',
        title: '발행 요청과 발행 완료를 구분한다',
        excerpt: '저장·전송·승인·배포 완료를 같은 상태로 다루지 않습니다.',
        reason: '실제 페이지 확인이 완료 조건에 포함됩니다.',
        authority: '사용자',
        scope: '외부 발행',
        version: '3d003ac6',
      },
      {
        id: 'publish-target-conflict',
        state: 'conflict',
        source: 'Request State',
        sourceKind: 'Request Event',
        title: '발행 대상이 하나로 정해지지 않음',
        excerpt: 'Daily 초안과 Digest 초안이 모두 현재 지시어의 후보입니다.',
        reason: '결과가 외부에 노출되므로 추론만으로 선택하지 않습니다.',
        authority: '과업',
        scope: '현재 요청',
        version: 'revision-11',
      },
    ],
  },
  {
    id: 'missing-source',
    label: '필수 출처 누락',
    workspace: 'research-note',
    taskType: 'technical-writing',
    revision: 6,
    status: 'degraded',
    statusText: '제한된 상태로 해석됨',
    statusDetail: 'Sense 연결은 확인했지만 현재 내용을 읽지 못했습니다.',
    request: '이 연구 노트의 결론을 다듬어줘.',
    requestNote: '프로젝트 문서는 읽었지만 연결된 한국어 생성 Skill을 불러오지 못했습니다.',
    objective: '연구 노트의 근거 수준을 유지하면서 결론을 다듬는다.',
    targets: ['연구 노트 결론'],
    boundary: '원문 주장보다 강한 결론을 만들지 않습니다.',
    completion: '출처가 허용하는 주장 수준과 열린 쟁점이 구분됩니다.',
    assumption: '누락된 Skill을 자동으로 복원하지 않고 출처 재연결을 기다립니다.',
    guidance: [
      {
        id: 'sense-unavailable',
        state: 'conflict',
        source: 'Sense / conversation-and-writing',
        sourceKind: 'Sense',
        title: '연결된 Skill 내용을 읽을 수 없음',
        excerpt: '출처는 선언되어 있으나 현재 snapshot을 만들지 못했습니다.',
        reason: '필수 생성 방법이 누락되어 Resolution이 degraded 상태입니다.',
        authority: '사용자',
        scope: '한국어 산출물',
        version: 'unavailable',
      },
      {
        id: 'research-agents',
        state: 'applied',
        source: 'research-note/AGENTS.md',
        sourceKind: 'AGENTS.md',
        title: '주장 강도와 근거 상태를 보존한다',
        excerpt: '관찰과 가능한 설명, 열린 결론을 구분한다.',
        reason: '현재 연구 노트에 직접 적용되는 프로젝트 지침입니다.',
        authority: '프로젝트',
        scope: '/research-note/**',
        version: '205f31c8',
      },
    ],
  },
];

export const sources = [
  { id: 'src-workspace-agents', name: 'Agent-Workspace/AGENTS.md', kind: 'AGENTS.md', coverage: '관리됨', scope: '/Agent-Workspace/**', version: '4e820fb1', observed: '방금' },
  { id: 'src-codex-agents', name: '~/.codex/AGENTS.md', kind: '사용자 지침', coverage: '관리됨', scope: '전역', version: '1d30ce4a', observed: '방금' },
  { id: 'src-sense', name: 'Sense', kind: '사용자 판단', coverage: '관리됨', scope: '여러 작업', version: 'profile-23', observed: '방금' },
  { id: 'src-skills', name: 'Sense Section Skills', kind: '작업 방법', coverage: '관리됨', scope: '연결 Section', version: '4 skills', observed: '1분 전' },
  { id: 'src-runtime', name: 'Codex runtime instructions', kind: '런타임', coverage: '접근 불가', scope: '현재 세션', version: '비공개', observed: '선언됨' },
];

export const runStages = [
  { key: 'available', title: '접근', state: 'complete', description: '5개 출처 가운데 4개 원문을 읽었습니다.' },
  { key: 'selected', title: '선택', state: 'complete', description: '12개 Clause를 현재 작업 후보로 계산했습니다.' },
  { key: 'planned', title: '계획', state: 'complete', description: '10개 Clause가 Execution Brief에 반영되었습니다.' },
  { key: 'acted', title: '실행', state: 'current', description: '프로토타입과 데이터 계약을 제작하고 있습니다.' },
  { key: 'verified', title: '검증', state: 'pending', description: '빌드와 배포 결과를 아직 확인하지 않았습니다.' },
];

export function resolutionFor(scenario: Scenario) {
  return {
    resolutionId: `resolution-${scenario.id}-r${scenario.revision}`,
    taskStateRef: `task://${scenario.id}?revision=${scenario.revision}`,
    status: scenario.status,
    executionBrief: {
      objective: scenario.objective,
      targets: scenario.targets,
      effectiveConstraints: scenario.guidance
        .filter((item) => item.state === 'applied')
        .map((item) => item.title),
      methods: ['구조적 범위 판정', '의미적 관련성 판정'],
      requiredEvidence: scenario.status === 'degraded' ? ['누락된 Sense snapshot'] : [],
      allowedActions: ['로컬 읽기', '프로토타입 편집'],
      approvalRequired: scenario.question ? [scenario.question] : [],
      successCriteria: [scenario.completion],
      assumptions: scenario.assumption ? [scenario.assumption] : [],
    },
    guidance: {
      appliedRefs: scenario.guidance.filter((item) => item.state === 'applied').map((item) => item.id),
      shadowedRefs: scenario.guidance.filter((item) => item.state === 'shadowed').map((item) => item.id),
      conflictRefs: scenario.guidance.filter((item) => item.state === 'conflict').map((item) => item.id),
    },
    questions: scenario.question ? [{ id: 'question-1', text: scenario.question, reason: '결과가 달라지는 고영향 선택', blocksExecution: true }] : [],
    missingSources: scenario.status === 'degraded' ? [{ sourceRef: 'sense://conversation-and-writing/skill', required: true }] : [],
    manifest: {
      resolverVersion: '0.3.0-prototype',
      sourceSnapshotHash: `prototype:${scenario.id}:${scenario.revision}`,
    },
  };
}
