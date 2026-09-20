/**
 * The footer: what this is, where the rest lives. Same content as the
 * landing page's footer so the two read as one product.
 */

import { Link } from "react-router-dom";
import { Logo } from "./ui";

const REPO = "https://github.com/ShashankVaish/address-simplfy";
const LANDING = "https://main.d16dqxnda3ut4f.amplifyapp.com/";

const STACK = ["Lambda", "Bedrock", "OpenSearch", "DynamoDB", "EventBridge", "Cedar", "Cognito"];

export function SiteFooter() {
  return (
    <footer className="mt-14 border-t border-hairline bg-surface text-base text-muted">
      <div className="mx-auto grid max-w-[1400px] grid-cols-1 gap-9 px-4 py-14 sm:grid-cols-2 sm:gap-x-8 lg:grid-cols-[minmax(0,1.5fr)_repeat(3,minmax(0,1fr))] lg:gap-x-12">
        <div className="flex min-w-0 flex-col items-start gap-4 sm:col-span-2 lg:col-span-1 lg:pr-4">
          <div className="flex items-center gap-3.5">
            <Logo className="h-12 self-center" />
            <div>
              <div className="text-lg font-extrabold tracking-tight text-ink">PataSetu</div>
              <div className="text-xs text-muted">address resolution for India</div>
            </div>
          </div>
          <p className="max-w-[40ch] leading-relaxed text-ink-2">Messy address in; structured fields, a coordinate, a DIGIPIN and a calibrated confidence out. Every outbound message authorised by Cedar first. Built and measured in four days.</p>
          <div className="flex flex-wrap gap-2">
            {STACK.map((s) => (
              <span key={s} className="pill border-leaf/40 bg-leaf/10 text-leaf">
                {s}
              </span>
            ))}
          </div>
        </div>
        <FootCol title="Console">
          <li>
            <Link to="/">Playground</Link>
          </li>
          <li>
            <Link to="/queue">Review queue</Link>
          </li>
          <li>
            <Link to="/guardrails">Guardrails</Link>
          </li>
        </FootCol>
        <FootCol title="About">
          <li>
            <a href={LANDING}>Website</a>
          </li>
          <li>
            <a href={`${LANDING}#how`}>How it works</a>
          </li>
          <li>
            <a href={`${LANDING}#results`}>Measured results</a>
          </li>
        </FootCol>
        <FootCol title="Project">
          <li>
            <a href={REPO} target="_blank" rel="noopener">
              Source on GitHub ↗
            </a>
          </li>
          <li>
            <a href={`${REPO}/blob/shashank/ARCHITECTURE.md`} target="_blank" rel="noopener">
              Architecture
            </a>
          </li>
          <li>
            <a href={`${REPO}/blob/shashank/docs/learnings.md`} target="_blank" rel="noopener">
              What we learned
            </a>
          </li>
        </FootCol>
      </div>
      <div className="mx-auto flex max-w-[1400px] flex-wrap justify-between gap-x-6 gap-y-2 border-t border-hairline px-4 py-5 text-xs">
        <span>© 2026 PataSetu · First Commit, Bharat Builds Tour (WeMakeDevs × AWS) · deployed in ap-south-1</span>
        <span>DIGIPIN is India Post's open addressing grid · Map data © OpenStreetMap contributors</span>
      </div>
    </footer>
  );
}

function FootCol({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <h4 className="eyebrow mb-4 tracking-[0.1em]">{title}</h4>
      <ul className="grid gap-3 [&_a]:text-ink-2 [&_a]:transition-colors [&_a]:duration-fast [&_a:hover]:text-saffron">{children}</ul>
    </div>
  );
}
