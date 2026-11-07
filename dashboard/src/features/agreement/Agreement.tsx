import type { Results } from "../../shared/types";
import { Empty } from "../../shared/States";
import { color, layout, size, space } from "../../shared/theme";

export function Agreement({ h, eq }: { h: Results["honest"]; eq: Results["equivalence"] }) {
  const pct = (x: number) => `${(x * 100).toFixed(2)}%`;
  return (
    <div style={{ display: "grid", gap: space[4], maxWidth: layout.measure }}>
      <p style={{ margin: 0 }}>
        On {h.completions.toLocaleString("en")} honest completions (the datasets' own solutions and mutants of them), the
        verifier agrees with eval-framework on <b className="num">{pct(h.agreement)}</b>. The first run agreed on{" "}
        <span className="num">{pct(h.first_run_agreement)}</span>; the gap was standard-library containers, built-in types as
        arguments and modules the tests borrow from the completion, all fixed. {pct(h.split_share)} of them ran with the tests
        outside the sandbox.
      </p>
      {h.disagreements.length === 0 ? (
        <Empty>No disagreements.</Empty>
      ) : (
        <table>
          <caption style={{ textAlign: "left", color: color.muted, fontSize: size.sm, paddingBottom: space[2] }}>
            Where they still differ
          </caption>
          <thead>
            <tr><th>Task</th><th>Completion</th><th>Why the verifier says no</th></tr>
          </thead>
          <tbody>
            {h.disagreements.map((d) => (
              <tr key={d.task + d.kind}><td className="num">{d.task}</td><td>{d.kind}</td><td>{d.reason}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      <p style={{ margin: 0, color: color.muted, fontSize: size.sm }}>
        eval-framework's scores in the sweeps come from its own classes run in a plain isolated process; on a random sample of{" "}
        {eq.sample} programs that runtime gave the same score as its real Docker path {eq.same} times.
      </p>
    </div>
  );
}
