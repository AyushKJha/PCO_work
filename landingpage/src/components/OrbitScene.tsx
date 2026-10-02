import { useEffect, useRef, type RefObject } from 'react';
import * as THREE from 'three';

const fragment=`
precision highp float;
uniform vec2 resolution;
uniform float time,progress;
varying vec2 vUv;
float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
void main(){
 vec2 p=(vUv-.5)*vec2(resolution.x/resolution.y,1.);p.x-=.15;
 float zoom=mix(1.4,.7,progress);p*=zoom;
 float r=length(p),a=atan(p.y,p.x);
 vec2 cell=floor(vUv*vec2(600.,360.));float star=step(.9976,hash(cell));
 float twinkle=.35+.25*sin(time*.5+hash(cell)*20.);
 vec3 color=vec3(star*twinkle);
 float lens=exp(-pow((r-.265)*15.,2.));
 float disc=exp(-abs(p.y+p.x*.19)*30.)*smoothstep(.22,.31,r)*exp(-r*2.);
 float ripple=.7+.3*sin(a*7.+log(max(r,.001))*13.-time*.35);
 float ring=exp(-pow((r-.265)*80.,2.));
 color+=vec3(lens*.09+disc*.7*ripple+ring*.5);
 color*=smoothstep(.225,.253,r);
 float fog=exp(-length(p-vec2(.6,.3))*3.)*.035;color+=vec3(fog);
 gl_FragColor=vec4(color,1.);
}`;
export default function OrbitScene({progress,paused}:{progress:RefObject<number>;paused:boolean}){
 const host=useRef<HTMLDivElement>(null),freeze=useRef(paused);freeze.current=paused;
 useEffect(()=>{
  const el=host.current;if(!el)return;let renderer:THREE.WebGLRenderer;
  try{renderer=new THREE.WebGLRenderer({antialias:false,powerPreference:'low-power'});}catch{return;}
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.25));el.appendChild(renderer.domElement);
  const scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(-1,1,1,-1,0,1),geometry=new THREE.PlaneGeometry(2,2);
  const uniforms={resolution:{value:new THREE.Vector2(1,1)},time:{value:0},progress:{value:0}};
  const material=new THREE.ShaderMaterial({uniforms,vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=vec4(position,1.);}',fragmentShader:fragment});scene.add(new THREE.Mesh(geometry,material));
  let frame=0,last=0,visible=true,dirty=true,old=-1;
  const media=matchMedia('(prefers-reduced-motion: reduce)');
  const resize=new ResizeObserver(()=>{const r=el.getBoundingClientRect();renderer.setSize(r.width,r.height,false);uniforms.resolution.value.set(r.width,r.height);dirty=true;});resize.observe(el);
  const observer=new IntersectionObserver(([e])=>{visible=e.isIntersecting;dirty=true;});observer.observe(el);
  const draw=(now:number)=>{frame=requestAnimationFrame(draw);if(!visible||document.hidden||now-last<24)return;const stopped=freeze.current||media.matches,p=progress.current;if(stopped&&!dirty&&old===p)return;if(!stopped)uniforms.time.value+=Math.min((now-last)/1000,.05);last=now;old=p;dirty=false;uniforms.progress.value=p;renderer.render(scene,camera);};
  frame=requestAnimationFrame(draw);
  return()=>{cancelAnimationFrame(frame);resize.disconnect();observer.disconnect();geometry.dispose();material.dispose();renderer.dispose();renderer.domElement.remove();};
 },[progress]);
 return <div className="trn-orbit-canvas" ref={host} aria-hidden="true"/>;
}
