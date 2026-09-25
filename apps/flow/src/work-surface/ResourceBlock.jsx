import React from 'react';
import {BlockTitle} from './BlockTitle.jsx';
import './resource-block.css';

export function ResourceBlock({block,context}){
 const {title,detail}=block.content;
 return <section className="su-panel ws-resource">
  {detail&&<span className="ws-resource-detail">{detail}</span>}
  <BlockTitle context={context}>{title}</BlockTitle>
 </section>;
}
