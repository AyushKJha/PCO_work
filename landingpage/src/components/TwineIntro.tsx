import { useEffect, useRef } from 'react';
import { ArrowRight } from 'lucide-react';
import { LivingLogo } from './LivingLogo';
import '../twine-intro.css';

/** Self-contained opening; no remote assets, artificial loading, or scroll interception. */
export function TwineIntro({onDone}:{onDone:()=>void}){
 const canvas=useRef<HTMLCanvasElement>(null);
 useEffect(()=>{
  const el=canvas.current,ctx=el?.getContext('2d');if(!el||!ctx)return;
  let w=1,h=1,frame=0,start=0;
  const resize=new ResizeObserver(()=>{const r=el.getBoundingClientRect();w=r.width;h=r.height;const d=Math.min(devicePixelRatio,1.5);el.width=w*d;el.height=h*d;ctx.setTransform(d,0,0,d,0,0);});resize.observe(el);
  const ease=(v:number)=>{const x=Math.max(0,Math.min(1,v));return x*x*(3-2*x);};
  const draw=(now:number)=>{
   if(!start)start=now;const t=(now-start)/1000,join=ease((t-1.1)/1.65),vanish=ease((t-2.6)/.6);
   ctx.clearRect(0,0,w,h);
   const nodes=Array.from({length:48},(_,i)=>{
    const a=i*2.39996+t*.18,r=Math.sqrt((i+.5)/48),z=Math.sin(i*9.1+t*.4);
    const x=w*.5+Math.cos(a)*r*w*.69*(1-join),y=h*.5+Math.sin(a)*r*h*.76*(1-join);
    return{x,y,z,i,a,r};
   }).sort((a,b)=>a.z-b.z);
   for(const n of nodes){
    const s=(10+(n.z+1)*13)*(1-join*.75),alpha=(.35+(n.z+1)*.3)*(1-vanish);
    ctx.save();ctx.globalAlpha=alpha;
    if(join>.05){ctx.beginPath();ctx.moveTo(n.x,n.y);ctx.bezierCurveTo(n.x+w*.1*(1-join),n.y,w*.5,h*.5+h*.06,w*.5,h*.5);ctx.strokeStyle=`rgba(210,215,220,${join*.25})`;ctx.lineWidth=.7;ctx.stroke();}
    ctx.translate(n.x,n.y);ctx.rotate(n.a*.3+t*.15);
    // Machined model cartridges, with a dark aperture and a silver bevel.
    const metal=ctx.createLinearGradient(-s,-s,s,s);metal.addColorStop(0,'#101214');metal.addColorStop(.22,'#91969a');metal.addColorStop(.38,'#f6f6f4');metal.addColorStop(.5,'#484d51');metal.addColorStop(.82,'#181b1e');metal.addColorStop(1,'#868b8e');
    ctx.fillStyle=metal;ctx.beginPath();ctx.roundRect(-s,-s*.65,s*2,s*1.3,s*.4);ctx.fill();ctx.strokeStyle='#ffffff35';ctx.lineWidth=.7;ctx.stroke();
    ctx.fillStyle='#090b0d';ctx.beginPath();ctx.ellipse(s*.25,0,s*.36,s*.3,0,0,Math.PI*2);ctx.fill();ctx.strokeStyle='#c8cdd14a';ctx.stroke();
    ctx.restore();
   }
   if(t<3.65)frame=requestAnimationFrame(draw);
  };
  frame=requestAnimationFrame(draw);return()=>{cancelAnimationFrame(frame);resize.disconnect();};
 },[]);
 return <div className="ti-opening" role="dialog" aria-label="TwineRun introduction" aria-modal="true">
  <div className="ti-top"><span>TwineRun®</span><span>INTELLIGENCE IN MOTION</span></div>
  <div className="ti-window"><canvas ref={canvas} aria-hidden="true"/><div className="ti-beam"/><div className="ti-living-mark"><LivingLogo/></div></div>
  <div className="ti-title" aria-label="TwineRun">{'TwineRun'.split('').map((c,i)=><span key={i} style={{animationDelay:`${1.8+i*.035}s`}}>{c}</span>)}</div>
  <div className="ti-bottom"><span>MANY POSSIBILITIES.<br/>ONE BETTER WAY TO RUN.</span><button autoFocus onClick={onDone}>Skip introduction <ArrowRight size={16}/></button></div>
 </div>;
}
