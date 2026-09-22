import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUpRight, Pause, Play } from 'lucide-react';
import '../astronaut-voyage.css';

const VoyageScene=lazy(()=>import('./VoyageScene'));
const scenes=[
 ['01 / LOST IN POSSIBILITY','A universe of AI.','Find your way.','More models. More decisions. More complexity. Give your agent a clear direction.'],
 ['02 / MAP THE UNKNOWN','Every connection.','A new possibility.','Trace the work. Explore different model assignments. See beyond the configuration you started with.'],
 ['03 / FIND YOUR ORBIT','Bring intelligence','back to earth.','Evaluate the trade-offs. Choose the configuration that meets your standard. Keep the final decision yours.'],
];
export function AstronautVoyage({paused,onPause,onLaunch}:{paused:boolean;onPause:()=>void;onLaunch:()=>void}){
 const root=useRef<HTMLElement>(null),progress=useRef(0);
 const [chapter,setChapter]=useState(0),[unavailable,setUnavailable]=useState(false);
 useEffect(()=>{
  const section=root.current;if(!section)return;
  let frame=0;
  const update=()=>{frame=0;const p=Math.max(0,Math.min(1,-section.getBoundingClientRect().top/Math.max(1,section.offsetHeight-innerHeight)));progress.current=p;section.style.setProperty('--voyage',p.toFixed(4));setChapter(p<.29?0:p<.67?1:2);};
  const schedule=()=>{if(!frame)frame=requestAnimationFrame(update);};
  const observer=new ResizeObserver(schedule);observer.observe(section);
  addEventListener('scroll',schedule,{passive:true});addEventListener('resize',schedule);update();
  return()=>{cancelAnimationFrame(frame);observer.disconnect();removeEventListener('scroll',schedule);removeEventListener('resize',schedule);};
 },[]);
 const s=scenes[chapter];
 return <section ref={root} className={`av-voyage av-chapter-${chapter}`} aria-label="An astronaut navigating an AI workflow">
  <div className="av-sticky">
   {!unavailable&&<Suspense fallback={<div className="av-scene-loading">Preparing the journey…</div>}><VoyageScene progress={progress} paused={paused} onUnavailable={()=>setUnavailable(true)}/></Suspense>}
   <div className={`av-traveller ${unavailable?'av-static':''}`}><img src="/art/astronaut-ai-v1.png" alt="Astronaut among AI pathways" fetchPriority="high" style={{animationPlayState:paused?'paused':'running'}}/></div>
   <div className="av-screen-shade"/>
   <div className="av-meta"><span><i/> HUMAN AMBITION. MACHINE INTELLIGENCE.</span><span>TWINERUN / EXPLORATION 001</span></div>
   <div className="av-copy" key={chapter}><span className="av-kicker">{s[0]}</span>{chapter===0?<h1>{s[1]}<br/><em>{s[2]}</em></h1>:<h2>{s[1]}<br/><em>{s[2]}</em></h2>}<p>{s[3]}</p><button onClick={onLaunch}>Find your better configuration <ArrowUpRight size={18}/></button></div>
   <div className="av-telemetry" aria-hidden="true"><span>AGENT / HUMAN IN THE LOOP</span><b>{chapter===0?'NAVIGATE COMPLEXITY':chapter===1?'EXPLORE ALTERNATIVES':'VERIFY THE DIFFERENCE'}</b><span>ARTISTIC VISUALIZATION · NOT LIVE DATA</span></div>
   <div className="av-bottom"><a href="#oj-product">Scroll into possibility <ArrowDown size={16}/></a><div>{['Observe','Explore','Verify'].map((label,i)=><button key={label} aria-label={`${label} scene`} aria-current={chapter===i?'step':undefined} className={chapter===i?'active':''} onClick={()=>{const el=root.current;if(el)scrollTo({top:scrollY+el.getBoundingClientRect().top+(el.offsetHeight-innerHeight)*i*.4,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}}>0{i+1}<b>{label}</b></button>)}</div><button onClick={onPause} aria-label={paused?'Resume animation':'Pause animation'}>{paused?<Play size={16}/>:<Pause size={16}/>}</button></div>
   <div className="av-progress"/>
  </div>
 </section>;
}
