import { color, size, space } from "./theme";

export function Skeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading results" style={{ display: "grid", gap: space[3] }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ height: space[5], width: `${92 - (i % 3) * 14}%` }} />
      ))}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" style={{ borderLeft: `3px solid ${color.fooled}`, padding: `${space[3]}px ${space[4]}px` }}>
      <p style={{ margin: 0, fontWeight: 700 }}>The results did not load.</p>
      <p style={{ margin: `${space[1]}px 0 ${space[3]}px`, color: color.muted, fontSize: size.sm }}>
        {message}. Run <code>python bench/report.py</code> to write dashboard/public/results.json.
      </p>
      <button className="btn" onClick={onRetry}>
        Load again
      </button>
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p style={{ color: color.muted, margin: 0 }}>{children}</p>;
}
