import type { Results } from "../../shared/types";
import { color, layout, size, space } from "../../shared/theme";

export function Upstream({ u }: { u: Results["upstream"] }) {
  return (
    <div style={{ display: "grid", gap: space[3], maxWidth: layout.measure }}>
      <p style={{ margin: 0 }}>
        A two-commit patch for eval-framework itself, tests first: {u.fixed} new tests that fail today and pass with it,{" "}
        {u.new_failures} existing tests broken. It closes {u.closes.map((s) => s.replaceAll("_", " ")).join(", ")}.
      </p>
      <p style={{ margin: 0, color: color.muted, fontSize: size.sm }}>
        Still open with the patch, because they need the tests outside the completion's process:{" "}
        {u.leaves_open.map((s) => s.replaceAll("_", " ")).join(", ")}.
      </p>
    </div>
  );
}
