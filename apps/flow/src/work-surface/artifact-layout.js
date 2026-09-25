export const layoutWidthPresets=Object.freeze([
 {value:12,label:'전체 폭'}, {value:8,label:'넓게'},
 {value:6,label:'절반'}, {value:4,label:'좁게'}
].map(Object.freeze));
const widths=new Set(layoutWidthPresets.map(option=>option.value));

export function validSurfaceLayout(layout){
 if(layout===undefined)return true;
 if(!layout||typeof layout!=='object'||Array.isArray(layout))return false;
 const {order,spans}=layout;
 return Array.isArray(order)&&order.every(id=>typeof id==='string')&&new Set(order).size===order.length&&
  spans&&typeof spans==='object'&&!Array.isArray(spans)&&
  Object.values(spans).every(width=>widths.has(width));
}

export function orderedArtifacts(work){
 const artifacts=work.artifacts||[],byId=new Map(artifacts.map(item=>[item.id,item]));
 const ordered=(work.surfaceLayout?.order||[]).map(id=>byId.get(id)).filter(Boolean);
 const seen=new Set(ordered.map(item=>item.id));
 return [...ordered,...artifacts.filter(item=>!seen.has(item.id))];
}

export function artifactSpan(work,id){
 return work.surfaceLayout?.spans?.[id]||12;
}

export function setArtifactSpan(work,id,span){
 if(!widths.has(span)||!work.artifacts.some(item=>item.id===id))return work;
 return {...work,surfaceLayout:{
  order:orderedArtifacts(work).map(item=>item.id),
  spans:{...(work.surfaceLayout?.spans||{}),[id]:span}
 }};
}

export function moveArtifact(work,id,step){
 const order=orderedArtifacts(work).map(item=>item.id),from=order.indexOf(id),to=from+step;
 if(from<0||to<0||to>=order.length)return work;
 [order[from],order[to]]=[order[to],order[from]];
 return {...work,surfaceLayout:{order,spans:{...(work.surfaceLayout?.spans||{})}}};
}
