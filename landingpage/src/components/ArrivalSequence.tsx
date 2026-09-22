import { useEffect, useRef, useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { TwineIntro } from './TwineIntro';
import { LivingLogo } from './LivingLogo';
import '../arrival-sequence.css';

export function ArrivalSequence({onDone}:{onDone:()=>void}){
 const [ready,setReady]=useState(false),[loaded,setLoaded]=useState(0),[error,setError]=useState(false);
 const done=useRef(onDone);done.current=onDone;
 useEffect(()=>{
  let alive=true;let completed=0;const started=performance.now();let finishTimer:number|undefined;
  const finish=()=>{if(!alive)return;completed++;setLoaded(completed);if(completed===3)finishTimer=window.setTimeout(()=>{if(alive)setReady(true);},Math.max(0,1400-(performance.now()-started)));};
  import('./VoyageScene').then(m=>m.preloadVoyageAssets()).then(finish).catch(()=>{if(alive)setError(true);finish();});
  document.fonts.ready.then(finish);finish();
  const timeout=window.setTimeout(()=>{if(alive){setError(true);setReady(true);}},6500);
  return()=>{alive=false;clearTimeout(timeout);clearTimeout(finishTimer);};
 },[]);
 useEffect(()=>{if(!ready)return;const timer=setTimeout(()=>done.current(),3950);return()=>clearTimeout(timer);},[ready]);
 if(ready)return <TwineIntro onDone={()=>done.current()}/>;
 return <div className="arrival" role="dialog" aria-label="Preparing the TwineRun experience" aria-modal="true">
  <header><span>TwineRun®</span><span>ASSEMBLING A WORLD OF INTELLIGENCE</span></header>
  <div className="arrival-system" aria-hidden="true"><svg className="arrival-links" viewBox="0 0 900 360">{[60,120,180,240,300].map((y,i)=><path key={y} style={{animationDelay:`${i*-.4}s`}} d={`M 100 ${y} C 320 ${y}, 360 180, 480 180 S 650 180, 810 180`}/>)}</svg><div className="arrival-agents">{['PLANNER','RETRIEVER','REASONER','WRITER','VERIFIER'].map((name,i)=><div key={name} style={{animationDelay:`${i*-.3}s`}}><b>0{i+1}</b><span>{name}</span><i/></div>)}</div><LivingLogo className="arrival-logo"/><span className="arrival-output">ONE<br/>CLEAR<br/>DIRECTION.</span></div>
  <div className="arrival-title"><span>Many minds.</span><span>One mission.</span></div>
  <footer><div><span>{error?'PREPARING FALLBACK SCENE':'PREPARING THE VISUAL EXPERIENCE'}</span><div className="arrival-bar"><i style={{transform:`scaleX(${loaded/3})`}}/></div><small>Visual metaphor · no AI jobs are running</small></div><strong aria-live="polite">{loaded}<small> / 3 assets</small></strong><button autoFocus onClick={()=>done.current()}>Skip <ArrowRight size={16}/></button></footer>
 </div>;
}
