import {hostCall} from "./host-files";
import {ownerFetch} from "./owner-client";
import type {FlowArtifact,FlowRead,FlowSource} from "./flow-content";
import type {FlowLinkedResource} from "./flow-content";

type ArtifactChunk={artifact:Pick<FlowArtifact,"id"|"revision">;version:string;offset:number;content:string;totalBytes:number;nextOffset:number|null};
const artifacts=new Map<string,FlowArtifact>();
export async function readFlowArtifact(workspaceId:string,selector:Record<string,unknown>,signal?:AbortSignal):Promise<FlowArtifact>{
 const key=JSON.stringify([workspaceId,selector]),known=artifacts.get(key);if(known)return known;
 let offset=0,version:string|undefined,total=-1;const parts:string[]=[];
 do{
  const part=await hostCall<ArtifactChunk>("flow_artifact_read",{workspace_id:workspaceId,...selector,offset,limit:262144,...(version?{version}:{})},signal);
  if(part.offset!==offset||version&&part.version!==version||part.totalBytes>8*1024*1024||total!==-1&&part.totalBytes!==total)throw new Error("작업물 버전을 확인하지 못했습니다.");
  version=part.version;total=part.totalBytes;parts.push(part.content);
  if(part.nextOffset===null)break;
  if(part.nextOffset<=offset)throw new Error("작업물 조회가 중단되었습니다.");
  offset=part.nextOffset;
 }while(true);
 signal?.throwIfAborted();
 const value=JSON.parse(parts.join("")) as FlowArtifact;
 if(!value.id||!Number.isSafeInteger(value.revision))throw new Error("작업물 형식을 확인하지 못했습니다.");
 artifacts.set(key,value);if(artifacts.size>12)artifacts.delete(artifacts.keys().next().value!);
 return value;
}
export async function readFlowWork(workspaceId:string,workId:string,artifactId?:string,signal?:AbortSignal):Promise<FlowRead>{
 const result=await hostCall<FlowRead>("flow_work_read",{workspace_id:workspaceId,work_id:workId},signal);
 const head=result.work.artifacts.find(a=>a.id===(artifactId||result.work.activeArtifactId))||result.work.artifacts[0];
 if(!head)throw new Error("작업물이 없습니다.");
 const artifact=await readFlowArtifact(workspaceId,{artifact_id:head.id,revision:head.revision},signal);
 return {...result,work:{...result.work,artifacts:result.work.artifacts.map(a=>a.id===artifact.id?artifact:a)}};
}
export async function readFlowMaterial(workspaceId:string,item:FlowSource,signal?:AbortSignal):Promise<FlowSource>{
 const {entry}=await hostCall<{entry:FlowSource}>("flow_library_read",{workspace_id:workspaceId,entry_id:item.id},signal);
 if(!entry.artifactRef)return entry;
 const artifact=await readFlowArtifact(workspaceId,{source_id:entry.id,revision:entry.artifactRef.revision},signal);
 return {...entry,artifact};
}
export async function resourceCall<T>(name:"flow_resource_search"|"flow_resource_read",input:unknown,signal?:AbortSignal):Promise<T>{
 const response=await ownerFetch("/api/flow-resource/"+name,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(input),signal});
 const data=await response.json() as {ok?:boolean;result:T;error?:{code?:string;message?:string}};
 if(!response.ok||data.ok===false)throw new Error(data.error?.message||"자료에 연결하지 못했습니다.");
 return data.result;
}
export type ResourceResult={reference:FlowLinkedResource;title:string;detail:string;sourceVersion?:string};
export type ResourcePage={items:ResourceResult[];nextCursor:string|null;partial?:boolean};
export type ResourceContent={reference:FlowLinkedResource;title:string;detail:string;format:string;version:string;href?:string;body:string;nextStartChar:number|null};
export async function readOriginal(reference:FlowLinkedResource,signal:AbortSignal):Promise<ResourceContent>{
 let start=0,version:string|undefined,first:ResourceContent|undefined;const parts:string[]=[];
 do{
  const part=await resourceCall<ResourceContent>("flow_resource_read",{reference,start_char:start,max_chars:32768,...(version?{expected_version:version}:{})},signal);
  if(version&&part.version!==version)throw new Error("원자료가 변경되었습니다. 다시 열어 주세요.");
  first??=part;version=part.version;parts.push(part.body);
  if(part.nextStartChar===null)break;
  if(part.nextStartChar<=start||part.nextStartChar>20*1024*1024)throw new Error("자료 조회 범위를 확인하지 못했습니다.");
  start=part.nextStartChar;
 }while(true);
 signal.throwIfAborted();return {...first!,body:parts.join(""),nextStartChar:null};
}
export {createFlowMutator} from "./flow-mutations";
