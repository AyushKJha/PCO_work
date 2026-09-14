import React, { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUpRight, ArrowRight, Plus, X, Pause, Play, RotateCcw } from 'lucide-react';
import { ComputationSculpture } from './ComputationSculpture';
import '../cinematic-home.css';

const chapters = [
  { id: 'observe', name: 'Observe', headline: 'Every decision.\nIn full view.', copy: 'See where your agent spends its tokens, time, and intelligence. Turn execution traces into a map you can act on.', detail: '01 / TRACE → PROFILE', image: '/art/chrome-knot.png' },
  { id: 'search', name: 'Explore', headline: 'More possibilities.\nLess guesswork.', copy: 'Search alternative model assignments within the limits you set. Compare candidates on cost, latency, and evaluated quality.', detail: '02 / SEARCH → COMPARE', image: '/art/verified-core.png' },
  { id: 'verify', name: 'Verify', headline: 'Let the evidence\nmake the call.', copy: 'Measure candidates against your own evals. Review the trade-offs, inspect the recommendation, and export a configuration you choose.', detail: '03 / EVALUATE → EXPORT', image: '/art/verified-core.png' },
];
const questions = [
  ['What does TwineRun actually optimize?', 'The model assignments inside an AI agent workflow. It profiles execution, evaluates candidate configurations, and presents cost, latency, and quality trade-offs for review.'],
  ['Does this change my production agent?', 'No automatic production routing changes. You review candidate results and export a configuration. Deployment stays in your hands.'],
  ['Can I bring my own evals?', 'Yes. The product includes evaluation suites and cases, baseline runs, confidence settings, and quality-tolerance controls. Runner configuration is required for execution.'],
  ['Is the interactive experiment a live benchmark?', 'No. It is an illustrative, local simulation showing the relationship between quality tolerance and candidate selection. Real results depend on your workflow, models, and evals.'],
];

export function CinematicHome({ onLaunchStudio }: { onLaunchStudio: () => void }) {
  const [intro, setIntro] = useState(() => !window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [menu, setMenu] = useState(false);
  const [paused, setPaused] = useState(false);
  const [tolerance, setTolerance] = useState(2);
  const [chapter, setChapter] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const menuPanel = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const header = useRef<HTMLElement>(null);
  const selected = chapters[chapter];
  const saving = 24 + tolerance * 7;

  useEffect(() => {
    if (!intro) return;
    const timer = window.setTimeout(() => setIntro(false), 2700);
    return () => window.clearTimeout(timer);
  }, [intro]);
  useEffect(() => {
    const previous = document.body.style.overflow;
    if (intro || menu) document.body.style.overflow = 'hidden';
    if (content.current) content.current.inert = intro || menu;
    if (header.current) header.current.inert = intro;
    return () => { document.body.style.overflow = previous; if (content.current) content.current.inert = false; };
  }, [intro, menu]);
  useEffect(() => {
    if (!menu) return;
    menuPanel.current?.querySelector<HTMLAnchorElement>('a')?.focus();
    const keys = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setMenu(false); menuButton.current?.focus(); }
      if (event.key === 'Tab') {
        const list = [menuButton.current, ...Array.from(menuPanel.current?.querySelectorAll<HTMLElement>('a,button') || [])].filter(Boolean) as HTMLElement[];
        const index = list.indexOf(document.activeElement as HTMLElement);
        event.preventDefault(); list[(index + (event.shiftKey ? -1 : 1) + list.length) % list.length]?.focus();
      }
    };
    window.addEventListener('keydown', keys);
    return () => window.removeEventListener('keydown', keys);
  }, [menu]);
  useEffect(() => {
    const page = root.current;
    if (!page) return;
    let frame = 0;
    const update = () => {
      const y = window.scrollY;
      page.style.setProperty('--scroll', String(Math.min(y / window.innerHeight, 1)));
      page.style.setProperty('--page-progress', String(y / Math.max(1, document.documentElement.scrollHeight - window.innerHeight)));
      frame = 0;
    };
    const scroll = () => { if (!frame) frame = requestAnimationFrame(update); };
    const reveal = new IntersectionObserver(entries => entries.forEach(entry => { if (entry.isIntersecting) entry.target.classList.add('tr-visible'); }), { threshold: .12 });
    page.querySelectorAll('[data-reveal]').forEach(node => reveal.observe(node));
    window.addEventListener('scroll', scroll, { passive: true }); update();
    return () => { cancelAnimationFrame(frame); reveal.disconnect(); window.removeEventListener('scroll', scroll); };
  }, []);
  const go = (id: string) => { setMenu(false); window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }), 0); };
  const launch = () => { setMenu(false); onLaunchStudio(); };

  return <div ref={root} className={`tr-site ${paused ? 'tr-paused' : ''} ${intro ? 'tr-entering' : 'tr-entered'}`}>
    <a className="tr-skip" href="#tr-main">Skip to content</a>
    {intro && <div className="tr-intro" role="dialog" aria-label="TwineRun introduction" aria-modal="true">
      <img className="tr-intro-logo" src="/twinerun-logo.svg" alt="TwineRun" />
      <div className="tr-intro-bottom"><span>FROM COMPLEXITY TO CLARITY</span><button autoFocus onClick={() => setIntro(false)}>Skip intro <ArrowRight size={14} /></button></div>
    </div>}
    <header ref={header} className="tr-header">
      <a href="/" className="tr-brand" aria-label="TwineRun home"><img src="/twinerun-logo.svg" alt="TwineRun" /></a>
      <span className="tr-header-caption">INTELLIGENCE, OPTIMIZED.</span>
      <div className="tr-header-actions"><button className="tr-launch" onClick={launch}>Open Studio <ArrowUpRight size={16} /></button><button ref={menuButton} className="tr-menu-toggle" aria-expanded={menu} aria-controls="tr-menu" aria-label={menu ? 'Close menu' : 'Open menu'} onClick={() => setMenu(!menu)}>{menu ? <X size={19} /> : <><span>Menu</span><Plus size={18} /></>}</button></div>
    </header>
    {menu && <div id="tr-menu" className="tr-menu" ref={menuPanel}><div><span className="tr-mono">EXPLORE TWinerun / 01—04</span>{[['tr-story','The idea'],['tr-experiment','The experiment'],['tr-process','The process']].map(([id,label],i) => <a href={`#${id}`} key={id} onClick={e => {e.preventDefault();go(id);}}><small>0{i+1}</small>{label}<ArrowUpRight /></a>)}<a href="/pricing"><small>04</small>Plans & pricing<ArrowUpRight /></a></div><footer><span>Less waste. More intelligence.</span><a href="/signin">Sign in <ArrowRight size={18} /></a></footer></div>}
    <div ref={content}>
      <main id="tr-main">
        <section className="tr-hero">
          <div className="tr-hero-art" aria-hidden="true"><img src="/art/chrome-knot.png" alt="" fetchPriority="high" /><div className="tr-art-shade" /></div>
          <div className="tr-hero-meta"><span><i /> THE OPTIMIZATION LAYER FOR AI AGENTS</span><span>LESS WASTE / MORE POSSIBILITY</span></div>
          <h1><span>Intelligence,</span><span className="tr-outline">untangled.</span></h1>
          <div className="tr-hero-bottom"><p>Your agent has a better way to run.<br />Find it. Prove it. Make it yours.</p><button className="tr-round-link" onClick={() => go('tr-experiment')}><span>Explore the<br />possibilities</span><span className="tr-circle"><ArrowDown /></span></button><span className="tr-hero-index">SCROLL TO UNRAVEL<br /><b>01 — 04</b></span></div>
          <span className="tr-hero-cross tr-cross-a" aria-hidden="true">+</span><span className="tr-hero-cross tr-cross-b" aria-hidden="true">+</span>
        </section>
        <div className="tr-ticker" aria-hidden="true"><div>{Array.from({length:4},(_,i)=><span key={i}>PROFILE THE COMPLEXITY <i>✳</i> FIND THE POSSIBILITY <i>✳</i> VERIFY THE DIFFERENCE <i>✳</i></span>)}</div></div>
        <section className="tr-story" id="tr-story">
          <div className="tr-section-top" data-reveal><span className="tr-mono">01 / THE IDEA</span><span className="tr-mono">A DIFFERENT KIND OF INTELLIGENCE.</span></div>
          <h2 data-reveal>Powerful agents.<br /><span>Without the excess.</span></h2>
          <div className="tr-story-bottom" data-reveal><span className="tr-asterisk" aria-hidden="true">✳</span><p>Not every step needs your most expensive model. TwineRun finds the hidden possibilities inside your workflow—and tests which ones deserve to become your next configuration.</p><button className="tr-text-action" onClick={launch}>Meet your optimization studio <ArrowUpRight /></button></div>
        </section>
        <section className="tr-experiment" id="tr-experiment">
          <div className="tr-section-top"><span className="tr-mono">02 / THE EXPERIMENT</span><span className="tr-mono"><i className="tr-dot" /> INTERACTIVE STUDY</span></div>
          <div className="tr-experiment-title" data-reveal><h2>Complexity.<br /><em>Under control.</em></h2><p>Move through the possibilities.<br />Watch the paths find their order.</p></div>
          <div className="tr-lab">
            <div className="tr-lab-art"><ComputationSculpture convergence={tolerance*18+10} paused={paused} /><span className="tr-lab-coordinate">FIG. 001 / CONFIGURATION SPACE</span><span className="tr-lab-hint">MOVE TO ROTATE</span></div>
            <div className="tr-lab-controls"><span className="tr-mono">YOUR QUALITY BAR. YOUR CALL.</span><h3>Make room for<br />a better trade-off.</h3><label htmlFor="quality-tolerance">Quality tolerance <strong>{tolerance.toFixed(1)} pp</strong></label><input id="quality-tolerance" type="range" min="0" max="5" step=".5" value={tolerance} onChange={e=>setTolerance(Number(e.target.value))} /><div className="tr-range-labels"><span>STRICTER</span><span>MORE EXPLORATION</span></div><div className="tr-lab-result" aria-live="polite"><strong>{saving.toFixed(0)}<small>%</small></strong><span>illustrative cost<br />reduction</span></div><div className="tr-lab-stats"><span>Candidate quality <b>{(98.8-tolerance*.4).toFixed(1)}%</b></span><span>Configurations explored <b>{16+Math.round(tolerance*8)}</b></span></div><p className="tr-disclosure">Illustrative simulation, not a benchmark. Real savings and quality depend on your agent and evals.</p><button className="tr-text-action" onClick={launch}>Try your own workflow <ArrowUpRight size={20} /></button></div>
          </div>
        </section>
        <section className="tr-process" id="tr-process">
          <div className="tr-section-top" data-reveal><span className="tr-mono">03 / THE PROCESS</span><span className="tr-mono">FROM RAW SIGNAL TO A REVIEWABLE PLAN.</span></div>
          <h2 data-reveal>See it.<br /><span>Then see beyond it.</span></h2>
          <div className="tr-chapter-tabs" role="tablist" aria-label="Optimization process">{chapters.map((item,i)=><button key={item.id} role="tab" id={`tab-${item.id}`} aria-selected={chapter===i} aria-controls="tr-chapter-panel" tabIndex={chapter===i?0:-1} onClick={()=>setChapter(i)} onKeyDown={e=>{if(['ArrowRight','ArrowLeft'].includes(e.key)){e.preventDefault();const n=(chapter+(e.key==='ArrowRight'?1:2))%3;setChapter(n);document.getElementById(`tab-${chapters[n].id}`)?.focus();}}}><small>0{i+1}</small>{item.name}<ArrowUpRight size={20}/></button>)}</div>
          <div className="tr-chapter" role="tabpanel" id="tr-chapter-panel" aria-labelledby={`tab-${selected.id}`} key={selected.id}><div className={`tr-chapter-image tr-chapter-image--${selected.id}`}><img src={selected.image} alt={chapter===0?'Interwoven chrome paths representing an agent workflow':'An exploded silver core representing candidate models being evaluated'} loading="lazy" /><span className="tr-image-tag">{selected.detail}</span></div><div className="tr-chapter-copy"><span className="tr-mono">{selected.detail}</span><h3>{selected.headline.split('\n').map(line=><span key={line}>{line}<br/></span>)}</h3><p>{selected.copy}</p><button className="tr-circle tr-circle-dark" aria-label="Open Studio to start this process" onClick={launch}><ArrowUpRight /></button></div></div>
        </section>
        <section className="tr-manifesto" data-reveal><div className="tr-manifesto-orbit" aria-hidden="true"/><span className="tr-mono">BUILT AROUND YOUR STANDARD.</span><h2>Spend less<br />on <span>the same</span><br />ambition.</h2><p>Keep your architecture. Bring your evals.<br />Let the evidence decide what changes.</p><button className="tr-pill" onClick={launch}>Find your better configuration <ArrowUpRight size={18}/></button></section>
        <section className="tr-faq" id="tr-questions"><div><span className="tr-mono">04 / A LITTLE CLARITY</span><h2>Good questions.<br /><span>Clear answers.</span></h2></div><div>{questions.map(([q,a],i)=><details key={q}><summary><small>0{i+1}</small>{q}<Plus size={20}/></summary><p>{a}</p></details>)}</div></section>
        <section className="tr-final"><div className="tr-section-top"><span className="tr-mono">YOUR NEXT CONFIGURATION IS OUT THERE.</span><span className="tr-mono">LET'S FIND IT.</span></div><button onClick={launch} className="tr-final-action">Run<br /><span>smarter.</span><ArrowUpRight/></button><div className="tr-final-bottom"><p>The optimization layer<br />for AI agents.</p><a href="/pricing">Explore plans <ArrowUpRight size={18}/></a><a href="/signup?returnTo=%2Fstudio">Create an account <ArrowUpRight size={18}/></a></div></section>
      </main>
      <footer className="tr-footer"><span>© {new Date().getFullYear()} TwineRun</span><a href="/signin">Sign in</a><button onClick={()=>{window.scrollTo({top:0,behavior:'auto'});setIntro(true);}}><RotateCcw size={13}/> Replay intro</button><button onClick={()=>setPaused(!paused)} aria-pressed={paused}>{paused?<Play size={13}/>:<Pause size={13}/>} {paused?'Resume motion':'Pause motion'}</button><span>BUILT FOR WHAT COMES NEXT.</span></footer>
    </div>
    <div className="tr-reading-progress" aria-hidden="true"/>
  </div>;
}
