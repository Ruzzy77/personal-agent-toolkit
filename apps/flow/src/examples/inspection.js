import {defineComposition} from '../work-surface/composition.js';

export const inspectionBlocks=[
 {id:'introduction',kind:'heading',content:{
  title:'검사 데이터 검토',
  description:'새 제품이나 공정에서는 기존 검사 모델이 흠집을 놓치거나 정상 부위를 불량으로 분류할 수 있다.\n제품과 촬영 조건을 확인하고 불량 유형별로 이미지를 비교한다.'
 }},
 {id:'inspection-image',kind:'image',content:{
  src:'/examples/metal.png',alt:'금속 부품 상단의 촬영 이미지. 오른쪽 표면에 작은 흠집이 보인다.',caption:'금속 부품 상단',
  selection:{x:.845,y:.32,width:.065,height:.15}
 }},
 {id:'region-comparison',kind:'comparison',content:{
  heading:'표면 비교',items:[
   {id:'baseline',label:'주변 표면',src:'/examples/metal.png',alt:'금속 부품 왼쪽 표면을 확대한 이미지',caption:'표면 질감',view:{scale:6.1,x:-.6,y:-1}},
   {id:'current',label:'흠집 의심 부위',src:'/examples/metal.png',alt:'금속 부품 오른쪽 흠집 의심 부위를 확대한 이미지',caption:'흠집 의심 부위',view:{scale:6.1,x:-4.4,y:-1}}
  ]
 }},
 {id:'camera-scene',kind:'media',content:{
  heading:'광학 검사 장비',poster:'/examples/camera.png',alt:'광학 검사 장비가 회로기판을 촬영하는 모습'
 }},
 {id:'factors',kind:'factors',content:{
  heading:'이미지 비교 항목',
  question:'무엇이 바뀌었는가?',
  items:[
   {id:'product',title:'제품',detail:'형상, 크기, 재질, 표면 상태',note:'재질이나 표면 상태가 바뀌면 흠집의 밝기와 경계도 달라질 수 있다.'},
   {id:'capture',title:'촬영 조건',detail:'조명, 촬영 각도, 해상도, 노출',note:'조명, 각도, 해상도가 바뀌면 흠집이 흐리거나 더 뚜렷하게 보일 수 있다.'},
   {id:'error',title:'불량 유형',detail:'흠집, 이물, 패턴 차이',note:'흠집, 이물, 패턴 차이를 나누어 모델이 놓친 사례를 확인한다.'}
  ]
 }},
 {id:'interpretation',kind:'text',content:{
  heading:'추가 확인',
  paragraphs:[
   '흠집 의심 부위의 밝기와 경계를 주변 표면과 비교한다.',
   '같은 부위를 조명과 촬영 각도별로 다시 촬영해 비교한다.'
  ]
 }}
];

export const inspectionLayouts={
 side:defineComposition({blocks:inspectionBlocks,rows:[
  {id:'intro',columns:[{span:12,ids:['introduction']}]},
  {id:'evidence',columns:[{span:8,ids:['inspection-image']},{span:4,ids:['region-comparison','camera-scene']}]},
  {id:'meaning',columns:[{span:8,ids:['factors']},{span:4,ids:['interpretation']}]}
 ]}),
 sequence:defineComposition({blocks:inspectionBlocks,rows:[
  {id:'intro',columns:[{span:12,ids:['introduction']}]},
  {id:'image',columns:[{span:12,ids:['inspection-image']}]},
  {id:'evidence',columns:[{span:6,ids:['region-comparison']},{span:6,ids:['camera-scene']}]},
  {id:'meaning',columns:[{span:12,ids:['factors']}]},
  {id:'interpretation',columns:[{span:12,ids:['interpretation']}]}
 ]})
};
