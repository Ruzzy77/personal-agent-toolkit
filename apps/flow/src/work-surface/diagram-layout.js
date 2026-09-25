export function layoutDiagramNodes(nodes,direction){
 const count=nodes.length;if(!count)return [];
 const columns=direction==='horizontal'?Math.min(count,3):Math.ceil(count/3);
 const rows=Math.ceil(count/columns);
 return nodes.map((node,index)=>({...node,
  x:1000*((index%columns)+0.5)/columns,
  y:600*(Math.floor(index/columns)+0.5)/rows
 }));
}
