import type { Config } from "tailwindcss";
import plugin from "tailwindcss/plugin";

/**
 * Colours are declared as CSS custom properties in globals.css so light and dark
 * swap in one place; Tailwind only names the roles.
 */
const config: Config = {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "var(--surface-1)",
        "surface-raised": "var(--surface-2)",
        plane: "var(--plane)",
        ink: "var(--text-primary)",
        "ink-secondary": "var(--text-secondary)",
        "ink-muted": "var(--text-muted)",
        hairline: "var(--hairline)",
        grid: "var(--gridline)",
        good: "var(--status-good)",
        warning: "var(--status-warning)",
        serious: "var(--status-serious)",
        critical: "var(--status-critical)",
        series: "var(--series-1)",
        profit: "var(--profit)",
        loss: "var(--loss)",
        brand: "var(--brand)",
        "brand-bright": "var(--brand-bright)",
        "brand-dim": "var(--brand-dim)",
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
    },
  },
  plugins: [
    // `touch:` applies on coarse pointers — phones and tablets — regardless of
    // width, so a landscape phone still gets finger-sized targets and a narrow
    // desktop window does not get oversized ones.
    plugin(({ addVariant }) => {
      addVariant("touch", "@media (pointer: coarse)");
    }),
  ],
};

export default config;
