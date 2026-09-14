import { useEffect, useRef } from 'react';

/** A small, dependency-free projected 3D particle sculpture. No provider calls. */
export function ComputationSculpture({ convergence, paused }: { convergence: number; paused: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const settings = useRef({ convergence, paused });
  settings.current = { convergence, paused };
  useEffect(() => {
    const element = canvas.current;
    const ctx = element?.getContext('2d');
    if (!element || !ctx) return;
    let width = 0, height = 0, frame = 0, time = 0, last = 0, visible = true;
    let pointer = { x: 0, y: 0 }, rotation = { x: 0, y: 0 }, blend = 0;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const resize = new ResizeObserver(() => {
      const bounds = element.getBoundingClientRect();
      width = bounds.width; height = bounds.height;
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      element.width = width * dpr; element.height = height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    });
    resize.observe(element);
    const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; });
    observer.observe(element);
    const move = (event: PointerEvent) => {
      const rect = element.getBoundingClientRect();
      pointer = { x: (event.clientX - rect.left) / rect.width - .5, y: (event.clientY - rect.top) / rect.height - .5 };
    };
    const leave = () => { pointer = { x: 0, y: 0 }; };
    element.addEventListener('pointermove', move);
    element.addEventListener('pointerleave', leave);
    const draw = (now: number) => {
      frame = requestAnimationFrame(draw);
      if (!visible || document.hidden || now - last < 30) return;
      last = now;
      if (!settings.current.paused && !reduced.matches) time += .006;
      blend += (settings.current.convergence / 100 - blend) * .06;
      rotation.x += (pointer.x * .6 - rotation.x) * .04;
      rotation.y += (pointer.y * .4 - rotation.y) * .04;
      ctx.clearRect(0, 0, width, height);
      const scale = Math.min(width, height) * .23;
      const points: { x: number; y: number; z: number; band: number; i: number }[] = [];
      for (let band = 0; band < 14; band++) {
        for (let i = 0; i < 170; i++) {
          const a = i / 170 * Math.PI * 2;
          const phase = band / 14 * Math.PI * 2;
          const radius = 1.1 + .44 * Math.cos(3 * a + phase + time);
          const chaos = (1 - blend) * .52;
          let x = radius * Math.cos(2 * a + time * .2) + Math.sin(a * 7 + phase) * chaos;
          let y = radius * Math.sin(2 * a + time * .2) + Math.cos(a * 5 + phase) * chaos;
          let z = .65 * Math.sin(3 * a + phase + time) + Math.sin(a * 4 + phase) * chaos;
          const yaw = time * .24 + rotation.x;
          const nx = x * Math.cos(yaw) + z * Math.sin(yaw);
          z = -x * Math.sin(yaw) + z * Math.cos(yaw); x = nx;
          const ny = y * Math.cos(.45 + rotation.y) - z * Math.sin(.45 + rotation.y);
          z = y * Math.sin(.45 + rotation.y) + z * Math.cos(.45 + rotation.y); y = ny;
          const perspective = 4 / (4 + z);
          points.push({ x: width / 2 + x * scale * perspective, y: height / 2 + y * scale * perspective, z, band, i });
        }
      }
      points.sort((a, b) => b.z - a.z);
      for (const point of points) {
        const light = Math.max(.15, Math.min(1, (1.8 - point.z) / 3));
        ctx.fillStyle = `rgba(235,235,235,${light})`;
        if (point.band === 3 && point.i % 43 === 0) ctx.fillStyle = '#ba84ff';
        ctx.beginPath(); ctx.arc(point.x, point.y, Math.max(.5, 1.55 - point.z * .45), 0, Math.PI * 2); ctx.fill();
      }
    };
    frame = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(frame); resize.disconnect(); observer.disconnect(); element.removeEventListener('pointermove', move); element.removeEventListener('pointerleave', leave); };
  }, []);
  return <canvas ref={canvas} className="tr-sculpture" role="img" aria-label="Interactive particle knot: move the pointer to rotate, and change quality tolerance to reorganize its paths." />;
}
