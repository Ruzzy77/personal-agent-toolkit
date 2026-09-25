import React from 'react';

export function BlockTitle({context,sub=false,className='ws-block-title',children}){
 const base=Number.isInteger(context?.headingLevel)?context.headingLevel:2;
 const level=Math.max(1,Math.min(6,base+(sub?1:0)));
 const Tag=`h${level}`;
 return <Tag className={className}>{children}</Tag>;
}
