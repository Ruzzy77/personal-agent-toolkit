const DEFAULT_ROWS=200;
const MAX_COLUMNS=80;
const MAX_CELL_LENGTH=20_000;

export function textContentFormat(path){
 if(typeof path!=='string')return 'plain';
 if(/\.(?:md|markdown)$/i.test(path))return 'markdown';
 if(/\.csv$/i.test(path))return 'csv';
 if(/\.tsv$/i.test(path))return 'tsv';
 return 'plain';
}

export function parseDelimitedText(body,format,maxRows=DEFAULT_ROWS){
 if(typeof body!=='string'||!['csv','tsv'].includes(format)||!Number.isInteger(maxRows)||maxRows<1)return null;
 const delimiter=format==='tsv'?'\t':',';
 const text=body.charCodeAt(0)===0xfeff?body.slice(1):body;
 const rows=[];
 let row=[],cell='',quoted=false,closed=false,atStart=true;
 function pushCell(){
  if(row.length>=MAX_COLUMNS)return false;
  row.push(cell);cell='';closed=false;atStart=true;
  return true;
 }
 function pushRow(){
  if(!pushCell())return false;
  rows.push(row);row=[];
  return true;
 }
 for(let i=0;i<text.length;i++){
  const char=text[i];
  if(quoted){
   if(char==='"'){
    if(text[i+1]==='"'){cell+='"';i++;}
    else{quoted=false;closed=true;}
   }else cell+=char;
  }else if(char==='"'&&atStart){quoted=true;atStart=false;}
  else if(char===delimiter){if(!pushCell())return null;}
  else if(char==='\n'||char==='\r'){
   if(!pushRow())return null;
   if(char==='\r'&&text[i+1]==='\n')i++;
   if(rows.length>maxRows)return {rows:rows.slice(0,maxRows),columns:Math.max(...rows.slice(0,maxRows).map(item=>item.length)),truncated:true};
  }else if(char==='"'||closed)return null;
  else{cell+=char;atStart=false;}
  if(cell.length>MAX_CELL_LENGTH)return null;
 }
 if(quoted)return null;
 if(text.length&&!((text.endsWith('\n')||text.endsWith('\r'))&&row.length===0&&cell==='')){
  if(!pushRow())return null;
 }
 if(rows.length>maxRows)return {rows:rows.slice(0,maxRows),columns:Math.max(...rows.slice(0,maxRows).map(item=>item.length)),truncated:true};
 return {rows,columns:rows.length?Math.max(...rows.map(item=>item.length)):0,truncated:false};
}
