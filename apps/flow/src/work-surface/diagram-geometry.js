export const DIAGRAM_WIDTH=1000;
export const DIAGRAM_HEIGHT=600;

const clamp=(value,min,max)=>Math.min(max,Math.max(min,value));

export function diagramLabelLines(label){
 const result=[];
 for(const line of String(label??'').split('\n')){
  const chars=Array.from(line);
  if(!chars.length)result.push('');
  while(chars.length)result.push(chars.splice(0,13).join(''));
 }
 return result.length?result:[''];
}

export function diagramNodeHeight(label){
 return Math.max(72,diagramLabelLines(label).length*22+32);
}

export function clampDiagramPosition(x,y){
 return {x:clamp(x,140,860),y:clamp(y,70,530)};
}

export function nudgeDiagramPosition(node,key){
 const direction={ArrowLeft:[-20,0],ArrowRight:[20,0],ArrowUp:[0,-20],ArrowDown:[0,20]}[key];
 return direction?clampDiagramPosition(node.x+direction[0],node.y+direction[1]):null;
}
