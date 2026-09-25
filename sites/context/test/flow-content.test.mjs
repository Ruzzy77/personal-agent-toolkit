import {strict as assert} from 'node:assert';
import test from 'node:test';
import {flowArtifactKind,flowContentForWeb,flowMediaUrl} from '../lib/flow-content.ts';

test('presentation and document labels follow the stored format',()=>{
  assert.equal(flowArtifactKind('document','slides'),'발표 자료');
  assert.equal(flowArtifactKind('document','document'),'문서');
  assert.equal(flowArtifactKind('content'),'작업 화면');
});

test('Flow media addresses stay in the owner bridge and registered workspace',()=>{
  const hash='a'.repeat(64);
  assert.equal(flowMediaUrl('workspace','/api/flow/assets/'+hash+'.png'),'/api/flow-media/workspace/assets/'+hash+'.png');
  assert.equal(flowMediaUrl('workspace','/examples/metal.png'),'/api/flow-media/workspace/examples/metal.png');
  assert.equal(flowMediaUrl('workspace','/examples/recording.m4a'),'/api/flow-media/workspace/examples/recording.m4a');
  assert.equal(flowMediaUrl('workspace','/api/flow/files/content?workspaceId=workspace&path=media%2Fsample.wav'),'/api/flow-media/workspace/files/content?path=media%2Fsample.wav');
  assert.equal(flowMediaUrl('workspace','/api/flow/files/content?workspaceId=workspace&path=docs%2Freport.pdf'),'/api/flow-media/workspace/files/content?path=docs%2Freport.pdf');
  assert.equal(flowMediaUrl('workspace','/api/flow/files/content?workspaceId=workspace&path=notes%2Fmemo.md'),'/api/flow-media/workspace/files/content?path=notes%2Fmemo.md');
  assert.equal(flowMediaUrl('workspace','/api/flow/files/content?workspaceId=other&path=sample.wav'),null);
  assert.equal(flowMediaUrl('workspace','https://outside.example/image.png'),null);
  assert.equal(flowMediaUrl('workspace','/examples/unsafe.svg'),null);
  assert.equal(flowMediaUrl('workspace','data:image/png;base64,AQID'),'data:image/png;base64,AQID');
  assert.equal(flowMediaUrl('workspace','data:image/png;base64,'+'A'.repeat(2_799_970))?.startsWith('data:image/png'),true);
  assert.equal(flowMediaUrl('workspace','data:image/png;base64,'+'A'.repeat(2_800_000)),null);
  assert.equal(flowMediaUrl('workspace','data:image/svg+xml;base64,PHN2Zz4='),null);
});

test('Flow content rewrites nested media without changing stored artifacts',()=>{
  const source={blocks:[
    {id:'one',kind:'media',content:{src:'/examples/metal.mp4',poster:'/examples/metal.png'}},
    {id:'two',kind:'comparison',content:{items:[{label:'A',src:'/examples/camera.png'}]}},
    {id:'three',kind:'references',content:{items:[{title:'원문',href:'https://example.org/paper'}]}},
    {id:'four',kind:'file',content:{name:'보고서.pdf',type:'PDF',href:'/api/flow/files/content?workspaceId=workspace&path=docs%2Freport.pdf'}},
    {id:'five',kind:'file',content:{name:'메모.md',type:'Markdown',href:'/api/flow/files/content?workspaceId=workspace&path=notes%2Fmemo.md'}},
  ],rows:[{id:'row',columns:[{span:12,ids:['one','two','three','four','five']}]}]};
  const result=flowContentForWeb('workspace',source);
  assert.equal(result.blocks[0].content.poster,'/api/flow-media/workspace/examples/metal.png');
  assert.equal(result.blocks[1].content.items[0].src,'/api/flow-media/workspace/examples/camera.png');
  assert.equal(result.blocks[2].content.items[0].href,'https://example.org/paper');
  assert.equal(result.blocks[3].content.href,'/api/flow-media/workspace/files/content?path=docs%2Freport.pdf');
  assert.equal(result.blocks[4].content.href,'/api/flow-media/workspace/files/content?path=notes%2Fmemo.md');
  assert.equal(source.blocks[0].content.poster,'/examples/metal.png');
});
