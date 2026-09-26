import React,{useState,useSyncExternalStore} from 'react';
import {UIKitRoot,FieldSelect} from '@personal-agent/ui-kit/react';
import '@personal-agent/ui-kit/components.css';
import '@personal-agent/ui-kit/document.css';
import './guide.css';
const options=[{value:'writing',label:'한국어 작성 기준'},{value:'design',label:'제품 설계'},{value:'record',label:'진행한 작업'}];
const descriptions={writing:'Sense의 한국어 생성 기준과 연결된 korean-generation 스킬을 확인합니다.',design:'Corpus의 Personal Agent Toolkit 설계에서 제품별 역할과 원본 관리 방식을 확인합니다.',record:'Journal에서 Toolkit 작업의 내용과 현재 상태를 확인합니다.'};
const subscribe=notify=>{const media=matchMedia('(pointer:coarse)');media.addEventListener('change',notify);return()=>media.removeEventListener('change',notify)};
export default function App(){
 const [value,setValue]=useState('writing');
 const coarse=useSyncExternalStore(subscribe,()=>matchMedia('(pointer:coarse)').matches,()=>false);
 return <UIKitRoot colorScheme="inherit"><div className="ui-document">
<main id="toolkit-guide" className="document su-stack" data-gap="section">
<header className="su-stack"><h1 className="guide-title">Toolkit 자료 활용</h1>
<p className="prose">필요한 원본을 찾아 읽고, 현재 작업에 연결합니다. 다시 사용할 내용은 적용 범위와 출처를 남겨 라이브러리에 정리합니다.</p></header>
<section className="su-section guide-section"><h2 className="subheading">공간별 역할</h2><div className="su-table-wrap"><table className="su-table">
<thead><tr><th scope="col">공간</th><th scope="col">다루는 내용</th></tr></thead><tbody>
<tr><th scope="row">작업공간</th><td>요청한 결과물을 만들고 같은 화면에서 다듬습니다.</td></tr>
<tr><th scope="row">라이브러리</th><td>참고 자료를 찾고, 다시 사용할 내용을 정리합니다.</td></tr>
<tr><th scope="row">파일</th><td>등록된 폴더의 원본과 완성본을 확인합니다.</td></tr>
</tbody></table></div><p className="note">자료는 원래 위치를 유지합니다. 필요한 작업에서 같은 원본을 참조할 수 있습니다.</p></section>
<section className="su-section guide-section"><h2 className="subheading">참고 자료</h2><div id="reference-select"><FieldSelect aria-label="참고 자료 선택" description={<span id="suggestion" aria-live="polite">{descriptions[value]}</span>} options={options} value={value} onChange={item=>setValue(item.value)} size={coarse?'2xl':'md'} block align="start" placeholder="자료 선택" loadingPlaceholder="자료 불러오는 중"/></div></section>
<section className="su-section guide-section"><h2 className="subheading">원본과 정리한 내용</h2><p className="prose">Sense는 사용자의 기준을, Corpus는 프로젝트 맥락과 설계 정본을 관리합니다. Flow에서는 이 내용을 참고해 작업물을 작성합니다. 라이브러리에 정리한 내용은 원본의 변경이나 새로운 기준의 채택을 뜻하지 않습니다.</p>
<details><summary>원본이 바뀌었을 때</summary><div>정리할 때 참고한 버전과 현재 원본을 비교합니다. 달라진 내용을 확인한 뒤 필요한 부분만 다시 정리합니다.</div></details></section>
</main>
 </div></UIKitRoot>;
}