import React from 'react';
import './data-table.css';

export function DataTable({columns=[],rows=[],rowNumbers=false,label}){
 return <div className="su-table-wrap ws-table-scroll ws-data-table">
  <table className="su-table" aria-label={label}>
   <thead><tr>{rowNumbers&&<th scope="col" className="ws-data-row-number">행</th>}{columns.map((column,index)=><th scope="col" key={index}>{column}</th>)}</tr></thead>
   <tbody>{rows.map((row,index)=><tr key={index}>
    {rowNumbers&&<th scope="row" className="ws-data-row-number">{index+1}</th>}
    {columns.map((_,columnIndex)=><td key={columnIndex}>{row[columnIndex]??''}</td>)}
   </tr>)}</tbody>
  </table>
 </div>;
}
