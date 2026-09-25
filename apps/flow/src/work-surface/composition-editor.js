import {defineComposition} from './composition.js';

import {workspaceFileType,workspaceFilePresentation} from './file-types.js';
export {workspaceFileType,workspaceMediaType,workspaceFilePresentation} from './file-types.js';

export function fileContentFromFile(content,path,href){
 const type=workspaceFilePresentation(path)?.label;
 if(!type||!content||typeof content!=='object'||typeof href!=='string'||!href)
  throw new TypeError('파일을 확인해 주세요.');
 return {...content,name:path.split('/').at(-1),type,size:'',href};
}

export function pdfContentFromFile(content,path,href){
 if(workspaceFileType(path)!=='PDF')throw new TypeError('PDF 파일을 확인해 주세요.');
 return fileContentFromFile(content,path,href);
}

export const contentKinds=[
 ['heading','제목'],['text','본문'],['image','이미지'],['comparison','이미지 비교'],['diagram','도식'],
 ['media','영상'],['table','표'],['metrics','수치'],['chart','막대그래프'],
 ['gallery','이미지 모음'],['steps','순서'],['references','참고 자료'],
 ['file','파일'],['code','코드'],['audio','오디오']
];

export function workspaceFileBlock(path,href,id=crypto.randomUUID()){
 return {id,kind:'file',content:fileContentFromFile({},path,href)};
}

export function newContentBlock(kind,id,newId=()=>crypto.randomUUID()){
 const content={
  heading:{title:'',description:''},
  text:{heading:'',paragraphs:['']},
  image:{src:'',alt:'',caption:''},
  comparison:{heading:'',items:[{id:newId(),label:'',src:'',alt:'',caption:''}]},
  media:{heading:'',alt:'',caption:''},
  diagram:{heading:'',nodes:[{id:newId(),label:'',detail:'',x:500,y:300}],edges:[]},
  table:{heading:'',columns:['항목','내용'],rows:[['','']]},
  metrics:{heading:'',items:[{id:newId(),label:'',value:'',unit:'',detail:''}]},
  chart:{heading:'',unit:'',caption:'',items:[{id:newId(),label:'',value:0,unit:''}]},
  gallery:{heading:'',images:[{id:newId(),src:'',alt:'',caption:''}]},
  steps:{heading:'',steps:[{id:newId(),title:'',text:''}]},
  references:{heading:'',items:[{id:newId(),title:'',detail:''}]},
  file:{name:'',type:'',size:'',description:''},
  code:{heading:'',language:'',code:''},
  audio:{heading:'',src:'',caption:'',transcript:''}
 }[kind];
 if(!content||typeof id!=='string'||!id)throw new TypeError('콘텐츠 형식을 확인해 주세요.');
 return {id,kind,content};
}

function copy(composition){
 return {blocks:[...composition.blocks],rows:composition.rows.map(row=>({...row,columns:row.columns.map(column=>({...column,ids:[...column.ids]}))}))};
}
function locate(rows,id){
 for(let r=0;r<rows.length;r++)for(let c=0;c<rows[r].columns.length;c++){
  const i=rows[r].columns[c].ids.indexOf(id);
  if(i!==-1)return {r,c,i};
 }
 return null;
}
export function contentOrder(composition){
 return composition.rows.flatMap(row=>row.columns.flatMap(column=>column.ids));
}
export function appendContentBlock(composition,block,afterId=null,beside=false,newId=()=>crypto.randomUUID()){
 const next=copy(composition);
 if(next.blocks.some(item=>item.id===block.id))throw new TypeError('이미 있는 콘텐츠입니다.');
 const pos=afterId?locate(next.rows,afterId):null;
 if(afterId&&!pos)throw new TypeError('배치할 콘텐츠를 찾지 못했습니다.');
 const row={id:newId(),columns:[{span:12,ids:[block.id]}]};
 if(beside&&pos){
  const target=next.rows[pos.r];
  if(target.columns.length!==1||target.columns[0].span!==12||target.columns[0].ids.length!==1)
   throw new TypeError('이 항목 옆에는 배치할 수 없습니다.');
  target.columns[0].span=6;
  target.columns.push({span:6,ids:[block.id]});
 }else next.rows.splice(pos?pos.r+1:next.rows.length,0,row);
 next.blocks.push(block);
 return defineComposition(next);
}
export function updateContentBlock(composition,id,content){
 if(!composition.blocks.some(block=>block.id===id))throw new TypeError('콘텐츠를 찾지 못했습니다.');
 return defineComposition({...composition,blocks:composition.blocks.map(block=>block.id===id?{...block,content}:block)});
}

export function removeContentBlock(composition,id){
 if(composition.blocks.length<=1)throw new TypeError('마지막 콘텐츠는 삭제할 수 없습니다.');
 const next=copy(composition),pos=locate(next.rows,id);
 if(!pos)throw new TypeError('콘텐츠를 찾지 못했습니다.');
 const row=next.rows[pos.r],column=row.columns[pos.c];
 column.ids.splice(pos.i,1);
 if(!column.ids.length)row.columns.splice(pos.c,1);
 if(!row.columns.length)next.rows.splice(pos.r,1);
 else if(row.columns.length===1)row.columns[0].span=12;
 next.blocks=next.blocks.filter(block=>block.id!==id);
 return defineComposition(next);
}
export function moveContentBlock(composition,id,step){
 const next=copy(composition),order=contentOrder(next),from=order.indexOf(id),to=from+step;
 if(from<0||to<0||to>=order.length)return composition;
 const a=locate(next.rows,order[from]),b=locate(next.rows,order[to]);
 next.rows[a.r].columns[a.c].ids[a.i]=order[to];
 next.rows[b.r].columns[b.c].ids[b.i]=id;
 return defineComposition(next);
}
export function contentColumnCapacity(composition,id){
 const pos=locate(composition.rows,id);
 if(!pos)return 0;
 const row=composition.rows[pos.r];
 return 12-row.columns.reduce((total,column)=>total+column.span,0)+row.columns[pos.c].span;
}
export function setContentColumnSpan(composition,id,span){
 if(!Number.isInteger(span)||span<1||span>12||span>contentColumnCapacity(composition,id))
  throw new TypeError('이 행에 놓을 수 없는 너비입니다.');
 const next=copy(composition),pos=locate(next.rows,id);
 next.rows[pos.r].columns[pos.c].span=span;
 return defineComposition(next);
}
function movableRowAbove(composition,id){
 const pos=locate(composition.rows,id);
 if(!pos||pos.r<1)return null;
 const row=composition.rows[pos.r];
 return row.columns.length===1&&row.columns[0].ids.length===1
  ?{pos,row,above:composition.rows[pos.r-1]}:null;
}

export function canJoinContentRowAbove(composition,id){
 const target=movableRowAbove(composition,id);
 if(!target)return false;
 const columns=target.above.columns;
 if(columns.length===1&&columns[0].span===12&&columns[0].ids.length===1)return true;
 return columns.length<3&&12-columns.reduce((sum,column)=>sum+column.span,0)>=4;
}

export function joinContentRowAbove(composition,id){
 if(!canJoinContentRowAbove(composition,id))throw new TypeError('윗줄에 새 열을 놓을 수 없습니다.');
 const next=copy(composition),{pos,above}=movableRowAbove(next,id);
 if(above.columns.length===1&&above.columns[0].span===12){
  above.columns[0].span=6;
  above.columns.push({span:6,ids:[id]});
 }else{
  const available=12-above.columns.reduce((sum,column)=>sum+column.span,0);
  const span=[8,6,4].find(value=>value<=available);
  above.columns.push({span,ids:[id]});
 }
 next.rows.splice(pos.r,1);
 return defineComposition(next);
}

export function stackContentRowAbove(composition,id,columnIndex=0){
 const next=copy(composition),target=movableRowAbove(next,id);
 if(!target||!Number.isInteger(columnIndex)||columnIndex<0||columnIndex>=target.above.columns.length)
  throw new TypeError('윗줄의 열을 확인해 주세요.');
 target.above.columns[columnIndex].ids.push(id);
 next.rows.splice(target.pos.r,1);
 return defineComposition(next);
}
export function separateContentBlock(composition,id,newId=()=>crypto.randomUUID()){
 const next=copy(composition),pos=locate(next.rows,id);
 if(!pos)throw new TypeError('콘텐츠를 찾지 못했습니다.');
 const row=next.rows[pos.r],column=row.columns[pos.c];
 if(row.columns.length===1&&column.ids.length===1)return composition;
 column.ids.splice(pos.i,1);
 if(!column.ids.length)row.columns.splice(pos.c,1);
 if(row.columns.length===1)row.columns[0].span=12;
 next.rows.splice(pos.r+1,0,{id:newId(),columns:[{span:12,ids:[id]}]});
 return defineComposition(next);
}
