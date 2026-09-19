import { NavLink, Route, Routes } from "react-router-dom";
import Playground from "./pages/Playground";
import ReviewQueue from "./pages/ReviewQueue";
import Guardrails from "./pages/Guardrails";

const link = ({ isActive }: { isActive: boolean }) =>
  `rounded-lg px-3 py-1.5 text-sm font-medium transition ${isActive ? "bg-ink text-white" : "text-gray-700 hover:bg-gray-200"}`;

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-gray-200 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-3">
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-bold tracking-tight">PataSetu</span>
            <span className="text-xs text-gray-500">address resolution for India</span>
          </div>
          <nav className="flex gap-1">
            <NavLink to="/" end className={link}>Playground</NavLink>
            <NavLink to="/queue" className={link}>Review queue</NavLink>
            <NavLink to="/guardrails" className={link}>Guardrails</NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Playground />} />
          <Route path="/queue" element={<ReviewQueue />} />
          <Route path="/guardrails" element={<Guardrails />} />
        </Routes>
      </main>
    </div>
  );
}
