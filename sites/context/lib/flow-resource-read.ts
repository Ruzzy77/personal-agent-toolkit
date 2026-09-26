import {readUIKitAsset} from "./flow-uikit-client";
import {hostCall} from "./host-files";
import {readDesignRecipes} from "./flow-design-client";
import {readOriginal} from "./flow-client";
import {contextHref,designResource,hostFileResource,uikitResource} from "./flow-resources";
import type {DesignRecipeRecord,FlowResourceSummary} from "./flow-resources";
import type {FlowLinkedResource} from "./flow-content";

export async function readLinked(reference:FlowLinkedResource,signal:AbortSignal,designRecipes?:Promise<DesignRecipeRecord[]>):Promise<FlowResourceSummary>{
 if(reference.kind==="host-file"){
  const info=await hostCall<{path:string;type:string}>("host_files",{root:reference.root,operation:"stat",path:reference.path},signal);
  signal.throwIfAborted();if(info.type!=="file"||info.path!==reference.path)throw new Error("파일을 열지 못했습니다.");
  return hostFileResource(reference.root,reference.path);
 }
 if(reference.kind==="uikit-asset")return uikitResource(await readUIKitAsset(reference.id,reference.revision,signal));
 if(reference.kind==="design-recipe"){
  const recipe=(await(designRecipes??readDesignRecipes(signal))).find(item=>item.id===reference.id);
  signal.throwIfAborted();if(!recipe)throw new Error("디자인 자료를 열지 못했습니다.");return designResource(recipe);
 }
 const content=await readOriginal(reference,signal);
 const href=content.href||(reference.kind==="context"?contextHref(reference.locator):"/flow?resource="+encodeURIComponent(JSON.stringify(reference)));
 const result={...reference,title:content.title,detail:content.detail,href,resolved:{body:content.body,format:content.format,version:content.version}};
 if(reference.kind==="user-context")return {...result,context:JSON.parse(content.body)};
 return result;
}
