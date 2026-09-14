import React, { useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, ChevronDown, Gauge, Menu, Play, ShieldCheck, Sparkles, X, Zap } from 'lucide-react';
import '../premium-home.css';

interface Props { onLaunchStudio: () => void; }

const navLinks = [['#product', 'Product'], ['#how-it-works', 'How it works'], ['#results', 'Results'], ['/pricing', 'Pricing']];

const Logo = () => <a href="#top" className="premium-logo" aria-label="TwineRun home"><img src="/twinerun-logo.png" alt="TwineRun" /></a>;

const AgentGraph = () => <div className="agent-stage" aria-label="Animated agent optimization preview">
  <div className="stage-topline"><div><span className="live-dot" /> OPTIMIZATION RUN</div><span>TR-0842</span></div>
  <div className="stage-canvas">
    <svg className="graph-lines" viewBox="0 0 640 440" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="lineGlow" x1="0" x2="1"><stop offset="0" stopColor="#6b6b70" stopOpacity=".12" /><stop offset=".5" stopColor="#f4f4f4" stopOpacity=".9" /><stop offset="1" stopColor="#6b6b70" stopOpacity=".12" /></linearGradient></defs><path d="M95 215 C175 215 175 110 258 110" /><path d="M95 215 C175 215 175 322 258 322" /><path d="M358 110 C445 110 432 215 535 215" /><path d="M358 322 C445 322 432 215 535 215" /><path className="active-path" d="M95 215 C175 215 175 110 258 110 S432 215 535 215" /></svg>
    <div className="agent-node node-input"><span>01</span><b>Router</b><small>GPT-4.1 mini</small></div>
    <div className="agent-node node-research"><span>02</span><b>Research</b><small>Claude Sonnet</small></div>
    <div className="agent-node node-write"><span>03</span><b>Synthesis</b><small>GPT-4.1</small></div>
    <div className="agent-node node-output node-active"><span><Sparkles size={12} /></span><b>Final answer</b><small>Quality verified</small></div>
    <div className="testing-chip"><span /> testing 14 configurations</div>
  </div>
  <div className="stage-metrics"><div><span>Cost / run</span><strong>$0.184</strong><em>-64.2%</em></div><div><span>Quality score</span><strong>97.8%</strong><em>+0.4pp</em></div><div><span>Latency</span><strong>3.2s</strong><em>-28.6%</em></div></div>
</div>;

const faqs = [
  ['Will TwineRun change my production agent?', 'Never without your approval. TwineRun evaluates candidate configurations offline, then gives you a reviewable execution plan and a clean diff.'],
  ['Do I need to replace my framework?', 'No. Keep your current agent architecture and providers. TwineRun sits above the model layer and optimizes each step independently.'],
  ['How does it protect output quality?', 'You define the eval set, minimum confidence, and quality tolerance. A cheaper candidate only survives when it clears every guardrail.'],
];

export const VesperHomeFinal: React.FC<Props> = ({ onLaunchStudio }) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const [introVisible, setIntroVisible] = useState(true);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.body.classList.add('premium-intro-active');
    const timer = window.setTimeout(() => setIntroVisible(false), 3800);
    return () => { window.clearTimeout(timer); document.body.classList.remove('premium-intro-active'); };
  }, []);

  useEffect(() => {
    if (!introVisible) document.body.classList.remove('premium-intro-active');
  }, [introVisible]);

  useEffect(() => {
    document.body.classList.toggle('premium-menu-open', menuOpen);
    return () => document.body.classList.remove('premium-menu-open');
  }, [menuOpen]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const onMove = (event: PointerEvent) => {
      root.style.setProperty('--pointer-x', `${event.clientX}px`);
      root.style.setProperty('--pointer-y', `${event.clientY}px`);
    };
    const observer = new IntersectionObserver((entries) => entries.forEach((entry) => entry.isIntersecting && entry.target.classList.add('is-visible')), { threshold: 0.14 });
    root.querySelectorAll('.scroll-reveal').forEach((element) => observer.observe(element));
    window.addEventListener('pointermove', onMove, { passive: true });
    return () => { observer.disconnect(); window.removeEventListener('pointermove', onMove); };
  }, []);

  const launch = () => { setMenuOpen(false); onLaunchStudio(); };

  return <div className="premium-home" ref={rootRef}>
    {introVisible && <div className="twine-intro" aria-hidden="true" onClick={() => setIntroVisible(false)}><div className="intro-light" /><div className="intro-horizon" /><div className="intro-identity"><img className="intro-logo" src="/twinerun-logo.png" alt="" /></div><p>PROFILE · PROVE · OPTIMIZE</p><div className="intro-progress"><i /></div><small>ENTERING THE OPTIMIZATION LAYER</small></div>}
    <div className="premium-aurora" aria-hidden="true" /><div className="premium-grid" aria-hidden="true" /><div className="premium-noise" aria-hidden="true" />
    <header className="premium-nav-shell"><nav className="premium-nav" aria-label="Primary navigation"><Logo /><div className="premium-nav-links">{navLinks.map(([href, label]) => <a key={href} href={href}>{label}</a>)}</div><div className="premium-nav-actions"><a href="/signin" className="nav-signin">Sign in</a><button type="button" className="premium-button premium-button--compact" onClick={launch}>Open Studio <ArrowRight size={14} /></button><button className="premium-menu-button" type="button" onClick={() => setMenuOpen(!menuOpen)} aria-expanded={menuOpen} aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}>{menuOpen ? <X /> : <Menu />}</button></div></nav><div className={`premium-mobile-menu ${menuOpen ? 'is-open' : ''}`}>{navLinks.map(([href, label]) => <a key={href} href={href} onClick={() => setMenuOpen(false)}>{label}</a>)}<button type="button" className="premium-button" onClick={launch}>Open Studio <ArrowRight size={15} /></button></div></header>
    <main id="top">
      <section className="premium-hero"><div className="hero-orbit hero-orbit--one" aria-hidden="true" /><div className="hero-orbit hero-orbit--two" aria-hidden="true" />
        <div className="premium-hero-copy"><div className="premium-eyebrow hero-enter"><span>Introducing profile-guided optimization</span><i>New</i></div><h1 className="hero-enter hero-enter--2">Every agent has a<br /><span>better configuration.</span></h1><p className="hero-enter hero-enter--3">TwineRun discovers it. Profile every step, test the right models, and cut AI spend while your quality bar stays exactly where you set it.</p><div className="hero-actions hero-enter hero-enter--4"><button type="button" className="premium-button premium-button--hero" onClick={launch}>Optimize your agent <ArrowRight size={16} /></button><a href="#product" className="premium-text-button"><span><Play size={12} fill="currentColor" /></span> See it in action</a></div><div className="hero-assurance hero-enter hero-enter--5"><span><Check size={13} /> Works with your stack</span><span><Check size={13} /> Approval before deploy</span></div></div>
        <div className="premium-hero-visual hero-enter hero-enter--4"><AgentGraph /></div><div className="scroll-cue"><span>Explore</span><ChevronDown size={15} /></div>
      </section>
      <section className="trust-strip scroll-reveal" aria-label="Supported model providers"><p>OPTIMIZE ACROSS THE MODELS YOU ALREADY USE</p><div className="provider-row"><span>OpenAI</span><span>Anthropic</span><span>Google</span><span>Meta</span><span>Mistral</span><span>Cohere</span></div></section>
      <section className="premium-section product-section" id="product"><div className="section-heading scroll-reveal"><div><span className="section-number">01</span><span className="section-kicker">The optimization layer</span></div><h2>Your agent is a system.<br /><em>Optimize it like one.</em></h2><p>Stop paying the premium model tax on every step. TwineRun understands where intelligence creates value—and where it does not.</p></div>
        <div className="feature-bento">
          <article className="bento-card bento-card--wide scroll-reveal"><div className="card-label"><Gauge size={15} /> PROFILE</div><h3>See the economics of every node.</h3><p>Cost, latency, token load, and quality contribution—mapped onto the architecture you already run.</p><div className="profile-chart" aria-hidden="true">{[58,82,42,94,68,36,76,53,88,47,64,72].map((height,i) => <i key={i} style={{ '--bar': `${height}%`, '--delay': `${i*45}ms` } as React.CSSProperties} />)}<span className="chart-threshold">efficiency frontier</span></div></article>
          <article className="bento-card scroll-reveal"><div className="card-label"><Zap size={15} /> SEARCH</div><h3>Test thousands.<br />Keep the winners.</h3><p>Parallel candidate search finds lower-cost combinations humans overlook.</p><div className="candidate-stack" aria-hidden="true"><i /><i /><i /><div><span>Candidate 042</span><b>Passed</b></div></div></article>
          <article className="bento-card scroll-reveal"><div className="card-label"><ShieldCheck size={15} /> VERIFY</div><h3>Your quality bar is the constraint.</h3><p>Every change proves itself against your evals and confidence threshold.</p><div className="quality-ring" aria-hidden="true"><div><strong>98.4</strong><small>QUALITY</small></div></div></article>
          <article className="bento-card bento-card--wide deploy-card scroll-reveal"><div><div className="card-label"><Sparkles size={15} /> DEPLOY</div><h3>A better plan, ready when you are.</h3><p>Review the exact model swaps, projected impact, and confidence before anything touches production.</p></div><div className="code-diff" aria-hidden="true"><span>research_agent</span><del>- model: gpt-4.1</del><ins>+ model: gpt-4.1-mini</ins><small>Quality delta&nbsp;&nbsp; +0.2pp</small></div></article>
        </div>
      </section>
      <section className="premium-section steps-section" id="how-it-works"><div className="section-heading section-heading--center scroll-reveal"><div><span className="section-number">02</span><span className="section-kicker">How it works</span></div><h2>From graph to gains<br /><em>in three precise moves.</em></h2></div><div className="steps-line scroll-reveal">
        <article><span>01</span><div className="step-icon"><svg viewBox="0 0 80 80"><circle cx="40" cy="40" r="25" /><circle cx="40" cy="40" r="7" /><path d="M40 15V7M65 40h8M40 65v8M15 40H7" /></svg></div><h3>Connect</h3><p>Import the graph and evals that define good performance.</p></article>
        <article><span>02</span><div className="step-icon step-icon--scan"><svg viewBox="0 0 80 80"><path d="M12 18h56M12 40h56M12 62h56" /><circle cx="31" cy="18" r="6" /><circle cx="52" cy="40" r="6" /><circle cx="24" cy="62" r="6" /></svg></div><h3>Optimize</h3><p>Search the model space within your cost and quality bounds.</p></article>
        <article><span>03</span><div className="step-icon"><svg viewBox="0 0 80 80"><path d="M15 43l15 15 36-39" /><path d="M62 41v22H17V18h31" /></svg></div><h3>Ship</h3><p>Approve a verified execution plan and deploy with confidence.</p></article>
      </div></section>
      <section className="premium-section result-section" id="results"><div className="result-panel scroll-reveal"><div className="result-copy"><div><span className="section-number">03</span><span className="section-kicker">The outcome</span></div><h2>Less spend.<br /><em>Same standard.</em></h2><p>A frontier you can defend: every saving tied to a tested configuration, every quality claim backed by your evals.</p><a href="/benchmarks" className="premium-inline-link">Explore benchmarks <ArrowRight size={15} /></a></div><div className="result-metrics"><div className="hero-stat"><strong>64<span>%</span></strong><p>average cost reduction in our reference workload</p></div><div><strong>0.4<span>pp</span></strong><p>quality improvement after optimization</p></div><div><strong>28<span>%</span></strong><p>lower end-to-end latency</p></div><div><strong>14<span>x</span></strong><p>configurations evaluated per run</p></div></div></div></section>
      <section className="premium-section faq-section"><div className="faq-heading scroll-reveal"><span className="section-kicker">Questions, answered</span><h2>Designed for careful<br />teams moving fast.</h2></div><div className="faq-list scroll-reveal">{faqs.map(([question,answer],index) => <details key={question} open={index===0}><summary><span>0{index+1}</span>{question}<i><ChevronDown size={17} /></i></summary><p>{answer}</p></details>)}</div></section>
      <section className="final-cta scroll-reveal"><div className="cta-glow" aria-hidden="true" /><p className="section-kicker">Your next run can cost less</p><h2>Find the intelligence<br /><em>your agent actually needs.</em></h2><button type="button" className="premium-button premium-button--hero" onClick={launch}>Start optimizing <ArrowRight size={16} /></button><p className="cta-note">No production changes without your approval.</p></section>
    </main>
    <footer className="premium-footer"><Logo /><p>Profile-guided optimization for production AI agents.</p><div><a href="/faqs">FAQ</a><a href="/pricing">Pricing</a><a href="mailto:hello@twinerun.ai">Contact</a></div><span>© {new Date().getFullYear()} TwineRun</span></footer>
  </div>;
};
