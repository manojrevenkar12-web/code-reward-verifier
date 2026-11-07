// Every colour, size and space the dashboard uses. Components read these, never literals.
export const color = {
  paper: "#F2F4F6",
  surface: "#FFFFFF",
  ink: "#111C27",
  muted: "#5A6876",
  faint: "#8A97A3",
  rule: "#D5DCE3",
  fooled: "#E5484D", // a hack scored as a pass
  held: "#2F7D6B", // correctly rejected / correct verdict
  tickOff: "#E3E8ED",
  focus: "#2D5BD7",
  codeBg: "#0F1720",
  codeInk: "#D6E2EE",
} as const;

export const font = {
  text: "'Atkinson Hyperlegible', system-ui, sans-serif",
  code: "'JetBrains Mono', ui-monospace, monospace",
} as const;

// Perfect fourth from 15 px.
export const size = { xs: 12, sm: 13.5, base: 15, md: 20, lg: 26.7, xl: 35.5, xxl: 47.4 } as const;
export const space = { 1: 4, 2: 8, 3: 12, 4: 16, 5: 24, 6: 32, 7: 48, 8: 72 } as const;
export const layout = { maxWidth: 1080, stripHeight: 18, labelWidth: 260, countWidth: 92, measure: 680 } as const;
export const motion = { reveal: 1.1, stagger: 0.06, ease: [0.22, 0.9, 0.3, 1] as const, open: 0.22 } as const;
export const breakpoint = { narrow: 760 } as const;

export function cssVariables(): string {
  return `:root{${Object.entries(color)
    .map(([k, v]) => `--${k}:${v};`)
    .join("")}}`;
}
