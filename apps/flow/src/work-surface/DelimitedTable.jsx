import React,{useState} from 'react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import {DataTable} from './DataTable.jsx';

export function DelimitedTable({data}){
 const [header,setHeader]=useState(true);
 const columns=Array.from({length:data.columns},(_,index)=>header?data.rows[0]?.[index]?.trim()||`열 ${index+1}`:`열 ${index+1}`);
 const rows=header?data.rows.slice(1):data.rows;
 return <div className="ws-delimited">
  <div className="ws-delimited-options"><Button type="button" color="primary" variant="ghost" pill={false} size="sm" aria-pressed={header} onClick={()=>setHeader(value=>!value)}>첫 행을 머리글로</Button></div>
  <DataTable columns={columns} rows={rows} rowNumbers label="파일 내용"/>
  {data.truncated&&<p role="status">처음 {data.rows.length}행만 표시합니다. 전체 내용은 원문에서 확인해 주세요.</p>}
 </div>;
}
