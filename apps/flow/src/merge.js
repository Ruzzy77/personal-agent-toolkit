// Merge only changes made from a known base. An overlapping edit is never guessed.
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const conflict=()=>{const e=new Error('같은 부분이 다른 곳에서 수정되었습니다.');e.status=409;throw e};
function changedField(base,desired,current,key){
 if(same(base?.[key],desired?.[key]))return current?.[key];
 if(!same(base?.[key],current?.[key]))conflict();
 return desired?.[key];
}
function mergeUnique(base,desired,current,key){
 const before=base?.[key]||[],after=desired?.[key]||[],live=current?.[key]||[];
 const ids=v=>v.map(x=>typeof x==='string'?x:x.id);
 const removed=ids(before).filter(x=>!ids(after).includes(x));
 if(removed.length)conflict();
 const additions=after.filter(x=>!ids(before).includes(typeof x==='string'?x:x.id));
 return [...live,...additions.filter(x=>!ids(live).includes(typeof x==='string'?x:x.id))];
}
function mergeArtifacts(base,desired,current){
 let next=[...current];
 for(const wanted of desired){
  const original=base.find(x=>x.id===wanted.id);
  if(!original){if(next.some(x=>x.id===wanted.id))conflict();next.push(wanted);continue}
  if(same(original,wanted))continue;
  const at=next.findIndex(x=>x.id===wanted.id);
  if(at<0||!same(next[at],original))conflict();
  next[at]=wanted;
 }
 for(const original of base.filter(x=>!desired.some(v=>v.id===x.id))){
  if(original.kind!=='blank'||original.title.trim()||(original.draftText||'').trim())conflict();
  const at=next.findIndex(x=>x.id===original.id);
  if(at<0||!same(next[at],original))conflict();
  next.splice(at,1);
 }
 return next;
}
function mergeLibrary(base,desired,current){
 const result=[...(current||[])];
 for(const entry of desired||[]){
  const before=(base||[]).find(e=>e.id===entry.id),at=result.findIndex(e=>e.id===entry.id);
  if(same(before,entry))continue;
  if(at<0){if(before)conflict();result.push(entry);}
  else{if(!same(before,result[at]))conflict();result[at]=entry;}
 }
 if((base||[]).some(e=>!(desired||[]).some(v=>v.id===e.id)))conflict();
 return result;
}
function mergeWork(base,desired,current){
 if(!base){if(current)conflict();return desired}
 if(!current)conflict();
 const next={...current};
 for(const field of ['name','purpose','sourceIds','linkedResources','activeArtifactId','surfaceLayout']){
  if(!same(base[field],desired[field]))next[field]=changedField(base,desired,current,field);
 }
 next.artifacts=mergeArtifacts(base.artifacts,desired.artifacts,current.artifacts);
 if(['name','purpose','sourceIds','linkedResources','surfaceLayout'].some(field=>!same(base[field],desired[field])))next.revision=current.revision+1;
 return next;
}
export function mergeStates(base,desired,current){
 if(!base||!desired||!current||base.workspaceId!==desired.workspaceId||base.workspaceId!==current.workspaceId)conflict();
 const next={...current,works:[...current.works],libraryEntries:mergeLibrary(base.libraryEntries,desired.libraryEntries,current.libraryEntries)};
 for(const wanted of desired.works){
  const original=base.works.find(w=>w.id===wanted.id);
  const at=next.works.findIndex(w=>w.id===wanted.id);
  const merged=mergeWork(original,wanted,at<0?null:next.works[at]);
  if(at<0)next.works.push(merged);else next.works[at]=merged;
 }
 if(base.works.some(w=>!desired.works.some(v=>v.id===w.id)))conflict();
 for(const field of ['savedIds','librarySnapshots','linkedFiles','collectedFiles','libraryFileIds'])
  next[field]=mergeUnique(base,desired,current,field);
 // View state and agent change receipts belong to the current saved state, not this browser.
 return next;
}
