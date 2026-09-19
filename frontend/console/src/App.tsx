/**
 * The shell: brand, three routes, an environment chip that says what this
 * console is talking to, and the theme switch. Nothing else lives up here.
 */

import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { health, isMock } from "./api/client";
import type { HealthResponse } from "./api/types";
import { Icon } from "./components/ui";
import { useTheme } from "./hooks/useTheme";
import Guardrails from "./pages/Guardrails";
import Playground from "./pages/Playground";
import ReviewQueue from "./pages/ReviewQueue";

const link = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm font-medium transition duration-fast ease-out ${isActive ? "bg-ink text-ground" : "text-ink-2 hover:bg-raised hover:text-ink"}`;

export default function App() {
  const { theme, resolved, cycle } = useTheme();
  const [env, setEnv] = useState<HealthResponse | null>(null);
  const [envError, setEnvError] = useState(false);

  useEffect(() => {
    let alive = true;
    health()
      .then((h) => alive && setEnv(h))
      .catch(() => alive && setEnvError(true));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-toast focus:rounded-md focus:bg-ink focus:px-3 focus:py-1.5 focus:text-ground">
        Skip to content
      </a>
      <header className="sticky top-0 z-sticky border-b border-hairline bg-ground/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center gap-3 px-4 py-2.5 sm:gap-6">
          <a href="https://main.d16dqxnda3ut4f.amplifyapp.com/" className="flex items-baseline gap-2 no-underline" title="PataSetu — about the project">
            <span className="text-md font-extrabold tracking-tight text-ink">PataSetu</span>
            <span className="hidden text-xs text-muted sm:inline">address resolution for India</span>
          </a>
          <nav className="flex gap-1" aria-label="Screens">
            <NavLink to="/" end className={link}>
              Playground
            </NavLink>
            <NavLink to="/queue" className={link}>
              Review queue
            </NavLink>
            <NavLink to="/guardrails" className={link}>
              Guardrails
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <EnvChip env={env} error={envError} />
            <button
              type="button"
              onClick={cycle}
              className="btn-ghost !px-2"
              aria-label={`Theme: ${theme}. Switch theme`}
              title={theme === "system" ? `Following system (${resolved})` : `Forced ${theme}`}
            >
              <Icon name={theme === "system" ? "system" : resolved === "dark" ? "moon" : "sun"} />
            </button>
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-[1400px] px-4 py-5 sm:py-6">
        <Routes>
          <Route path="/" element={<Playground theme={resolved} />} />
          <Route path="/queue" element={<ReviewQueue />} />
          <Route path="/guardrails" element={<Guardrails />} />
        </Routes>
      </main>
    </div>
  );
}

/** What this console is talking to. Mock is said out loud; it is never hidden. */
function EnvChip({ env, error }: { env: HealthResponse | null; error: boolean }) {
  if (isMock) {
    return (
      <span className="pill border-saffron/40 bg-saffron/10 text-saffron" title="VITE_API_URL is unset: fixtures, not the backend">
        mock
      </span>
    );
  }
  if (error) {
    return (
      <span className="pill border-deny/40 bg-deny/10 text-deny" title="The API did not answer /health">
        api unreachable
      </span>
    );
  }
  if (!env) return <span className="bone h-5 w-24 rounded-full" aria-hidden="true" />;
  return (
    <span className="pill border-leaf/40 bg-leaf/10 text-leaf" title={`provider ${env.provider} · calibration ${env.calibration} · init ${env.init_ms} ms`}>
      <span className="h-1.5 w-1.5 rounded-full bg-leaf" aria-hidden="true" />
      live · stack {env.serving_stack}
    </span>
  );
}
