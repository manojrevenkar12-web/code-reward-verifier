import { motion, useReducedMotion } from "framer-motion";
import { useId, useMemo } from "react";
import { color, layout, motion as m } from "../../shared/theme";

/** One tick per task: lit when that scorer gave the hack a pass. Drawn as a single path per colour. */
export function Strip({ mask, lit, delay, label }: { mask: string; lit: string; delay: number; label: string }) {
  const reduce = useReducedMotion();
  const clip = useId();
  const n = mask.length;
  const h = layout.stripHeight;
  const { on, off } = useMemo(() => {
    let a = "";
    let b = "";
    for (let i = 0; i < n; i++) {
      const seg = `M${i} 0h0.72v${h}h-0.72Z`;
      if (mask[i] === "1") a += seg;
      else b += seg;
    }
    return { on: a, off: b };
  }, [mask, n, h]);
  return (
    <svg viewBox={`0 0 ${n} ${h}`} preserveAspectRatio="none" role="img" aria-label={label}
      style={{ width: "100%", height: h, display: "block" }}>
      <defs>
        <clipPath id={clip}>
          <motion.rect x={0} y={0} height={h}
            initial={{ width: reduce ? n : 0 }} animate={{ width: n }}
            transition={{ duration: reduce ? 0 : m.reveal, delay: reduce ? 0 : delay, ease: m.ease }} />
        </clipPath>
      </defs>
      <g clipPath={`url(#${clip})`}>
        <path d={off} fill={color.tickOff} />
        <path d={on} fill={lit} />
      </g>
    </svg>
  );
}
