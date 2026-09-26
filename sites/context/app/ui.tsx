'use client';

import Link from 'next/link';
import {ToolkitThemeProvider} from './toolkit-theme';
import { Children, isValidElement, useSyncExternalStore, type ButtonHTMLAttributes, type ReactNode, type OptionHTMLAttributes } from 'react';
import { AppsSDKUIProvider } from '@openai/apps-sdk-ui/components/AppsSDKUIProvider';
import { Button, type ButtonProps } from '@openai/apps-sdk-ui/components/Button';
import { Input, type InputProps } from '@openai/apps-sdk-ui/components/Input';
import { Textarea, type TextareaProps } from '@openai/apps-sdk-ui/components/Textarea';
import { FieldSelect as KitSelect } from '@personal-agent/ui-kit/react';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { Menu } from '@openai/apps-sdk-ui/components/Menu';
import { ArrowUp, ArrowDown, RefreshCw, Ellipsis, type LucideIcon } from 'lucide-react';

export { Menu, Tooltip };
export { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
export function UIProvider({children}:{children:ReactNode}) { return <ToolkitThemeProvider><AppsSDKUIProvider linkComponent={Link}>{children}</AppsSDKUIProvider></ToolkitThemeProvider>; }
const subscribe = (notify:()=>void) => { const media=matchMedia('(pointer:coarse)'); media.addEventListener('change',notify); return ()=>media.removeEventListener('change',notify); };
function useControlSize() { return useSyncExternalStore(subscribe,()=>matchMedia('(pointer:coarse)').matches,()=>false) ? '2xl' as const : 'md' as const; }
function textOf(children:ReactNode):string { return Children.toArray(children).map(child=>isValidElement<{children?:ReactNode}>(child)?textOf(child.props.children):String(child)).join('').trim(); }
const actionIcons:Partial<Record<string,LucideIcon>>={'새로고침':RefreshCw,'위로':ArrowUp,'아래로':ArrowDown};
export function UiButton({children,className='',type='submit',...props}:Omit<ButtonHTMLAttributes<HTMLButtonElement>,'color'>) {
  const size=useControlSize(), label=textOf(children), Icon=actionIcons[label];
  const iconOnly=Boolean(Icon)||className.includes('icon-button');
  const primary=/(^|\s)(primary|management-primary)(\s|$)/.test(className);
  const button=<Button color="primary" variant={primary?'solid':iconOnly?'ghost':'outline'} pill={false} size={size} uniform={iconOnly} selected={props['aria-pressed']===true} type={type} {...props} aria-label={props['aria-label']||(Icon?label:undefined)} className={className}>{Icon?<Icon size="1em" aria-hidden="true"/>:children}</Button>;
  const tooltip=props['aria-label']||label;
  return iconOnly&&tooltip?<Tooltip content={tooltip} compact>{button}</Tooltip>:button;
}
export function IconButton({label,children,...props}:Omit<ButtonProps,'color'> & {label:string}) {
  const size=useControlSize();
  return <Tooltip content={label} compact><Button color="primary" variant="ghost" size={size} pill={false} uniform aria-label={label} {...props}>{children}</Button></Tooltip>;
}
export function UiInput(props:InputProps) { const size=useControlSize(); return <Input size={size} {...props}/>; }
export function UiTextarea(props:TextareaProps) { const size=useControlSize(); return <Textarea size={size} {...props}/>; }
// The pinned SDK does not forward aria-label to its trigger. This is the
// UI Kit NamedControl bridge; the SDK still owns its arrow, list and keyboard UX.
export function FieldSelect({children,'aria-label':label,...props}:{children:ReactNode;'aria-label':string;value:string;onChange:(option:{value:string;label:string})=>void;disabled?:boolean;required?:boolean;id?:string;name?:string}) {
  const size=useControlSize();
  const options=Children.toArray(children).filter(isValidElement).map(child=>{const p=(child.props as OptionHTMLAttributes<HTMLOptionElement>);return{value:String(p.value??textOf(p.children)),label:textOf(p.children),disabled:p.disabled};});
  return <KitSelect aria-label={label} restoreFocus {...props} size={size} pill={false} options={options} searchPlaceholder="검색" searchEmptyMessage="검색 결과 없음"/>;
}
export function ActionMenu({children,label='도구',disabled=false}:{children:ReactNode;label?:string;disabled?:boolean}) {
  return <Menu><Menu.Trigger><IconButton label={label} disabled={disabled}><Ellipsis size="1em" aria-hidden="true"/></IconButton></Menu.Trigger><Menu.Content align="end" minWidth={180}>{children}</Menu.Content></Menu>;
}

export function IconLink({href,label,children}:{href:string;label:string;children:ReactNode}) { return <Tooltip content={label} compact><Link href={href} className="su-btn su-icon-btn" data-variant="ghost" aria-label={label}>{children}</Link></Tooltip>; }
