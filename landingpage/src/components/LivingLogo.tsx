import { useEffect, useId, useState } from 'react';
import './living-logo.css';

export interface LivingLogoProps {
  className?: string;
  animated?: boolean;
  label?: string;
}

const strandPaths = [
  {
    d: 'M18 34 C39 34 48 34 59 42 C74 53 82 67 97 74 C113 81 132 81 157 81',
    drift: 'M18 34 C39 37 48 38 59 45 C74 57 82 71 97 78 C113 84 132 85 157 81',
  },
  {
    d: 'M18 58 C40 58 49 58 61 67 C74 77 80 89 92 94 C104 99 112 95 122 89 C133 83 142 81 157 81',
    drift: 'M18 58 C40 61 49 62 61 71 C74 81 80 93 92 98 C104 103 112 99 122 93 C133 87 142 84 157 81',
  },
  {
    d: 'M18 81 C40 81 49 81 60 71 C73 59 80 51 91 53 C103 55 110 67 121 74 C132 80 141 81 157 81',
    drift: 'M18 81 C40 84 49 84 60 74 C73 63 80 55 91 57 C103 59 110 71 121 78 C132 84 141 84 157 81',
  },
  {
    d: 'M18 104 C40 104 49 104 61 95 C74 85 80 75 91 71 C103 66 111 70 121 75 C133 81 142 81 157 81',
    drift: 'M18 104 C40 101 49 100 61 91 C74 81 80 71 91 67 C103 62 111 66 121 71 C133 77 142 78 157 81',
  },
  {
    d: 'M18 125 C41 125 50 124 62 115 C76 104 81 91 93 86 C105 80 113 79 123 80 C137 82 145 81 157 81',
    drift: 'M18 125 C41 122 50 120 62 111 C76 100 81 87 93 82 C105 76 113 75 123 76 C137 78 145 79 157 81',
  },
] as const;

function usePrefersReducedMotion() {
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    const updatePreference = () => setPrefersReducedMotion(mediaQuery.matches);

    updatePreference();
    mediaQuery.addEventListener?.('change', updatePreference);

    return () => mediaQuery.removeEventListener?.('change', updatePreference);
  }, []);

  return prefersReducedMotion;
}

export function LivingLogo({
  className = '',
  animated = true,
  label = 'TwineRun',
}: LivingLogoProps) {
  const reducedMotion = usePrefersReducedMotion();
  const rawId = useId();
  const id = rawId.replace(/[^a-zA-Z0-9_-]/g, '');
  const activeMotion = animated && !reducedMotion;
  const silverGradientId = `${id}-silver`;
  const nodeGradientId = `${id}-node`;
  const coreGradientId = `${id}-core`;
  const glowFilterId = `${id}-glow`;

  return (
    <svg
      viewBox="0 0 185 173"
      role="img"
      aria-label={label}
      data-animated={activeMotion ? 'true' : 'false'}
      className={`living-logo ${activeMotion ? 'is-active' : ''} ${className}`.trim()}
      xmlns="http://www.w3.org/2000/svg"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <linearGradient id={silverGradientId} x1="18" y1="34" x2="157" y2="81" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#aeb7c0" />
          <stop offset="0.22" stopColor="#f4f7f8" />
          <stop offset="0.48" stopColor="#7e8994" />
          <stop offset="0.7" stopColor="#eef2f4" />
          <stop offset="1" stopColor="#aeb8c1" />
        </linearGradient>
        <radialGradient id={nodeGradientId} cx="35%" cy="30%" r="75%">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.3" stopColor="#dfe5e9" />
          <stop offset="0.7" stopColor="#8d99a4" />
          <stop offset="1" stopColor="#56616c" />
        </radialGradient>
        <radialGradient id={coreGradientId} cx="43%" cy="40%" r="65%">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.24" stopColor="#f5f8fa" />
          <stop offset="0.58" stopColor="#b4c0c9" stopOpacity="0.95" />
          <stop offset="1" stopColor="#7e8b96" stopOpacity="0.05" />
        </radialGradient>
        <filter id={glowFilterId} x="-100%" y="-100%" width="300%" height="300%" colorInterpolationFilters="sRGB">
          <feGaussianBlur stdDeviation="4.5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <g className="living-logo__strands" fill="none" strokeLinecap="round" strokeLinejoin="round">
        {strandPaths.map(({ d, drift }, index) => (
          <g key={d}>
            <path
              className="living-logo__strand"
              d={d}
              pathLength="1"
              stroke={`url(#${silverGradientId})`}
              strokeWidth="6.2"
            >
              {activeMotion && (
                <animate
                  attributeName="d"
                  values={`${d};${drift};${d}`}
                  dur={`${7 + index * 0.55}s`}
                  begin={`${index * 0.18}s`}
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.45 0 0.55 1;0.45 0 0.55 1"
                />
              )}
            </path>
            <path
              className="living-logo__strand-edge"
              d={d}
              pathLength="1"
              stroke="#f4f7f8"
              strokeWidth="1.05"
              opacity="0.62"
            >
              {activeMotion && (
                <animate
                  attributeName="d"
                  values={`${d};${drift};${d}`}
                  dur={`${7 + index * 0.55}s`}
                  begin={`${index * 0.18}s`}
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.45 0 0.55 1;0.45 0 0.55 1"
                />
              )}
            </path>
            {activeMotion && (
              <path
                className="living-logo__glint"
                d={d}
                pathLength="1"
                stroke="#ffffff"
                strokeWidth="2.1"
                strokeDasharray="0.035 0.965"
                style={{ animationDelay: `${index * -0.7}s` }}
              >
                <animate
                  attributeName="d"
                  values={`${d};${drift};${d}`}
                  dur={`${7 + index * 0.55}s`}
                  begin={`${index * 0.18}s`}
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.45 0 0.55 1;0.45 0 0.55 1"
                />
              </path>
            )}
          </g>
        ))}
      </g>

      <g className="living-logo__nodes">
        {[34, 58, 81, 104, 125].map((cy) => (
          <circle key={cy} className="living-logo__node" cx="18" cy={cy} r="5.1" fill={`url(#${nodeGradientId})`} />
        ))}
        <circle className="living-logo__halo" cx="157" cy="81" r="12" fill={`url(#${coreGradientId})`} filter={`url(#${glowFilterId})`} />
        <circle className="living-logo__endpoint" cx="157" cy="81" r="5.9" fill="#f8fbfc" />
        <circle className="living-logo__endpoint-core" cx="155.4" cy="79.4" r="2.1" fill="#ffffff" opacity="0.92" />
      </g>
    </svg>
  );
}

export default LivingLogo;
