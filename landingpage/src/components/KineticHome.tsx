import { lazy, Suspense, useEffect, useRef, useState, type CSSProperties } from 'react';
import { ArrowDown, ArrowUpRight, ArrowRight, Plus, Minus, Pause, Play, X } from 'lucide-react';
import { LivingLogo } from './LivingLogo';
import '../kinetic-home.css';
import '../kinetic-refinements.css';
import '../brand-entrance.css';

const IntelligenceScene=lazy(()=>import('./IntelligenceScene'));
const OrbitScene=lazy(()=>import('./OrbitScene'));
const clamp=(n:number)=>Math.max(0,Math.min(1,n));
const studies=[
 {name:'The trace',tag:'01 / UNDERSTAND',title:'Make the invisible visible.',description:'Follow your agent’s model calls, tokens, latency, and execution paths. Understand where the work actually happens.',detail:'Start with the workflow you already have. TwineRun profiles its execution and makes the model assignments visible, so you can decide where to investigate.',kind:'trace'},
 {name:'The search',tag:'02 / EXPLORE',title:'There is another way.',description:'Explore model assignments across your workflow. Find configurations that deserve a closer look.',detail:'Set your quality tolerance and explore alternative models per step. Candidate configurations are evaluated against your own test suite before you make a decision.',kind:'search'},
 {name:'The evidence',tag:'03 / DECIDE',title:'Possibility. With proof.',description:'Compare evaluated candidates. Keep your quality standard. Export the configuration you choose.',detail:'Review the cost, latency, and quality trade-offs from evaluated runs. Recommendations stay under your control: you choose what to export and when to deploy it.',kind:'evidence'},
];
function StudyArt({kind,paused}:{kind:string;paused:boolean}){
 return <div className={`trn-study-art trn-art-${kind} ${paused?'is-paused':''}`} aria-hidden="true">
  {kind==='trace'?<><div className="trn-trace-lines">{Array.from({length:7},(_,i)=><div key={i} style={{'--i':i} as CSSProperties}><i/><span/><b/></div>)}</div><div className="trn-trace-orb"/></>:kind==='search'?<><div className="trn-search-system">{Array.from({length:5},(_,i)=><i key={i} style={{'--i':i} as CSSProperties}/>)}<b/></div><div className="trn-search-grain"/></>:<><div className="trn-evidence-stack">{Array.from({length:5},(_,i)=><div key={i} style={{'--i':i} as CSSProperties}><span/><i/><i/><i/></div>)}</div><span className="trn-evidence-mark">↗</span></>}
 </div>;
}
export function KineticHome({onLaunchStudio}:{onLaunchStudio:()=>void}){
 const reduced=useRef(matchMedia('(prefers-reduced-motion: reduce)').matches);
 const [intro,setIntro]=useState(true),[closing,setClosing]=useState(false),[sceneReady,setSceneReady]=useState(false),[fontsReady,setFontsReady]=useState(false);
 const [paused,setPaused]=useState(false),[organized,setOrganized]=useState(false),[menu,setMenu]=useState(false),[study,setStudy]=useState<number|null>(null),[arrived,setArrived]=useState(false);
 const root=useRef<HTMLDivElement>(null),orbit=useRef<HTMLElement>(null),orbitProgress=useRef(0),dialog=useRef<HTMLDivElement>(null),menuButton=useRef<HTMLButtonElement>(null),detailButton=useRef<HTMLButtonElement|null>(null);
 const finish=()=>{setClosing(true);};
 const [introPlayed,setIntroPlayed]=useState(false);
 useEffect(()=>{const t=setTimeout(()=>setIntroPlayed(true),reduced.current?700:4200);return()=>clearTimeout(t);},[]);
 useEffect(()=>{if(intro&&introPlayed&&sceneReady&&fontsReady)setClosing(true);},[intro,introPlayed,sceneReady,fontsReady]);
 // A slow or unavailable GPU must never trap visitors behind the intro.
 useEffect(()=>{if(!intro)return;const t=setTimeout(()=>setClosing(true),7500);return()=>clearTimeout(t);},[intro]);
 useEffect(()=>{let alive=true;document.fonts.ready.then(()=>{if(alive)setFontsReady(true);});return()=>{alive=false;};},[]);
 useEffect(()=>{const html=document.documentElement,prior=html.style.scrollBehavior;html.style.scrollBehavior=reduced.current?'auto':'smooth';const update=()=>root.current?.classList.toggle('trn-scrolled',scrollY>100);addEventListener('scroll',update,{passive:true});update();return()=>{html.style.scrollBehavior=prior;removeEventListener('scroll',update);};},[]);
 useEffect(()=>{if(!closing)return;const t=setTimeout(()=>{if(!location.hash)scrollTo({top:0,behavior:'instant'});setIntro(false);setClosing(false);},reduced.current?0:1100);return()=>clearTimeout(t);},[closing]);
 useEffect(()=>{if(!intro&&!menu&&study===null)return;const previous=document.body.style.overflow;document.body.style.overflow='hidden';return()=>{document.body.style.overflow=previous;};},[intro,menu,study]);
 useEffect(()=>{
  const el=root.current;if(!el)return;
  const observer=new IntersectionObserver(entries=>entries.forEach(e=>{if(e.isIntersecting){e.target.classList.add('trn-revealed');observer.unobserve(e.target);}}),{threshold:.12});
  el.querySelectorAll('[data-reveal]').forEach(e=>observer.observe(e));return()=>observer.disconnect();
 },[]);
 useEffect(()=>{
  const section=orbit.current;if(!section)return;let frame=0;
  const update=()=>{frame=0;const p=clamp(-section.getBoundingClientRect().top/Math.max(1,section.offsetHeight-innerHeight));orbitProgress.current=p;section.style.setProperty('--orbit',p.toFixed(4));setArrived(p>.62);};
  const schedule=()=>{if(!frame)frame=requestAnimationFrame(update);};const resize=new ResizeObserver(schedule);resize.observe(section);
  addEventListener('scroll',schedule,{passive:true});addEventListener('resize',schedule);update();
  return()=>{cancelAnimationFrame(frame);resize.disconnect();removeEventListener('scroll',schedule);removeEventListener('resize',schedule);};
 },[]);
 useEffect(()=>{
  if(!menu&&study===null)return;
  const prior=document.activeElement as HTMLElement;dialog.current?.querySelector<HTMLButtonElement>('button')?.focus();
  const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){setMenu(false);setStudy(null);}if(e.key==='Tab'){
   const focusable=dialog.current?.querySelectorAll<HTMLElement>('button,a[href]');if(!focusable?.length)return;
   const first=focusable[0],last=focusable[focusable.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
  }};addEventListener('keydown',key);
  return()=>{removeEventListener('keydown',key);prior?.focus();};
 },[menu,study]);
 const jump=(id:string)=>{setMenu(false);requestAnimationFrame(()=>document.getElementById(id)?.scrollIntoView({behavior:reduced.current?'auto':'smooth'}));};
 const loading=(Number(sceneReady)+Number(fontsReady))*50;
 return <div ref={root} className={`trn ${paused?'trn-paused':''} ${closing||!intro?'trn-entered':''}`}>
  {intro&&<div className={`trn-loader trn-entrance ${closing?'trn-loader-exit':''}`} role="dialog" aria-modal="true" aria-labelledby="trn-entrance-title">
   <div className="trn-loader-top"><span>TWINERUN®</span><button className="trn-intro-skip" onClick={finish} disabled={closing}>Skip intro ↗</button></div>
   <div className="trn-entrance-light" aria-hidden="true"/>
   <div className="trn-entrance-center">
    <div className="trn-entrance-emblem"><div className="trn-entrance-orbits" aria-hidden="true"><i/><i/><i/></div><LivingLogo animated/><span className="trn-entrance-signal" aria-hidden="true">TRACE · SEARCH · EVALUATE</span></div>
    <h1 id="trn-entrance-title" aria-label="TwineRun"><span className="trn-entrance-letters" aria-hidden="true">{'TwineRun'.split('').map((letter,i)=><b key={i} style={{'--letter':i} as CSSProperties}>{letter}</b>)}</span><span>®</span></h1>
    <p>Many minds.<span>One clear direction.</span></p>
    <div className="trn-entrance-countdown" aria-hidden="true"><i/><span>INTELLIGENCE, ALIGNED.</span></div>
   </div>
   <div className="trn-entrance-footer"><span>FROM COMPLEXITY TO CLARITY</span><span role="status">{loading===100?'EXPERIENCE READY':'PREPARING THE EXPERIENCE'} <i style={{transform:`scaleX(${loading/100})`}}/></span><span>OPTIMIZE YOUR POSSIBILITIES ↗</span></div>
  </div>}
  <div inert={intro||menu||study!==null}>
  <a className="trn-skip" href="#trn-method">Skip to the product</a>
  <header className="trn-header"><a className="trn-brand" href="/" aria-label="TwineRun home"><LivingLogo animated={!paused&&!intro}/><span>TwineRun<sup>®</sup></span></a>
   <div className="trn-header-right"><button className="trn-motion" onClick={()=>setPaused(v=>!v)} aria-label={paused?'Resume motion':'Pause motion'}>{paused?<Play size={16}/>:<Pause size={16}/>}</button><button className="trn-pill trn-pill-dark" onClick={onLaunchStudio}>OPEN STUDIO <span>•</span></button><button className="trn-menu-button" ref={menuButton} onClick={()=>setMenu(true)} aria-label="Open menu" aria-expanded={menu}>MENU <span>••</span></button></div>
  </header>
  <main>
   <section className="trn-hero" aria-label="Intelligence in motion"><div className="trn-hero-heading"><span className="trn-label">THE OPTIMIZATION LAYER FOR AI AGENTS</span><h1>We turn complex AI workflows into better ways to run.</h1></div>
    <div className="trn-hero-frame">
     <Suspense fallback={<div className="trn-scene-placeholder"/>}><IntelligenceScene paused={paused||(intro&&!closing)} organized={organized} onReady={()=>setSceneReady(true)}/></Suspense>
     <span className="trn-frame-tag"><i/> INTELLIGENCE IN MOTION</span>
     <div className="trn-frame-bottom"><span>{organized?'Everything, in its right place.':'Many possibilities. One clear direction.'}</span><button onClick={()=>setOrganized(v=>!v)} aria-pressed={organized}>{organized?'Release the models':'Bring it together'}<ArrowUpRight size={18}/></button></div>
     <span className="trn-drag-hint">MOVE TO EXPLORE</span>
    </div>
    <a className="trn-scroll-rule" href="#trn-idea"><Plus/><Plus/><span>SCROLL TO EXPLORE <ArrowDown size={13}/></span><Plus/><Plus/></a>
   </section>
   <section className="trn-idea" id="trn-idea"><span className="trn-label" data-reveal>01 / A DIFFERENT WAY FORWARD</span><h2 data-reveal>Big ambition.<br/><span>Better placed<br className="trn-mobile-break"/> intelligence.</span></h2>
    <div className="trn-idea-bottom"><div className="trn-brand-study" data-reveal><div className="trn-brand-study-ring"/><LivingLogo animated={!paused}/><span>FROM COMPLEXITY TO CLARITY</span></div><div className="trn-idea-copy" data-reveal><p>The most powerful model doesn’t belong in every step. TwineRun finds the possibilities inside your agent—and helps you test which ones deserve to become its next configuration.</p><button className="trn-pill" onClick={()=>jump('trn-method')}><span>•</span> MEET THE SYSTEM <ArrowUpRight size={16}/></button></div></div>
   </section>
   <section className="trn-gallery" id="trn-method"><div className="trn-gallery-heading" data-reveal><h2>Look beyond<br/>the default.</h2><p>THREE WAYS TO SEE YOUR AGENT.<br/>A WHOLE NEW WAY TO THINK ABOUT IT.</p></div>
    <div className="trn-study-grid">{studies.map((s,i)=><article className={`trn-study trn-study-${i}`} key={s.kind} data-reveal>
     <button className="trn-study-image" onClick={e=>{detailButton.current=e.currentTarget;setStudy(i);}} aria-label={`Explore ${s.name.toLowerCase()}`}><StudyArt kind={s.kind} paused={paused}/><span className="trn-study-tag">{s.tag}</span><span className="trn-study-open"><ArrowUpRight size={22}/></span><span className="trn-study-art-name">{s.name}</span></button>
     <div className="trn-study-text"><h3>{s.title}</h3><p>{s.description}</p></div>
    </article>)}</div>
    <div className="trn-gallery-foot"><span>VISUAL STUDIES OF THE PRODUCT. EXPLORE YOUR OWN WORKFLOW IN STUDIO.</span><button className="trn-pill" onClick={onLaunchStudio}>EXPLORE TWINERUN <ArrowUpRight size={16}/></button></div>
   </section>
   <section className="trn-orbit" ref={orbit} id="trn-universe" aria-label="A universe of possibilities"><div className="trn-orbit-sticky">
    <Suspense fallback={null}><OrbitScene progress={orbitProgress} paused={paused||intro}/></Suspense>
    <div className="trn-orbit-grain"/>
    <div className="trn-explorer" aria-hidden="true"><img src="/art/astronaut-ai-v1.png" alt="" loading="lazy"/></div>
    <div className="trn-orbit-top"><span>HUMAN AMBITION / MACHINE INTELLIGENCE</span><span>EXPLORATION 001</span></div>
    <div className="trn-orbit-copy"><span className="trn-label">STEP INTO A NEW POSSIBILITY</span><h2>A universe<br/>of models.<br/><span>Your direction.</span></h2><p>Explore the alternatives.<br/>Keep the intelligence that matters.</p></div>
    <div className="trn-orbit-arrival" aria-hidden={!arrived} inert={!arrived}><LivingLogo animated={!paused}/><h2>Less waste.<br/>More possibility.</h2><button className="trn-pill" onClick={onLaunchStudio}>FIND YOUR BETTER CONFIGURATION <ArrowUpRight size={16}/></button></div>
    <div className="trn-orbit-bottom"><span>KEEP SCROLLING <ArrowDown size={14}/></span><span>A JOURNEY FROM COMPLEXITY TO CLARITY</span></div>
   </div></section>
   <section className="trn-philosophy" id="trn-philosophy"><div className="trn-section-rule"><span>THE WAY WE THINK</span><Plus/></div><h2 data-reveal>Your quality bar.<br/>Your next move.</h2><div className="trn-philosophy-grid">{[['01','Start with reality.','Profile how your agent runs. Let execution traces tell you where to look.'],['02','Test the possibility.','Explore candidates against your own evaluation suite and quality tolerance.'],['03','Keep the decision.','Compare the evidence. Choose the configuration. Deploy when you’re ready.']].map(row=><div key={row[0]} data-reveal><span>{row[0]}</span><h3>{row[1]}</h3><p>{row[2]}</p></div>)}</div></section>
   <section className="trn-finale" id="trn-start"><span className="trn-label" data-reveal>WHAT COULD YOUR AGENT BECOME?</span><button onClick={onLaunchStudio} className="trn-finale-link" data-reveal>Let’s run<br/>better.<ArrowUpRight strokeWidth={1}/></button><div className="trn-finale-line"><span>BUILD SOMETHING THAT GOES FURTHER.</span><Plus/></div></section>
  </main>
  <footer className="trn-footer"><div className="trn-footer-top"><a className="trn-brand" href="/"><LivingLogo animated={!paused}/><span>TwineRun<sup>®</sup></span></a><p>More from your intelligence.<br/>Less from your compute.</p><div><a href="/pricing">Plans <ArrowUpRight size={14}/></a><a href="/signin">Sign in <ArrowUpRight size={14}/></a><button onClick={onLaunchStudio}>Open Studio <ArrowUpRight size={14}/></button></div></div><div className="trn-footer-bottom"><span>© {new Date().getFullYear()} TWINERUN</span><span>FROM COMPLEXITY TO CLARITY</span><button onClick={()=>scrollTo({top:0,behavior:reduced.current?'auto':'smooth'})}>BACK TO TOP ↑</button></div></footer>
  </div>
  {menu&&<div className="trn-menu" ref={dialog} role="dialog" aria-modal="true" aria-label="Navigation"><header><span>TwineRun®</span><button onClick={()=>setMenu(false)} aria-label="Close menu">CLOSE <X size={18}/></button></header><nav>{[['The idea','trn-idea'],['The system','trn-method'],['The possibility','trn-universe'],['Get started','trn-start']].map(([name,id],i)=><button key={id} onClick={()=>jump(id)}><small>0{i+1}</small>{name}<ArrowUpRight/></button>)}</nav><footer><a href="/pricing">Plans ↗</a><a href="/signin">Sign in ↗</a><span>INTELLIGENCE. IN MOTION.</span></footer></div>}
  {study!==null&&<div className="trn-detail-backdrop" onClick={()=>setStudy(null)}><div className="trn-detail" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="trn-detail-title" onClick={e=>e.stopPropagation()}><button className="trn-detail-close" onClick={()=>setStudy(null)} aria-label="Close study"><X size={21}/></button><StudyArt kind={studies[study].kind} paused={paused}/><div className="trn-detail-copy"><span className="trn-label">{studies[study].tag}</span><h2 id="trn-detail-title">{studies[study].title}</h2><p>{studies[study].detail}</p><button className="trn-pill trn-pill-dark" onClick={()=>{setStudy(null);onLaunchStudio();}}>TAKE IT INTO STUDIO <ArrowUpRight size={16}/></button></div></div></div>}
 </div>;
}
