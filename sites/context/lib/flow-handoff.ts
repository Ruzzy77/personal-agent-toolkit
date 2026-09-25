import { validResourceReference } from "@personal-agent/flow-surface/resource-reference";
import type { FlowLinkedResource } from "./flow-content";

export const FLOW_LAST_WORK_KEY = "toolkit-flow-current-work-v1";

export type FlowSelection = { workspaceId: string; workId: string };
export type FlowFileHandoff = { root: string; path: string };

const safeText = (value: unknown, max: number): value is string =>
  typeof value === "string" && value.length > 0 && value.length <= max && !/[\u0000-\u001f]/.test(value);

export function flowResourceHref(reference: FlowLinkedResource): string | null {
  if (!validResourceReference(reference)) return null;
  return "/flow?" + new URLSearchParams({ resource: JSON.stringify(reference) });
}

export function fileHandoffFromSearch(search: string): FlowFileHandoff | null {
  const params = new URLSearchParams(search);
  const roots = params.getAll("fileRoot"), paths = params.getAll("filePath");
  if (roots.length !== 1 || paths.length !== 1 || !safeText(roots[0], 256) || !safeText(paths[0], 4096)) return null;
  return { root: roots[0], path: paths[0] };
}

export function resourceHandoffFromSearch(search: string): FlowLinkedResource | null {
  const params = new URLSearchParams(search);
  const payloads = params.getAll("resource");
  if (payloads.length) {
    if (payloads.length !== 1 || params.has("fileRoot") || params.has("filePath") || payloads[0].length > 10_000) return null;
    try {
      const value: unknown = JSON.parse(payloads[0]);
      return validResourceReference(value) ? value as FlowLinkedResource : null;
    } catch { return null; }
  }
  const file = fileHandoffFromSearch(search);
  const legacy: unknown = file && { kind: "host-file", ...file };
  return validResourceReference(legacy) ? legacy as FlowLinkedResource : null;
}

export function flowSelectionFromStorage(value: string | null): FlowSelection | null {
  if (!value || value.length > 500) return null;
  try {
    const selection: unknown = JSON.parse(value);
    if (!selection || typeof selection !== "object") return null;
    const { workspaceId, workId } = selection as Record<string, unknown>;
    return safeText(workspaceId, 160) && safeText(workId, 160) ? { workspaceId, workId } : null;
  } catch { return null; }
}

export function selectFlowWorkspace(available: readonly string[], preferred: string | undefined, requested: string | null, remembered: FlowSelection | null): string | undefined {
  return [preferred, requested, remembered?.workspaceId].find(id => id && available.includes(id)) ?? available[0];
}

export function selectFlowWork(available: readonly string[], requested: string | null, remembered: FlowSelection | null, workspaceId: string): string | undefined {
  if (requested && available.includes(requested)) return requested;
  if (remembered?.workspaceId === workspaceId && available.includes(remembered.workId)) return remembered.workId;
  return available[0];
}
