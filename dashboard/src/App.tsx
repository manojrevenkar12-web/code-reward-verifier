import { motion, useReducedMotion } from "framer-motion";
import { Agreement } from "./features/agreement/Agreement";
import { HackStrips } from "./features/hack-strips/HackStrips";
import { Speed } from "./features/speed/Speed";
import { Upstream } from "./features/upstream/Upstream";
import { ErrorState, Skeleton } from "./shared/States";
import { color, layout, motion as m, size, space } from "./shared/theme";
import { useResults } from "./shared/useResults";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginTop: space[8] }}>
      <h2 style={{ fontSize: size.md, margin: `0 0 ${space[4]}px` }}>{title}</h2>
      {children}
    </section>
  );
}

export function App() {
  const r = useResults();
  const reduce = useReducedMotion();
  const d = r.state === "ready" ? r.data : null;
  const efFooled = d ? d.attacks.reduce((s, a) => s + a.eval_framework_passed, 0) : 0;
  const total = d ? d.attacks.reduce((s, a) => s + a.completions, 0) : 0;
  const oursFooled = d ? d.attacks.reduce((s, a) => s + a.ours_passed, 0) : 0;
  return (
    <main style={{ maxWidth: layout.maxWidth, margin: "0 auto", padding: `${space[7]}px ${space[4]}px ${space[8]}px` }}>
      <header style={{ maxWidth: layout.measure }}>
        <p style={{ margin: 0, color: color.muted, fontSize: size.sm }}>code-reward-verifier</p>
        <motion.h1 initial={{ opacity: reduce ? 1 : 0, y: reduce ? 0 : 8 }} animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: m.ease }}
          style={{ fontSize: size.xxl, lineHeight: 1.05, letterSpacing: -0.6, margin: `${space[2]}px 0 ${space[4]}px` }}>
          A completion that prints <code className="inline">True</code> should not earn a reward.
        </motion.h1>
        <p style={{ fontSize: size.md, color: color.muted, margin: 0, lineHeight: 1.4 }}>
          {d ? (
            <>
              Across {d.attacks.length} reward hacks on {(total / d.attacks.length).toLocaleString("en")} HumanEval and MBPP
              tasks, eval-framework scored <span style={{ color: color.fooled, fontWeight: 700 }}>{efFooled.toLocaleString("en")}</span> of{" "}
              {total.toLocaleString("en")} as passes. This verifier scored{" "}
              <span style={{ color: oursFooled ? color.fooled : color.held, fontWeight: 700 }}>{oursFooled.toLocaleString("en")}</span>.
            </>
          ) : (
            "Code-execution verdicts for RL rewards and coding evals, measured against eval-framework."
          )}
        </p>
      </header>

      <section style={{ marginTop: space[7] }}>
        {r.state === "loading" && <Skeleton rows={10} />}
        {r.state === "error" && <ErrorState message={r.message} onRetry={r.retry} />}
        {d && <HackStrips attacks={d.attacks} />}
      </section>

      {d && (
        <>
          <Section title="Honest completions still pass">
            <Agreement h={d.honest} eq={d.equivalence} />
          </Section>
          <Section title="Fast enough for RL rollouts">
            <Speed rates={d.throughput} lanes={d.lanes} />
          </Section>
          <Section title="A fix for eval-framework itself">
            <Upstream u={d.upstream} />
          </Section>
          <footer style={{ marginTop: space[8], color: color.faint, fontSize: size.xs, maxWidth: layout.measure }}>
            Generated {d.generated_at} at commit {d.commit} on {d.host}. {d.tests} tests.{" "}
            {d.heldout && `Held-out attacks pinned by hash ${d.heldout.sha256.slice(0, 12)} at ${d.heldout.pinned_at} and run once.`}
          </footer>
        </>
      )}
    </main>
  );
}
