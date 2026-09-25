import test from 'node:test';
import assert from 'node:assert/strict';
import {saveUserEdit} from '../lib/flow-user-edit.ts';

const edit={workspaceId:'workspace',workId:'work-1',artifactId:'artifact-1',baseRevision:2,artifact:{id:'artifact-1',kind:'content'},wasBlank:false,idempotencyKey:'retry-key-1'};

test('a first edit of a blank work saves without a second action',async()=>{
  const calls=[];
  const call=async(name,input)=>{calls.push({name,input});return {change:{id:'change-1',status:'completed'}}};
  assert.equal(await saveUserEdit(call,{...edit,wasBlank:true}), 'completed');
  assert.deepEqual(calls.map(item=>item.name),['flow_change_submit']);
  assert.equal(calls[0].input.mode,'initialize');
  assert.equal(calls[0].input.idempotency_key,edit.idempotencyKey);
});

test('a direct user edit submits and applies in one UI action',async()=>{
  const calls=[];
  const call=async(name,input)=>{
    calls.push({name,input});
    return {change:{id:'change-2',status:name==='flow_change_submit'?'review':'completed'}};
  };
  assert.equal(await saveUserEdit(call,edit),'completed');
  assert.deepEqual(calls.map(item=>item.name),['flow_change_submit','flow_change_action']);
  assert.equal(calls[0].input.mode,'proposal');
  assert.deepEqual(calls[1].input,{workspace_id:edit.workspaceId,change_id:'change-2',action:'apply'});
});

test('a lost apply response is checked before reporting failure',async()=>{
  const calls=[];
  const call=async name=>{
    calls.push(name);
    if(name==='flow_change_submit')return {change:{id:'change-3',status:'review'}};
    if(name==='flow_change_action')throw new Error('response lost');
    return {changes:[{id:'change-3',status:'completed'}]};
  };
  assert.equal(await saveUserEdit(call,edit),'completed');
  assert.deepEqual(calls,['flow_change_submit','flow_change_action','flow_work_read']);
});

test('an unapplied edit keeps its retry key and can be completed on retry',async()=>{
  const calls=[];
  let applied=false;
  const call=async(name,input)=>{
    calls.push({name,input});
    if(name==='flow_change_submit')return {change:{id:'change-4',status:applied?'completed':'review'}};
    if(name==='flow_change_action'){
      if(!applied){applied=true;throw new Error('temporary failure')}
      return {change:{id:'change-4',status:'completed'}};
    }
    return {changes:[{id:'change-4',status:'review'}]};
  };
  await assert.rejects(saveUserEdit(call,edit),/temporary failure/);
  assert.equal(await saveUserEdit(call,edit),'completed');
  assert.deepEqual(calls.filter(item=>item.name==='flow_change_submit').map(item=>item.input.idempotency_key),[edit.idempotencyKey,edit.idempotencyKey]);
});

test('a conflicting change is not reported as saved',async()=>{
  const call=async name=>({change:{id:'change-5',status:name==='flow_change_submit'?'review':'conflict'}});
  assert.equal(await saveUserEdit(call,edit),'conflict');
});
