export type ImageRegion={x:number;y:number;width:number;height:number};
export const fullImageRegion:ImageRegion;
export function validImageRegion(region:ImageRegion|null|undefined):boolean;
export function imageRegionBetween(first:{x:number;y:number},second:{x:number;y:number}):ImageRegion;
export function imageCropGeometry(width:number,height:number,crop?:ImageRegion|null):{aspectRatio:number;imageStyle:{width:string;maxWidth:string;left:string;top:string}}|null;
