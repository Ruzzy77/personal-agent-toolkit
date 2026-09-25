export type UIKitAsset={id:string;title:string;description:string;kind:'template'|'reference'|'asset';format:string;preview?:string;revision:string};
export function uikitAssetsFromResult(value:unknown):UIKitAsset[]{
 const result=value as {revision?:unknown;items?:unknown[]}|null;
 if(!result||typeof result.revision!=='string'||!/^[a-f0-9]{64}$/.test(result.revision)||!Array.isArray(result.items))throw new Error('UIKit 목록을 읽을 수 없습니다.');
 const revision=result.revision;
 return result.items.flatMap(raw=>{
  const x=raw as Partial<UIKitAsset>;
  if(!x||typeof x.id!=='string'||!/^[a-z0-9][a-z0-9-]{0,100}$/.test(x.id)||typeof x.title!=='string'||typeof x.description!=='string'||typeof x.format!=='string'||!['template','reference','asset'].includes(x.kind||''))return [];
  return [{id:x.id,title:x.title,description:x.description,kind:x.kind!,format:x.format,revision,...(typeof x.preview==='string'?{preview:x.preview}:{})}];
 });
}
