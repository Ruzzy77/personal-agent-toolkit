export function defineComposition({blocks,rows}){
 if(!Array.isArray(blocks)||!Array.isArray(rows)||!rows.length)throw new TypeError('콘텐츠와 배치를 확인해 주세요.');
 const ids=new Set();
 for(const block of blocks){
  if(!block||typeof block.id!=='string'||!block.id||typeof block.kind!=='string'||!block.kind||!block.content||typeof block.content!=='object'||Array.isArray(block.content)||ids.has(block.id))throw new TypeError('콘텐츠 식별자 또는 형식을 확인해 주세요.');
  ids.add(block.id);
 }
 const used=new Set(),rowIds=new Set();
 for(const row of rows){
  if(!row||typeof row.id!=='string'||!row.id||rowIds.has(row.id)||!Array.isArray(row.columns)||!row.columns.length)throw new TypeError('배치 행을 확인해 주세요.');
  rowIds.add(row.id);
  let width=0;
  for(const column of row.columns){
   if(!Number.isInteger(column?.span)||column.span<1||column.span>12||!Array.isArray(column.ids)||!column.ids.length)throw new TypeError('배치 열을 확인해 주세요.');
   width+=column.span;
   for(const id of column.ids){
    if(!ids.has(id)||used.has(id))throw new TypeError('배치된 콘텐츠를 확인해 주세요.');
    used.add(id);
   }
  }
  if(width>12)throw new TypeError('한 행의 너비가 화면 폭을 넘습니다.');
 }
 if(used.size!==ids.size)throw new TypeError('배치되지 않은 콘텐츠가 있습니다.');
 return {blocks,rows};
}

export function stackComposition(blocks){
 return defineComposition({blocks,rows:blocks.map(block=>({id:'row-'+block.id,columns:[{span:12,ids:[block.id]}]}))});
}

export function packComposition(blocks,spanOf=()=>12){
 const rows=[];let columns=[],taken=0;
 const flush=()=>{if(columns.length){rows.push({id:'row-'+rows.length,columns});columns=[];taken=0}};
 for(const block of blocks){
  const span=spanOf(block);
  if(!Number.isInteger(span)||span<1||span>12)throw new TypeError('콘텐츠 너비를 확인해 주세요.');
  if(taken+span>12)flush();
  columns.push({span,ids:[block.id]});taken+=span;
 }
 flush();
 return defineComposition({blocks,rows});
}

export function splitContentHeading(composition){
 const row=composition.rows[0],column=row?.columns[0];
 const heading=row?.columns.length===1&&column.span===12
  ?composition.blocks.find(block=>block.id===column.ids[0]):null;
 if(heading?.kind!=='heading'||!heading.content.title?.trim())return {heading:null,body:composition};
 const ids=column.ids.slice(1);
 return {
  heading,
  body:{...composition,blocks:composition.blocks.filter(block=>block.id!==heading.id),
   rows:ids.length?[{...row,columns:[{...column,ids}]},...composition.rows.slice(1)]:composition.rows.slice(1)}
 };
}
