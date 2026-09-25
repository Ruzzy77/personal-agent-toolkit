import {validResourceReference,resourceReferenceKey} from './work-surface/resource-reference.js';
const text=v=>typeof v==='string';
export function validLibraryEntry(e){
 return e&&text(e.id)&&e.id.length>0&&e.id.length<=160&&text(e.title)&&!!e.title.trim()&&e.title.length<=160&&
  Number.isSafeInteger(e.revision)&&e.revision>=0&&e.scope&&['work','workspace','personal'].includes(e.scope.kind)&&
  (e.scope.kind!=='work'||text(e.scope.workId)&&!!e.scope.workId)&&
  (e.body===undefined||text(e.body)&&e.body.length<=200000)&&
  (e.reference===undefined||validResourceReference(e.reference))&&
  (e.sourceVersion===undefined||text(e.sourceVersion)&&e.sourceVersion.length<=256)&&
  (!!e.body?.trim()||!!e.reference);
}
export const libraryEntryKey=e=>e.reference?e.scope.kind+(e.scope.kind==='work'?':'+e.scope.workId:'')+':'+resourceReferenceKey(e.reference):null;
export const libraryEntryVisible=(e,workId)=>!workId||e.scope.kind!=='work'||e.scope.workId===workId;
export function libraryEntrySource(e){
 return {...e,kind:'자료',collection:e.scope.kind==='work'?'작업 자료':e.scope.kind==='personal'?'공통 자료':'프로젝트 자료',
  ...(e.reference?.kind==='host-file'?{filePath:e.reference.path,root:e.reference.root,live:true}:{}),body:e.body||''};
}
