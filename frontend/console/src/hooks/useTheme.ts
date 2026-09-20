/**
 * Theme: "system" (nothing stamped, prefers-color-scheme decides), or an
 * explicit "light" / "dark" on <html data-theme>. The choice is remembered per
 * browser; storage may be unavailable, and then the choice simply does not
 * survive a reload.
 */

import { useCallback, useEffect, useState } from "react";

export type Theme = "system" | "light" | "dark";
const KEY = "patasetu.theme";

function read(): Theme {
  try {
    const t = localStorage.getItem(KEY);
    return t === "dark" || t === "light" ? t : "system";
  } catch {
    return "system";
  }
}

function apply(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

/** The theme actually on screen, resolving "system". */
export function resolvedTheme(theme: Theme): "light" | "dark" {
  if (theme !== "system") return theme;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useTheme(): { theme: Theme; resolved: "light" | "dark"; cycle: () => void } {
  const [theme, setTheme] = useState<Theme>(read);
  const [resolved, setResolved] = useState<"light" | "dark">(() => resolvedTheme(read()));

  useEffect(() => {
    apply(theme);
    setResolved(resolvedTheme(theme));
    try {
      if (theme === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, theme);
    } catch {
      /* storage blocked: the choice lives for this page only */
    }
  }, [theme]);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setResolved(resolvedTheme(theme));
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  // One button, three states, in the order people expect: follow the system,
  // then force the opposite of what the system gave, then force it back.
  const cycle = useCallback(() => {
    setTheme((t) => (t === "system" ? (resolvedTheme("system") === "dark" ? "light" : "dark") : t === "dark" ? "light" : "dark"));
  }, []);

  return { theme, resolved, cycle };
}
