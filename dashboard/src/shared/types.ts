export interface Attack {
  name: string;
  set: "dev" | "heldout";
  description: string;
  completions: number;
  eval_framework_passed: number;
  patched_passed: number | null;
  ours_passed: number;
  ours_outcomes: Record<string, number>;
  // one entry per task, in dataset order: did each scorer give a pass?
  ef_mask: string; // "1"/"0" per task
  ours_mask: string;
  example: { task: string; completion: string };
}

export interface Rate {
  label: string;
  per_s: number;
  p50_ms: number | null;
  p99_ms: number | null;
  note: string;
}

export interface Results {
  generated_at: string;
  commit: string;
  host: string;
  tests: number;
  attacks: Attack[];
  honest: {
    completions: number;
    agree: number;
    agreement: number;
    first_run_agreement: number;
    disagreements: { task: string; kind: string; reason: string }[];
    split_share: number;
  };
  equivalence: { sample: number; same: number };
  throughput: Rate[];
  lanes: { shared_p99_ms: number; separate_p99_ms: number; shared_p50_ms: number; separate_p50_ms: number; burst: number };
  upstream: { fixed: number; new_failures: number; closes: string[]; leaves_open: string[] };
  heldout: { sha256: string; pinned_at: string } | null;
}
