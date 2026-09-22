import { useEffect, useState } from 'react';
import { ArrowDown, ArrowUpRight } from 'lucide-react';
import { AstronautVoyage } from './AstronautVoyage';
import { ArrivalSequence } from './ArrivalSequence';
import { LivingLogo } from './LivingLogo';
import { SignalRoom } from './SignalRoom';
import '../observatory.css';
import '../zinger.css';

export function ObservatoryHome({onLaunchStudio}:{onLaunchStudio:()=>void}){
  const [intro,setIntro]=useState(()=>!matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [paused,setPaused]=useState(false),[selection,setSelection]=useState(1);
  useEffect(()=>{if(!intro)return;const before=document.body.style.overflow;document.body.style.overflow='hidden';return()=>{document.body.style.overflow=before;};},[intro]);
  return <div className={`oj ${paused?'oj-paused':''}`}>
    {intro&&<ArrivalSequence onDone={()=>{if(!location.hash)scrollTo({top:0,behavior:'instant'});setIntro(false);}}/>}
    <div inert={intro}>
    <a className="oj-skip" href="#oj-product">Skip visual journey</a>
    <header className="oj-header"><a href="/" className="oj-brand"><LivingLogo className="av-header-logo" animated={!paused&&!intro}/>TwineRun<span>®</span></a><nav aria-label="Main navigation"><a href="#oj-product">The system</a><a href="/pricing">Pricing</a><button onClick={onLaunchStudio}>Open Studio <ArrowUpRight size={16}/></button></nav></header>
    <main>
      <AstronautVoyage paused={paused||intro} onPause={()=>setPaused(v=>!v)} onLaunch={onLaunchStudio}/>
      <SignalRoom onLaunch={onLaunchStudio} paused={paused||intro}/>
      <section className="oj-product" id="oj-product">
        <div className="oj-section-label"><span>THE IDEA / 001</span><span>NOT MORE INTELLIGENCE. BETTER PLACED INTELLIGENCE.</span></div>
        <h2>Big ambition.<br/><span>Smaller footprint.</span></h2>
        <div className="oj-statement"><span className="oj-star">✳</span><p>Your best model doesn’t belong in every step.<br/><br/>TwineRun turns the way your agent runs into a map of what it could become. Profile the workflow. Search the alternatives. Keep the quality that matters.</p><a href="#oj-workbench">Look inside <ArrowDown size={20}/></a></div>
        <div className="oj-workbench" id="oj-workbench">
          <div className="oj-workbench-title"><span>INSIDE THE SYSTEM</span><span>ILLUSTRATIVE WORKFLOW / NOT LIVE RESULTS</span></div>
          <div className="oj-workbench-grid"><div className="oj-map"><span className="oj-label">ONE WORKFLOW. DIFFERENT POSSIBILITIES.</span><div className="oj-path-line"/><div className="oj-nodes">{['Retrieve','Reason','Write','Verify'].map((s,i)=><div className="oj-node" key={s}><span>0{i+1}</span><b>{s}</b><small>{selection===0?'BASELINE MODEL':i===1?'REASONING MODEL':'CANDIDATE MODEL'}</small><i/></div>)}</div><div className="oj-map-footer"><span>INPUT → AGENT WORKFLOW → EVALUATION</span><span>4 NODES / 1 DECISION</span></div></div><div className="oj-comparison"><span className="oj-label">EXPLORE A CONFIGURATION</span><h3>Same purpose.<br/>A different path.</h3><div className="oj-switch" role="group" aria-label="Illustrative configuration"><button aria-pressed={selection===0} onClick={()=>setSelection(0)}>Baseline</button><button aria-pressed={selection===1} onClick={()=>setSelection(1)}>Candidate</button></div><dl><div><dt>Model assignment</dt><dd>{selection?'Per-step':'Uniform'}</dd></div><div><dt>Quality check</dt><dd>{selection?'Your eval suite':'Reference run'}</dd></div><div><dt>Deployment</dt><dd>Your decision</dd></div></dl><p>A conceptual preview. Actual recommendations require your workflow, configured runners, and evaluation results.</p><button className="oj-text-link" onClick={onLaunchStudio}>Explore your workflow <ArrowUpRight size={19}/></button></div></div>
        </div>
      </section>
      <section className="oj-proof"><div className="oj-section-label"><span>THE METHOD / 002</span><span>NO LEAP OF FAITH REQUIRED.</span></div><h2>From possibility<br/>to <em>proof.</em></h2><div className="oj-proof-grid">{[['01','Read the signal.','Profile execution traces to see how your agent spends tokens, time, and model calls.','TRACE / PROFILE'],['02','Explore the space.','Search alternative assignments with explicit quality tolerance and confidence settings.','SEARCH / COMPARE'],['03','Make the call.','Inspect evaluated candidates and export the configuration you choose. No automatic production switch.','EVALUATE / EXPORT']].map(s=><article key={s[0]}><span className="oj-proof-number">{s[0]}</span><h3>{s[1]}</h3><p>{s[2]}</p><small>{s[3]} <ArrowUpRight size={14}/></small></article>)}</div></section>
      <section className="oj-horizon"><div className="oj-horizon-light" aria-hidden="true"/><span className="oj-label">YOUR ARCHITECTURE. YOUR EVALS. YOUR STANDARD.</span><h2>Beyond the<br/><em>default.</em></h2><p>The next version of your agent<br/>starts with a better question.</p><button onClick={onLaunchStudio}>What could run better? <ArrowUpRight size={22}/></button><span className="oj-horizon-note">TWINERUN / FROM COMPLEXITY TO CLARITY</span></section>
    </main><footer className="oj-footer"><a href="/" className="oj-brand"><LivingLogo className="av-header-logo" animated={!paused&&!intro}/>TwineRun</a><span>© {new Date().getFullYear()} TwineRun</span><a href="/signin">Sign in ↗</a><a href="/pricing">Plans ↗</a><button onClick={()=>scrollTo({top:0,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'})}>Back to orbit ↑</button></footer>
    </div>
  </div>;
}
