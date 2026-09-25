import React from 'react';
import {Editable} from './Editable.jsx';

export function BlankSurface({artifact,onText,onFinish,onFile}){
 function hasFiles(event){return Array.from(event.dataTransfer?.types||[]).includes('Files')}
 return <div className="blank-surface"
  onDragOver={event=>{if(hasFiles(event))event.preventDefault()}}
  onDrop={event=>{if(!hasFiles(event))return;event.preventDefault();const file=event.dataTransfer.files?.[0];if(file)onFile(file)}}>
  <Editable value={artifact.draftText||""} onChange={onText} onBlur={onFinish} onPasteFile={onFile} label="내용 입력" placeholder="내용을 입력하세요."/>
 </div>;
}
