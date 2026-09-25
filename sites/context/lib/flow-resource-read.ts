import {readUIKitAsset} from "./flow-uikit-client";
import {uikitResource} from "./flow-resources";
import { ownerFetch } from "./owner-client";
import { hostCall } from "./host-files";
import { contextCall, readCanonical } from "./context";
import { readDesignRecipes } from "./flow-design-client";
import { contextResource, designResource, hostFileResource, journalResource, libraryResource } from "./flow-resources";
import type { DesignRecipeRecord, FlowResourceSummary } from "./flow-resources";
import type { FlowLinkedResource } from "./flow-content";
import type { JournalItem } from "./journal";
import type { LibraryIssueSummary } from "./library";

export async function readLinked(reference:FlowLinkedResource, signal:AbortSignal, designRecipes?:Promise<DesignRecipeRecord[]>):Promise<FlowResourceSummary> {
  if(reference.kind==="user-context"){
    const data=await contextCall("hypes_read",{seed_refs:[reference.id],limit:20,max_hops:1});
    if(signal.aborted)throw new Error("자료를 열 수 없습니다.");
    const context=data as unknown as NonNullable<FlowResourceSummary["context"]>;
    if(!Array.isArray(context.nodes)||!Array.isArray(context.predicates)||!Array.isArray(context.edges))throw new Error("사용자 맥락을 열 수 없습니다.");
    const subject=context.nodes.find(node=>node.node_id===reference.id)||context.predicates.find(item=>item.predicate_id===reference.id);
    if(!subject)throw new Error("사용자 맥락을 찾을 수 없습니다.");
    return {kind:"user-context",id:reference.id,title:subject.name,detail:"사용자 맥락",context,href:"/flow?resource="+encodeURIComponent(JSON.stringify(reference))};
  }
  if (reference.kind==="journal-item") {
    const response=await ownerFetch(`/api/journal/items/${encodeURIComponent(reference.id)}`,{signal});
    if (!response.ok) throw new Error("기록을 열 수 없습니다.");
    const data=await response.json() as {ok:boolean;result?:{item:JournalItem}};
    if (!data.ok||!data.result?.item||data.result.item.id!==reference.id) throw new Error("기록을 열 수 없습니다.");
    return journalResource(data.result.item);
  }
  if(reference.kind==="library-issue"){
    const response=await ownerFetch(`/api/library/issues/${encodeURIComponent(reference.id)}`,{signal});
    if (!response.ok) throw new Error("발간물을 열 수 없습니다.");
    const data=await response.json() as {issue?:LibraryIssueSummary};
    if (!data.issue||data.issue.id!==reference.id) throw new Error("발간물을 열 수 없습니다.");
    return libraryResource(data.issue);
  }
  if(reference.kind==="host-file"){
    const info=await hostCall<{path:string;type:string}>("host_files",{root:reference.root,operation:"stat",path:reference.path});
    if(signal.aborted||info.type!=="file"||info.path!==reference.path)throw new Error("파일을 열 수 없습니다.");
    return hostFileResource(reference.root,reference.path);
  }
  if(reference.kind==="uikit-asset")return uikitResource(await readUIKitAsset(reference.id,reference.revision,signal));
  if(reference.kind==="design-recipe"){
    const recipe=(await (designRecipes??readDesignRecipes(signal))).find(item=>item.id===reference.id);
    if(signal.aborted||!recipe)throw new Error("디자인 자료를 열 수 없습니다.");
    return designResource(recipe);
  }
  const locator=reference.locator;
  if(locator.product==="source"){
    const data=await contextCall("corpus_file_read",{space_id:locator.spaceId,read_ref:locator.readRef,source_view:"text",max_chars:1000});
    const source=data.source as {relative_path?:unknown;space_id?:unknown}|undefined;
    if(signal.aborted||source?.space_id!==locator.spaceId||typeof source.relative_path!=="string"||!source.relative_path.trim())throw new Error("원자료를 열 수 없습니다.");
    return contextResource(locator,source.relative_path);
  }
  if(locator.product==="corpus"){
    const data=await contextCall("corpus_document_read",{space_id:locator.spaceId,document_id:locator.documentId,max_chars:1});
    const document=data.document as {document_id?:string;title?:string;relocated_from?:{document_id?:string}}|undefined;
    const title=document?.title;
    if(signal.aborted||!title||(document.document_id!==locator.documentId&&document.relocated_from?.document_id!==locator.documentId))throw new Error("문서를 열 수 없습니다.");
    return contextResource(locator,title);
  }
  const source=await readCanonical(locator);
  if(signal.aborted)throw new Error("자료를 열 수 없습니다.");
  return contextResource(locator,source.title);
}
