import {strict as assert} from 'node:assert';
import test from 'node:test';
import {activeContentSources,contentDraftValid,contentPreview,imageSlot,importContentImages} from '../lib/flow-composition.ts';
import {contentKinds,newContentBlock,canJoinContentRowAbove,joinContentRowAbove,stackContentRowAbove,setContentColumnSpan} from '@personal-agent/flow-surface/composition-editor';
import {stackComposition} from '@personal-agent/flow-surface/composition';
import {canConvertArtifactToContent,contentFromArtifact,contentBlockCapacity,appendBlocksToArtifact} from '@personal-agent/flow-surface/artifact-conversion';
import {imageCropGeometry} from '@personal-agent/flow-surface/image-region';

const source={root:'workspace',path:'images/part.png',version:'sha256:'+'a'.repeat(64)};
const pending='/api/file-content?root=workspace&path=images%2Fpart.png&preview=1';
const asset='/api/flow/assets/'+'b'.repeat(64)+'.png';
const composition={
 blocks:[
  {id:'heading',kind:'heading',content:{title:'검사 사진'}},
  {id:'photo',kind:'image',content:{src:pending,alt:'부품 상단'}},
  {id:'gallery',kind:'gallery',content:{images:[{id:'detail',src:pending,alt:'세부 사진'}]}}
 ],
 rows:[{id:'one',columns:[{span:12,ids:['heading']}]},{id:'two',columns:[{span:6,ids:['photo']},{span:6,ids:['gallery']}]}]
};
const sources={[imageSlot('photo')]:source,[imageSlot('gallery','detail')]:source,'deleted':source};

test('content draft validates pending images without changing saved media addresses',()=>{
 assert.equal(contentDraftValid(composition,sources),true);
 const preview=contentPreview('workspace',composition,sources,()=>pending);
 assert.equal(preview.blocks[1].content.src,pending);
 assert.equal(preview.blocks[2].content.images[0].src,pending);
 assert.equal(composition.blocks[1].content.src,pending);
 assert.deepEqual(Object.keys(activeContentSources(composition,sources)).sort(),[imageSlot('gallery','detail'),imageSlot('photo')].sort());
});

test('content image import deduplicates active sources and preserves row layout',async()=>{
 let calls=0;
 const result=await importContentImages(composition,sources,async()=>{calls++;return asset});
 assert.equal(calls,1);
 assert.equal(result.blocks[1].content.src,asset);
 assert.equal(result.blocks[2].content.images[0].src,asset);
 assert.deepEqual(result.rows,composition.rows);
 assert.equal(composition.blocks[1].content.src,pending);
});

test('content image import rejects malformed or unimported images',async()=>{
 await assert.rejects(()=>importContentImages(composition,{},async()=>asset),/invalid_content/);
});

test('the same diagram block can be edited and saved in Toolkit Web',()=>{
 assert.ok(contentKinds.some(([kind])=>kind==='diagram'));
 const diagram=newContentBlock('diagram','process',()=> 'first-node');
 diagram.content.nodes[0].label='원본 확인';
 diagram.content.nodes.push({id:'second-node',label:'조건 비교',detail:'조명과 각도',x:750,y:300});
 diagram.content.edges.push({id:'first-edge',from:'first-node',to:'second-node',label:''});
 const composition=stackComposition([diagram]);
 assert.equal(contentDraftValid(composition,{}),true);
 assert.equal(contentPreview('workspace',composition,{},()=>'' ).blocks[0].content.nodes[1].label,'조건 비교');
});

test('Toolkit Web can place content in a third column or below an existing block',()=>{
 const content=stackComposition([newContentBlock('text','a'),newContentBlock('text','b'),newContentBlock('text','c'),newContentBlock('text','d')]);
 let arranged=joinContentRowAbove(content,'b');
 arranged=setContentColumnSpan(arranged,'a',4);
 arranged=setContentColumnSpan(arranged,'b',4);
 assert.equal(canJoinContentRowAbove(arranged,'c'),true);
 arranged=joinContentRowAbove(arranged,'c');
 arranged=stackContentRowAbove(arranged,'d',1);
 assert.deepEqual(arranged.rows[0].columns.map(column=>column.ids),[['a'],['b','d'],['c']]);
 assert.equal(contentDraftValid(arranged,{}),true);
 assert.deepEqual(content.rows.map(row=>row.columns[0].ids),[['a'],['b'],['c'],['d']]);
});

test('Toolkit Web receives the shared document-to-content conversion',()=>{
 const artifact={id:'same-work',kind:'document',revision:3,title:'검사 기록',format:'document',blocks:[{id:'note',heading:'관찰',text:'기존 내용'}]};
 assert.equal(canConvertArtifactToContent(artifact),true);
 const converted=contentFromArtifact(artifact);
 assert.equal(converted.id,artifact.id);
 assert.equal(converted.revision,artifact.revision);
 assert.deepEqual(converted.composition.blocks[0].content,{heading:'관찰',paragraphs:['기존 내용']});
});

test('Toolkit Web can add a linked block to an existing document work surface',()=>{
 const artifact={id:'same-work',kind:'document',revision:3,title:'검사 기록',format:'document',
  blocks:[{id:'note',heading:'관찰',text:'기존 내용'}]};
 const linked={id:'resource',kind:'resource',content:{
  reference:{kind:'journal-item',id:'123e4567-e89b-42d3-a456-426614174000'},
  title:'현장 기록',detail:'Journal'
 }};
 assert.equal(contentBlockCapacity(artifact),99);
 const result=appendBlocksToArtifact(artifact,[linked]);
 assert.equal(result.id,artifact.id);
 assert.equal(result.revision,artifact.revision);
 assert.deepEqual(result.composition.blocks.map(block=>block.id),['note','resource']);
 assert.equal(result.composition.blocks[0].content.paragraphs[0],'기존 내용');
 assert.equal(contentDraftValid(result.composition,{}),true);
});

test('Toolkit Web keeps a cropped image inside mixed content',()=>{
 const artifact={id:'image-work',kind:'image',revision:2,title:'검사 이미지',src:'/api/flow/assets/'+'a'.repeat(64)+'.png',alt:'부품 상단',width:1200,height:800,crop:{x:.2,y:.1,width:.5,height:.6}};
 assert.equal(canConvertArtifactToContent(artifact),true);
 const converted=contentFromArtifact(artifact,()=> 'photo');
 assert.equal(converted.id,artifact.id);
 assert.equal(converted.composition.blocks[0].content.crop.width,.5);
 assert.equal(contentDraftValid(converted.composition,{}),true);
 assert.equal(imageCropGeometry(1200,800,artifact.crop).aspectRatio,1.25);
});
