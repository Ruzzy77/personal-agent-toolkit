export type FlowBlock = { id: string; kind: string; content: Record<string, unknown> };
export type FlowComposition = { blocks: FlowBlock[]; rows: Array<{id:string;columns:Array<{span:number;ids:string[]}>}> };
export type FlowArtifact = {
  id: string; revision: number; kind: string; title: string;
  html?:string; assets?:Array<{name:string;src:string}>; draftText?: string; composition?: FlowComposition; blocks?: Array<{id:string;heading:string;text:string}>;
  format?: string; src?: string; alt?: string; width?: number; height?: number;
  crop?: {x:number;y:number;width:number;height:number}|null;
  nodes?: Array<{id:string;label:string;detail?:string;x:number;y:number}>;
  edges?: Array<{id:string;from:string;to:string;label?:string}>;
};
import type { ResourceReference } from "@personal-agent/flow-surface/resource-reference";
export type FlowContextLocator = Extract<ResourceReference,{kind:"context"}>["locator"];
export type FlowLinkedResource = ResourceReference;
export type FlowWork = {
  id:string; name:string; purpose:string; revision:number;
  activeArtifactId:string; artifacts:FlowArtifact[]; sourceIds:string[]; linkedResources?:FlowLinkedResource[];
  surfaceLayout?: {order:string[];spans:Record<string,number>};
};
export type FlowWorkItem = {id:string;name:string;purpose:string;revision:number;workspaceId:string;artifact:{id:string;title:string;kind:string;format?:string;revision:number};reviewCount?:number};
export type FlowSource = {id:string;title:string;kind?:string;collection?:string;body?:string;path?:string;filePath?:string;artifact?:FlowArtifact;artifactRef?:Pick<FlowArtifact,"id"|"revision"|"kind"|"title">;live?:boolean;root?:string;reference?:FlowLinkedResource;scope?:{kind:"work"|"workspace"|"personal";workId?:string};sourceVersion?:string;example?:boolean};
export type FlowChange = {id:string;kind:string;workId:string;artifactId:string;status:string;mode:string;appliedRevision?:number;before?:FlowArtifact;proposal?:{artifact?:FlowArtifact}};
export type FlowSourceCatalogItem = Pick<FlowSource,"id"|"title"|"kind"|"collection"|"scope"|"reference"|"sourceVersion"|"example"> & {artifactId?:string;artifactRevision?:number};
export type FlowRead = {stateRevision?:string;work:FlowWork;sources:FlowSource[];sourceCatalog?:FlowSourceCatalogItem[];changes:FlowChange[];undo?:FlowChange|null;nextSourceOffset?:number|null;nextChangeOffset?:number|null;link:string|null};

const WORKSPACE = /^[a-z0-9][a-z0-9._-]*$/;
const ASSET = /^\/api\/flow\/assets\/([a-f0-9]{64}\.(?:png|jpg|webp|gif|mp3|wav|ogg|mp4|webm|m4a|aac|pdf))$/;
const EXAMPLE = /^\/examples\/([A-Za-z0-9._-]+\.(?:png|jpe?g|webp|gif|mp4|webm|mp3|wav|ogg|m4a|aac|csv))$/;

export function flowMediaUrl(workspaceId:string, value:unknown):string|null {
  if (!WORKSPACE.test(workspaceId) || typeof value !== 'string') return null;
  if (value.length <= 2_800_000 && /^data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/.test(value)) return value;
  const asset = ASSET.exec(value);
  if (asset) return `/api/flow-media/${workspaceId}/assets/${asset[1]}`;
  const example = EXAMPLE.exec(value);
  if (example) return `/api/flow-media/${workspaceId}/examples/${example[1]}`;
  try {
    const url = new URL(value, 'http://flow.local');
    const path = url.searchParams.get('path');
    if (value.startsWith('/api/flow/files/content?') && url.pathname === '/api/flow/files/content' &&
        url.searchParams.get('workspaceId') === workspaceId && path && path.length <= 2048)
      return `/api/flow-media/${workspaceId}/files/content?path=${encodeURIComponent(path)}`;
  } catch { return null; }
  return null;
}

export function flowContentForWeb(workspaceId:string, composition:FlowComposition):FlowComposition {
  return {...composition, blocks:composition.blocks.map(block => {
    const content = {...block.content};
    for (const key of ['src','poster','href']) {
      if (typeof content[key] !== 'string') continue;
      const rewritten = flowMediaUrl(workspaceId, content[key]);
      if (rewritten) content[key] = rewritten;
      else if (key !== 'href') delete content[key];
    }
    for (const key of ['items','images']) {
      if (!Array.isArray(content[key])) continue;
      content[key] = content[key].map(item => {
        if (!item || typeof item !== 'object') return item;
        const value = {...item as Record<string,unknown>};
        if (typeof value.src === 'string') value.src = flowMediaUrl(workspaceId, value.src) ?? '';
        if (typeof value.href === 'string') value.href = flowMediaUrl(workspaceId, value.href) ?? value.href;
        return value;
      });
    }
    return {...block, content};
  })};
}

export function flowArtifactKind(kind:string,format?:string):string {
  if (kind==='document' && format==='slides') return '발표 자료';
  return ({content:'작업 화면',document:'문서',image:'이미지',diagram:'도식',blank:'작업물'} as Record<string,string>)[kind] ?? '작업물';
}
