import {ownerFetch} from './owner-client';
import {uikitAssetsFromResult} from './flow-uikit';
export async function readUIKitAssets(signal:AbortSignal){
 const response=await ownerFetch('/api/uikit/search',{signal});
 if(!response.ok)throw new Error('UIKit 자료를 읽을 수 없습니다.');
 return uikitAssetsFromResult(await response.json());
}
export async function readUIKitAsset(id:string,revision:string,signal:AbortSignal){
 const response=await ownerFetch('/api/uikit/read?'+new URLSearchParams({id,revision}),{signal});
 if(!response.ok)throw new Error('UIKit 자료를 읽을 수 없습니다.');
 const value=await response.json() as {revision:string;item:unknown};
 const item=uikitAssetsFromResult({revision:value.revision,items:[value.item]})[0];
 if(!item||item.id!==id||item.revision!==revision)throw new Error('UIKit 자료가 일치하지 않습니다.');
 return item;
}
