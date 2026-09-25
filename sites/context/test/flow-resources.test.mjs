import {strict as assert} from 'node:assert';
import test from 'node:test';
import {flowResourceIdentity,flowResourceKey,journalResource,libraryResource,designResource,hostFileResource,contextResource,contextHref} from '../lib/flow-resources.ts';
import {contextLocatorFromSearch} from '../lib/context-links.ts';
import {resourceBlockFromSummary,resourceContentArtifact,hasResourceBlock} from '../lib/flow-resource-block.ts';
import {designRecipesFromCatalog} from '../lib/flow-design.ts';

test('Flow links refer to live product identities, not copied content or external destinations',()=>{
  const journal=journalResource({id:'123e4567-e89b-42d3-a456-426614174000',title:'후속 실험',summary:'기록 원문',weekId:'2026-09-21',resolution:'active'});
  assert.equal(flowResourceKey(journal),'journal-item:123e4567-e89b-42d3-a456-426614174000');
  assert.deepEqual(journal,{kind:'journal-item',id:'123e4567-e89b-42d3-a456-426614174000',title:'후속 실험',detail:'진행 중',href:'/journal?week=2026-09-21&item=123e4567-e89b-42d3-a456-426614174000'});
  assert.deepEqual(flowResourceIdentity(journal),{kind:'journal-item',id:journal.id});
  const library=libraryResource({id:'research:2026-09-24:09',title:'분석 기록',date:'2026-09-24',collection:'research',canonicalPath:'/editions/research/brief/issues/2026-09-24/09'});
  assert.equal(library.href,'/editions/research/brief/issues/2026-09-24/09');
  assert.equal(libraryResource({...library,canonicalPath:'https://external.example/steal'}).href,'/library');
  assert.equal(journalResource({...journal,weekId:'bad?week=x'}).href,'/journal?item=123e4567-e89b-42d3-a456-426614174000');
  const design=designResource({id:'document-minimal',name:'UI Kit 문서',description:'',version:'2.0',status:'validated',selection_ready:true,templates:{},gallery:{korean_name:'UI Kit 문서'}});
  assert.equal(flowResourceKey(design),'design-recipe:document-minimal');
  assert.deepEqual(design,{kind:'design-recipe',id:'document-minimal',title:'UI Kit 문서',detail:'디자인 자료',href:'/design#design-document-minimal'});
  assert.equal(designResource({...design,id:'../other',templates:{},name:'미사용',description:'',version:'1',status:'draft',selection_ready:false}).href,'/design');
  const file=hostFileResource('workspace','notes/한글.md');
  assert.equal(flowResourceKey(file),'host-file:workspace:notes/한글.md');
  assert.deepEqual(flowResourceIdentity(file),{kind:'host-file',root:'workspace',path:'notes/한글.md'});
  assert.equal(file.href,'/api/file-content?root=workspace&path=notes%2F%ED%95%9C%EA%B8%80.md');
  assert.equal(file.title,'한글.md');
  const projectFile=hostFileResource('research-note/main','자료/그림 1.png');
  assert.equal(flowResourceKey(projectFile),'host-file:research-note/main:자료/그림 1.png');
  assert.equal(projectFile.href,'/api/file-content?root=research-note%2Fmain&path=%EC%9E%90%EB%A3%8C%2F%EA%B7%B8%EB%A6%BC+1.png');
});

test('Flow places a linked resource in an existing mixed screen without copying its body',()=>{
  const resource=journalResource({id:'123e4567-e89b-42d3-a456-426614174000',title:'검사 기록',summary:'원문 내용',weekId:'2026-09-21',resolution:'active'});
  const block=resourceBlockFromSummary(resource,'resource-1');
  assert.deepEqual(block,{id:'resource-1',kind:'resource',content:{
    reference:{kind:'journal-item',id:resource.id},title:'검사 기록',detail:'진행 중',
  }});
  const composition={blocks:[{id:'intro',kind:'text',content:{paragraphs:['기존 내용']}},block],
    rows:[{id:'one',columns:[{span:12,ids:['intro']}]},{id:'two',columns:[{span:12,ids:['resource-1']}]}]};
  assert.equal(hasResourceBlock(composition,resource),true);
  assert.equal(hasResourceBlock(composition,libraryResource({id:'daily:2026-09-21',title:'다른 자료',date:'2026-09-21',collection:'daily',canonicalPath:'/editions/daily/2026-09-21'})),false);
  assert.equal(composition.blocks[0].content.paragraphs[0],'기존 내용');
});

test('a Toolkit resource becomes the first Flow work surface without copying its body',()=>{
  const resource=journalResource({id:'123e4567-e89b-42d3-a456-426614174000',title:'검사 기록',summary:'원문 내용',weekId:'2026-09-21',resolution:'active'});
  const artifact=resourceContentArtifact(resource,'artifact-1','block-1');
  assert.equal(artifact.kind,'content');
  assert.equal(artifact.title,'검사 기록');
  assert.equal(artifact.composition.blocks.length,1);
  assert.deepEqual(artifact.composition.blocks[0],resourceBlockFromSummary(resource,'block-1'));
  assert.equal(hasResourceBlock(artifact.composition,resource),true);
  assert.equal(JSON.stringify(artifact).includes('원문 내용'),false);
});

test('Flow links Sense and Corpus through canonical locators and safe same-origin paths',()=>{
  const sense={product:'sense',sectionId:'conversation-and-writing'};
  const corpus={product:'corpus',spaceId:'research',documentId:'methods:notes'};
  const item={product:'context-item',spaceId:'research',itemId:'item_1'};
  const skill={product:'context-skill',spaceId:'research'};
  const readRef='read1.'+Buffer.from(JSON.stringify({version:1,spaceId:'research',connectionId:'main',corpusId:'source-corpus',unitId:'unit-1'})).toString('base64url');
  const source={product:'source',spaceId:'research',readRef};
  assert.equal(contextHref(sense),'/context?sense=conversation-and-writing');
  assert.equal(contextHref({...sense,skill:true}),'/context?sense=conversation-and-writing&skill=1');
  assert.equal(contextHref(corpus),'/context?space=research&document=methods%3Anotes');
  assert.equal(contextHref(item),'/context?space=research&item=item_1');
  assert.equal(contextHref(skill),'/context?space=research&skill=1');
  assert.equal(contextHref(source),'/context?space=research&source='+readRef);
  const reference=contextResource(corpus,'연구 메모');
  assert.equal(flowResourceKey(reference),'context:corpus:research:methods:notes');
  assert.equal(reference.title,'연구 메모');
  assert.equal(reference.detail,'문서');
  assert.deepEqual(flowResourceIdentity(reference),{kind:'context',locator:corpus});
  assert.equal(flowResourceKey(contextResource({...sense,skill:true},'스킬')),'context:sense:conversation-and-writing:skill');
  assert.equal(contextResource(source,'자료/원본.txt').detail,'원자료');
  assert.equal(flowResourceKey(contextResource(source,'자료/원본.txt')),'context:source:research:'+readRef);
  for(const locator of [sense,{...sense,skill:true},corpus,item,skill,source]) assert.deepEqual(contextLocatorFromSearch(contextHref(locator),['research']),locator);
  assert.equal(contextLocatorFromSearch('/context?space=unknown&document=notes',['research']),null);
  assert.equal(contextLocatorFromSearch('/context?sense=../secret',['research']),null);
  assert.equal(contextLocatorFromSearch('/context?space=research&document=../secret',['research']),null);
  assert.equal(contextLocatorFromSearch('/context?space=research&source=opaque-ref',['research']),null);
  assert.equal(contextLocatorFromSearch('/context?space=other&source='+readRef,['research']),null);
  assert.equal(contextLocatorFromSearch('/context?space=research&source='+readRef+'&source='+readRef,['research']),null);
});

test('Design references use current catalog identities and preview metadata',()=>{
  const recipes=designRecipesFromCatalog({recipes:[
    {id:'document-minimal',name:'UI Kit 문서',description:'안내 문서',version:'2.0',status:'validated',selection_ready:true,templates:{document:'templates/document.html'},profiles:{preview:['templates/document.html']},gallery:{korean_name:'UI Kit 문서'}},
    {id:'../outside',name:'잘못된 항목',description:'',version:'1',status:'draft',selection_ready:false},
  ]});
  assert.equal(recipes.length,1);
  assert.deepEqual(recipes[0].profiles,{preview:['templates/document.html']});
  assert.deepEqual(recipes[0].templates,{document:'templates/document.html'});
  assert.equal(designResource(recipes[0]).href,'/design#design-document-minimal');
  assert.throws(()=>designRecipesFromCatalog({recipes:null}));
});

test('UIKit references pin publication and do not copy files into Flow',async()=>{
 const {uikitAssetsFromResult}=await import('../lib/flow-uikit.ts');
 const {uikitResource}=await import('../lib/flow-resources.ts');
 const {validResourceReference}=await import('@personal-agent/flow-surface/resource-reference');
 const revision='a'.repeat(64);
 const [asset]=uikitAssetsFromResult({revision,items:[{id:'continuous-report',kind:'template',format:'html',title:'보고서',description:'연속형 문서'}]});
 const value=uikitResource(asset),ref=flowResourceIdentity(value);
 assert.deepEqual(ref,{kind:'uikit-asset',id:asset.id,revision});assert.equal(validResourceReference(ref),true);
 assert.equal(flowResourceKey(ref),'uikit-asset:'+asset.id+':'+revision);
 assert.equal(new URL(value.href).searchParams.get('revision'),revision);
 assert.equal(validResourceReference({...ref,revision:'bad'}),false);
 assert.throws(()=>uikitAssetsFromResult({revision:'bad',items:[]}));
});
