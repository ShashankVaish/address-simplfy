/** @type {import('tailwindcss').Config} */

// Semantic colours read CSS custom properties (RGB triplets, see
// src/index.css) so the theme switches at runtime and utilities keep alpha:
// `bg-leaf/10` works. No hex lives in components.
const v = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ground: v("ground"),
        surface: v("surface"),
        raised: v("raised"),
        sunken: v("sunken"),
        ink: v("ink"),
        "ink-2": v("ink-2"),
        muted: v("muted"),
        hairline: v("hairline"),
        "hairline-2": v("hairline-2"),
        saffron: v("saffron"),
        leaf: v("leaf"),
        deny: v("deny"),
        allow: v("leaf"),
        "needs-info": v("saffron"),
        warning: v("warning"),
        "code-ink": v("code-ink"),
        "code-dim": v("code-dim"),
        "code-kw": v("code-kw"),
        "code-ty": v("code-ty"),
        "code-st": v("code-st"),
        "code-nu": v("code-nu"),
      },
      fontFamily: {
        sans: ["Inter", "Noto Sans Devanagari", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "Noto Sans Devanagari", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["11px", { lineHeight: "16px" }],
        xs: ["12px", { lineHeight: "16px" }],
        sm: ["13px", { lineHeight: "18px" }],
        base: ["14px", { lineHeight: "20px" }],
        md: ["16px", { lineHeight: "22px" }],
        lg: ["20px", { lineHeight: "26px" }],
        xl: ["25px", { lineHeight: "30px" }],
        "2xl": ["31px", { lineHeight: "36px" }],
        "3xl": ["39px", { lineHeight: "42px" }],
      },
      borderRadius: {
        sm: "4px",
        md: "8px",
        lg: "12px",
      },
      boxShadow: {
        lift: "var(--shadow-lift)",
        verdict: "var(--shadow-verdict)",
      },
      transitionDuration: {
        fast: "120ms",
        base: "180ms",
        slow: "260ms",
      },
      transitionTimingFunction: {
        out: "var(--ease-out)",
        settle: "var(--ease-settle)",
      },
      zIndex: {
        sticky: "10",
        overlay: "20",
        popover: "30",
        toast: "40",
      },
    },
  },
  plugins: [],
};
