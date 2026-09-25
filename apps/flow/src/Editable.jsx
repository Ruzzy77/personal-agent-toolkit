import React,{useLayoutEffect,useRef} from 'react';
export function Editable({as='p',value,onChange,onActivate,label,className='',placeholder='',id,onSelectText,onPasteFile,onBlur}){
 const ref=useRef(null);
 useLayoutEffect(()=>{if(ref.current&&document.activeElement!==ref.current&&ref.current.innerText!==value)ref.current.innerText=value},[value]);
 function reportSelection(){const selection=window.getSelection();if(!selection||selection.isCollapsed||!selection.rangeCount||!ref.current?.contains(selection.getRangeAt(0).commonAncestorContainer))return;const selectedText=selection.toString();if(selectedText.trim())onSelectText?.(selectedText)}
 return React.createElement(as,{ref,contentEditable:true,suppressContentEditableWarning:true,role:'textbox','aria-label':label,'aria-multiline':true,'data-placeholder':placeholder,id,
  className:'editable '+className,onFocus:onActivate,onBlur:e=>onBlur?.(e.currentTarget.innerText),onMouseUp:reportSelection,onKeyUp:reportSelection,
  onInput:e=>onChange(e.currentTarget.innerText),
  onPaste:e=>{if(onPasteFile&&e.clipboardData.files?.length){e.preventDefault();onPasteFile(e.clipboardData.files[0]);return}e.preventDefault();const text=e.clipboardData.getData('text/plain');const selection=window.getSelection();if(selection?.rangeCount){const r=selection.getRangeAt(0);r.deleteContents();const n=document.createTextNode(text);r.insertNode(n);r.setStartAfter(n);r.collapse(true);selection.removeAllRanges();selection.addRange(r);onChange(ref.current.innerText)}},
 });
}
