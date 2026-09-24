import type { Config } from "tailwindcss";
import plugin from "tailwindcss/plugin";

/**
 * Colours are declared as CSS custom properties in globals.css so light and dark
 * swap in one place; Tailwind only names the roles.
 *
 * A bare `var(--x)` cannot take an opacity modifier, and Tailwind drops such a
 * class without a word: `bg-critical/10`, `border-critical/40`, `bg-surface/95`
 * simply did not exist, so alert tints were missing, their borders fell back to
 * white, and the phone tab bar had no background. Each role is therefore a
 * colour-mix of the variable with transparent, at whatever opacity the class asks for.
 */
const role = (cssVar: string) =>
  `color-mix(in srgb, var(${cssVar}) calc(<alpha-value> * 100%), transparent)`;
const config: Config = {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: role("--surface-1"),
        "surface-raised": role("--surface-2"),
        plane: role("--plane"),
        ink: role("--text-primary"),
        "ink-secondary": role("--text-secondary"),
        "ink-muted": role("--text-muted"),
        hairline: role("--hairline"),
        grid: role("--gridline"),
        good: role("--status-good"),
        warning: role("--status-warning"),
        serious: role("--status-serious"),
        critical: role("--status-critical"),
        series: role("--series-1"),
        profit: role("--profit"),
        loss: role("--loss"),
        brand: role("--brand"),
        "brand-bright": role("--brand-bright"),
        "brand-dim": role("--brand-dim"),
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
