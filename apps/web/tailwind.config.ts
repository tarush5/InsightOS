import type { Config } from "tailwindcss";

/**
 * Palette is fixed by the product brief (spec section 66). Accents are used
 * sparingly and carry meaning: cyan = active AI process, violet = model output,
 * amber/red = degraded confidence or critical alerts.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        base: "#08090C",
        surface: "#101218",
        elevated: "#161923",
        hairline: "#22252F",
        ink: { DEFAULT: "#E8EAF0", muted: "#9BA1B0", faint: "#5D6474" },
        cyan: { DEFAULT: "#22D3EE", dim: "#0E7490" },
        violet: { DEFAULT: "#8B5CF6", dim: "#5B34C4" },
        ok: "#34D399",
        warn: "#F59E0B",
        crit: "#F43F5E",
      },
      fontFamily: {
        display: ["var(--font-display)", "system-ui", "sans-serif"],
        sans: ["var(--font-body)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      borderRadius: { xl: "0.875rem", "2xl": "1.25rem" },
      keyframes: {
        scanline: { "0%": { transform: "translateY(-100%)" }, "100%": { transform: "translateY(400%)" } },
        pulseSoft: { "0%,100%": { opacity: "0.45" }, "50%": { opacity: "1" } },
        riseIn: { from: { opacity: "0", transform: "translateY(8px)" }, to: { opacity: "1", transform: "none" } },
      },
      animation: {
        scanline: "scanline 2.4s linear infinite",
        pulseSoft: "pulseSoft 2s ease-in-out infinite",
        riseIn: "riseIn 320ms cubic-bezier(0.22,1,0.36,1) both",
      },
    },
  },
  plugins: [],
};
export default config;
