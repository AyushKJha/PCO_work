import { useState } from 'react';
import { ArrowUpRight } from 'lucide-react';
import { LivingLogo } from './LivingLogo';

const studies=[
 {name:'The trace',title:'Follow every thought.',copy:'A workflow is more than its final answer. Follow the model calls, token use, and time spent along the way.',label:'EXECUTION / OBSERVE'},
 {name:'The search',title:'One agent. Many futures.',copy:'Explore alternative model assignments. Your quality tolerance defines the boundaries of the search.',label:'CONFIGURATION / EXPLORE'},
 {name:'The evidence',title:'Keep what proves itself.',copy:'Compare candidates against your evaluation suite. Select a recommendation, then export it on your terms.',label:'EVALUATION / VERIFY'},
];
export function SignalRoom({onLaunch,paused=false}:{onLaunch:()=>void;paused?:boolean}){
 const [study,setStudy]=useState(0);
 return <section className={`zr-room zr-study-${study}`} aria-label="Explore the optimization system">
   <div className="zr-room-head"><span>THE SIGNAL ROOM / INTERACTIVE STUDY</span><span>03 WAYS TO SEE YOUR AGENT</span></div>
   <div className="zr-room-grid">
    <div className="zr-instrument" aria-hidden="true">
     <div className="zr-orbit zr-orbit-one"/><div className="zr-orbit zr-orbit-two"/><div className="zr-orbit zr-orbit-three"/>
     <div className="zr-constellation">{Array.from({length:20},(_,i)=>{const a=i*Math.PI*2/20;return <i key={i} style={{left:`${50+Math.cos(a)*36}%`,top:`${50+Math.sin(a)*36}%`,width:12+(i%4)*9,height:12+(i%4)*9,animationDelay:`${i*-.32}s`}}/>;})}</div>
     <svg className="zr-paths" viewBox="0 0 500 500"><g>{Array.from({length:10},(_,i)=><path key={i} d={study===0?`M ${70+i*38} 65 C ${480-i*30} 180, ${20+i*30} 340, ${70+i*38} 435`:study===1?`M 250 250 Q ${i%2?40:460} ${i*45} ${250+180*Math.cos(i*.628)} ${250+180*Math.sin(i*.628)}`:`M ${70+i*38} 65 C ${70+i*38} 240, 250 190, 250 410`} />)}</g></svg>
     <div className="zr-core"><LivingLogo animated={!paused}/></div>
     <span className="zr-instrument-note">{studies[study].label}<br/>VISUALIZATION, NOT LIVE TELEMETRY</span>
    </div>
    <div className="zr-room-copy"><div className="zr-room-tabs" role="group" aria-label="Choose a system study">{studies.map((s,i)=><button key={s.name} onClick={()=>setStudy(i)} aria-pressed={study===i}>{s.name}</button>)}</div><div key={study} className="zr-study-copy"><span>0{study+1} / THE SYSTEM</span><h2>{studies[study].title}</h2><p>{studies[study].copy}</p></div><button className="zr-room-launch" onClick={onLaunch}>Take it into Studio <ArrowUpRight size={22}/></button></div>
   </div>
 </section>;
}
