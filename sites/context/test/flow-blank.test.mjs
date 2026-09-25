import {strict as assert} from 'node:assert';
import test from 'node:test';
import {blankTextContent,blankTextReady} from '../lib/flow-blank.ts';
import {contentDraftFromBlank} from '@personal-agent/flow-surface/blank-content';
import {updateContentBlock} from '@personal-agent/flow-surface/composition-editor';
import {contentDraftValid} from '../lib/flow-composition.ts';

test('blank text becomes content without replacing the work surface identity',()=>{
 const blank={id:'same',kind:'blank',revision:3,title:'검사 작업',draftText:'첫 관찰\n\n다음 관찰'};
 assert.equal(blankTextReady(blank),true);
 const artifact=blankTextContent(blank);
 assert.equal(artifact.id,'same');assert.equal(artifact.revision,3);
 assert.equal(artifact.kind,'content');assert.deepEqual(artifact.composition.blocks[0].content.paragraphs,['첫 관찰','다음 관찰']);
 assert.equal(artifact.composition.rows[0].columns[0].span,12);
 assert.equal(blank.kind,'blank');assert.equal(blank.draftText,'첫 관찰\n\n다음 관찰');
});
test('blank text cannot silently lose an empty title or excess paragraphs',()=>{
 assert.equal(blankTextReady({id:'same',kind:'blank',revision:0,title:'',draftText:'내용'}),false);
 const crowded={id:'same',kind:'blank',revision:0,title:'제목',draftText:Array.from({length:101},()=> '문단').join('\n\n')};
 assert.equal(blankTextReady(crowded),false);
 assert.throws(()=>blankTextContent(crowded));
});

test('blank work adds another content kind without discarding typed text',()=>{
 const blank={id:'same',kind:'blank',revision:3,title:'검사 작업',draftText:'첫 관찰\n\n다음 관찰'};
 let count=0;
 const draft=contentDraftFromBlank(blank,'table',()=> 'block-'+(++count));
 assert.equal(draft.id,blank.id);assert.equal(draft.revision,blank.revision);
 assert.deepEqual(draft.composition.blocks.map(block=>block.kind),['text','table']);
 assert.deepEqual(draft.composition.blocks[0].content.paragraphs,['첫 관찰','다음 관찰']);
 assert.equal(blank.kind,'blank');
});

test('an image draft stays unsaved until its source is ready',()=>{
 const blank={id:'same',kind:'blank',revision:4,title:'부품 사진',draftText:'원본 사진을 확인한다.'};
 let count=0;
 const draft=contentDraftFromBlank(blank,'image',()=> 'block-'+(++count));
 assert.equal(contentDraftValid(draft.composition,{}),false);
 const image=draft.composition.blocks.find(block=>block.kind==='image');
 const ready=updateContentBlock(draft.composition,image.id,{...image.content,src:'/examples/metal.png',alt:'부품 상단',width:1200,height:800});
 assert.equal(contentDraftValid(ready,{}),true);
 assert.deepEqual(ready.blocks.find(block=>block.kind==='text').content.paragraphs,['원본 사진을 확인한다.']);
 assert.equal(draft.id,blank.id);
});
