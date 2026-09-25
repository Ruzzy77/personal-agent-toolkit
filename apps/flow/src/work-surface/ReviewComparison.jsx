"use client";

import React from 'react';
import './review-comparison.css';

export function ReviewComparison({current,proposed,className=''}){
 return <div className={'ws-review-comparison '+className}>
  <section aria-label="현재"><span className="ws-review-label">현재</span>{current}</section>
  <section aria-label="수정안"><span className="ws-review-label">수정안</span>{proposed}</section>
 </div>;
}
