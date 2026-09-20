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
    <footer className="mt-10 border-t border-hairline text-sm text-muted">
      <div className="mx-auto grid max-w-[1400px] grid-cols-1 gap-7 px-4 py-8 sm:grid-cols-2 lg:grid-cols-[minmax(0,1.4fr)_repeat(3,minmax(0,1fr))]">
        <div className="flex min-w-0 flex-col gap-3 sm:col-span-2 lg:col-span-1">
          <Logo className="h-8" />
          <p className="max-w-[36ch] text-ink-2">
            <b className="text-ink">PataSetu</b> — address resolution for India. Messy address in; structured fields, a coordinate, a DIGIPIN and a calibrated confidence out. Every outbound message authorised by Cedar first.
          </p>
          <div className="flex flex-wrap gap-1.5">
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
      <div className="mx-auto flex max-w-[1400px] flex-wrap justify-between gap-x-4 gap-y-1 border-t border-hairline px-4 py-4 text-2xs">
        <span>First Commit · Bharat Builds Tour (WeMakeDevs × AWS) · September 2026 · ap-south-1</span>
        <span>DIGIPIN is India Post's open addressing grid · Map data © OpenStreetMap contributors</span>
      </div>
    </footer>
  );
}

function FootCol({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <h4 className="eyebrow mb-2.5">{title}</h4>
      <ul className="grid gap-1.5 [&_a]:text-ink-2 [&_a:hover]:text-ink [&_a:hover]:underline [&_a:hover]:underline-offset-4">{children}</ul>
    </div>
  );
}
