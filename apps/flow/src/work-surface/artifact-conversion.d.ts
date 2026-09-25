import type {ContentBlock, ContentComposition, ConvertibleArtifact} from './index.js';
export function contentFromArtifact(artifact:ConvertibleArtifact,newId?:()=>string):{id:string;kind:'content';revision:number;title:string;composition:ContentComposition};
export function canConvertArtifactToContent(artifact:ConvertibleArtifact):boolean;
export function keepsOriginalImage(artifact:ConvertibleArtifact,composition:ContentComposition|undefined):boolean;

export type ContentTargetArtifact=ConvertibleArtifact & {draftText?:string;composition?:ContentComposition};
export function contentBlockCapacity(artifact:ContentTargetArtifact):number;
export function appendBlocksToArtifact(artifact:ContentTargetArtifact,blocks:ContentBlock[],newId?:()=>string):{id:string;kind:'content';revision:number;title:string;composition:ContentComposition};
