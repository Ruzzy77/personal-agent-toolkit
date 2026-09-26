import test from 'node:test';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {defineComposition,stackComposition,packComposition,splitContentHeading} from '../src/work-surface/composition.js';
import {inspectionBlocks,inspectionLayouts} from '../src/examples/inspection.js';
import {catalogSections} from '../src/examples/catalog.js';
import {safeContentHref,inlineFileSource,inlinePdfHref,inlineTextHref,pdfPreviewHref,resolveRelativeImagePath,markdownImageHref} from '../src/work-surface/content-links.js';
import {layoutDiagramNodes} from '../src/work-surface/diagram-layout.js';
import {parseDelimitedText,textContentFormat} from '../src/work-surface/delimited.js';
import {diagramLabelLines,diagramNodeHeight,clampDiagramPosition,nudgeDiagramPosition} from '../src/work-surface/diagram-geometry.js';
import {fullImageRegion,imageRegionBetween,validImageRegion,imageCropGeometry} from '../src/work-surface/image-region.js';
import {validContentComposition,contentAssetSources} from '../src/work-surface/validation.js';
import {appendContentBlock,newContentBlock,workspaceFileBlock,workspaceFilePresentation,workspaceFileType,workspaceMediaType,fileContentFromFile,pdfContentFromFile,updateContentBlock} from '../src/work-surface/composition-editor.js';
import {contentImageTargetSlot,setContentSource} from '../src/work-surface/content-assets.js';
import {blankParagraphs,contentDraftFromBlank,contentFromBlank} from '../src/work-surface/blank-content.js';
import {contentFromArtifact,canConvertArtifactToContent,keepsOriginalImage,contentBlockCapacity,appendBlocksToArtifact} from '../src/work-surface/artifact-conversion.js';
import {artifactText,diagramArtifact,uploadedImageArtifact} from '../src/model.js';

const blocks=[
 {id:'title',kind:'heading',content:{title:'제목'}},
 {id:'table',kind:'table',content:{columns:['조건','값'],rows:[['A','1']]}},
 {id:'note',kind:'text',content:{paragraphs:['내용']}}
];

test('blank text and a media block share one content surface without losing identity',()=>{
 const blank={id:'same',kind:'blank',revision:4,title:'검사 기록',draftText:'첫 관찰\n\n다음 관찰'};
 const file={id:'photo',kind:'image',content:{src:'/examples/metal.png',alt:'금속 부품'}};
 const result=contentFromBlank(blank,file,()=> 'note');
 assert.equal(result.id,'same');assert.equal(result.revision,4);assert.equal(result.kind,'content');
 assert.deepEqual(result.composition.blocks.map(block=>block.kind),['text','image']);
 assert.deepEqual(result.composition.blocks[0].content.paragraphs,['첫 관찰','다음 관찰']);
 assert.deepEqual(result.composition.rows.map(row=>row.columns[0].ids[0]),['note','photo']);
 assert.equal(blank.kind,'blank');assert.equal(blank.draftText,'첫 관찰\n\n다음 관찰');
 const onlyFile=contentFromBlank({...blank,draftText:''},file);
 assert.deepEqual(onlyFile.composition.blocks,[file]);
 assert.equal(blankParagraphs(Array.from({length:101},()=> '문단').join('\n\n')),null);
 assert.throws(()=>contentFromBlank({...blank,draftText:' '}));
 assert.throws(()=>contentFromBlank({...blank,draftText:'data'}, {id:'bad',kind:'image',content:{src:'https://outside.example/a.png',alt:'외부'}}));
});

test('an unfinished image or diagram starts in the same mixed-content draft',()=>{
 const blank={id:'same',kind:'blank',revision:5,title:'검사 기록',draftText:'첫 관찰\n\n다음 관찰'};
 let serial=0;
 const image=contentDraftFromBlank(blank,'image',()=> 'id-'+(++serial));
 assert.equal(image.id,blank.id);
 assert.equal(image.revision,blank.revision);
 assert.deepEqual(image.composition.blocks.map(block=>block.kind),['text','image']);
 assert.deepEqual(image.composition.blocks[0].content.paragraphs,['첫 관찰','다음 관찰']);
 assert.equal(validContentComposition(image.composition),false);
 const diagram=contentDraftFromBlank({...blank,draftText:''},'diagram',()=> 'id-'+(++serial));
 assert.deepEqual(diagram.composition.blocks.map(block=>block.kind),['diagram']);
 assert.equal(diagram.composition.blocks[0].content.nodes.length,1);
 const text=contentDraftFromBlank(blank,'text',()=> 'id-'+(++serial));
 assert.deepEqual(text.composition.blocks.map(block=>block.kind),['text']);
 assert.throws(()=>contentDraftFromBlank({...blank,draftText:Array.from({length:101},()=> '문단').join('\n\n')},'image'));
 assert.throws(()=>contentDraftFromBlank(blank,'unknown'));
 assert.equal(blank.kind,'blank');
 const readyDiagram=contentFromArtifact(diagramArtifact('도식')).composition.blocks[0];
 const combined=contentFromBlank(blank,readyDiagram,()=> 'text-block');
 assert.equal(validContentComposition(combined.composition),true);
 assert.deepEqual(combined.composition.blocks.map(block=>block.kind),['text','diagram']);
 assert.equal(combined.id,blank.id);
});

test('documents and diagrams become mixed-content screens without losing their text, links or identity',()=>{
 const document={id:'document-1',kind:'document',revision:7,title:'검사 기록',format:'document',blocks:[
  {id:'intro',heading:'관찰',text:'첫 줄\n둘째 줄'}, {id:'detail',heading:'비교',text:'조건 A와 B'}
 ]};
 const converted=contentFromArtifact(document);
 assert.equal(canConvertArtifactToContent(document),true);
 assert.equal(converted.id,document.id);
 assert.equal(converted.revision,document.revision);
 assert.deepEqual(converted.composition.blocks.map(block=>block.id),['intro','detail']);
 assert.deepEqual(converted.composition.blocks.map(block=>block.content),[
  {heading:'관찰',paragraphs:['첫 줄\n둘째 줄']},{heading:'비교',paragraphs:['조건 A와 B']}
 ]);
 assert.equal(validContentComposition(converted.composition),true);
 const diagram={id:'diagram-1',kind:'diagram',revision:2,title:'검사 순서',nodes:[
  {id:'a',label:'원본 확인',detail:'기록과 대조',x:250,y:300},
  {id:'b',label:'조건 비교',detail:'',x:750,y:300}
 ],edges:[{id:'ab',from:'a',to:'b',label:'비교'}]};
 const visual=contentFromArtifact(diagram,()=> 'diagram-block');
 assert.equal(visual.id,diagram.id);
 assert.equal(visual.composition.blocks[0].kind,'diagram');
 assert.deepEqual(visual.composition.blocks[0].content.nodes,diagram.nodes);
 assert.deepEqual(visual.composition.blocks[0].content.edges,diagram.edges);
 visual.composition.blocks[0].content.nodes[0].label='수정한 항목';
 assert.equal(diagram.nodes[0].label,'원본 확인');
 assert.equal(canConvertArtifactToContent({...document,format:'slides'}),false);
 assert.equal(canConvertArtifactToContent({...document,blocks:Array.from({length:101},(_,i)=>({id:String(i),heading:'',text:''}))}),false);
 assert.equal(canConvertArtifactToContent({...diagram,edges:[{id:'ab',from:'a',to:'missing',label:''}]}),false);
});

test('new content joins a blank or a legacy artifact without replacing its original content',()=>{
 const note={id:'new-note',kind:'text',content:{paragraphs:['추가한 자료']}};
 const blank={id:'blank-work',kind:'blank',revision:2,title:'',draftText:'먼저 쓴 내용'};
 let sequence=0;
 const fromBlank=appendBlocksToArtifact(blank,[note],()=> 'generated-'+(++sequence));
 assert.equal(contentBlockCapacity(blank),99);
 assert.equal(fromBlank.id,blank.id);
 assert.deepEqual(fromBlank.composition.blocks.map(block=>block.kind),['text','text']);
 assert.deepEqual(fromBlank.composition.blocks[0].content.paragraphs,['먼저 쓴 내용']);
 assert.equal(blank.kind,'blank');
 const document={id:'document-work',kind:'document',revision:3,title:'검사 기록',format:'document',
  blocks:[{id:'original',heading:'관찰',text:'기존 내용'}]};
 const fromDocument=appendBlocksToArtifact(document,[note],()=> 'generated-'+(++sequence));
 assert.equal(contentBlockCapacity(document),99);
 assert.equal(fromDocument.id,document.id);
 assert.equal(fromDocument.revision,document.revision);
 assert.deepEqual(fromDocument.composition.blocks.map(block=>block.id),['original','new-note']);
 assert.deepEqual(fromDocument.composition.blocks[0].content.paragraphs,['기존 내용']);
 assert.equal(document.kind,'document');
 const image={id:'image-work',kind:'image',revision:5,title:'부품 사진',src:'/examples/metal.png',
  alt:'부품',width:1200,height:800,crop:{x:.2,y:.1,width:.5,height:.6}};
 const fromImage=appendBlocksToArtifact(image,[note],()=> 'image-block');
 assert.equal(fromImage.id,image.id);
 assert.equal(fromImage.composition.blocks[0].kind,'image');
 assert.equal(keepsOriginalImage(image,fromImage.composition),true);
 assert.equal(fromImage.composition.blocks[1].id,'new-note');
 const diagram={id:'diagram-work',kind:'diagram',revision:4,title:'검사 순서',
  nodes:[{id:'a',label:'촬영',x:250,y:300}],edges:[]};
 const fromDiagram=appendBlocksToArtifact(diagram,[note],()=> 'diagram-block');
 assert.deepEqual(fromDiagram.composition.blocks.map(block=>block.kind),['diagram','text']);
 assert.equal(fromDiagram.composition.blocks[0].content.nodes[0].label,'촬영');
 const current=appendBlocksToArtifact(fromDocument,[{id:'third',kind:'text',content:{paragraphs:['다음 내용']}}]);
 assert.deepEqual(current.composition.blocks.map(block=>block.id),['original','new-note','third']);
 assert.equal(fromDocument.composition.blocks.length,2);
 assert.equal(contentBlockCapacity({...document,format:'slides'}),0);
 assert.throws(()=>appendBlocksToArtifact({...document,format:'slides'},[note]));
 assert.equal(contentBlockCapacity({...fromDocument,composition:stackComposition(
  Array.from({length:100},(_,index)=>({id:'item-'+index,kind:'text',content:{paragraphs:['내용']}})))}),0);
 assert.throws(()=>appendBlocksToArtifact(fromImage,[note]));
});

test('an uploaded image starts as content rather than a separate image format',()=>{
 const image=uploadedImageArtifact('부품 사진','part.png',{src:'/examples/metal.png',width:1200,height:800});
 assert.equal(image.kind,'content');
 assert.equal(image.composition.blocks[0].kind,'image');
 assert.deepEqual(image.composition.blocks[0].content,{src:'/examples/metal.png',alt:'part.png',caption:'',width:1200,height:800});
 assert.equal(validContentComposition(image.composition),true);
 assert.throws(()=>uploadedImageArtifact('부품 사진','part.png',{src:'https://invalid.example/a.png',width:1200,height:800}));
});

test('offline image content accepts bounded raster data but rejects SVG and oversized input',()=>{
 const image={id:'inline',kind:'image',content:{src:'data:image/png;base64,iVBORw0KGgo=',alt:'사진'}};
 assert.equal(validContentComposition(stackComposition([image])),true);
 image.content.src='data:image/svg+xml;base64,PHN2Zz4=';
 assert.equal(validContentComposition(stackComposition([image])),false);
 image.content.src='data:image/png;base64,'+'a'.repeat(2800000);
 assert.equal(validContentComposition(stackComposition([image])),false);
});

test('a diagram joins text in one saved surface and keeps the shared layout',()=>{
 const diagram=newContentBlock('diagram','flow',()=> 'node-a');
 assert.equal(diagram.content.nodes[0].id,'node-a');
 diagram.content.nodes[0].label='원본 확인';
 diagram.content.nodes.push({id:'node-b',label:'조건별 비교',detail:'조명과 각도',x:750,y:300});
 diagram.content.edges.push({id:'edge-a-b',from:'node-a',to:'node-b',label:'대조'});
 const text={id:'note',kind:'text',content:{paragraphs:['촬영 조건을 비교한다.']}};
 const composition=defineComposition({blocks:[text,diagram],rows:[{id:'row',columns:[{span:6,ids:['note']},{span:6,ids:['flow']}]}]});
 assert.equal(validContentComposition(composition),true);
 assert.deepEqual(composition.rows[0].columns.map(column=>column.span),[6,6]);
 assert.match(artifactText({kind:'content',composition}),/조건별 비교/);
 assert.match(artifactText({kind:'content',composition}),/조명과 각도/);
 const stale=structuredClone(diagram);stale.content.edges[0].to='missing';
 assert.equal(validContentComposition(stackComposition([stale])),false);
 const duplicate=structuredClone(diagram);duplicate.content.nodes[1].id='node-a';
 assert.equal(validContentComposition(stackComposition([duplicate])),false);
 const blank=structuredClone(diagram);blank.content.nodes[0].label='';
 assert.equal(validContentComposition(stackComposition([blank])),false);
});

test('a cropped image keeps its original bounds when moved into a mixed screen',()=>{
 const crop={x:.2,y:.1,width:.5,height:.6};
 const image={id:'image-1',kind:'image',revision:4,title:'부품 사진',src:'/examples/metal.png',
  alt:'부품 상단',width:1200,height:800,crop};
 const converted=contentFromArtifact(image,()=> 'photo');
 const block=converted.composition.blocks[0];
 assert.equal(converted.id,image.id);
 assert.equal(converted.revision,image.revision);
 assert.equal(block.kind,'image');
 assert.equal(block.content.src,image.src);
 assert.deepEqual(block.content.crop,crop);
 assert.equal(validContentComposition(converted.composition),true);
 const geometry=imageCropGeometry(image.width,image.height,crop);
 assert.equal(geometry.aspectRatio,1.25);
 assert.equal(geometry.imageStyle.width,'200%');
 assert.equal(geometry.imageStyle.left,'-40%');
 assert.ok(Math.abs(parseFloat(geometry.imageStyle.top)+100/6)<.00001);
 const invalid=structuredClone(block);
 delete invalid.content.width;
 assert.equal(validContentComposition(stackComposition([invalid])),false);
 invalid.content.width=1200;invalid.content.crop={x:.8,y:.1,width:.5,height:.6};
 assert.equal(validContentComposition(stackComposition([invalid])),false);
 assert.equal(canConvertArtifactToContent({...image,crop:{x:.8,y:.1,width:.5,height:.6}}),false);
 assert.equal(keepsOriginalImage(image,converted.composition),true);
 assert.equal(keepsOriginalImage(image,stackComposition([{id:'note',kind:'text',content:{paragraphs:['원본 없음']}}])),false);
 assert.equal(keepsOriginalImage(image,stackComposition([{...block,content:{...block.content,width:800}}])),false);
});

test('image region selection clamps pointer positions and rejects invalid dimensions',()=>{
 assert.equal(validImageRegion(fullImageRegion),true);
 const region=imageRegionBetween({x:1.2,y:.8},{x:-.2,y:.1});
 assert.equal(region.x,0);assert.equal(region.y,.1);assert.equal(region.width,1);
 assert.ok(Math.abs(region.height-.7)<Number.EPSILON);
 assert.equal(validImageRegion({x:.7,y:.2,width:.4,height:.5}),false);
 assert.equal(validImageRegion({x:0,y:0,width:0,height:1}),false);
});

test('content stays the same when the layout changes',()=>{
 const side=defineComposition({blocks,rows:[
  {id:'head',columns:[{span:12,ids:['title']}]},
  {id:'body',columns:[{span:8,ids:['table']},{span:4,ids:['note']}]}
 ]});
 const stacked=stackComposition(blocks);
 assert.equal(side.blocks,blocks);
 assert.equal(stacked.blocks,blocks);
 assert.deepEqual(side.rows[1].columns.map(c=>c.span),[8,4]);
 assert.deepEqual(stacked.rows.map(row=>row.columns[0].ids[0]),['title','table','note']);
});

test('editing text in place keeps the other blocks and their layout',()=>{
 const composition=stackComposition(blocks);
 const next=updateContentBlock(composition,'note',{paragraphs:['수정한 내용']});
 assert.deepEqual(next.rows,composition.rows);
 assert.equal(next.blocks[0],composition.blocks[0]);
 assert.equal(next.blocks[1],composition.blocks[1]);
 assert.equal(next.blocks[2].content.paragraphs[0],'수정한 내용');
 assert.equal(composition.blocks[2].content.paragraphs[0],'내용');
 assert.throws(()=>updateContentBlock(composition,'missing',{paragraphs:['없음']}));
});

test('source targets share image slots across blocks, posters, and gallery items',()=>{
 assert.equal(contentImageTargetSlot({kind:'image',blockId:'photo',field:'src'}),JSON.stringify(['photo',null]));
 assert.equal(contentImageTargetSlot({kind:'image',blockId:'movie',field:'poster'}),JSON.stringify(['movie','poster']));
 assert.equal(contentImageTargetSlot({kind:'image',blockId:'gallery',field:'src',itemId:'first'}),JSON.stringify(['gallery','first']));
});

test('replacing media sources keeps the rest of the content and layout',()=>{
 const composition=stackComposition([
  {id:'intro',kind:'text',content:{paragraphs:['기존 내용']}},
  {id:'photo',kind:'image',content:{src:'/old.png',alt:'사진',selection:{x:0,y:0,width:1,height:1},crop:{x:.2,y:.2,width:.5,height:.5},width:800,height:600}},
  {id:'movie',kind:'media',content:{src:'/old.mp4',poster:'/old-poster.png',heading:'촬영 영상'}},
  {id:'audio',kind:'audio',content:{src:'/old.mp3',transcript:'현장 설명'}}
 ]);
 const image=setContentSource(composition,{kind:'image',blockId:'photo',field:'src'},'/new.png',{width:1200,height:900});
 assert.deepEqual(image.rows,composition.rows);
 assert.equal(image.blocks[0],composition.blocks[0]);
 assert.equal(image.blocks[2],composition.blocks[2]);
 assert.deepEqual(image.blocks[1].content,{src:'/new.png',alt:'사진',selection:undefined,crop:null,width:1200,height:900});
 assert.equal(composition.blocks[1].content.src,'/old.png');
 const poster=setContentSource(image,{kind:'image',blockId:'movie',field:'poster'},'/new-poster.png');
 assert.equal(poster.blocks[2].content.src,'/old.mp4');
 assert.equal(poster.blocks[2].content.poster,'/new-poster.png');
 const video=setContentSource(poster,{kind:'video',blockId:'movie',field:'src'},'/new.mp4');
 const audio=setContentSource(video,{kind:'audio',blockId:'audio',field:'src'},'/new.mp3');
 assert.equal(audio.blocks[2].content.src,'/new.mp4');
 assert.equal(audio.blocks[3].content.src,'/new.mp3');
 assert.equal(audio.blocks[3].content.transcript,'현장 설명');
 assert.equal(setContentSource(audio,{kind:'image',blockId:'missing',field:'src'},'/none.png'),audio);
 assert.equal(setContentSource(audio,{kind:'video',blockId:'photo',field:'src'},'/wrong.mp4'),audio);
});

test('replacing gallery and comparison items leaves other items untouched',()=>{
 const composition=stackComposition([
  {id:'gallery',kind:'gallery',content:{images:[{id:'a',src:'/a.png',alt:'A'},{id:'b',src:'/b.png',alt:'B'}]}},
  {id:'comparison',kind:'comparison',content:{items:[{id:'one',src:'/one.png',view:{scale:2,x:.2,y:.3}},{id:'two',src:'/two.png',view:{scale:3,x:.1,y:.4}}]}}
 ]);
 const gallery=setContentSource(composition,{kind:'image',blockId:'gallery',field:'src',itemId:'a'},'/new-a.png');
 assert.equal(gallery.blocks[0].content.images[0].src,'/new-a.png');
 assert.equal(gallery.blocks[0].content.images[1],composition.blocks[0].content.images[1]);
 const comparison=setContentSource(gallery,{kind:'image',blockId:'comparison',field:'src',itemId:'one'},'/new-one.png');
 assert.equal(comparison.blocks[1].content.items[0].src,'/new-one.png');
 assert.equal(comparison.blocks[1].content.items[0].view,undefined);
 assert.equal(comparison.blocks[1].content.items[1],composition.blocks[1].content.items[1]);
 assert.deepEqual(comparison.rows,composition.rows);
 assert.equal(setContentSource(comparison,{kind:'image',blockId:'gallery',field:'src',itemId:'missing'},'/x.png'),comparison);
});

test('replacing a file updates its metadata and keeps the description',()=>{
 const composition=stackComposition([{id:'file',kind:'file',content:{name:'old.txt',type:'텍스트',size:'12KB',description:'검사 자료',href:'/old'}}]);
 const href='/api/flow/files/content?workspaceId=workspace&path=notes%2Fmemo.md';
 const next=setContentSource(composition,{kind:'file',blockId:'file',field:'href'},href,undefined,'notes/memo.md');
 assert.deepEqual(next.blocks[0].content,{name:'memo.md',type:'Markdown',size:'',description:'검사 자료',href});
 assert.deepEqual(next.rows,composition.rows);
 assert.equal(composition.blocks[0].content.name,'old.txt');
 assert.throws(()=>setContentSource(composition,{kind:'file',blockId:'file',field:'href'},href,undefined,'notes/../secret.md'));
 assert.equal(setContentSource(composition,{kind:'file',blockId:'file',field:'src'},href,undefined,'notes/memo.md'),composition);
});

test('composition rejects hidden, duplicate, unknown and overflowing content',()=>{
 assert.throws(()=>defineComposition({blocks,rows:[{id:'a',columns:[{span:12,ids:['title']}]}]}),/배치되지 않은/);
 assert.throws(()=>defineComposition({blocks,rows:[{id:'a',columns:[{span:6,ids:['title','table']},{span:6,ids:['title','note']}]}]}),/배치된 콘텐츠/);
 assert.throws(()=>defineComposition({blocks,rows:[{id:'a',columns:[{span:12,ids:['missing']}]}]}),/배치된 콘텐츠/);
 assert.throws(()=>defineComposition({blocks,rows:[{id:'a',columns:[{span:8,ids:['title']},{span:8,ids:['table','note']}]}]}),/화면 폭/);
});

test('inspection example uses one content set in two compositions',()=>{
 assert.equal(inspectionLayouts.side.blocks,inspectionBlocks);
 assert.equal(inspectionLayouts.sequence.blocks,inspectionBlocks);
 const placed=layout=>layout.rows.flatMap(row=>row.columns.flatMap(column=>column.ids));
 assert.deepEqual(new Set(placed(inspectionLayouts.side)),new Set(placed(inspectionLayouts.sequence)));
 assert.deepEqual(inspectionLayouts.side.rows[1].columns.map(column=>column.span),[8,4]);
});

test('linear work pieces pack into rows without changing their order',()=>{
 const layout=packComposition(blocks,block=>block.id==='title'?8:4);
 assert.deepEqual(layout.rows.map(row=>row.columns.map(column=>column.ids[0])),[['title','table'],['note']]);
 assert.deepEqual(layout.rows.map(row=>row.columns.map(column=>column.span)),[[8,4],[4]]);
});

test('the catalog places every shared content kind in a valid layout',()=>{
 const kinds=new Set(catalogSections.flatMap(section=>section.composition.blocks.map(block=>block.kind)));
 assert.deepEqual([...kinds].sort(),['heading','text','image','comparison','diagram','media','table','metrics','chart','gallery','steps','references','file','code','audio','resource'].sort());
 for(const section of catalogSections){
  const layout=section.composition;
  assert.equal(new Set(layout.rows.flatMap(row=>row.columns.flatMap(column=>column.ids))).size,layout.blocks.length);
 }
});

test('a selected PDF keeps the file description and opens page previews',()=>{
 const href='/api/flow/files/content?workspaceId=workspace&path=reports%2Fresult.pdf';
 const content=pdfContentFromFile({name:'older.pdf',type:'TXT',size:'12KB',description:'검사 결과'},'reports/result.pdf',href);
 assert.deepEqual(content,{name:'result.pdf',type:'PDF',size:'',description:'검사 결과',href});
 assert.equal(pdfPreviewHref(content.href,2),'/api/flow/files/preview?workspaceId=workspace&path=reports%2Fresult.pdf&page=2');
 assert.throws(()=>pdfContentFromFile({},'reports/result.txt',href));
 assert.throws(()=>pdfContentFromFile({},'reports/result.pdf',''));
});

test('workspace text files become file content without copying or losing the description',()=>{
 const href='/api/flow/files/content?workspaceId=workspace&path=notes%2Fmemo.md';
 const content=fileContentFromFile({name:'previous.txt',type:'텍스트',size:'12KB',description:'원본 메모'},'notes/memo.md',href);
 assert.deepEqual(content,{name:'memo.md',type:'Markdown',size:'',description:'원본 메모',href});
 assert.equal(workspaceFileType('data/report.CSV'),'CSV');
 assert.equal(workspaceFileType('data/measurements.tsv'),'TSV');
 assert.equal(workspaceFileType('docs/page.html'),'HTML');
 assert.equal(workspaceFileType('docs/page.htm'),'HTML');
 assert.equal(workspaceFileType('docs/report.pdf'),'PDF');
 assert.deepEqual(workspaceMediaType('recordings/meeting.m4a'),{kind:'audio',mime:'audio/mp4'});
 assert.deepEqual(workspaceMediaType('recordings/voice.AAC'),{kind:'audio',mime:'audio/aac'});
 assert.deepEqual(workspaceMediaType('clips/demo.webm'),{kind:'video',mime:'video/webm'});
 assert.equal(workspaceMediaType('../private.m4a'),null);
 assert.equal(workspaceMediaType('notes.txt'),null);
 for(const path of ['notes/run.exe','notes/.hidden.md','notes/../private.md','/absolute/notes.md','notes/constructor']){
  assert.equal(workspaceFileType(path),null);
  assert.throws(()=>fileContentFromFile({},path,href));
 }
 assert.equal(inlineTextHref(href),href);
 assert.equal(inlineTextHref('/api/flow/files/content?workspaceId=workspace&path=docs%2Fpage.html'),'/api/flow/files/content?workspaceId=workspace&path=docs%2Fpage.html');
 assert.equal(inlineTextHref('/api/flow-media/workspace/files/content?path=notes%2Fmemo.md'),'/api/flow-media/workspace/files/content?path=notes%2Fmemo.md');
 assert.equal(inlineTextHref('/api/flow/files/content?workspaceId=workspace&path=docs%2Freport.pdf'),null);
 assert.equal(inlineTextHref('https://example.org/memo.md'),null);
});

test('CSV and TSV retain quoted text and source rows without guessing headers',()=>{
 assert.equal(textContentFormat('data/results.CSV'),'csv');
 assert.equal(textContentFormat('data/results.tsv'),'tsv');
 assert.equal(textContentFormat('data/results.txt'),'plain');
 const csv=parseDelimitedText('\ufeffname,value\r\n"a,b","01"\r\n"line\nbreak","say ""yes"""\r\n','csv');
 assert.deepEqual(csv,{rows:[['name','value'],['a,b','01'],['line\nbreak','say "yes"']],columns:2,truncated:false});
 assert.deepEqual(parseDelimitedText('항목\t값\n첫째\t2','tsv'),{rows:[['항목','값'],['첫째','2']],columns:2,truncated:false});
 assert.deepEqual(parseDelimitedText('A,B\n1\n','csv'),{rows:[['A','B'],['1']],columns:2,truncated:false});
 assert.deepEqual(parseDelimitedText('A\nB\nC\n','csv',2),{rows:[['A'],['B']],columns:1,truncated:true});
 assert.equal(parseDelimitedText('"열지 않은 값','csv'),null);
 assert.equal(parseDelimitedText('"닫힌 값"뒤','csv'),null);
 assert.equal(parseDelimitedText('x,'.repeat(81),'csv'),null);
});

test('Markdown images resolve only safe raster files beside a workspace document',()=>{
 const href='/api/flow/files/content?workspaceId=workspace&path=personal-agent-toolkit%2FREADME.md';
 assert.equal(resolveRelativeImagePath('personal-agent-toolkit/README.md','./assets/banner.png'),'personal-agent-toolkit/assets/banner.png');
 assert.equal(resolveRelativeImagePath('docs/chapter/notes.md','../images/photo%201.webp'),'docs/images/photo 1.webp');
 assert.equal(markdownImageHref(href,'./assets/banner.png'),'/api/flow/files/content?workspaceId=workspace&path=personal-agent-toolkit%2Fassets%2Fbanner.png');
 for(const source of ['https://outside.example/a.png','//outside.example/a.png','../../other.png','./active.svg','a%2Fb.png','./x.png?download=1','javascript:alert(1)'])
  assert.equal(markdownImageHref(href,source),null);
 assert.equal(markdownImageHref('https://outside.example/README.md','./assets/banner.png'),null);
});

test('content links reject executable and local-file URLs',()=>{
 assert.equal(safeContentHref('/examples/inspection-sample.csv'),'/examples/inspection-sample.csv');
 assert.equal(safeContentHref('https://example.org/report'),'https://example.org/report');
 assert.equal(safeContentHref('javascript:alert(1)'),null);
 assert.equal(safeContentHref('file:///etc/passwd'),null);
 assert.equal(safeContentHref('//example.org/report'),null);
 assert.equal(inlinePdfHref('/api/flow/files/content?workspaceId=workspace&path=docs%2Freport.pdf'),'/api/flow/files/content?workspaceId=workspace&path=docs%2Freport.pdf');
 assert.equal(inlinePdfHref('/api/flow-media/workspace/files/content?path=docs%2Freport.pdf'),'/api/flow-media/workspace/files/content?path=docs%2Freport.pdf');
 assert.equal(inlinePdfHref('https://example.org/report.pdf'),null);
 assert.equal(inlinePdfHref('/api/flow/files/content?workspaceId=workspace&path=docs%2Freport.txt'),null);
 assert.equal(pdfPreviewHref('/api/flow/files/content?workspaceId=workspace&path=docs%2Freport.pdf'),'/api/flow/files/preview?workspaceId=workspace&path=docs%2Freport.pdf&page=1');
 assert.equal(pdfPreviewHref('/api/flow-media/workspace/files/content?path=docs%2Freport.pdf',2),'/api/flow-media/workspace/files/preview?path=docs%2Freport.pdf&page=2');
 assert.equal(pdfPreviewHref('/api/flow/files/content?workspaceId=workspace&path=docs%2Freport.pdf',0),null);
});


test('catalog content is valid as a saved Flow work surface',()=>{
 for(const section of catalogSections)assert.equal(validContentComposition(section.composition),true);
 const source=structuredClone(catalogSections[0].composition);
 assert.deepEqual(contentAssetSources(source),[]);
 source.blocks[0].kind='unknown';
 assert.equal(validContentComposition(source),false);
});

test('saved content rejects unsafe media, links, and broken placement',()=>{
 const sample=structuredClone(catalogSections[0].composition);
 const image=sample.blocks.find(block=>block.kind==='image');
 image.content.src='https://example.org/tracker.png';
 assert.equal(validContentComposition(sample),false);
 image.content.src='/examples/metal.png';
 sample.rows[0].columns[0].ids=['missing'];
 assert.equal(validContentComposition(sample),false);
 const resources=structuredClone(catalogSections[2].composition);
 resources.blocks.find(block=>block.kind==='file').content.href='javascript:alert(1)';
 assert.equal(validContentComposition(resources),false);
});


test('saved media uses playable file types and rejects mismatched sources',()=>{
 const blocks=[
  {id:'video',kind:'media',content:{heading:'영상',src:'/api/flow/files/content?workspaceId=workspace&path=clip.mp4',poster:'/examples/camera.png'}},
  {id:'audio',kind:'audio',content:{heading:'소리',src:'/api/flow/files/content?workspaceId=workspace&path=tone.wav'}}
 ];
 const composition=stackComposition(blocks);
 assert.equal(validContentComposition(composition),true);
 blocks[0].content.src='/api/flow/files/content?workspaceId=workspace&path=still.png';
 assert.equal(validContentComposition(composition),false);
 blocks[0].content.src='/api/flow/files/content?workspaceId=workspace&path=clip.mp4';
 blocks[1].content.src='/api/flow/files/content?workspaceId=workspace&path=notes.md';
 assert.equal(validContentComposition(composition),false);
});

test('Toolkit resource blocks keep canonical identities and accept the existing layout rules',()=>{
 const reference={kind:'context',locator:{product:'corpus',spaceId:'research',documentId:'methods:notes'}};
 const resource={id:'linked',kind:'resource',content:{reference,title:'연구 메모',detail:'Corpus · 문서'}};
 const composition=stackComposition([resource,{id:'summary',kind:'text',content:{paragraphs:['요약']}}]);
 assert.equal(validContentComposition(composition),true);
 const existing=stackComposition([{id:'summary',kind:'text',content:{paragraphs:['기존 내용']}}]);
 const appended=appendContentBlock(existing,resource);
 assert.deepEqual(appended.blocks[0],existing.blocks[0]);
 assert.deepEqual(appended.rows[0],existing.rows[0]);
 assert.equal(validContentComposition(appended),true);
 const arranged=defineComposition({...composition,rows:[{id:'row',columns:[{span:8,ids:['linked']},{span:4,ids:['summary']}]}]});
 assert.equal(validContentComposition(arranged),true);
 assert.deepEqual(arranged.blocks[0].content.reference,reference);
 for(const invalid of [
  {kind:'host-file',root:'workspace',path:'../secrets.txt'},
  {kind:'context',locator:{product:'corpus',spaceId:'research',documentId:'../outside'}},
  {kind:'library-issue',id:'https://external.example/issue'},
  {kind:'journal-item',id:'not-an-id'},
 ]){
  assert.equal(validContentComposition(stackComposition([{...resource,content:{...resource.content,reference:invalid}}])),false);
 }
 assert.equal(validContentComposition(stackComposition([{...resource,content:{...resource.content,title:''}}])),false);
});

test('diagram layout preserves nodes while changing only their positions',()=>{
 const nodes=[{id:'a',label:'첫째',x:0,y:0},{id:'b',label:'둘째',x:0,y:0},{id:'c',label:'셋째',x:0,y:0}];
 const horizontal=layoutDiagramNodes(nodes,'horizontal');
 const vertical=layoutDiagramNodes(nodes,'vertical');
 assert.deepEqual(horizontal.map(node=>node.id),nodes.map(node=>node.id));
 assert.deepEqual(vertical.map(node=>node.label),nodes.map(node=>node.label));
 assert.deepEqual(horizontal.map(node=>node.y),[300,300,300]);
 assert.deepEqual(vertical.map(node=>node.y),[100,300,500]);
 assert.deepEqual(nodes.map(node=>node.x),[0,0,0]);
 assert.deepEqual(layoutDiagramNodes([],'vertical'),[]);
});

test('diagram labels, dragging limits and arrow keys share the same geometry',()=>{
 assert.deepEqual(diagramLabelLines('가나다라마바사아자차카타파하'),['가나다라마바사아자차카타파','하']);
 assert.equal(diagramNodeHeight('짧은 항목'),72);
 assert.equal(diagramNodeHeight('첫째 줄\n둘째 줄\n셋째 줄'),98);
 assert.deepEqual(clampDiagramPosition(-10,900),{x:140,y:530});
 assert.deepEqual(nudgeDiagramPosition({x:145,y:525},'ArrowLeft'),{x:140,y:525});
 assert.deepEqual(nudgeDiagramPosition({x:145,y:525},'ArrowDown'),{x:145,y:530});
 assert.equal(nudgeDiagramPosition({x:500,y:300},'Enter'),null);
});

test('content authoring keeps existing rows while placing and moving blocks',async()=>{
 const {newContentBlock,appendContentBlock,joinContentRowAbove,separateContentBlock,moveContentBlock,removeContentBlock,setContentColumnSpan,contentOrder}=await import('../src/work-surface/composition-editor.js');
 const first=newContentBlock('heading','first'),second=newContentBlock('text','second'),third=newContentBlock('chart','third');
 let layout=stackComposition([first,second,third]);
 layout=joinContentRowAbove(layout,'second');
 assert.deepEqual(layout.rows.map(row=>row.columns.map(column=>column.span)),[[6,6],[12]]);
 layout=setContentColumnSpan(layout,'first',4);
 layout=setContentColumnSpan(layout,'second',8);
 assert.deepEqual(layout.rows[0].columns.map(column=>column.span),[4,8]);
 layout=moveContentBlock(layout,'third',-1);
 assert.deepEqual(contentOrder(layout),['first','third','second']);
 layout=separateContentBlock(layout,'third',()=> 'separate');
 assert.deepEqual(layout.rows.map(row=>row.columns.map(column=>column.ids)),[[['first']],[['third']],[['second']]]);
 layout=removeContentBlock(layout,'third');
 assert.deepEqual(contentOrder(layout),['first','second']);
 layout=appendContentBlock(layout,newContentBlock('table','table'),'second',true);
 assert.deepEqual(layout.rows[1].columns.map(column=>column.span),[6,6]);
 assert.deepEqual(contentOrder(layout),['first','second','table']);
 assert.equal(validContentComposition(layout),true);
});

test('three columns and stacked content keep one composition without losing blocks',async()=>{
 const {canJoinContentRowAbove,joinContentRowAbove,stackContentRowAbove,setContentColumnSpan,separateContentBlock,contentOrder}=await import('../src/work-surface/composition-editor.js');
 const source=stackComposition([
  newContentBlock('text','first'),newContentBlock('text','second'),
  newContentBlock('text','third'),newContentBlock('text','fourth')
 ]);
 assert.equal(canJoinContentRowAbove(source,'second'),true);
 let layout=joinContentRowAbove(source,'second');
 layout=setContentColumnSpan(layout,'first',4);
 layout=setContentColumnSpan(layout,'second',4);
 assert.equal(canJoinContentRowAbove(layout,'third'),true);
 layout=joinContentRowAbove(layout,'third');
 assert.deepEqual(layout.rows[0].columns.map(column=>column.span),[4,4,4]);
 assert.deepEqual(contentOrder(layout),['first','second','third','fourth']);
 assert.equal(canJoinContentRowAbove(layout,'fourth'),false);
 assert.throws(()=>joinContentRowAbove(layout,'fourth'),/새 열/);
 assert.throws(()=>stackContentRowAbove(layout,'fourth',3),/열을 확인/);
 layout=stackContentRowAbove(layout,'fourth',1);
 assert.deepEqual(layout.rows[0].columns.map(column=>column.ids),[['first'],['second','fourth'],['third']]);
 assert.equal(layout.rows.length,1);
 assert.equal(validContentComposition(layout),true);
 assert.deepEqual(source.rows.map(row=>row.columns[0].ids),[['first'],['second'],['third'],['fourth']]);
 layout=separateContentBlock(layout,'fourth',()=> 'separated');
 assert.equal(layout.rows.length,2);
 assert.deepEqual(layout.rows[1].columns[0].ids,['fourth']);
 assert.equal(validContentComposition(layout),true);
});

test('media block accepts a still image without an empty video source',async()=>{
 const {newContentBlock}=await import('../src/work-surface/composition-editor.js');
 const block=newContentBlock('media','still');
 block.content.poster='/examples/metal.png';
 assert.equal(validContentComposition(stackComposition([block])),true);
});

test('preview keeps completed blocks visible while another block is unfinished',async()=>{
 const {contentImageSlot,previewableContentDraft,validContentDraft}=await import('../src/work-surface/content-assets.js');
 const text={id:'note',kind:'text',content:{paragraphs:['표면 상태를 비교한다.']}};
 const image={id:'photo',kind:'image',content:{src:'',alt:'부품 사진'}};
 const layout=defineComposition({blocks:[text,image],rows:[{id:'side-by-side',columns:[{span:6,ids:['note']},{span:6,ids:['photo']}]}]});
 assert.equal(validContentDraft(layout,{}),false);
 const preview=previewableContentDraft(layout,{});
 assert.deepEqual(preview?.blocks.map(block=>block.id),['note']);
 assert.deepEqual(preview?.rows[0].columns.map(column=>column.ids),[['note']]);
 assert.equal(validContentDraft(preview,{}),true);
 assert.equal(layout.blocks[1].content.src,'');
 assert.equal(previewableContentDraft(stackComposition([image]),{}),null);
 const pending={...image,content:{...image.content,src:'blob:pending'}};
 const withPending=defineComposition({...layout,blocks:[text,pending]});
 const source={[contentImageSlot('photo')]:{path:'part.png'}};
 assert.deepEqual(previewableContentDraft(withPending,source)?.blocks.map(block=>block.id),['note','photo']);
});

test('pending content images validate and import once without moving blocks',async()=>{
 const {contentImageSlot,activeContentSources,validContentDraft,materializeContentImages}=await import('../src/work-surface/content-assets.js');
 const image={id:'image',kind:'image',content:{src:'blob:pending',alt:'사진'}};
 const gallery={id:'gallery',kind:'gallery',content:{images:[{id:'one',src:'blob:pending',alt:'세부'}]}};
 const layout=stackComposition([image,gallery]);
 const source={path:'part.png'};
 const pending={[contentImageSlot('image')]:source,[contentImageSlot('gallery','one')]:source,stale:{path:'deleted.png'}};
 assert.equal(validContentDraft(layout,pending),true);
 assert.equal(Object.keys(activeContentSources(layout,pending)).length,2);
 let count=0;
 const result=await materializeContentImages(layout,pending,async()=>{count++;return '/api/flow/assets/'+'a'.repeat(64)+'.png'});
 assert.equal(count,1);
 assert.deepEqual(result.rows,layout.rows);
 assert.equal(validContentComposition(result),true);
 assert.equal(layout.blocks[0].content.src,'blob:pending');
});


test('one file entry keeps its identity while the shared presentation selects a viewer',()=>{
 const base=stackComposition([{id:'note',kind:'text',content:{paragraphs:['기존 내용']}}]);
 for(const [path,viewer,mime] of [
  ['photo.png','image','image/png'],['clip.mp4','video','video/mp4'],
  ['recording.m4a','audio','audio/mp4'],['report.pdf','pdf','application/pdf'],
  ['table.csv','text','application/json'],['page.html','text','application/json'],
 ]){
  const href='/api/flow/files/content?workspaceId=workspace&path='+path;
  const presentation=workspaceFilePresentation(path),block=workspaceFileBlock(path,href,'file');
  assert.equal(presentation.viewer,viewer);assert.equal(presentation.responseType,mime);
  assert.equal(block.kind,'file');assert.equal(block.content.href,href);
  const next=appendContentBlock(base,block);
  assert.deepEqual(next.blocks[0],base.blocks[0]);assert.deepEqual(next.rows[0],base.rows[0]);
  assert.equal(validContentComposition(next),true);
 }
 assert.equal(workspaceFilePresentation('unknown.bin'),null);
 assert.equal(workspaceFilePresentation('../secret.png'),null);
 assert.equal(workspaceFilePresentation('note.md').maxBytes,512*1024);
 assert.equal(workspaceFilePresentation('clip.mp4').maxBytes,20*1024*1024);
});

test('inline file viewers accept registered file routes rather than arbitrary web addresses',()=>{
 for(const href of [
  '/api/flow/files/content?workspaceId=workspace&path=photo.png',
  '/api/flow-media/workspace/files/content?path=photo.png',
  '/examples/metal.png','/examples/sample-tone.wav',
  '/api/flow-media/workspace/examples/sample-tone.wav',
 ])assert.equal(inlineFileSource(href)?.href,href);
 for(const href of ['https://outside.example/photo.png','//outside.example/photo.png','javascript:alert(1)','/examples/sample.html','/unregistered/photo.png',String.raw`/\outside.example/api/flow/files/content?path=photo.png`])
  assert.equal(inlineFileSource(href),null);
});

test('a leading full-width heading becomes the single surface title without changing saved content',()=>{
 const source=structuredClone(inspectionLayouts.side),before=structuredClone(source);
 const {heading,body}=splitContentHeading(source);
 assert.equal(heading.kind,'heading');
 assert.equal(heading,source.blocks.find(block=>block.id===heading.id));
 assert.equal(body.blocks.length,source.blocks.length-1);
 assert.deepEqual(body.rows,source.rows.slice(1));
 assert.deepEqual(source,before);
 assert.deepEqual(defineComposition(body),body);
 assert.deepEqual([heading.id,...body.rows.flatMap(row=>row.columns.flatMap(column=>column.ids))],
  source.rows.flatMap(row=>row.columns.flatMap(column=>column.ids)));
 const onlyHeading=splitContentHeading(stackComposition([blocks[0]]));
 assert.equal(onlyHeading.heading,blocks[0]);
 assert.deepEqual(onlyHeading.body,{blocks:[],rows:[]});
});

test('title presentation preserves stacked content and does not guess from similar words',()=>{
 const sameColumn=defineComposition({blocks,rows:[{id:'all',columns:[{span:12,ids:['title','table','note']}]}]});
 const {body}=splitContentHeading(sameColumn);
 assert.deepEqual(body.rows[0].columns,[{span:12,ids:['table','note']}]);
 assert.deepEqual(sameColumn.rows[0].columns[0].ids,['title','table','note']);
 assert.equal(validContentComposition(body),true);
 const sectionHeading={id:'section',kind:'heading',content:{title:'제목 세부'}};
 const cases=[
  stackComposition([blocks[2],blocks[0],sectionHeading]),
  defineComposition({blocks,rows:[{id:'narrow',columns:[{span:6,ids:['title','table','note']}]}]}),
  defineComposition({blocks,rows:[{id:'split',columns:[{span:8,ids:['title']},{span:4,ids:['table','note']}]}]}),
  stackComposition([{...blocks[0],content:{title:' '}},blocks[2]]),
  stackComposition([{...blocks[0],kind:'text',content:{heading:'제목',paragraphs:['본문']}}]),
 ];
 for(const source of cases){
  const result=splitContentHeading(source);
  assert.equal(result.heading,null);
  assert.equal(result.body,source);
 }
 const repeated=splitContentHeading(stackComposition([blocks[0],sectionHeading]));
 assert.equal(repeated.body.blocks[0],sectionHeading);
});

test('HTML artifacts accept only bounded documents and immutable asset manifests',async()=>{
 const {validHtmlArtifact,HTML_LIMIT,htmlArtifactContainer,artifactPolicy}=await import('../src/work-surface/html-artifact.js');
 const artifact={kind:'html',html:'<h1>보고서</h1>',assets:[{name:'images/part.png',src:'/api/flow/assets/'+'a'.repeat(64)+'.png'}]};
 assert.equal(validHtmlArtifact(artifact),true);
 for(const invalid of [
  {...artifact,html:''},{...artifact,html:'가'.repeat(Math.ceil(HTML_LIMIT/3)+1)},
  {...artifact,assets:[{...artifact.assets[0],name:'../photo.png'}]},
  {...artifact,assets:[{...artifact.assets[0],src:'https://external.invalid/photo.png'}]},
  {...artifact,assets:[artifact.assets[0],artifact.assets[0]]},
 ])assert.equal(validHtmlArtifact(invalid),false);
 const shell=htmlArtifactContainer('<h1>본문</h1><script>parent.document.body.remove()</script>','test-channel');
 assert.ok(shell.includes("frame-src 'none'"));
 assert.ok(shell.includes("frame.srcdoc="));
 assert.ok(shell.includes('sandbox="allow-scripts"'));
 assert.ok(!shell.includes('allow-same-origin'));
 assert.ok(!shell.includes('<h1>본문</h1>'));
 assert.ok(shell.includes('\\u003c'));
 assert.ok(artifactPolicy.includes("connect-src 'none'"));
 assert.ok(artifactPolicy.includes("frame-src 'none'"));
});

test('local original links preserve the registered root and use the authenticated Flow site', async()=>{
 const {resourceLink}=await import('../src/resource-link.js');
 const ref={kind:'host-file',root:'readonly',path:'reports/원본.pdf'};
 const url=new URL(resourceLink(ref,'https://toolkit.example','workspace'));
 assert.equal(url.pathname,'/flow');
 assert.deepEqual(JSON.parse(url.searchParams.get('resource')),ref);
 assert.equal(url.searchParams.get('workspace'),'workspace');
 assert.equal(resourceLink(ref,'javascript:alert(1)','workspace'),null);
 assert.equal(resourceLink(ref,'https://user:secret@toolkit.example','workspace'),null);
 assert.equal(resourceLink(ref,'','workspace'),null);
});

test('prototype URLs no longer expose a separate manual component editor',async()=>{
 const source=await readFile(new URL('../src/main.jsx',import.meta.url),'utf8');
 assert.ok(source.includes('<App />'));
 assert.ok(!source.includes('ComponentCatalog'));
 assert.ok(!source.includes('InspectionExample'));
});

test('work references show only attached material and fold duplicate direct links into it',async()=>{
 const {workReferences,disconnectWorkReference}=await import('../src/work-surface/work-references.js');
 const reference={kind:'host-file',root:'research-note/main',path:'자료/실험.csv'};
 const source={id:'selected',title:'실험 조건',body:'정리한 내용',reference,scope:{kind:'work',workId:'first'}};
 const work={sourceIds:['selected'],linkedResources:[reference],artifacts:[{id:'unchanged',html:'<h1>결과</h1>'}]};
 const before=structuredClone(work);
 const items=workReferences(work,[source,{id:'unrelated',title:'다른 작업 자료'}],'workspace');
 assert.equal(items.length,1);assert.equal(items[0].title,source.title);assert.equal(items[0].source,source);
 assert.deepEqual(items[0].linkedReferences,[reference]);
 assert.deepEqual(disconnectWorkReference(work,items[0]),{sourceIds:[],linkedResources:[]});
 assert.deepEqual(work,before);assert.equal(source.body,'정리한 내용');
});
test('missing references remain reachable and disconnect does not remove other work data',async()=>{
 const {workReferences,disconnectWorkReference}=await import('../src/work-surface/work-references.js');
 const reference={kind:'context',locator:{product:'sense',sectionId:'conversation-and-writing'}};
 const work={sourceIds:['missing'],linkedResources:[reference]};
 const items=workReferences(work,[]);
 assert.equal(items.length,2);assert.equal(items[0].missing,true);assert.equal(items[0].title,'연결한 자료');
 assert.deepEqual(disconnectWorkReference(work,items[0]),{sourceIds:[],linkedResources:[reference]});
 assert.deepEqual(disconnectWorkReference(work,items[1]),{sourceIds:['missing'],linkedResources:[]});
});
test('live file references use their registered root and never substitute another root',async()=>{
 const {sourceReference,workReferences}=await import('../src/work-surface/work-references.js');
 const file={id:'file',title:'원본',filePath:'자료/원본.csv',live:true};
 assert.equal(sourceReference(file),null);
 assert.deepEqual(sourceReference(file,'workspace'),{kind:'host-file',root:'workspace',path:file.filePath});
 const reference={kind:'host-file',root:'research-note/main',path:file.filePath};
 assert.deepEqual(sourceReference({...file,reference},'workspace'),reference);
 assert.equal(sourceReference({...file,filePath:'../secret'},'workspace'),null);
 assert.equal(sourceReference({...file,live:false},'workspace'),null);
 const work={sourceIds:[file.id],linkedResources:[reference]};
 assert.equal(workReferences(work,[file],'workspace').length,2);
});
test('distinct curated content sharing an original is not discarded by reference deduplication',async()=>{
 const {workReferences}=await import('../src/work-surface/work-references.js');
 const reference={kind:'context',locator:{product:'corpus',spaceId:'project',documentId:'methods'}};
 const sources=[{id:'a',title:'첫 정리',reference,body:'첫 내용'},{id:'b',title:'다른 정리',reference,body:'다른 내용'}];
 const items=workReferences({sourceIds:['a','a','b'],linkedResources:[reference]},sources);
 assert.equal(items.length,2);assert.deepEqual(items.map(item=>item.source.body),['첫 내용','다른 내용']);
 assert.equal(items.filter(item=>item.linkedReferences.length).length,1);
});
