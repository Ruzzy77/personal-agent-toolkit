import test from 'node:test';
import assert from 'node:assert/strict';
import {createFlowMutator} from '../lib/flow-mutations.ts';

test('lost responses reuse a receipt while independent changes retain separate version guards',async()=>{
 const calls=[];let fail=true;
 const change=createFlowMutator(async(name,input)=>{calls.push({name,input});if(fail){fail=false;throw new Error('response lost')}return {saved:true}});
 const input={workspace_id:'workspace',entry:{id:'one',body:'새 내용'},expected_revision:2};
 await assert.rejects(change('flow_library_upsert',input),/response lost/);
 await change('flow_library_upsert',input);
 assert.equal(calls[0].input.idempotency_key,calls[1].input.idempotency_key);
 await change('flow_library_upsert',{...input,expected_revision:3});
 assert.notEqual(calls[1].input.idempotency_key,calls[2].input.idempotency_key);
 assert.equal(calls[2].input.expected_revision,3);
});
