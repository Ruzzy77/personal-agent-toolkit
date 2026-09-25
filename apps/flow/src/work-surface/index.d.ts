import type {ComponentType, ReactNode} from 'react';
export type ContentBlock = {id:string;kind:string;content:Record<string,unknown>};
export type ContentComposition = {blocks:ContentBlock[];rows:Array<{id:string;columns:Array<{span:number;ids:string[]}>}>};
export type ContentContext = {headingLevel?:number;resolveFilePresentation?:(path:string)=>FilePresentation|null;filePreviewRenderers?:Record<string,ComponentType<FilePreviewRendererProps>>;[key:string]:unknown};
export function WorkSurface(props:{composition:ContentComposition;renderers:Record<string,ComponentType<any>>;context?:ContentContext;label?:string;className?:string;id?:string}):ReactNode;
export function ReviewComparison(props:{current?:ReactNode;proposed?:ReactNode;className?:string}):ReactNode;
export const contentRenderers:Record<string,ComponentType<any>>;
export function ResourceBlock(props:{block:ContentBlock;context?:ContentContext}):ReactNode;
export function DiagramBlock(props:{block:ContentBlock;context?:ContentContext}):ReactNode;
export type ArtifactPreviewArtifact = {
  id?:string; revision?:number; kind:string; title:string; format?:string; composition?:ContentComposition;
  blocks?:Array<{id:string;heading:string;text:string}>;
  src?:string; alt?:string; width?:number; height?:number;
  crop?:{x:number;y:number;width:number;height:number}|null;
  nodes?:Array<{id:string;label:string;detail?:string;x:number;y:number}>;
  edges?:Array<{id:string;from:string;to:string;label?:string}>;
};
export function ArtifactPreview(props:{
  artifact:ArtifactPreviewArtifact|null|undefined;
  renderers?:Record<string,ComponentType<any>>;
  prepareContent?:(value:ContentComposition)=>ContentComposition;
  resolveMediaUrl?:(value:unknown)=>string|null;
  headingLevel?:number;compact?:boolean;className?:string;
}):ReactNode;
export function defineComposition(value:ContentComposition):ContentComposition;
export function stackComposition(blocks:ContentBlock[]):ContentComposition;
export function packComposition(blocks:ContentBlock[],spanOf?:(block:ContentBlock)=>number):ContentComposition;
export function blankParagraphs(value:unknown):string[]|null;
export function contentDraftFromBlank(artifact:{id:string;kind:'blank';revision:number;title:string;draftText?:string},kind?:string,newId?:()=>string):{id:string;kind:'content';revision:number;title:string;composition:ContentComposition};
export function contentFromBlank(artifact:{id:string;kind:'blank';revision:number;title:string;draftText?:string},additionalBlock?:ContentBlock|null,newId?:()=>string):{id:string;kind:'content';revision:number;title:string;composition:ContentComposition};
export type ConvertibleArtifact={id:string;kind:string;revision:number;title:string;format?:string;blocks?:Array<{id:string;heading:string;text:string}>;nodes?:Array<{id:string;label:string;detail?:string;x:number;y:number}>;edges?:Array<{id:string;from:string;to:string;label?:string}>;src?:string;alt?:string;width?:number;height?:number;crop?:{x:number;y:number;width:number;height:number}|null};
export function contentFromArtifact(artifact:ConvertibleArtifact,newId?:()=>string):{id:string;kind:'content';revision:number;title:string;composition:ContentComposition};
export function canConvertArtifactToContent(artifact:ConvertibleArtifact):boolean;
export function keepsOriginalImage(artifact:ConvertibleArtifact,composition:ContentComposition|undefined):boolean;
export type ContentTargetArtifact=ConvertibleArtifact & {draftText?:string;composition?:ContentComposition};
export function contentBlockCapacity(artifact:ContentTargetArtifact):number;
export function appendBlocksToArtifact(artifact:ContentTargetArtifact,blocks:ContentBlock[],newId?:()=>string):{id:string;kind:'content';revision:number;title:string;composition:ContentComposition};
export function safeContentHref(value:unknown):string|null;
export function inlineFileSource(value:unknown):{href:string;path:string}|null;
export type FilePreviewRendererProps={href:string;path:string;name:string;headingLevel:number};
export const filePreviewRenderers:Record<string,ComponentType<FilePreviewRendererProps>>;
export function FilePreview(props:{href:string;name?:string;headingLevel?:number;resolvePresentation?:(path:string)=>FilePresentation|null;renderers?:Record<string,ComponentType<FilePreviewRendererProps>>}):ReactNode;
export function resolveRelativeImagePath(documentPath:string,relative:string):string|null;
export function markdownImageHref(fileHref:string,relative:string):string|null;

export function layoutDiagramNodes<T extends {x:number;y:number}>(nodes:T[],direction:"horizontal"|"vertical"):T[];
export type DiagramNode={id:string;label:string;detail?:string;x:number;y:number};
export type DiagramEdge={id:string;from:string;to:string;label?:string};
export type DiagramSelection={kind:"node"|"edge";id:string};
export type DiagramMove={id:string;x:number;y:number;source:"pointer"|"keyboard";revision?:number};
export const DIAGRAM_WIDTH:number;
export const DIAGRAM_HEIGHT:number;
export function diagramLabelLines(label:string):string[];
export function diagramNodeHeight(label:string):number;
export function clampDiagramPosition(x:number,y:number):{x:number;y:number};
export function nudgeDiagramPosition(node:{x:number;y:number},key:string):{x:number;y:number}|null;
export function DiagramCanvas(props:{title?:string;nodes?:DiagramNode[];edges?:DiagramEdge[];selected?:DiagramSelection|null;onSelect?:(selection:DiagramSelection|null,meta:{source:"pointer"|"keyboard"|"focus"})=>void;onMoveNode?:(move:DiagramMove)=>void;revision?:number;readOnly?:boolean;disabled?:boolean;fit?:boolean;className?:string}):ReactNode;
export function DiagramFields(props:{content:{heading?:string;nodes:DiagramNode[];edges:DiagramEdge[]};onChange:(content:{heading?:string;nodes:DiagramNode[];edges:DiagramEdge[]})=>void;busy?:boolean;headingLabel?:string;headingMaxLength?:number}):ReactNode;


export const contentKinds:Array<[string,string]>;
export function newContentBlock(kind:string,id:string,newId?:()=>string):ContentBlock;
export function workspaceFileType(path:string):string|null;
export function workspaceMediaType(path:string):{kind:"audio"|"video";mime:string}|null;
export type FilePresentation={viewer:string;label:string;responseType:string;maxBytes:number};
export function workspaceFilePresentation(path:string):FilePresentation|null;
export function workspaceFileBlock(path:string,href:string,id?:string):ContentBlock;
export function fileContentFromFile(content:Record<string,unknown>,path:string,href:string):Record<string,unknown>;
export function pdfContentFromFile(content:Record<string,unknown>,path:string,href:string):Record<string,unknown>;
export function contentOrder(composition:ContentComposition):string[];
export function appendContentBlock(composition:ContentComposition,block:ContentBlock,afterId?:string|null,beside?:boolean,newId?:()=>string):ContentComposition;
export function updateContentBlock(composition:ContentComposition,id:string,content:Record<string,unknown>):ContentComposition;
export function removeContentBlock(composition:ContentComposition,id:string):ContentComposition;
export function moveContentBlock(composition:ContentComposition,id:string,step:number):ContentComposition;
export function contentColumnCapacity(composition:ContentComposition,id:string):number;
export function setContentColumnSpan(composition:ContentComposition,id:string,span:number):ContentComposition;
export function canJoinContentRowAbove(composition:ContentComposition,id:string):boolean;
export function joinContentRowAbove(composition:ContentComposition,id:string):ContentComposition;
export function stackContentRowAbove(composition:ContentComposition,id:string,columnIndex?:number):ContentComposition;
export function separateContentBlock(composition:ContentComposition,id:string,newId?:()=>string):ContentComposition;

export type ContentPick=(kind:"image"|"video"|"audio"|"pdf"|"file",blockId:string,field:"src"|"poster"|"href",itemId?:string)=>void;
export function CompositionEditor(props:{composition:ContentComposition;onChange:(value:ContentComposition)=>void;onPick:ContentPick;onAddFile?:()=>void;resolveImageUrl?:(src:string,blockId:string)=>string|null;busy?:boolean;className?:string}):ReactNode;
export function ContentFields(props:{block:ContentBlock;onChange:(content:Record<string,unknown>)=>void;onPick:ContentPick;resolveImageUrl?:(src:string,blockId:string)=>string|null;busy?:boolean}):ReactNode;

export function contentImageSlot(blockId:string,itemId?:string):string;
export type ContentSourceTarget={kind:"image"|"video"|"audio"|"file";blockId:string;field:"src"|"poster"|"href";itemId?:string};
export function contentImageTargetSlot(target:ContentSourceTarget):string;
export function setContentSource(composition:ContentComposition,target:ContentSourceTarget,src:string,size?:{width:number;height:number},path?:string):ContentComposition;
export function activeContentSources<T>(composition:ContentComposition|undefined,sources:Record<string,T>):Record<string,T>;
export function replaceContentImages<T>(composition:ContentComposition,sources:Record<string,T>,resolve:(source:T)=>string):ContentComposition;
export function validContentDraft<T>(composition:ContentComposition,sources:Record<string,T>):boolean;
export function previewableContentDraft<T>(composition:ContentComposition,sources:Record<string,T>):ContentComposition|null;
export function materializeContentImages<T>(composition:ContentComposition,sources:Record<string,T>,importImage:(source:T)=>Promise<string>,keyOf?:(source:T)=>unknown):Promise<ContentComposition>;

export function MediaPlayer(props:{kind:"video"|"audio";src:string;poster?:string;label?:string;className?:string}):ReactNode;
export function PdfPreview(props:{href:string;name?:string;className?:string}):ReactNode;
export function TextFilePreview(props:{href:string;name?:string;headingLevel?:number}):ReactNode;
export function TextContentView(props:{body?:string;path?:string;title?:string;headingLevel?:number;resolveImageSrc?:(value:string)=>string|null;className?:string}):ReactNode;
export function HtmlContentView(props:{body?:string;src?:string;name?:string;className?:string;showSourceToggle?:boolean}):ReactNode;
export function DataTable(props:{columns?:string[];rows?:string[][];rowNumbers?:boolean;label?:string}):ReactNode;
export function DelimitedTable(props:{data:{rows:string[][];columns:number;truncated:boolean}}):ReactNode;
export function textContentFormat(path:unknown):'markdown'|'csv'|'tsv'|'plain';
export function parseDelimitedText(body:unknown,format:string,maxRows?:number):{rows:string[][];columns:number;truncated:boolean}|null;
export function MarkdownContent(props:{body:string;title?:string;headingLevel?:number;className?:string;resolveImageSrc?:(value:string)=>string|null}):ReactNode;
export function safeMarkdownHref(value:unknown):string|null;

export type ImageRegion={x:number;y:number;width:number;height:number};
export const fullImageRegion:ImageRegion;
export function validImageRegion(region:ImageRegion|null|undefined):boolean;
export function imageRegionBetween(first:{x:number;y:number},second:{x:number;y:number}):ImageRegion;
export function imageCropGeometry(width:number,height:number,crop?:ImageRegion|null):{aspectRatio:number;imageStyle:{width:string;maxWidth:string;left:string;top:string}}|null;
export function ImageDialog(props:{image:{src:string;alt?:string;title?:string}|null;onClose:()=>void}):ReactNode;
export function ImageRegionEditor(props:{src:string;alt?:string;width:number;height:number;value:ImageRegion|null|undefined;onChange:(value:ImageRegion)=>void;disabled?:boolean;onError?:()=>void}):ReactNode;

export function SurfaceHeader(props:{title:ReactNode;meta?:ReactNode;actions?:ReactNode;className?:string}):ReactNode;
export function EditorActions(props:{busy?:boolean;disabled?:boolean;cancelDisabled?:boolean;onCancel:()=>void;saveLabel?:string;className?:string}):ReactNode;
export function EditorLayout(props:{children:ReactNode;preview:ReactNode;previewLabel?:string;headingLevel?:number;className?:string;fieldsClassName?:string;previewClassName?:string}):ReactNode;
export function ArtifactLayoutMenu(props:{title?:string;span:number;index:number;count:number;disabled?:boolean;onChange:(kind:"span"|"move",value:number)=>void}):ReactNode;

export const WorkCanvas: ComponentType<{work:any;artifactId?:string;onSelect?:(id:string)=>void;renderers?:any;prepareContent?:(composition:any)=>any;resolveMediaUrl?:(value:any)=>string|null}>;
export const HtmlArtifact: ComponentType<{artifact:any;resolveMediaUrl?:(value:any)=>string|null}>;

export const AppHeader: ComponentType<any>;
export const B: ComponentType<any>;
export const Overlay: ComponentType<any>;
export const BrowseToolbar: ComponentType<any>;
export const LibraryBrowser: ComponentType<any>;

export function exportHtmlArtifact(artifact:any,resolveMediaUrl?:(value:string)=>string|null):Promise<string>;
