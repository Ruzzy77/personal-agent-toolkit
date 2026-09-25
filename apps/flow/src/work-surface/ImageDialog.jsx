"use client";

import React from 'react';
import {Dialog} from '@personal-agent/ui-kit/react';
import './image-dialog.css';

export function ImageDialog({image,onClose}){
 return <Dialog open={!!image} onOpenChange={open=>{if(!open)onClose()}} title={image?.title||'이미지'} className="ws-image-dialog">
  {image&&<img src={image.src} alt={image.alt||''}/>}
 </Dialog>;
}
