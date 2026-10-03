import { useEffect, useState } from "react";

const SEVERITY_CONFIG = {
  LOW: { angle: -68, color: "#2BC8A8", label: "LOW" },
  MEDIUM: { angle: -23, color: "#F2A93B", label: "MEDIUM" },
  HIGH: { angle: 23, color: "#E5484D", label: "HIGH" },
  CRITICAL: { angle: 68, color: "#B4183C", label: "CRITICAL" },
};

/**
 * A dashboard-style radial gauge. The needle sweeps from a resting position
 * to the angle matching the diagnosed severity, echoing a real tachometer /
 * temperature gauge — the signature visual motif of this app.
 */
export default function SeverityGauge({ severity = "MEDIUM", size = 108 }) {
  const config = SEVERITY_CONFIG[String(severity).toUpperCase()] ?? SEVERITY_CONFIG.MEDIUM;
  const [angle, setAngle] = useState(-90);

  useEffect(() => {
    const t = setTimeout(() => setAngle(config.angle), 60);
    return () => clearTimeout(t);
  }, [config.angle]);

  const cx = 100;
  const cy = 100;
  const r = 78;

  return (
    <div className="relative shrink-0" style={{ width: size, height: size * 0.62 }}>
      <svg viewBox="0 0 200 124" className="w-full h-full overflow-visible">
        {/* zone arcs */}
        <path d="M 22 100 A 78 78 0 0 1 79 27" fill="none" stroke="#2BC8A8" strokeWidth="10" strokeLinecap="round" opacity="0.85" />
        <path d="M 82 24 A 78 78 0 0 1 118 24" fill="none" stroke="#F2A93B" strokeWidth="10" strokeLinecap="round" opacity="0.85" />
        <path d="M 121 27 A 78 78 0 0 1 178 100" fill="none" stroke="#E5484D" strokeWidth="10" strokeLinecap="round" opacity="0.85" />

        {/* tick marks */}
        {[-90, -45, 0, 45, 90].map((deg) => {
          const rad = (deg - 90) * (Math.PI / 180);
          const x1 = cx + (r + 10) * Math.cos(rad);
          const y1 = cy + (r + 10) * Math.sin(rad);
          const x2 = cx + (r + 18) * Math.cos(rad);
          const y2 = cy + (r + 18) * Math.sin(rad);
          return (
            <line key={deg} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#576081" strokeWidth="2" />
          );
        })}

        {/* needle */}
        <g
          style={{
            transformOrigin: "100px 100px",
            transform: `rotate(${angle}deg)`,
            transition: "transform 0.85s cubic-bezier(0.22,1,0.36,1)",
          }}
        >
          <line x1={cx} y1={cy} x2={cx} y2={cy - r + 14} stroke={config.color} strokeWidth="4" strokeLinecap="round" />
        </g>
        <circle cx={cx} cy={cy} r="7" fill="#0B1120" stroke={config.color} strokeWidth="3" />
      </svg>

      <div className="absolute inset-x-0 bottom-0 text-center">
        <span
          className="font-display text-[11px] font-bold tracking-[0.25em]"
          style={{ color: config.color }}
        >
          {config.label}
        </span>
      </div>
    </div>
  );
}
