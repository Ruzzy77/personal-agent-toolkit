type ChangeReceipt = {change:{id:string;status:string}};
type WorkChanges = {changes:Array<{id:string;status:string}>};
type FlowCall = <T>(name:string,input:unknown)=>Promise<T>;

type EditedArtifact = {
  workspaceId:string;
  workId:string;
  artifactId:string;
  baseRevision:number;
  artifact:unknown;
  wasBlank:boolean;
  idempotencyKey:string;
};

function result(status:string):"completed"|"conflict" {
  if(status==="completed"||status==="conflict")return status;
  throw new Error("작업물 저장 상태를 확인할 수 없습니다.");
}

/** A user's editor save applies its own proposal; other proposals still need explicit review. */
export async function saveUserEdit(call:FlowCall,edit:EditedArtifact):Promise<"completed"|"conflict"> {
  const {workspaceId,workId,artifactId,baseRevision,artifact,wasBlank,idempotencyKey}=edit;
  const submitted=await call<ChangeReceipt>("flow_change_submit",{
    workspace_id:workspaceId,work_id:workId,mode:wasBlank?"initialize":"proposal",
    artifact_id:artifactId,base_revision:baseRevision,artifact,idempotency_key:idempotencyKey,
  });
  const change=submitted.change;
  if(wasBlank||change.status!=="review")return result(change.status);
  try {
    const applied=await call<ChangeReceipt>("flow_change_action",{
      workspace_id:workspaceId,change_id:change.id,action:"apply",
    });
    return result(applied.change.status);
  } catch (failure) {
    // The response may be lost after the change was applied. Read the receipt before retrying.
    try {
      const latest=await call<WorkChanges>("flow_work_read",{workspace_id:workspaceId,work_id:workId});
      const status=latest.changes.find(item=>item.id===change.id)?.status;
      if(status==="completed"||status==="conflict")return status;
    } catch { /* Keep the original failure. */ }
    throw failure;
  }
}
