import React,{useId,useState} from 'react';
import {Button} from '@openai/apps-sdk-ui/components/Button';
import {ChevronDown,ChevronRight} from 'lucide-react';
import './flow-controls.css';

export function ListItemAction({icon,label,description,children,...props}){
 return <Button type="button" color="primary" variant="ghost" size="2xl" pill={false} block selected={props['aria-current']==='true'} {...props}>
  <span className="flow-list-action-content">
   {icon}<span className="flow-list-action-label" title={typeof label==='string'?label:undefined}>{label}</span>
   {description&&<span className="flow-list-action-description">{description}</span>}{children}
  </span>
 </Button>;
}

export function Disclosure({label,open,onOpenChange,defaultOpen=false,children}){
 const [expanded,setExpanded]=useState(defaultOpen);
 const isOpen=open??expanded,id=useId(),Icon=isOpen?ChevronDown:ChevronRight;
 function toggle(){const next=!isOpen;if(open===undefined)setExpanded(next);onOpenChange?.(next)}
 return <div className="flow-disclosure">
  <Button type="button" color="primary" variant="ghost" size="lg" pill={false} block aria-expanded={isOpen} aria-controls={id} onClick={toggle}>
   <span className="flow-list-action-content"><span className="flow-list-action-label" title={typeof label==='string'?label:undefined}>{label}</span><Icon size="1em" aria-hidden="true"/></span>
  </Button>
  <div id={id} hidden={!isOpen}><div className="flow-disclosure-content su-stack">{children}</div></div>
 </div>;
}
