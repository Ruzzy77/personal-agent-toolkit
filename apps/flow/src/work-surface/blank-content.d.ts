import type {ContentBlock,ContentComposition} from './index.js';

export type BlankContentSource={id:string;kind:'blank';revision:number;title:string;draftText?:string};
export type InitializedContent={id:string;kind:'content';revision:number;title:string;composition:ContentComposition};
export function blankParagraphs(value:unknown):string[]|null;
export function contentDraftFromBlank(artifact:BlankContentSource,kind?:string,newId?:()=>string):InitializedContent;
export function contentFromBlank(artifact:BlankContentSource,additionalBlock?:ContentBlock|null,newId?:()=>string):InitializedContent;
