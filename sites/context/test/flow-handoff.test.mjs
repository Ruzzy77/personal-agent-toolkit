import {strict as assert} from 'node:assert';
import test from 'node:test';
import {
  FLOW_LAST_WORK_KEY, fileHandoffFromSearch, flowResourceHref,
  flowSelectionFromStorage, resourceHandoffFromSearch, selectFlowWork, selectFlowWorkspace,
} from '../lib/flow-handoff.ts';

test('Files hand off the original file identity through the shared Flow route',()=>{
  const reference={kind:'host-file',root:'research-note/main',path:'자료/그림 1.png'};
  const href=flowResourceHref(reference);
  assert.ok(href);
  assert.deepEqual(resourceHandoffFromSearch(new URL(href,'https://toolkit.example').search),reference);
  const legacy='?work=work-1&fileRoot=workspace&filePath=notes%2Freport.md';
  assert.deepEqual(fileHandoffFromSearch(legacy),{root:'workspace',path:'notes/report.md'});
  assert.deepEqual(resourceHandoffFromSearch(legacy),{kind:'host-file',root:'workspace',path:'notes/report.md'});
  assert.equal(flowResourceHref({kind:'host-file',root:'workspace',path:'../outside.md'}),null);
  for(const search of [
    '?fileRoot=workspace','?filePath=notes%2Freport.md',
    '?fileRoot=workspace&fileRoot=other&filePath=notes%2Freport.md',
    '?fileRoot=workspace&filePath=a&filePath=b',
    '?fileRoot=&filePath=a','?fileRoot=workspace&filePath=',
    '?fileRoot=workspace&filePath=a%0Ab',
    '?fileRoot=workspace&filePath=..%2Foutside.md',
  ])assert.equal(resourceHandoffFromSearch(search),null);
});

test('Journal, Library, Design and Context use validated source identities',()=>{
  const readRef='read1.'+Buffer.from(JSON.stringify({version:1,spaceId:'research',connectionId:'main',corpusId:'source-corpus',unitId:'unit-1'})).toString('base64url');
  const references=[
    {kind:'journal-item',id:'123e4567-e89b-42d3-a456-426614174000'},
    {kind:'library-issue',id:'research:2026-09-24:09'},
    {kind:'design-recipe',id:'document-minimal'},
    {kind:'context',locator:{product:'sense',sectionId:'conversation-and-writing'}},
    {kind:'context',locator:{product:'corpus',spaceId:'research',documentId:'methods:notes'}},
    {kind:'context',locator:{product:'source',spaceId:'research',readRef}},
  ];
  for(const reference of references){
    const href=flowResourceHref(reference);
    assert.ok(href,JSON.stringify(reference));
    assert.deepEqual(resourceHandoffFromSearch(new URL(href,'https://toolkit.example').search),reference);
  }
  for(const reference of [
    {kind:'journal-item',id:'not-an-id'},
    {kind:'library-issue',id:'research:bad-date'},
    {kind:'design-recipe',id:'../outside'},
    {kind:'context',locator:{product:'source',spaceId:'research',readRef:'secret'}},
    {kind:'context',locator:{product:'source',spaceId:'other',readRef}},
    {kind:'host-file',root:'workspace',path:'../outside'},
  ])assert.equal(flowResourceHref(reference),null);
  for(const search of [
    '?resource=not-json',
    '?resource='+encodeURIComponent(JSON.stringify({kind:'journal-item',id:'not-an-id'})),
    '?resource='+encodeURIComponent(JSON.stringify({kind:'journal-item',id:'123e4567-e89b-42d3-a456-426614174000',href:'https://outside.example'})),
    '?resource='+encodeURIComponent(JSON.stringify(references[0]))+'&resource=x',
    '?resource='+encodeURIComponent(JSON.stringify(references[0]))+'&fileRoot=workspace&filePath=a',
    '?resource='+encodeURIComponent('x'.repeat(10001)),
  ])assert.equal(resourceHandoffFromSearch(search),null);
});

test('Flow restores only a valid work selection from browser session',()=>{
  assert.equal(FLOW_LAST_WORK_KEY,'toolkit-flow-current-work-v1');
  const current={workspaceId:'main',workId:'work-2'};
  assert.deepEqual(flowSelectionFromStorage(JSON.stringify(current)),current);
  for(const value of [null,'not-json','[]','{}',JSON.stringify({workspaceId:'main',workId:''}),
    JSON.stringify({workspaceId:'main',workId:'a\nb'}),'x'.repeat(501)])
    assert.equal(flowSelectionFromStorage(value),null);
});

test('explicit workspace and work selection take priority over remembered work',()=>{
  const remembered={workspaceId:'main',workId:'work-2'};
  const spaces=['main','other'];
  assert.equal(selectFlowWorkspace(spaces,'other','main',remembered),'other');
  assert.equal(selectFlowWorkspace(spaces,undefined,'other',remembered),'other');
  assert.equal(selectFlowWorkspace(spaces,undefined,null,remembered),'main');
  assert.equal(selectFlowWorkspace(spaces,undefined,'missing',null),'main');
  assert.equal(selectFlowWorkspace([],undefined,null,remembered),undefined);
  assert.equal(selectFlowWork(['work-1','work-2'],'work-1',remembered,'main'),'work-1');
  assert.equal(selectFlowWork(['work-1','work-2'],null,remembered,'main'),'work-2');
  assert.equal(selectFlowWork(['other-1'],'missing',remembered,'other'),'other-1');
  assert.equal(selectFlowWork([],null,remembered,'main'),undefined);
});
