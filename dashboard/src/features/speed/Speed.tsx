import { motion, useInView, useReducedMotion } from "framer-motion";
import { useRef } from "react";
import type { Results } from "../../shared/types";
import { color, layout, motion as m, size, space } from "../../shared/theme";

export function Speed({ rates, lanes }: { rates: Results["throughput"]; lanes: Results["lanes"] }) {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref, { once: true, margin: "-80px" });
  const reduce = useReducedMotion();
  const max = Math.max(...rates.map((r) => r.per_s));
  return (
    <div ref={ref} style={{ display: "grid", gap: space[6] }}>
      <div style={{ display: "grid", gap: space[4] }}>
        {rates.map((r, i) => (
          <div key={r.label} className="bar-row">
            <div style={{ fontSize: size.sm }}>
              <div style={{ fontWeight: 700 }}>{r.label}</div>
              <div style={{ color: color.muted, fontSize: size.xs }}>{r.note}</div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: space[3] }}>
              <div style={{ flex: 1, height: 10, background: color.tickOff, borderRadius: 2, overflow: "hidden" }}>
                <motion.div initial={{ width: 0 }} animate={{ width: seen || reduce ? `${(r.per_s / max) * 100}%` : 0 }}
                  transition={{ duration: reduce ? 0 : m.reveal * 0.8, delay: reduce ? 0 : i * m.stagger, ease: m.ease }}
                  style={{ height: "100%", background: i >= rates.length - 2 ? color.held : color.muted }} />
              </div>
              <span className="num" style={{ width: layout.countWidth, textAlign: "right", fontWeight: 700 }}>
                {r.per_s.toLocaleString("en")}/s
              </span>
            </div>
          </div>
        ))}
      </div>
      <p style={{ margin: 0, maxWidth: layout.measure }}>
        During an RL burst of {lanes.burst.toLocaleString("en")} completions, a single eval request waits{" "}
        <b className="num">{(lanes.shared_p99_ms / 1000).toFixed(1)} s</b> (p99) when everything shares one queue, and{" "}
        <b className="num">{lanes.separate_p99_ms.toLocaleString("en")} ms</b> with its own lane (weights rl 3 : eval 1).
      </p>
    </div>
  );
}
