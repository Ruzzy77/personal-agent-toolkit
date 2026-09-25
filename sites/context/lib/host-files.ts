import { ownerFetch } from "./owner-client";
export type CorpusLink = { space_id: string; connection_id: string };
export type HostRoot = { id: string; permission: string; corpus?: CorpusLink | null; locations?: Array<{root:string;path:string;permission:string;corpus:CorpusLink}> };
export type FileEntry = { name: string; path: string; type: "file" | "directory" | "symlink"; bytes: number; mime: string; version?: string };
export type FileDraft = { root: string; path: string; base: string; body: string; version: string };
export type Transfer = { transfer_id: string; token: string; offset: number; chunk_bytes: number };
type ResultData = {ok?:boolean;error?:{code?:string;message?:string};offset?:number};
export class FileFailure extends Error { constructor(public code: string, message: string) { super(message); } }
export async function hostCall<T>(name: string, input: unknown = {}, signal?: AbortSignal): Promise<T> {
  const response = await ownerFetch("/api/host/" + name, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(input), signal });
  const data = await response.json() as ResultData;
  if (!response.ok || data.ok === false || data.error) throw new FileFailure(data.error?.code ?? "failed", data.error?.message ?? "파일 작업을 완료하지 못했습니다.");
  return data as T;
}
export const fileUrl = (root: string, path: string, preview = false) => "/api/file-content?" + new URLSearchParams({ root, path, ...(preview ? {preview:"1"} : {}) });
export function joined(parent: string, name: string) { return parent === "." ? name : parent + "/" + name; }
export function linkedCorpus(root: HostRoot | undefined, path: string): CorpusLink | null {
  if (!root) return null;
  return [...(root.locations ?? [])].filter(item => path === item.path || path.startsWith(item.path + "/"))
    .sort((a,b) => b.path.length-a.path.length)[0]?.corpus ?? root.corpus ?? null;
}
export function draftKey(root: string, path: string) { return "toolkit-file:" + JSON.stringify([root,path]); }
export function restoredDraft(value: string | null, current: FileDraft): FileDraft {
  if (!value) return current;
  try { const draft=JSON.parse(value); return draft.root===current.root && draft.path===current.path && typeof draft.body==="string" && typeof draft.base==="string" && typeof draft.version==="string" ? draft : current; }
  catch { return current; }
}
export async function uploadChunks(transfer: Transfer, file: File, progress: (offset: number)=>void, signal: AbortSignal) {
  const url="/api/transfers/"+transfer.transfer_id;
  const headers={"X-Toolkit-Transfer-Token":transfer.token};
  const status=await ownerFetch(url+"/status",{headers,signal});
  if (!status.ok) throw new Error("업로드 상태를 확인하지 못했습니다.");
  let offset=Number((await status.json() as {offset:number}).offset);
  while (offset<file.size) {
    const response=await ownerFetch(url+"/chunk",{method:"PUT",signal,headers:{...headers,"Content-Type":"application/octet-stream","Upload-Offset":String(offset)},body:file.slice(offset,offset+transfer.chunk_bytes)});
    const data=await response.json() as ResultData;
    if (!response.ok) throw new FileFailure(data.error?.code??"upload_failed",data.error?.message??"업로드가 중단되었습니다.");
    offset=Number(data.offset);progress(offset);
  }
  const response=await ownerFetch(url+"/commit",{method:"POST",headers,signal});
  const data=await response.json() as ResultData;
  if (!response.ok) throw new FileFailure(data.error?.code??"upload_failed",data.error?.message??"업로드를 저장하지 못했습니다.");
  return data;
}
