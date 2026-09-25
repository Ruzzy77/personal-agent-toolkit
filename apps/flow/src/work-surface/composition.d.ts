import type {ContentBlock,ContentComposition} from './index.js';
export function defineComposition(value:ContentComposition):ContentComposition;
export function stackComposition(blocks:ContentBlock[]):ContentComposition;
export function packComposition(blocks:ContentBlock[],spanOf?:(block:ContentBlock)=>number):ContentComposition;
