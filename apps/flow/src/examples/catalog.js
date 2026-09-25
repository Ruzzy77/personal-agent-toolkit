import {defineComposition} from '../work-surface/composition.js';

const contentBlocks=[
 {id:'page-heading',kind:'heading',content:{title:'한 화면에 모아 보는 자료',description:'본문과 이미지를 같은 작업 화면에 배치한다.'}},
 {id:'description',kind:'text',content:{heading:'관찰',paragraphs:['금속 부품의 흠집 의심 부위와 주변 표면을 비교한다. 촬영 장비 이미지는 별도 자료로 함께 둔다.']}},
 {id:'main-image',kind:'image',content:{src:'/examples/metal.png',alt:'금속 부품 상단의 촬영 이미지',caption:'금속 부품 상단',width:1672,height:941}},
 {id:'detail-image',kind:'image',content:{src:'/examples/metal.png',alt:'금속 부품의 흠집 의심 부위',caption:'의심 부위 확대',width:1672,height:941,crop:{x:.74,y:.30,width:.22,height:.17}}},
 {id:'gallery',kind:'gallery',content:{heading:'이미지 모음',images:[
  {id:'metal',src:'/examples/metal.png',alt:'금속 부품 상단',caption:'부품 이미지'},
  {id:'camera',src:'/examples/camera.png',alt:'회로기판 위의 광학 검사 장비',caption:'촬영 장비'}
 ]}},
 {id:'comparison',kind:'comparison',content:{heading:'표면 비교',items:[
  {id:'plain',label:'주변 표면',src:'/examples/metal.png',alt:'금속 부품 왼쪽 표면 확대',caption:'표면 질감',view:{scale:6.1,x:-.6,y:-1}},
  {id:'scratch',label:'흠집 의심 부위',src:'/examples/metal.png',alt:'금속 부품 오른쪽 흠집 의심 부위 확대',caption:'흠집 의심 부위',view:{scale:6.1,x:-4.4,y:-1}}
 ]}},
 {id:'media',kind:'media',content:{heading:'촬영 장비 이미지',poster:'/examples/camera.png',alt:'회로기판 위의 광학 검사 장비'}}
];

const dataBlocks=[
 {id:'metrics',kind:'metrics',content:{heading:'자료 현황 예시',items:[
  {id:'images',label:'이미지',value:12,unit:'장'},
  {id:'documents',label:'문서',value:5,unit:'개'},
  {id:'tables',label:'표',value:3,unit:'개'}
 ]}},
 {id:'chart',kind:'chart',content:{heading:'자료 유형별 수량 예시',items:[
  {id:'images',label:'이미지',value:12,unit:'장'},
  {id:'documents',label:'문서',value:5,unit:'개'},
  {id:'tables',label:'표',value:3,unit:'개'}
 ]}},
 {id:'table',kind:'table',content:{heading:'촬영 기록 예시',columns:['부품','조명','확인 사항'],rows:[
  ['A','정면','흠집 의심 부위'],['A','측면','표면 반사'],['B','정면','이물 의심 부위']
 ]}},
 {id:'diagram',kind:'diagram',content:{heading:'이미지 확인 흐름',nodes:[
  {id:'original',label:'원본 확인',detail:'전체 촬영 범위를 본다.',x:175,y:300},
  {id:'crop',label:'의심 부위 확대',detail:'주변 표면과 함께 본다.',x:500,y:300},
  {id:'compare',label:'조건별 비교',detail:'조명과 촬영 각도를 대조한다.',x:825,y:300}
 ],edges:[
  {id:'edge-original-crop',from:'original',to:'crop',label:''},
  {id:'edge-crop-compare',from:'crop',to:'compare',label:''}
 ]}},
 {id:'steps',kind:'steps',content:{heading:'이미지 확인 순서',steps:[
  {id:'original',title:'원본 확인',text:'전체 이미지에서 부품과 촬영 범위를 확인한다.'},
  {id:'crop',title:'의심 부위 확대',text:'주변 표면과 밝기, 경계를 비교한다.'},
  {id:'lighting',title:'촬영 조건 대조',text:'조명과 각도가 다른 이미지를 함께 본다.'}
 ]}}
];

const resourceBlocks=[
 {id:'linked-record',kind:'resource',content:{reference:{kind:'journal-item',id:'123e4567-e89b-42d3-a456-426614174000'},title:'검사 기록 예시',detail:'Journal 예시'}},
 {id:'references',kind:'references',content:{heading:'관련 자료',items:[
  {id:'part',title:'금속 부품 이미지',detail:'표면과 흠집 의심 부위',href:'/examples/metal.png'},
  {id:'equipment',title:'촬영 장비 이미지',detail:'광학 검사 장비',href:'/examples/camera.png'}
 ]}},
 {id:'file',kind:'file',content:{name:'촬영 기록.csv',type:'CSV',description:'조명별 관찰 내용을 담은 예시 파일',href:'/examples/inspection-sample.csv'}},
 {id:'code',kind:'code',content:{heading:'간단한 집계',language:'JavaScript',code:'const counts = records.reduce((result, row) => {\n  result[row.type] = (result[row.type] ?? 0) + 1;\n  return result;\n}, {});'}},
 {id:'audio',kind:'audio',content:{heading:'신호음 예시',src:'/examples/sample-tone.wav',caption:'짧은 오디오 파일'}}
];

export const catalogSections=[
 {id:'visual',title:'문장과 이미지',composition:defineComposition({blocks:contentBlocks,rows:[
  {id:'heading',columns:[{span:12,ids:['page-heading']}]},
  {id:'image',columns:[{span:8,ids:['main-image']},{span:4,ids:['description']}]},
  {id:'gallery',columns:[{span:6,ids:['gallery']},{span:6,ids:['comparison']}]},
  {id:'media',columns:[{span:6,ids:['detail-image']},{span:6,ids:['media']}]}
 ]})},
 {id:'data',title:'수치와 순서',composition:defineComposition({blocks:dataBlocks,rows:[
  {id:'metrics',columns:[{span:12,ids:['metrics']}]},
  {id:'visual-data',columns:[{span:6,ids:['chart']},{span:6,ids:['table']}]},
  {id:'steps',columns:[{span:6,ids:['steps']}]},
  {id:'diagram',columns:[{span:12,ids:['diagram']}]}
 ]})},
 {id:'resources',title:'자료와 파일',composition:defineComposition({blocks:resourceBlocks,rows:[
  {id:'references',columns:[{span:6,ids:['references']},{span:6,ids:['file']}]},
  {id:'code-audio',columns:[{span:6,ids:['code']},{span:6,ids:['audio']}]},
  {id:'linked-record',columns:[{span:12,ids:['linked-record']}]}
 ]})}
];

export const catalogArtifacts=[
 {id:'artifact-document',kind:'document',title:'촬영 범위',blocks:[
  {id:'scope',heading:'금속 부품 상단',text:'부품 상단의 표면을 전체 촬영한 뒤, 흠집이 의심되는 부위를 확대해 주변 표면과 비교한다.'},
  {id:'conditions',heading:'촬영 조건',text:'정면과 측면에서 촬영한 이미지를 함께 보며 조명에 따른 반사 차이를 확인한다.'}
 ]},
 {id:'artifact-slides',kind:'document',format:'slides',title:'이미지 검토 요약',blocks:[
  {id:'scope',heading:'촬영 범위',text:'금속 부품 상단을 정면과 측면에서 촬영했습니다.'},
  {id:'detail',heading:'흠집 의심 부위',text:'오른쪽 표면의 선형 흔적을 주변 표면과 같은 배율로 비교합니다.'},
  {id:'next',heading:'다음 촬영',text:'반사를 줄인 조명에서 같은 위치를 다시 촬영합니다.'}
 ]},
 {id:'artifact-image',kind:'image',title:'부품 촬영 이미지',src:'/examples/metal.png',alt:'금속 부품 상단의 촬영 이미지'},
 {id:'artifact-diagram',kind:'diagram',title:'이미지 확인 흐름',nodes:[
  {id:'original',label:'원본 확인',x:175,y:300},
  {id:'crop',label:'의심 부위 확대',x:500,y:300},
  {id:'compare',label:'주변 표면 비교',x:825,y:300}
 ],edges:[
  {id:'edge-1',from:'original',to:'crop'},
  {id:'edge-2',from:'crop',to:'compare'}
 ]}
];

export const catalogReview={
 current:{id:'review-current',kind:'document',title:'촬영 기록',blocks:[
  {id:'surface',heading:'표면',text:'금속 부품 상단을 정면에서 촬영했다.'},
  {id:'detail',heading:'확대 이미지',text:'흠집 의심 부위를 확대했다.'}
 ]},
 proposed:{id:'review-proposed',kind:'document',title:'촬영 기록',blocks:[
  {id:'surface',heading:'표면',text:'금속 부품 상단을 정면과 측면에서 촬영했다.'},
  {id:'detail',heading:'확대 이미지',text:'흠집 의심 부위와 주변 표면을 같은 배율로 확대했다.'}
 ]}
};
