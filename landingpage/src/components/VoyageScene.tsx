import { useEffect, useRef, type RefObject } from 'react';
import * as THREE from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';

let asset:Promise<void>|undefined;
export function preloadVoyageAssets(){
 if(!asset){const image=new Image();image.src='/art/astronaut-ai-v1.png';asset=image.decode().catch(e=>{asset=undefined;throw e;});}
 return asset;
}
export default function VoyageScene({progress,paused,onUnavailable}:{progress:RefObject<number>;paused:boolean;onUnavailable:()=>void}){
 const mount=useRef<HTMLDivElement>(null),settings=useRef({paused,onUnavailable});settings.current={paused,onUnavailable};
 useEffect(()=>{
  const host=mount.current;if(!host)return;
  let renderer:THREE.WebGLRenderer;
  try{renderer=new THREE.WebGLRenderer({alpha:false,antialias:true,powerPreference:'high-performance'});}catch{settings.current.onUnavailable();return;}
  host.appendChild(renderer.domElement);renderer.setPixelRatio(Math.min(devicePixelRatio,1.35));renderer.setClearColor(0x070809);renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.25;
  const scene=new THREE.Scene();scene.fog=new THREE.FogExp2(0x070809,.028);
  const camera=new THREE.PerspectiveCamera(42,1,.1,120);
  const pmrem=new THREE.PMREMGenerator(renderer),room=new RoomEnvironment(),env=pmrem.fromScene(room,.04);scene.environment=env.texture;room.dispose();pmrem.dispose();
  scene.add(new THREE.HemisphereLight(0xdde1e6,0x181a1c,1.5));
  const key=new THREE.DirectionalLight(0xffffff,4.2);key.position.set(-4,5,6);scene.add(key);
  const rim=new THREE.DirectionalLight(0xf0f0f0,5);rim.position.set(5,2,-5);scene.add(rim);
  const edge=new THREE.PointLight(0xffffff,10,16,2);edge.position.set(0,-1,2);scene.add(edge);
  const objects:THREE.Object3D[]=[],geometries:THREE.BufferGeometry[]=[],materials:THREE.Material[]=[];
  const silver=new THREE.MeshPhysicalMaterial({color:0x999b9e,roughness:.19,metalness:1,envMapIntensity:1.4});materials.push(silver);
  const dim=new THREE.MeshStandardMaterial({color:0x1b1f22,metalness:.7,roughness:.4});materials.push(dim);
  const luminous=new THREE.MeshBasicMaterial({color:0xdde2e6});materials.push(luminous);
  const sphere=new THREE.SphereGeometry(.035,10,8);geometries.push(sphere);
  const capsule=new THREE.CapsuleGeometry(.085,.15,4,10);geometries.push(capsule);
  const modelNodes=new THREE.InstancedMesh(capsule,silver,75);modelNodes.instanceMatrix.setUsage(THREE.DynamicDrawUsage);scene.add(modelNodes);
  const particlesGeometry=new THREE.BufferGeometry(),positions=new Float32Array(1400*3);
  for(let i=0;i<1400;i++){positions[i*3]=Math.sin(i*23.71)*27;positions[i*3+1]=Math.cos(i*31.33)*17;positions[i*3+2]=-45+((i*71)%997)/997*60;}
  particlesGeometry.setAttribute('position',new THREE.BufferAttribute(positions,3));geometries.push(particlesGeometry);
  const particlesMaterial=new THREE.PointsMaterial({color:0xc9cdd0,size:.013,transparent:true,opacity:.5,sizeAttenuation:true});materials.push(particlesMaterial);scene.add(new THREE.Points(particlesGeometry,particlesMaterial));
  const streams=new THREE.Group();scene.add(streams);
  const paths:THREE.CatmullRomCurve3[]=[],pulses:THREE.Mesh[]=[];
  // An actual three-dimensional conduit. The camera passes between these paths.
  for(let i=0;i<18;i++){
   const points:THREE.Vector3[]=[];
   for(let j=0;j<=16;j++){const z=3-j*2.8,a=i/18*Math.PI*2+j*.38,r=2.6+.45*Math.sin(j*.8+i);points.push(new THREE.Vector3(Math.cos(a)*r,Math.sin(a)*r,z));}
   const curve=new THREE.CatmullRomCurve3(points);paths.push(curve);
   const geometry=new THREE.TubeGeometry(curve,150,i%5===0?.036:.012,5,false);geometries.push(geometry);
   const tube=new THREE.Mesh(geometry,i%3===0?silver:dim);streams.add(tube);
   const pulse=new THREE.Mesh(sphere,luminous);streams.add(pulse);pulses.push(pulse);
  }
  const gates=new THREE.Group();scene.add(gates);
  for(let i=0;i<6;i++){const geometry=new THREE.TorusGeometry(3.2,.018,6,100);geometries.push(geometry);const ring=new THREE.Mesh(geometry,silver);ring.position.z=-i*5.5-2;ring.rotation.set(.12*i,.15*Math.sin(i),i*.1);gates.add(ring);}
  const destination=new THREE.Group();destination.position.z=-19;scene.add(destination);
  const globeGeometry=new THREE.IcosahedronGeometry(.45,2);geometries.push(globeGeometry);const core=new THREE.Mesh(globeGeometry,silver);destination.add(core);
  const orbitGeometry=new THREE.TorusGeometry(.85,.007,4,70);geometries.push(orbitGeometry);
  for(let i=0;i<3;i++){const ring=new THREE.Mesh(orbitGeometry,luminous);ring.rotation.set(i*.8,i*1.1,0);destination.add(ring);}
  let w=1,h=1,visible=true,dirty=true,frame=0,last=0,time=0,smoothP=0,oldP=-1,px=0,py=0,lookX=0,lookY=0;
  const reduce=matchMedia('(prefers-reduced-motion: reduce)');
  const resize=new ResizeObserver(()=>{const r=host.getBoundingClientRect();w=r.width;h=r.height;renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();dirty=true;});resize.observe(host);
  const observer=new IntersectionObserver(([e])=>{visible=e.isIntersecting;dirty=true;});observer.observe(host);
  const move=(e:PointerEvent)=>{px=e.clientX/innerWidth-.5;py=e.clientY/innerHeight-.5;};addEventListener('pointermove',move,{passive:true});
  const dummy=new THREE.Object3D(),point=new THREE.Vector3(),target=new THREE.Vector3();
  const cameraPath=new THREE.CatmullRomCurve3([new THREE.Vector3(0,0,8.8),new THREE.Vector3(.3,.18,7.2),new THREE.Vector3(-.15,.05,2),new THREE.Vector3(.25,.05,-3),new THREE.Vector3(0,0,-9)]);
  const draw=(now:number)=>{
   frame=requestAnimationFrame(draw);if(!visible||document.hidden||now-last<15)return;
   const frozen=settings.current.paused||reduce.matches,raw=progress.current||0;
   smoothP=frozen?raw:THREE.MathUtils.lerp(smoothP,raw,.075);
   if(frozen&&!dirty&&Math.abs(smoothP-oldP)<.0001)return;
   const delta=Math.min((now-last)/1000,.05);if(!frozen)time+=delta;last=now;dirty=false;oldP=smoothP;
   lookX=THREE.MathUtils.lerp(lookX,frozen?0:px,.035);lookY=THREE.MathUtils.lerp(lookY,frozen?0:py,.035);
   const p=smoothP;
   cameraPath.getPoint(p,point);camera.position.copy(point);camera.position.x+=lookX*.32;camera.position.y-=lookY*.2;
   if(w<650){camera.position.z+=1.3;camera.fov=48;}else camera.fov=42;camera.updateProjectionMatrix();
   target.set(0,0,camera.position.z-9);camera.lookAt(target);
   streams.rotation.z=Math.sin(time*.075)*.04;
   pulses.forEach((pulse,i)=>{paths[i].getPoint((time*.018+i*.083)%1,pulse.position);});
   for(let i=0;i<75;i++){const a=i*2.39996+time*.035,z=-34+(i%15)*2.8,r=2.5+(i%4)*.4;dummy.position.set(Math.cos(a)*r,Math.sin(a)*r,z);dummy.rotation.set(time*.1+i,i*.71,time*.05);dummy.scale.setScalar(.7+(i%3)*.3);dummy.updateMatrix();modelNodes.setMatrixAt(i,dummy.matrix);}modelNodes.instanceMatrix.needsUpdate=true;
   core.rotation.set(time*.12,time*.18,0);destination.rotation.z=time*.04;
   key.position.z=camera.position.z+3;rim.position.z=camera.position.z-8;edge.position.z=camera.position.z-5;
   renderer.render(scene,camera);
  };
  const lost=(e:Event)=>{e.preventDefault();cancelAnimationFrame(frame);settings.current.onUnavailable();};renderer.domElement.addEventListener('webglcontextlost',lost);
  frame=requestAnimationFrame(draw);
  return()=>{cancelAnimationFrame(frame);resize.disconnect();observer.disconnect();removeEventListener('pointermove',move);renderer.domElement.removeEventListener('webglcontextlost',lost);geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());modelNodes.dispose();env.dispose();renderer.dispose();renderer.domElement.remove();objects.length=0;};
 },[progress]);
 return <div className="av-webgl" ref={mount} aria-hidden="true"/>;
}
