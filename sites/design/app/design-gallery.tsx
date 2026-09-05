"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

type Capabilities = Record<string, boolean>;
type FormatId = "web" | "document" | "slides" | "image";
type ContentId = "interactive" | "summary" | "long" | "technical";
type NeedId =
  | "interactive_ui"
  | "charts"
  | "diagrams"
  | "print"
  | "long_form"
  | "dark_mode"
  | "minimal_content";
type SupportLevel = "guidance" | "assets" | "verified";

type SpecimenColors = {
  canvas: string;
  canvas_dark?: string;
  ink: string;
  ink_dark?: string;
  accent: string;
  accent_dark?: string;
};

type Gallery = {
  korean_name: string;
  purpose: string;
  note: string;
  directions: string[];
  swatches: string[];
  template_labels?: Record<string, string>;
  use_labels?: Record<string, string>;
  avoid_labels?: Record<string, string>;
  specimen?: SpecimenColors;
};

type Pattern = {
  id: string;
  name: string;
  use_when: string;
  principles: string[];
  checks: string[];
};

type Recipe = {
  id: string;
  name: string;
  description: string;
  version: string;
  created: string;
  status?: string;
  selection_ready?: boolean;
  kind: "recipe";
  visibility: "public" | "private";
  pattern_refs: string[];
  formats: FormatId[];
  format_fit: Record<FormatId, "primary" | "supported">;
  format_support: Record<FormatId, SupportLevel[]>;
  format_guide: string;
  use_for: string[];
  avoid_for: string[];
  capabilities: Capabilities;
  templates: Record<string, string>;
  gallery?: Gallery;
  validation: { checked_on: string };
};

export type Catalog = {
  catalog_schema_version: number;
  library: {
    id: string;
    name: string;
    version: string;
    license: string;
    description: string;
  };
  patterns: Pattern[];
  recipes: Recipe[];
};

type WebMcpTool = {
  name: string;
  title?: string;
  description: string;
  inputSchema: Record<string, unknown>;
  annotations?: {
    readOnlyHint?: boolean;
    untrustedContentHint?: boolean;
  };
  execute(input: unknown): unknown | Promise<unknown>;
};

declare global {
  interface Document {
    readonly modelContext?: {
      registerTool(
        tool: WebMcpTool,
        options?: { signal?: AbortSignal },
      ): void | Promise<void>;
    };
  }
}

const formats: Array<{ id: FormatId; label: string; requestName: string }> = [
  { id: "web", label: "웹·앱", requestName: "웹·앱 화면" },
  { id: "document", label: "문서", requestName: "문서" },
  { id: "slides", label: "슬라이드", requestName: "슬라이드" },
  { id: "image", label: "이미지", requestName: "이미지" },
];

const contents: Array<{
  id: ContentId;
  label: string;
  description: string;
  sample: string;
  requestStarter: string;
  useFor: string[];
}> = [
  {
    id: "interactive",
    label: "탐색·조작",
    description: "목록 · 입력 · 선택",
    sample: "목록 · 선택",
    requestStarter: "여러 항목을 쉽게 찾고 선택할 수 있는 결과물",
    useFor: ["interactive-content", "collection-content", "tool-content"],
  },
  {
    id: "summary",
    label: "요약·자료",
    description: "핵심 · 표 · 그래프",
    sample: "요약 · 자료",
    requestStarter: "요약과 자료를 빠르게 읽을 수 있는 결과물",
    useFor: ["summary-content", "data-content", "visual-explanation"],
  },
  {
    id: "long",
    label: "긴 글",
    description: "목차 · 장 · 본문",
    sample: "목차 · 본문",
    requestStarter: "내용이 길어도 원하는 부분을 쉽게 찾을 수 있는 결과물",
    useFor: ["long-reading", "guide-content", "book-content"],
  },
  {
    id: "technical",
    label: "기술 설명",
    description: "구성 · 절차 · 도식",
    sample: "구성 · 절차",
    requestStarter: "구성 요소의 관계와 작업 순서가 분명한 결과물",
    useFor: ["technical-content", "structured-explanation", "diagram-content"],
  },
];

const needs: Array<{
  id: NeedId;
  label: string;
  formats: FormatId[];
}> = [
  { id: "minimal_content", label: "필수 내용만", formats: ["web", "document", "slides", "image"] },
  { id: "interactive_ui", label: "버튼과 입력", formats: ["web"] },
  { id: "charts", label: "표와 그래프", formats: ["web", "document", "slides", "image"] },
  { id: "diagrams", label: "그림과 도식", formats: ["web", "document", "slides", "image"] },
  { id: "print", label: "인쇄용", formats: ["document", "slides", "image"] },
  { id: "long_form", label: "긴 내용", formats: ["web", "document"] },
  { id: "dark_mode", label: "어두운 배경", formats: ["web", "slides", "image"] },
];

const formatIds = formats.map((item) => item.id);
const contentIds = contents.map((item) => item.id);
const needIds = needs.map((item) => item.id);

type RecipeNote = {
  koreanName: string;
  purpose: string;
  note: string;
};

type RankedReference = {
  recipe: Recipe;
  score: number;
  reasons: string[];
  tensions: string[];
};

const formatDirections: Record<FormatId, string[]> = {
  web: [
    "넓은 화면과 좁은 화면에서 내용 순서와 조작이 유지되는지 확인",
    "키보드로 이동한 위치가 보이고 모든 버튼과 입력을 사용할 수 있는지 확인",
  ],
  document: [
    "페이지가 나뉠 때 제목, 표, 그림과 설명이 어색하게 갈라지지 않는지 확인",
    "최종 페이지 크기로 출력해 글자, 여백과 표가 읽히는지 확인",
  ],
  slides: [
    "16:9 전체 화면에서 제목과 본문이 멀리서도 읽히는지 확인",
    "슬라이드마다 한 가지 내용만 남고 표나 도식이 넘치지 않는지 확인",
  ],
  image: [
    "최종 비율과 크기에서 제목과 주요 대상이 잘리지 않는지 확인",
    "작게 보아도 제목과 한 가지 핵심 내용이 구분되는지 확인",
  ],
};

function mergeLabels(
  catalog: Catalog,
  pick: (gallery: Gallery) => Record<string, string> | undefined,
) {
  const merged: Record<string, string> = {};
  for (const recipe of catalog.recipes) {
    if (recipe.gallery) Object.assign(merged, pick(recipe.gallery));
  }
  return merged;
}

type CatalogRuntime = {
  availableRecipes: Recipe[];
  unselectedRecipes: Recipe[];
  patternById: Map<string, Pattern>;
  recipeIds: string[];
  templateLabels: Record<string, string>;
  useLabels: Record<string, string>;
  avoidLabels: Record<string, string>;
};

function buildRuntime(catalog: Catalog): CatalogRuntime {
  const availableRecipes = catalog.recipes.filter(
    (recipe) => recipe.status === "validated" && recipe.selection_ready === true,
  );
  return {
    availableRecipes,
    unselectedRecipes: catalog.recipes.filter(
      (recipe) => !availableRecipes.includes(recipe),
    ),
    patternById: new Map(catalog.patterns.map((pattern) => [pattern.id, pattern])),
    recipeIds: availableRecipes.map((recipe) => recipe.id),
    templateLabels: mergeLabels(catalog, (gallery) => gallery.template_labels),
    useLabels: mergeLabels(catalog, (gallery) => gallery.use_labels),
    avoidLabels: mergeLabels(catalog, (gallery) => gallery.avoid_labels),
  };
}

const capabilityLabels: Record<string, string> = {
  dark_mode: "어두운 배경",
  print: "인쇄",
  charts: "표와 그래프",
  diagrams: "그림과 도식",
  long_form: "긴 내용",
  interactive_ui: "버튼과 입력",
  motion: "움직임",
  image_layout: "이미지 배치",
  minimal_content: "필수 내용만",
};

const capabilityOrder: Record<FormatId, string[]> = {
  web: ["minimal_content", "interactive_ui", "charts", "diagrams", "long_form", "dark_mode", "motion", "print", "image_layout"],
  document: ["minimal_content", "print", "charts", "diagrams", "long_form", "image_layout", "dark_mode", "interactive_ui", "motion"],
  slides: ["minimal_content", "image_layout", "charts", "diagrams", "dark_mode", "print", "motion", "long_form", "interactive_ui"],
  image: ["minimal_content", "image_layout", "diagrams", "charts", "dark_mode", "print", "motion", "long_form", "interactive_ui"],
};

function formatLabel(format: FormatId) {
  return formats.find((item) => item.id === format)?.label || format;
}

function requestFormatName(format: FormatId) {
  return formats.find((item) => item.id === format)?.requestName || format;
}

function needLabel(need: NeedId) {
  return needs.find((item) => item.id === need)?.label || need;
}

function labelsFor(values: string[], labels: Record<string, string>) {
  return [...new Set(values.map((value) => labels[value] || value))];
}

function patternNames(runtime: CatalogRuntime, recipe: Recipe) {
  return recipe.pattern_refs.map((id) => runtime.patternById.get(id)?.name || id);
}

function selectedContent(content: ContentId) {
  return contents.find((item) => item.id === content) || contents[0];
}

function contentMatches(recipe: Recipe, content: ContentId) {
  const contentUses = selectedContent(content).useFor;
  return recipe.use_for.some((use) => contentUses.includes(use));
}

function contentConflicts(recipe: Recipe, content: ContentId) {
  const contentUses = selectedContent(content).useFor;
  return recipe.avoid_for.some((avoid) => contentUses.includes(avoid));
}

function scoreRecipe(
  recipe: Recipe,
  format: FormatId,
  content: ContentId,
  selectedNeeds: NeedId[],
) {
  const formatScore = recipe.format_fit[format] === "primary" ? 8 : 3;
  let score = formatScore + (contentMatches(recipe, content) ? 20 : 0);
  if (contentConflicts(recipe, content)) score -= 18;

  selectedNeeds.forEach((need) => {
    if (need === "minimal_content") {
      score += recipe.capabilities[need] ? 24 : -3;
    } else {
      score += recipe.capabilities[need] ? 2 : -3;
    }
  });

  return score;
}

function referenceReasons(
  runtime: CatalogRuntime,
  recipe: Recipe,
  format: FormatId,
  content: ContentId,
  selectedNeeds: NeedId[],
) {
  const reasons: string[] = [];
  if (contentMatches(recipe, content)) {
    reasons.push(`${selectedContent(content).label} 내용에 맞는 용도로 정리되어 있음`);
  }
  if (recipe.format_fit[format] === "primary") {
    reasons.push(`${formatLabel(format)} 형식에 특히 잘 맞음`);
  }
  const supportedNeeds = selectedNeeds.filter((need) => recipe.capabilities[need]);
  if (supportedNeeds.length) {
    reasons.push(`선택한 기능 중 ${supportedNeeds.map(needLabel).join(", ")} 지원`);
  }
  const names = patternNames(runtime, recipe);
  if (names.length) reasons.push(`${names.join(" · ")} 패턴을 참고할 수 있음`);
  return reasons.slice(0, 3);
}

function referenceTensions(
  recipe: Recipe,
  format: FormatId,
  content: ContentId,
  selectedNeeds: NeedId[],
) {
  const tensions: string[] = [];
  const unsupportedNeeds = selectedNeeds.filter((need) => !recipe.capabilities[need]);
  if (unsupportedNeeds.length) {
    tensions.push(`${unsupportedNeeds.map(needLabel).join(", ")} 지원은 제한적`);
  }
  if (contentConflicts(recipe, content)) {
    tensions.push(`${selectedContent(content).label} 내용에는 전체 레시피보다 일부 원리만 적합`);
  } else if (!contentMatches(recipe, content)) {
    tensions.push("선택한 내용 유형과 직접 일치하지 않아 원리를 선별해야 함");
  }
  if (!recipe.format_support[format].includes("assets")) {
    tensions.push(`${formatLabel(format)} 형식은 원칙만 제공하며 적용 자산은 없음`);
  } else if (recipe.format_fit[format] !== "primary") {
    tensions.push(`${formatLabel(format)} 형식은 적용 가능하지만 주요 형식은 아님`);
  }
  return tensions.slice(0, 2);
}

function rankReferences(
  runtime: CatalogRuntime,
  format: FormatId,
  content: ContentId,
  selectedNeeds: NeedId[],
  limit = 3,
): RankedReference[] {
  return runtime.availableRecipes
    .map((recipe, index) => ({
      recipe,
      index,
      score: scoreRecipe(recipe, format, content, selectedNeeds),
      reasons: referenceReasons(runtime, recipe, format, content, selectedNeeds),
      tensions: referenceTensions(recipe, format, content, selectedNeeds),
    }))
    .sort((left, right) => right.score - left.score || left.index - right.index)
    .slice(0, limit)
    .map(({ recipe, score, reasons, tensions }) => ({ recipe, score, reasons, tensions }));
}

function strengthsFor(recipe: Recipe, format: FormatId, limit = 3) {
  return capabilityOrder[format]
    .filter((key) => recipe.capabilities[key])
    .map((key) => capabilityLabels[key] || key)
    .slice(0, limit);
}

function noteFor(runtime: CatalogRuntime, recipe: Recipe): RecipeNote {
  const gallery = recipe.gallery;
  if (gallery) {
    return { koreanName: gallery.korean_name, purpose: gallery.purpose, note: gallery.note };
  }

  const match = recipe.name.match(/^(.+?)\s*\((.+)\)$/);
  const purpose = labelsFor(recipe.use_for, runtime.useLabels)[0] || "등록된 레시피";
  return {
    koreanName: match?.[1]?.trim() || recipe.name,
    purpose,
    note: recipe.description,
  };
}

function buildRequestText(runtime: CatalogRuntime, {
  recipe,
  format,
  content,
  subject,
  selectedNeeds,
  selectedDirections,
}: {
  recipe: Recipe;
  format: FormatId;
  content: ContentId;
  subject: string;
  selectedNeeds: NeedId[];
  selectedDirections: string[];
}) {
  const contentChoice = selectedContent(content);
  const requirementLine = selectedNeeds.length
    ? `필요한 기능: ${selectedNeeds.map(needLabel).join(", ")}`
    : "";
  const patternLine = patternNames(runtime, recipe).join(", ");
  const improvementLine = selectedDirections.length
    ? `완성본을 실제로 렌더링해 아래 항목을 확인하고, 바꾼 내용과 더 손볼 부분을 알려 주세요.\n${selectedDirections.map((direction) => `- ${direction}`).join("\n")}`
    : "완성본을 실제 크기로 렌더링해 글자, 여백과 내용 순서를 확인해 주세요.";

  return [
    `만들 것: ${subject.trim()}`,
    `형식: ${formatLabel(format)}`,
    `내용: ${contentChoice.label}`,
    `참고 방향: ${noteFor(runtime, recipe).koreanName} 레시피${patternLine ? ` · ${patternLine}` : ""}`,
    `레시피 ID: ${recipe.id}${recipe.version ? ` · 버전: ${recipe.version}` : ""}`,
    `${noteFor(runtime, recipe).koreanName} 레시피를 참고해 ${requestFormatName(format)} 결과물을 만들어 주세요. 레시피를 그대로 복제하지 말고 목적에 맞는 조형 원리와 재료만 선택해 적용해 주세요. 기존 브랜드나 디자인 시스템이 있으면 유지하고, 충돌하는 색상·서체·구성은 바꾸지 마세요.`,
    requirementLine,
    improvementLine,
  ].filter(Boolean).join("\n\n");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertObject(value: unknown, allowedKeys: string[]) {
  if (!isRecord(value)) throw new Error("입력은 객체여야 합니다.");
  const unknownKeys = Object.keys(value).filter((key) => !allowedKeys.includes(key));
  if (unknownKeys.length) throw new Error(`지원하지 않는 입력 항목: ${unknownKeys.join(", ")}`);
  return value;
}

function parseFormat(value: unknown): FormatId {
  if (typeof value !== "string" || !formatIds.includes(value as FormatId)) {
    throw new Error("format이 올바르지 않습니다.");
  }
  return value as FormatId;
}

function parseContent(value: unknown): ContentId {
  if (typeof value !== "string" || !contentIds.includes(value as ContentId)) {
    throw new Error("content가 올바르지 않습니다.");
  }
  return value as ContentId;
}

function parseNeeds(value: unknown, format: FormatId): NeedId[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new Error("needs는 배열이어야 합니다.");
  const parsed: NeedId[] = [];
  for (const item of value) {
    if (typeof item !== "string" || !needIds.includes(item as NeedId)) {
      throw new Error(`지원하지 않는 기능: ${String(item)}`);
    }
    const need = needs.find((candidate) => candidate.id === item);
    if (!need?.formats.includes(format)) {
      throw new Error(`${need?.label || item} 기능은 ${formatLabel(format)} 형식에서 선택할 수 없습니다.`);
    }
    if (!parsed.includes(item as NeedId)) parsed.push(item as NeedId);
  }
  return parsed;
}

function parseReferenceId(runtime: CatalogRuntime, value: unknown) {
  if (typeof value !== "string" || !runtime.recipeIds.includes(value)) {
    throw new Error("reference_id가 올바르지 않습니다.");
  }
  return value;
}

function asFindInput(input: unknown) {
  const value = assertObject(input, ["format", "content", "needs", "limit"]);
  const format = parseFormat(value.format);
  const content = parseContent(value.content);
  const selectedNeeds = parseNeeds(value.needs, format);
  const limit = value.limit === undefined ? 3 : value.limit;
  if (typeof limit !== "number" || !Number.isInteger(limit) || limit < 1 || limit > 3) {
    throw new Error("limit은 1에서 3 사이의 정수여야 합니다.");
  }
  return { format, content, selectedNeeds, limit };
}

function asCompareInput(runtime: CatalogRuntime, input: unknown) {
  const value = assertObject(input, ["reference_ids", "format"]);
  if (!Array.isArray(value.reference_ids) || value.reference_ids.length < 2 || value.reference_ids.length > 3) {
    throw new Error("reference_ids에는 2개 또는 3개의 참고 후보가 필요합니다.");
  }
  const referenceIds = value.reference_ids.map((id) => parseReferenceId(runtime, id));
  if (new Set(referenceIds).size !== referenceIds.length) {
    throw new Error("reference_ids에는 같은 후보를 두 번 넣을 수 없습니다.");
  }
  const format = value.format === undefined ? undefined : parseFormat(value.format);
  return { referenceIds, format };
}

function asBriefInput(runtime: CatalogRuntime, input: unknown) {
  const value = assertObject(input, ["reference_id", "format", "content", "subject", "needs"]);
  const referenceId = parseReferenceId(runtime, value.reference_id);
  const format = parseFormat(value.format);
  const content = parseContent(value.content);
  if (typeof value.subject !== "string" || !value.subject.trim() || value.subject.trim().length > 240) {
    throw new Error("subject는 1자 이상 240자 이하의 문자열이어야 합니다.");
  }
  return {
    referenceId,
    format,
    content,
    subject: value.subject.trim(),
    selectedNeeds: parseNeeds(value.needs, format),
  };
}

function referencePayload(runtime: CatalogRuntime, reference: RankedReference, rank: number) {
  return {
    rank,
    reference_id: reference.recipe.id,
    name: noteFor(runtime, reference.recipe).koreanName,
    version: reference.recipe.version,
    patterns: reference.recipe.pattern_refs.map((id) => ({
      id,
      name: runtime.patternById.get(id)?.name || id,
    })),
    reasons: reference.reasons,
    tensions: reference.tensions,
  };
}

function waitForVisibleUpdate() {
  return new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}

function Modal({
  open,
  labelledBy,
  onClose,
  children,
}: {
  open: boolean;
  labelledBy: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (open && !dialog.open) {
      previousFocusRef.current = document.activeElement as HTMLElement | null;
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
      previousFocusRef.current?.focus();
    }

    return () => {
      if (dialog.open) {
        dialog.close();
        previousFocusRef.current?.focus();
      }
    };
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      className="modal-shell"
      aria-labelledby={labelledBy}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          onClose();
        }
      }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      {children}
    </dialog>
  );
}

function Specimen({
  recipe,
  format,
  sample,
}: {
  recipe: Recipe;
  format: FormatId;
  sample: string;
}) {
  if (recipe.id === "hanji") {
    return (
      <div className="specimen specimen--hanji" data-format={format} aria-hidden="true">
        <div className="specimen-canvas">
          <p className="hanji-headline">{sample}</p>
          <div className="hanji-lines"><i /><i /><i /></div>
        </div>
      </div>
    );
  }

  if (recipe.id === "seochaek") {
    return (
      <div className="specimen specimen--seochaek" data-format={format} aria-hidden="true">
        <div className="specimen-canvas">
          <div className="book-page">
            <p>{sample}</p>
            <div className="book-lines"><i /><i /><i /></div>
          </div>
        </div>
      </div>
    );
  }

  if (recipe.id === "formwork") {
    return (
      <div className="specimen specimen--formwork" data-format={format} aria-hidden="true">
        <div className="specimen-canvas">
          <div className="formwork-stamp">DOC / 04</div>
          <div className="formwork-title">{sample}</div>
          <div className="formwork-grid"><span>입력</span><strong>A</strong><span>출력</span><strong>B</strong></div>
          <div className="formwork-orange" />
        </div>
      </div>
    );
  }

  if (recipe.id === "baekja") {
    return (
      <div className="specimen specimen--baekja" data-format={format} aria-hidden="true">
        <div className="specimen-canvas">
          <div className="baekja-orbit"><span /></div>
          <p>{sample}</p>
          <div className="baekja-actions"><span>전체 보기</span><i /></div>
        </div>
      </div>
    );
  }

  if (recipe.id === "yeobaek") {
    return (
      <div className="specimen specimen--yeobaek" data-format={format} aria-hidden="true">
        <div className="specimen-canvas">
          <p>{sample}</p>
          <span className="yeobaek-action">열기</span>
        </div>
      </div>
    );
  }

  if (recipe.id === "saegin") {
    return (
      <div className="specimen specimen--saegin" data-format={format} aria-hidden="true">
        <div className="specimen-canvas">
          <span className="saegin-marker" />
          <p>{sample}</p>
          <div className="saegin-lines"><i /><i /><i /></div>
          <div className="saegin-foot"><span>INDEX</span><i /></div>
        </div>
      </div>
    );
  }

  const colors = recipe.gallery?.specimen;
  if (colors) {
    const specimenVars = {
      "--sp-canvas": colors.canvas,
      "--sp-canvas-dark": colors.canvas_dark || colors.canvas,
      "--sp-ink": colors.ink,
      "--sp-ink-dark": colors.ink_dark || colors.ink,
      "--sp-accent": colors.accent,
      "--sp-accent-dark": colors.accent_dark || colors.accent,
    } as CSSProperties;
    return (
      <div className="specimen specimen--branded" data-format={format} aria-hidden="true" style={specimenVars}>
        <div className="specimen-canvas">
          <span className="branded-marker" />
          <p>{sample}</p>
          <div className="branded-lines"><i /><i /><i /></div>
        </div>
      </div>
    );
  }

  return (
    <div className="specimen specimen--generic" data-format={format} aria-hidden="true">
      <div className="specimen-canvas"><strong>{sample}</strong></div>
    </div>
  );
}

function RecipeCard({
  runtime,
  recipe,
  format,
  sample,
  referenceRank,
  compared,
  compareFull,
  onRequest,
  onPreview,
  onCompare,
}: {
  runtime: CatalogRuntime;
  recipe: Recipe;
  format: FormatId;
  sample: string;
  referenceRank?: number;
  compared: boolean;
  compareFull: boolean;
  onRequest: () => void;
  onPreview: () => void;
  onCompare: () => void;
}) {
  const note = noteFor(runtime, recipe);
  const strengths = strengthsFor(recipe, format);
  const fitLabel = recipe.format_fit[format] === "primary" ? "특히 잘 맞음" : "적용 가능";
  const supportLabel = recipe.format_support[format].includes("assets")
    ? "적용 자산 있음"
    : "원칙 제공";
  const showTemplates =
    (format === "web" || format === "document") && Object.keys(recipe.templates).length > 0;

  return (
    <article
      className={`design-card design-card--${recipe.id} ${referenceRank ? "is-reference" : ""}`}
      id={`design-${recipe.id}`}
    >
      <Specimen recipe={recipe} format={format} sample={sample} />
      <div className="card-body">
        <div className="card-heading">
          <h3>{note.koreanName}</h3>
          {referenceRank ? <span className="recommendation-badge">참고 후보 {referenceRank}</span> : null}
        </div>

        <p className="format-fit">{formatLabel(format)} · {fitLabel} · {supportLabel}</p>
        <p className="purpose">{note.purpose}</p>
        <p className="best-when">{note.note}</p>
        <ul className="pattern-tags" aria-label={`${note.koreanName} 설계 패턴`}>
          {patternNames(runtime, recipe).map((name) => <li key={name}>{name}</li>)}
        </ul>
        <ul className="strengths" aria-label={`${note.koreanName}의 주요 특성`}>
          {strengths.map((strength) => <li key={strength}>{strength}</li>)}
        </ul>

        <div className="card-actions">
          <button className="primary-action" type="button" onClick={onRequest}>
            요청 만들기 <span aria-hidden="true">→</span>
          </button>
          <button className="preview-action" type="button" onClick={onPreview}>미리보기</button>
          <button
            className={`compare-button ${compared ? "is-selected" : ""}`}
            type="button"
            aria-pressed={compared}
            disabled={!compared && compareFull}
            onClick={onCompare}
          >
            {compared ? "비교에서 빼기" : "비교에 담기"}
          </button>
        </div>

        {showTemplates ? (
          <div className="template-links" aria-label={`${note.koreanName} HTML 틀`}>
            <span>HTML 틀</span>
            {Object.entries(recipe.templates).map(([templateName, templatePath]) => (
              <a key={templateName} href={`/api/design/files/${recipe.id}/${templatePath}`} target="_blank" rel="noreferrer">
                {runtime.templateLabels[templateName] || templateName}
              </a>
            ))}
          </div>
        ) : null}
      </div>
    </article>
  );
}

function CompareColumn({ runtime, recipe, format }: { runtime: CatalogRuntime; recipe: Recipe; format: FormatId }) {
  const note = noteFor(runtime, recipe);
  return (
    <section className={`compare-column compare-column--${recipe.id}`}>
      <h3>{note.koreanName}</h3>
      <p className="compare-purpose">{note.purpose}</p>

      <div className="compare-group">
        <h4>연결 패턴</h4>
        <ul>{patternNames(runtime, recipe).map((label) => <li key={label}>{label}</li>)}</ul>
      </div>

      <div className="compare-group">
        <h4>형식</h4>
        <ul>
          {formats.map((item) => (
            <li key={item.id} className={item.id === format ? "is-current" : undefined}>
              {item.label} · {recipe.format_fit[item.id] === "primary" ? "특히 잘 맞음" : "적용 가능"}
              {recipe.format_support[item.id].includes("assets") ? " · 적용 자산 있음" : " · 원칙 제공"}
            </li>
          ))}
        </ul>
      </div>

      <div className="compare-group">
        <h4>잘 맞는 내용</h4>
        <ul>{labelsFor(recipe.use_for, runtime.useLabels).map((label) => <li key={label}>{label}</li>)}</ul>
      </div>

      <div className="compare-group">
        <h4>지원 기능</h4>
        <ul>{strengthsFor(recipe, format, Number.POSITIVE_INFINITY).map((label) => <li key={label}>{label}</li>)}</ul>
      </div>

      <div className="compare-group compare-group--avoid">
        <h4>잘 맞지 않는 내용</h4>
        <ul>{labelsFor(recipe.avoid_for, runtime.avoidLabels).map((label) => <li key={label}>{label}</li>)}</ul>
      </div>
    </section>
  );
}

function RequestBuilder({
  runtime,
  recipe,
  format,
  content,
  initialSubject,
  selectedNeeds,
  onClose,
}: {
  runtime: CatalogRuntime;
  recipe: Recipe;
  format: FormatId;
  content: ContentId;
  initialSubject: string;
  selectedNeeds: NeedId[];
  onClose: () => void;
}) {
  const note = noteFor(runtime, recipe);
  const directions = [...formatDirections[format], ...(recipe.gallery?.directions || [])];
  const [subject, setSubject] = useState(initialSubject);
  const [selectedDirections, setSelectedDirections] = useState(directions);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const requestText = useMemo(
    () => buildRequestText(runtime, { recipe, format, content, subject, selectedNeeds, selectedDirections }),
    [content, format, recipe, selectedDirections, selectedNeeds, subject],
  );

  function toggleDirection(direction: string) {
    setCopyState("idle");
    setSelectedDirections((current) =>
      current.includes(direction)
        ? current.filter((item) => item !== direction)
        : [...current, direction],
    );
  }

  async function copyRequest() {
    try {
      await navigator.clipboard.writeText(requestText);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
  }

  const firstTemplate = Object.entries(recipe.templates)[0];
  const showTemplate = (format === "web" || format === "document") && firstTemplate;

  return (
    <section className="request-panel">
      <header>
        <h2 id="request-title">{note.koreanName} 참고 요청 만들기</h2>
        <button type="button" aria-label="요청 만들기 닫기" onClick={onClose}>닫기</button>
      </header>

      <div className="request-grid">
        <div className="request-form">
          <div>
            <label htmlFor="request-subject">만들 것</label>
            <p className="field-help">무엇을 만들고 누가 쓸지 한 줄로 적어 주세요.</p>
            <textarea
              id="request-subject"
              autoFocus
              value={subject}
              onChange={(event) => {
                setSubject(event.target.value);
                setCopyState("idle");
              }}
              rows={3}
            />
          </div>

          <fieldset className="improvement-fieldset">
            <legend>함께 살펴볼 부분</legend>
            <div className="improvement-options">
              {directions.map((direction) => (
                <label key={direction}>
                  <input
                    type="checkbox"
                    checked={selectedDirections.includes(direction)}
                    onChange={() => toggleDirection(direction)}
                  />
                  <span>{direction}</span>
                </label>
              ))}
            </div>
          </fieldset>
        </div>

        <div className="request-output">
          <div className="request-output-heading"><h3>요청문</h3></div>
          <textarea readOnly value={requestText} rows={15} aria-label="완성된 요청문" />
          <div className="request-actions">
            <button type="button" onClick={copyRequest} disabled={!subject.trim()}>요청문 복사</button>
            {showTemplate ? (
              <a href={`/api/design/files/${recipe.id}/${firstTemplate[1]}`} target="_blank" rel="noreferrer">
                {runtime.templateLabels[firstTemplate[0]] || firstTemplate[0]} HTML 틀
              </a>
            ) : null}
          </div>
          {copyState !== "idle" ? (
            <p className={`copy-status copy-status--${copyState}`} role="status">
              {copyState === "copied" ? "요청문을 복사했습니다." : "복사하지 못했습니다. 요청문을 직접 선택해 주세요."}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function UnselectedRecipe({ recipe }: { recipe: Recipe }) {
  const name = recipe.gallery?.korean_name || recipe.name || recipe.id;
  const version = typeof recipe.version === "string" ? recipe.version.trim() : "";
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const request = `Design에서 ID가 ${recipe.id}인 레시피의 현재 정보와 파일 목록을 확인해 주세요.${version ? ` 화면에 표시된 버전은 ${version}입니다.` : ""} 레시피나 파일은 수정하지 마세요.`;

  async function copyRequest() {
    try {
      await navigator.clipboard.writeText(request);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
  }

  return (
    <details className="unselected-recipe">
      <summary>{name}</summary>
      <p className="recipe-identity">ID: {recipe.id}{version ? ` · 버전: ${version}` : ""}</p>
      <p>현재 선택 후보가 아닙니다. 등록된 정보는 연결된 Design 도구로 확인할 수 있습니다.</p>
      <label htmlFor={`lookup-${recipe.id}`}>정보 확인 요청문</label>
      <textarea id={`lookup-${recipe.id}`} readOnly value={request} rows={3} />
      <button type="button" onClick={copyRequest}>정보 확인 요청문 복사</button>
      {copyState !== "idle" ? (
        <p className={`copy-status copy-status--${copyState}`} role="status">
          {copyState === "copied" ? "요청문을 복사했습니다." : "복사하지 못했습니다. 요청문을 직접 선택해 주세요."}
        </p>
      ) : null}
    </details>
  );
}

export default function DesignGallery({ catalog }: { catalog: Catalog }) {
  const runtime = useMemo(() => buildRuntime(catalog), [catalog]);
  const { availableRecipes, unselectedRecipes, recipeIds } = runtime;
  const emptyMessage = catalog.recipes.length === 0
    ? "등록된 레시피가 없습니다. 연결된 Design 도구로 레시피를 등록한 뒤 다시 열어 주세요."
    : "등록된 레시피는 있지만 현재 선택 후보로 표시할 항목은 없습니다. 아래 목록에서 레시피 정보 확인 방법을 볼 수 있습니다.";
  const [format, setFormat] = useState<FormatId>("web");
  const [content, setContent] = useState<ContentId>("interactive");
  const [selectedNeeds, setSelectedNeeds] = useState<NeedId[]>([]);
  const [referenceLimit, setReferenceLimit] = useState(3);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [requestSubject, setRequestSubject] = useState<string | undefined>();
  const [comparedIds, setComparedIds] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const formatRef = useRef(format);

  const visibleNeeds = needs.filter((need) => need.formats.includes(format));
  const previewRecipe = availableRecipes.find((recipe) => recipe.id === previewId);
  const requestRecipe = availableRecipes.find((recipe) => recipe.id === requestId);
  const contentChoice = selectedContent(content);
  const rankedReferences = useMemo(
    () => rankReferences(runtime, format, content, selectedNeeds, referenceLimit),
    [content, format, referenceLimit, runtime, selectedNeeds],
  );
  const rankById = new Map(
    rankedReferences.map((reference, index) => [reference.recipe.id, index + 1]),
  );
  const comparedRecipes = comparedIds
    .map((id) => availableRecipes.find((recipe) => recipe.id === id))
    .filter((recipe): recipe is Recipe => Boolean(recipe));

  useEffect(() => {
    formatRef.current = format;
  }, [format]);

  useEffect(() => {
    const context = document.modelContext;
    if (!context?.registerTool) return;
    const lifecycle = new AbortController();
    const commonAnnotations = { readOnlyHint: true, untrustedContentHint: false };
    const tools: WebMcpTool[] = [
      {
        name: "design_library_find",
        title: "디자인 참고 방향 찾기",
        description:
          "결과물의 형식, 내용과 필요한 기능에 가까운 디자인 참고 후보 1~3개를 찾고 화면의 선택 상태를 갱신합니다.",
        inputSchema: {
          type: "object",
          properties: {
            format: { type: "string", enum: formatIds },
            content: { type: "string", enum: contentIds },
            needs: {
              type: "array",
              items: { type: "string", enum: needIds },
              uniqueItems: true,
            },
            limit: { type: "integer", minimum: 1, maximum: 3, default: 3 },
          },
          required: ["format", "content"],
          additionalProperties: false,
        },
        annotations: commonAnnotations,
        async execute(input) {
          const value = asFindInput(input);
          const references = rankReferences(
            runtime,
            value.format,
            value.content,
            value.selectedNeeds,
            value.limit,
          );
          setFormat(value.format);
          setContent(value.content);
          setSelectedNeeds(value.selectedNeeds);
          setReferenceLimit(value.limit);
          setPreviewId(null);
          setCompareOpen(false);
          setRequestId(null);
          setRequestSubject(undefined);
          await waitForVisibleUpdate();
          return {
            criteria: {
              format: value.format,
              content: value.content,
              needs: value.selectedNeeds,
            },
            references: references.map((reference, index) =>
              referencePayload(runtime, reference, index + 1),
            ),
            ...(references.length === 0 ? {
              message: emptyMessage,
              unselected_recipe_ids: unselectedRecipes.map((recipe) => recipe.id),
            } : {}),
          };
        },
      },
      {
        name: "design_library_compare",
        title: "디자인 참고 후보 비교하기",
        description:
          "디자인 참고 후보 2~3개를 비교 목록에 담고 같은 화면의 비교 창을 엽니다.",
        inputSchema: {
          type: "object",
          properties: {
            reference_ids: {
              type: "array",
              items: { type: "string", enum: recipeIds },
              minItems: 2,
              maxItems: 3,
              uniqueItems: true,
            },
            format: { type: "string", enum: formatIds },
          },
          required: ["reference_ids"],
          additionalProperties: false,
        },
        annotations: commonAnnotations,
        async execute(input) {
          const value = asCompareInput(runtime, input);
          if (value.format) {
            setFormat(value.format);
            setSelectedNeeds((current) => current.filter((need) =>
              needs.find((candidate) => candidate.id === need)?.formats.includes(value.format as FormatId),
            ));
          }
          setComparedIds(value.referenceIds);
          setPreviewId(null);
          setRequestId(null);
          setRequestSubject(undefined);
          setCompareOpen(true);
          await waitForVisibleUpdate();
          return {
            comparison_open: true,
            reference_ids: value.referenceIds,
            format: value.format ?? formatRef.current,
          };
        },
      },
      {
        name: "design_library_prepare_brief",
        title: "디자인 참고 요청문 준비하기",
        description:
          "선택한 참고 후보와 제작 조건으로 요청문을 만들고 같은 화면의 요청 작성 창을 엽니다. 결과물을 생성하거나 저장하지는 않습니다.",
        inputSchema: {
          type: "object",
          properties: {
            reference_id: { type: "string", enum: recipeIds },
            format: { type: "string", enum: formatIds },
            content: { type: "string", enum: contentIds },
            subject: { type: "string", minLength: 1, maxLength: 240 },
            needs: {
              type: "array",
              items: { type: "string", enum: needIds },
              uniqueItems: true,
            },
          },
          required: ["reference_id", "format", "content", "subject"],
          additionalProperties: false,
        },
        annotations: commonAnnotations,
        async execute(input) {
          const value = asBriefInput(runtime, input);
          const recipe = availableRecipes.find(
            (candidate) => candidate.id === value.referenceId,
          );
          if (!recipe) throw new Error("참고 후보를 찾을 수 없습니다.");
          const directions = [
            ...formatDirections[value.format],
            ...(recipe.gallery?.directions || []),
          ];
          const requestText = buildRequestText(runtime, {
            recipe,
            format: value.format,
            content: value.content,
            subject: value.subject,
            selectedNeeds: value.selectedNeeds,
            selectedDirections: directions,
          });
          setFormat(value.format);
          setContent(value.content);
          setSelectedNeeds(value.selectedNeeds);
          setPreviewId(null);
          setCompareOpen(false);
          setRequestSubject(value.subject);
          setRequestId(value.referenceId);
          await waitForVisibleUpdate();
          return {
            prepared: true,
            reference_id: value.referenceId,
            request_text: requestText,
          };
        },
      },
    ];

    for (const tool of tools) {
      if (tool.name === "design_library_compare" && recipeIds.length < 2) continue;
      if (tool.name === "design_library_prepare_brief" && recipeIds.length === 0) continue;
      try {
        const registration = context.registerTool(tool, { signal: lifecycle.signal });
        void Promise.resolve(registration).catch(() => undefined);
      } catch {
        // WebMCP is optional; the visible interface remains fully usable.
      }
    }
    return () => lifecycle.abort();
  }, [runtime, emptyMessage]);

  function chooseFormat(nextFormat: FormatId) {
    setFormat(nextFormat);
    setReferenceLimit(3);
    setSelectedNeeds((current) =>
      current.filter((needId) =>
        needs.find((need) => need.id === needId)?.formats.includes(nextFormat),
      ),
    );
  }

  function toggleCompared(id: string) {
    setComparedIds((current) => {
      if (current.includes(id)) return current.filter((item) => item !== id);
      if (current.length >= 3) return current;
      return [...current, id];
    });
  }

  function toggleNeed(need: NeedId) {
    setReferenceLimit(3);
    setSelectedNeeds((current) =>
      current.includes(need) ? current.filter((item) => item !== need) : [...current, need],
    );
  }

  function showReferenceCard(id: string) {
    window.setTimeout(() => {
      document
        .getElementById(`design-${id}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 0);
  }

  function openRequest(id: string, subject?: string) {
    setRequestSubject(subject);
    setRequestId(id);
  }

  function closeRequest() {
    setRequestId(null);
    setRequestSubject(undefined);
  }

  return (
    <main className={`gallery gallery--${theme}`} id="library">
      <header className="topbar">
        <a
          className="brand"
          href="#library"
          aria-label="디자인 참고 라이브러리 맨 위로"
          onClick={(event) => {
            event.preventDefault();
            document
              .getElementById("library")
              ?.scrollIntoView({ behavior: "smooth", block: "start" });
          }}
        >
          Design Reference Library
        </a>
        <button
          className="theme-button"
          type="button"
          onClick={() => setTheme(theme === "light" ? "dark" : "light")}
        >
          {theme === "light" ? "어둡게" : "밝게"}
        </button>
      </header>

      <section className="finder" aria-labelledby="finder-title">
        <header className="finder-heading">
          <h1 id="finder-title">디자인 기준 찾기</h1>
          <p>하나의 정답 대신, 목적에 가까운 패턴과 레시피를 비교해 필요한 부분만 고릅니다.</p>
        </header>

        <div className="finder-layout">
          <div className="finder-controls">
            <fieldset>
              <legend>형식</legend>
              <div className="format-options">
                {formats.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    aria-pressed={format === item.id}
                    onClick={() => chooseFormat(item.id)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend>내용</legend>
              <div className="content-options">
                {contents.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    aria-pressed={content === item.id}
                    onClick={() => {
                      setContent(item.id);
                      setReferenceLimit(3);
                    }}
                  >
                    <strong>{item.label}</strong>
                    <span>{item.description}</span>
                  </button>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend>필요한 기능</legend>
              <div className="need-options">
                {visibleNeeds.map((need) => (
                  <button
                    key={need.id}
                    type="button"
                    aria-pressed={selectedNeeds.includes(need.id)}
                    onClick={() => toggleNeed(need.id)}
                  >
                    <span aria-hidden="true">
                      {selectedNeeds.includes(need.id) ? "✓" : "+"}
                    </span>
                    {need.label}
                  </button>
                ))}
              </div>
            </fieldset>
          </div>

          <aside className="finder-result" aria-live="polite">
            <p className="result-label">참고 방향 · 가까운 순서</p>
            {rankedReferences.length === 0 ? <p className="result-empty">{emptyMessage}</p> : null}
            <div className="candidate-list">
              {rankedReferences.map((reference, index) => {
                const note = noteFor(runtime, reference.recipe);
                return (
                  <article className="candidate-item" key={reference.recipe.id}>
                    <header>
                      <span className="candidate-rank">후보 {index + 1}</span>
                      <h2>{note.koreanName}</h2>
                    </header>
                    <p className="result-purpose">{note.purpose}</p>
                    <ul className="candidate-reasons">
                      {reference.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                    </ul>
                    {reference.tensions.length ? (
                      <div className="candidate-tensions">
                        <strong>살펴볼 점</strong>
                        <ul>
                          {reference.tensions.map((tension) => <li key={tension}>{tension}</li>)}
                        </ul>
                      </div>
                    ) : null}
                    <div className="candidate-actions">
                      <button type="button" onClick={() => openRequest(reference.recipe.id)}>
                        요청 만들기
                      </button>
                      <button type="button" onClick={() => showReferenceCard(reference.recipe.id)}>
                        자세히 보기
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          </aside>
        </div>
      </section>

      <section className="design-section" aria-labelledby="design-list-title">
        <header className="section-heading">
          <h2 id="design-list-title">레시피와 예시</h2>
          <p>이름은 출발점입니다. 연결된 패턴과 현재 프로젝트의 규칙을 함께 보고 선택해 주세요.</p>
        </header>
        {availableRecipes.length > 0 ? <div className="design-grid" aria-live="polite">
          {availableRecipes.map((recipe) => (
            <RecipeCard
              runtime={runtime}
              key={recipe.id}
              recipe={recipe}
              format={format}
              sample={contentChoice.sample}
              referenceRank={rankById.get(recipe.id)}
              compared={comparedIds.includes(recipe.id)}
              compareFull={comparedIds.length >= 3}
              onRequest={() => openRequest(recipe.id)}
              onPreview={() => setPreviewId(recipe.id)}
              onCompare={() => toggleCompared(recipe.id)}
            />
          ))}
        </div> : null}
        {catalog.recipes.length === 0 ? <p className="catalog-empty">등록된 레시피가 여기에 표시됩니다.</p> : null}
        {unselectedRecipes.length > 0 ? (
          <section className="unselected-recipes" aria-labelledby="unselected-title">
            <h3 id="unselected-title">선택 후보 외 레시피</h3>
            <p>이름을 열어 등록 정보를 확인할 요청문을 복사할 수 있습니다. 추천·비교·미리보기에는 포함되지 않습니다.</p>
            {unselectedRecipes.map((recipe) => <UnselectedRecipe key={recipe.id} recipe={recipe} />)}
          </section>
        ) : null}
      </section>

      {comparedIds.length > 0 && (
        <aside className="compare-tray" aria-label="비교할 참고 후보">
          <div>
            <span className="tray-count">{comparedIds.length}/3</span>
            <p>{comparedRecipes.map((recipe) => noteFor(runtime, recipe).koreanName).join(" · ")}</p>
          </div>
          {comparedIds.length >= 2 ? (
            <button type="button" onClick={() => setCompareOpen(true)}>나란히 비교</button>
          ) : (
            <span className="tray-hint">한 가지 더 선택해 주세요</span>
          )}
        </aside>
      )}

      <Modal open={Boolean(requestRecipe)} labelledBy="request-title" onClose={closeRequest}>
        {requestRecipe ? (
          <RequestBuilder
            runtime={runtime}
            key={`${requestRecipe.id}-${format}-${content}-${requestSubject || "default"}`}
            recipe={requestRecipe}
            format={format}
            content={content}
            initialSubject={requestSubject || contentChoice.requestStarter}
            selectedNeeds={selectedNeeds}
            onClose={closeRequest}
          />
        ) : null}
      </Modal>

      <Modal
        open={Boolean(previewRecipe)}
        labelledBy="preview-title"
        onClose={() => setPreviewId(null)}
      >
        {previewRecipe ? (
          <section className="preview-panel">
            <header>
              <h2 id="preview-title">{noteFor(runtime, previewRecipe).koreanName} 미리보기</h2>
              <div className="panel-actions">
                <a
                  href={`/api/design/files/${previewRecipe.id}/styleguide.html`}
                  target="_blank"
                  rel="noreferrer"
                >
                  새 탭에서 보기
                </a>
                <button
                  type="button"
                  aria-label="미리보기 닫기"
                  onClick={() => setPreviewId(null)}
                >
                  닫기
                </button>
              </div>
            </header>
            <iframe
              src={`/api/design/files/${previewRecipe.id}/styleguide.html`}
              title={`${noteFor(runtime, previewRecipe).koreanName} 디자인 미리보기`}
              sandbox=""
            />
          </section>
        ) : null}
      </Modal>

      <Modal
        open={compareOpen && comparedRecipes.length >= 2}
        labelledBy="compare-title"
        onClose={() => setCompareOpen(false)}
      >
        {comparedRecipes.length >= 2 ? (
          <section className="compare-panel">
            <header>
              <h2 id="compare-title">{comparedRecipes.length}개 참고 후보 비교</h2>
              <button
                type="button"
                aria-label="비교 닫기"
                onClick={() => setCompareOpen(false)}
              >
                닫기
              </button>
            </header>
            <div className="compare-grid">
              {comparedRecipes.map((recipe) => (
                <CompareColumn runtime={runtime} key={recipe.id} recipe={recipe} format={format} />
              ))}
            </div>
          </section>
        ) : null}
      </Modal>
    </main>
  );
}
