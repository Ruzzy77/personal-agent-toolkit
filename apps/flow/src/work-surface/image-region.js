export const fullImageRegion=Object.freeze({x:0,y:0,width:1,height:1});

export function validImageRegion(region){
 return Boolean(region&&['x','y','width','height'].every(key=>Number.isFinite(region[key]))&&
  region.x>=0&&region.y>=0&&region.width>0&&region.height>0&&
  region.x+region.width<=1.000001&&region.y+region.height<=1.000001);
}

export function imageRegionBetween(first,second){
 const clamp=value=>Math.max(0,Math.min(1,value));
 const x1=clamp(first.x),y1=clamp(first.y),x2=clamp(second.x),y2=clamp(second.y);
 return {x:Math.min(x1,x2),y:Math.min(y1,y2),width:Math.abs(x2-x1),height:Math.abs(y2-y1)};
}

export function imageCropGeometry(width,height,crop=null){
 if(!Number.isFinite(width)||width<=0||!Number.isFinite(height)||height<=0||crop&&!validImageRegion(crop))return null;
 const region=crop||fullImageRegion;
 return {
  aspectRatio:width/height*region.width/region.height,
  imageStyle:{width:(100/region.width)+'%',maxWidth:'none',left:(-region.x/region.width*100)+'%',top:(-region.y/region.height*100)+'%'}
 };
}
