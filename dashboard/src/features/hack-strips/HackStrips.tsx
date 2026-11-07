import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import type { Attack } from "../../shared/types";
import { color, font, layout, motion as m, size, space } from "../../shared/theme";
import { Strip } from "./Strip";

const fmt = (n: number) => n.toLocaleString("en");

function Count({ n, of, bad }: { n: number; of: number; bad: boolean }) {
  return (
    <span className="num" style={{ color: bad && n > 0 ? color.fooled : color.held, fontWeight: 700, fontSize: size.sm }}>
      {fmt(n)} <span style={{ color: color.faint, fontWeight: 400 }}>/ {fmt(of)}</span>
    </span>
  );
}

function Row({ a, i, open, onToggle }: { a: Attack; i: number; open: boolean; onToggle: () => void }) {
  const delay = i * m.stagger;
  return (
    <li style={{ borderTop: `1px solid ${color.rule}` }}>
      <button className="row" aria-expanded={open} onClick={onToggle}>
        <span className="row-label">
          <span style={{ fontWeight: 700 }}>{a.name.replaceAll("_", " ")}</span>
          {a.set === "heldout" && <span style={{ color: color.muted, fontSize: size.xs }}> held out</span>}
        </span>
        <span className="row-strips">
          <span className="strip-line">
            <Strip mask={a.ef_mask} lit={color.fooled} delay={delay} label={`eval-framework passed ${a.eval_framework_passed} of ${a.completions}`} />
            <Count n={a.eval_framework_passed} of={a.completions} bad />
          </span>
          <span className="strip-line">
            <Strip mask={a.ours_mask} lit={color.fooled} delay={delay + m.stagger / 2} label={`this verifier passed ${a.ours_passed} of ${a.completions}`} />
            <Count n={a.ours_passed} of={a.completions} bad />
          </span>
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div key="detail" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} transition={{ duration: m.open, ease: m.ease }} style={{ overflow: "hidden" }}>
            <div style={{ padding: `0 0 ${space[4]}px`, display: "grid", gap: space[3] }} className="detail">
              <p style={{ margin: 0, maxWidth: layout.measure }}>{a.description}</p>
              <p style={{ margin: 0, color: color.muted, fontSize: size.sm }}>
                Verifier outcomes: {Object.entries(a.ours_outcomes).map(([k, v]) => `${fmt(v)} ${k}`).join(", ")}
                {a.patched_passed !== null && `. With the upstream patch, eval-framework passes ${fmt(a.patched_passed)}.`}
              </p>
              <pre style={{ margin: 0, background: color.codeBg, color: color.codeInk, fontFamily: font.code, fontSize: size.xs,
                padding: space[4], borderRadius: 4, overflowX: "auto", lineHeight: 1.5 }}>
                <code>{`# ${a.example.task}\n${a.example.completion}`}</code>
              </pre>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}

export function HackStrips({ attacks }: { attacks: Attack[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!attacks.length) return null;
  return (
    <div>
      <div className="legend" aria-hidden>
        <span className="row-label" />
        <span style={{ color: color.muted, fontSize: size.xs }}>
          Top strip eval-framework, bottom strip this verifier. One tick per task; red where the hack was scored as a pass.
        </span>
      </div>
      <ol style={{ listStyle: "none", margin: 0, padding: 0, borderBottom: `1px solid ${color.rule}` }}>
        {attacks.map((a, i) => (
          <Row key={a.name} a={a} i={i} open={open === a.name} onToggle={() => setOpen(open === a.name ? null : a.name)} />
        ))}
      </ol>
    </div>
  );
}
