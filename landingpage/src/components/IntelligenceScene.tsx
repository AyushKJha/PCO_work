import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/examples/jsm/geometries/RoundedBoxGeometry.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';

/** Original model cartridges: a physical assembly that resolves into a workflow. */
export default function IntelligenceScene({paused,organized,onReady}:{paused:boolean;organized:boolean;onReady:()=>void}) {
  const host=useRef<HTMLDivElement>(null),state=useRef({paused,organized,onReady});
  state.current={paused,organized,onReady};
  const fallback=useRef<HTMLImageElement>(null);
  useEffect(()=>{
    const el=host.current;if(!el)return;
    let renderer:THREE.WebGLRenderer;
    try{renderer=new THREE.WebGLRenderer({antialias:true,powerPreference:'high-performance'});}
    catch{if(fallback.current)fallback.current.hidden=false;state.current.onReady();return;}
    renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));
    renderer.setClearColor(0x171719);renderer.outputColorSpace=THREE.SRGBColorSpace;
    renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.1;
    el.appendChild(renderer.domElement);
    const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(38,1,.1,60);
    const generator=new THREE.PMREMGenerator(renderer),room=new RoomEnvironment(),environment=generator.fromScene(room,.04);
    scene.environment=environment.texture;room.dispose();generator.dispose();
    scene.add(new THREE.HemisphereLight(0xffffff,0x4f4f50,2.5));
    const key=new THREE.DirectionalLight(0xffffff,4);key.position.set(-3,5,5);scene.add(key);
    const rim=new THREE.DirectionalLight(0xffffff,5);rim.position.set(4,-2,3);scene.add(rim);
    const materials=[
      new THREE.MeshPhysicalMaterial({color:0xeeeeeb,roughness:.25,metalness:.24,clearcoat:1}),
      new THREE.MeshPhysicalMaterial({color:0x151517,roughness:.16,metalness:.75,clearcoat:1}),
      new THREE.MeshPhysicalMaterial({color:0xa4a4a6,roughness:.1,metalness:1,clearcoat:1}),
    ];
    const geometry=new RoundedBoxGeometry(.76,.76,.76,3,.15),disc=new THREE.CylinderGeometry(.2,.2,.028,24),torus=new THREE.TorusGeometry(.39,.07,12,40);
    const dark=new THREE.MeshStandardMaterial({color:0x080809,roughness:.35,metalness:.6});
    const chrome=new THREE.MeshPhysicalMaterial({color:0xbbbbbe,roughness:.15,metalness:1});
    const light=new THREE.MeshBasicMaterial({color:0xf8f8f5});
    const group=new THREE.Group();scene.add(group);
    const modules:Array<{object:THREE.Group;free:THREE.Vector3;order:THREE.Vector3;rotation:THREE.Vector3;phase:number}>=[];
    for(let i=0;i<42;i++){
      const object=new THREE.Group(),body=new THREE.Mesh(geometry,materials[i%3]);object.add(body);
      // Recessed input/output ports make each object a model node, not an arbitrary icon.
      const port=new THREE.Mesh(disc,dark);port.rotation.x=Math.PI/2;port.position.z=.397;object.add(port);
      const rimPort=new THREE.Mesh(new THREE.TorusGeometry(.205,.012,6,24),chrome);rimPort.position.z=.414;object.add(rimPort);
      if(i%3===0){const halo=new THREE.Mesh(torus,materials[2]);halo.rotation.y=.9;object.add(halo);}
      const pulse=new THREE.Mesh(new THREE.SphereGeometry(.027,8,6),light);pulse.position.set(.27,.24,.392);object.add(pulse);
      const angle=i*2.39996,r=.35+Math.sqrt(i/42)*3.05;
      const free=new THREE.Vector3(Math.cos(angle)*r*1.45,Math.sin(angle)*r*.8,Math.sin(i*3.72)*1.4);
      const row=Math.floor(i/7),col=i%7,order=new THREE.Vector3((col-3)*1.03,(row-2.5)*.95,Math.sin(col*.8)*.25);
      modules.push({object,free,order,rotation:new THREE.Vector3(i*.77,i*.43,i*.33),phase:i*1.37});
      group.add(object);
    }
    const wireMaterial=new THREE.LineBasicMaterial({color:0xaaaaab,transparent:true,opacity:0});
    const lineGeometry=new THREE.BufferGeometry(),linePositions=new Float32Array(42*6);
    lineGeometry.setAttribute('position',new THREE.BufferAttribute(linePositions,3));
    const wires=new THREE.LineSegments(lineGeometry,wireMaterial);group.add(wires);
    let width=1,height=1,visible=true,dirty=true,frame=0,last=0,time=0,morph=0,pointerX=0,pointerY=0,aimX=0,aimY=0,scroll=0;
    const reduce=matchMedia('(prefers-reduced-motion: reduce)');
    const resize=new ResizeObserver(()=>{const r=el.getBoundingClientRect();width=r.width;height=r.height;renderer.setSize(width,height,false);camera.aspect=width/Math.max(1,height);camera.updateProjectionMatrix();dirty=true;});resize.observe(el);
    const intersection=new IntersectionObserver(([e])=>{visible=e.isIntersecting;dirty=true;});intersection.observe(el);
    const move=(e:PointerEvent)=>{const r=el.getBoundingClientRect();aimX=(e.clientX-r.left)/r.width-.5;aimY=(e.clientY-r.top)/r.height-.5;};
    const leave=()=>{aimX=aimY=0;};el.addEventListener('pointermove',move);el.addEventListener('pointerleave',leave);
    const updateScroll=()=>{const r=el.getBoundingClientRect();scroll=THREE.MathUtils.clamp(-r.top/Math.max(height,1),0,1);dirty=true;};addEventListener('scroll',updateScroll,{passive:true});
    const draw=(now:number)=>{
      frame=requestAnimationFrame(draw);if(!visible||document.hidden||now-last<15)return;
      const freeze=state.current.paused||reduce.matches,target=state.current.organized?1:Math.min(scroll*.65,.55);
      if(freeze&&!dirty&&Math.abs(morph-target)<.001)return;
      const dt=Math.min((now-last)/1000,.04);last=now;if(!freeze)time+=dt;
      morph=freeze?target:THREE.MathUtils.damp(morph,target,3.5,dt);dirty=false;
      pointerX=THREE.MathUtils.damp(pointerX,freeze?0:aimX,4,dt);pointerY=THREE.MathUtils.damp(pointerY,freeze?0:aimY,4,dt);
      camera.position.set(pointerX*.8,-pointerY*.5,width<650?11.4:8.4);camera.lookAt(0,0,0);
      group.rotation.set(.13+pointerY*.16,.08+pointerX*.25,-.13*(1-morph));group.position.y=-scroll*.3;
      modules.forEach((v,i)=>{
        v.object.position.copy(v.free).lerp(v.order,morph);
        const drift=(1-morph);v.object.position.y+=Math.sin(time*.35+v.phase)*.17*drift;v.object.position.z+=Math.cos(time*.28+v.phase)*.16*drift;
        const dx=v.object.position.x-pointerX*6,dy=v.object.position.y+pointerY*4,repulse=Math.max(0,1.2-Math.hypot(dx,dy))*.32*drift;
        v.object.position.x+=dx*repulse;v.object.position.y+=dy*repulse;
        v.object.rotation.set((v.rotation.x+time*.09)*drift,(v.rotation.y+time*.06)*drift,(v.rotation.z+time*.04)*drift);
        const scale=1+(Math.sin(time*.4+v.phase)*.04)*drift;v.object.scale.setScalar(scale);
        const next=modules[(i+1)%42];for(let k=0;k<3;k++){linePositions[i*6+k]=v.object.position.getComponent(k);linePositions[i*6+3+k]=next.object.position.getComponent(k);}
      });
      wireMaterial.opacity=morph*.28;lineGeometry.attributes.position.needsUpdate=true;
      renderer.render(scene,camera);
    };
    // Render an initial frame before releasing the loader.
    modules.forEach(v=>v.object.position.copy(v.free));camera.position.z=8.4;camera.lookAt(0,0,0);renderer.compile(scene,camera);renderer.render(scene,camera);state.current.onReady();
    const lost=(e:Event)=>{e.preventDefault();cancelAnimationFrame(frame);if(fallback.current)fallback.current.hidden=false;};
    renderer.domElement.addEventListener('webglcontextlost',lost);
    frame=requestAnimationFrame(draw);
    return()=>{cancelAnimationFrame(frame);resize.disconnect();intersection.disconnect();el.removeEventListener('pointermove',move);el.removeEventListener('pointerleave',leave);removeEventListener('scroll',updateScroll);renderer.domElement.removeEventListener('webglcontextlost',lost);scene.traverse(o=>{if(o instanceof THREE.Mesh)o.geometry.dispose();});geometry.dispose();disc.dispose();torus.dispose();lineGeometry.dispose();wireMaterial.dispose();[...materials,dark,chrome,light].forEach(m=>m.dispose());environment.dispose();renderer.dispose();renderer.domElement.remove();};
  },[]);
  return <div className="trn-intelligence" ref={host}><img ref={fallback} hidden src="/art/chrome-knot.png" alt="" /></div>;
}
