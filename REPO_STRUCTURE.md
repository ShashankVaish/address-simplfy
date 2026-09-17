# Repository Structure — PataSetu

A monorepo with three top-level concerns: `backend/` (the engine), `frontend/` (two apps), and `docs/` (what judges read). Everything else is tooling.

---

## Top level

```
patasetu/
├── README.md                    # Entry point. Judges read this first
├── REQUIREMENTS.md              # Functional + non-functional + acceptance criteria
├── ARCHITECTURE.md              # Deep design — pipeline, data model, service map
├── REPO_STRUCTURE.md            # This file
├── TASKS.md                     # Four-day board, owners, gates, kill list
├── LICENSE                      # MIT
├── .gitignore
├── .env.example                 # Never commit a real .env
├── docker-compose.yml           # Local OpenSearch + LocalStack
│
├── backend/                     # Python 3.12 · AWS SAM
├── frontend/                    # React + Vite + TypeScript
├── docs/                        # Diagrams, blog draft, demo script
└── .github/workflows/           # CI: lint, test, deploy
```

---

## `backend/`

```
backend/
├── template.yaml                # SAM — ALL infrastructure. Nothing console-clicked
├── samconfig.toml
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml               # ruff + pytest config
├── env.json                     # sam local env vars (gitignored)
│
├── layers/
│   └── common/
│       └── python/patasetu/
│           ├── __init__.py
│           ├── providers.py     # ★ cloud vs local switch — WRITE THIS DAY 1
│           ├── config.py        # env vars, thresholds, model ids in ONE place
│           ├── models.py        # Pydantic: Address, Landmark, Resolution, Evidence
│           │
│           ├── digipin.py       # ★ vendored official encoder. DO NOT hand-roll
│           ├── normalize.py     # S0 — unicode fold, translit, abbreviations
│           ├── parse.py         # S1 — regex, gazetteer, phone stripping
│           ├── retrieve.py      # S2 — OpenSearch hybrid query + RRF fusion
│           ├── structure.py     # S3 — Bedrock, constrained JSON, model cascade
│           ├── geocode.py       # S4 — graph → Location Service → centroid
│           ├── confidence.py    # S6 — feature blend + isotonic calibration
│           ├── decide.py        # S7 — RESOLVED / NEEDS_INFO / AMBIGUOUS
│           │
│           ├── store.py         # DynamoDB — all five access patterns
│           ├── search.py        # OpenSearch client, index mgmt, upserts
│           ├── events.py        # EventBridge emit helpers
│           ├── cache.py         # three-level cache (exact, near-dup, landmark)
│           ├── logging.py       # structured JSON logs + correlation_id
│           └── metrics.py       # CloudWatch custom metric helpers
│
├── functions/
│   ├── resolver/                # POST /v1/resolve — THE HOT PATH
│   │   ├── app.py
│   │   └── requirements.txt
│   ├── clarifier/               # Strands agent — question generation
│   │   ├── app.py
│   │   ├── agent.py             # Strands Agent definition + tools
│   │   └── prompts/
│   │       ├── structure.txt    # the five rules (see ARCHITECTURE §S3)
│   │       ├── question.txt
│   │       └── voice_extract.txt
│   ├── authorizer/              # Cedar decision endpoint
│   │   ├── app.py
│   │   └── policies/
│   │       ├── contact.cedar
│   │       ├── overwrite.cedar
│   │       └── entities.json
│   ├── learner/                 # graph write-back, voice-note extraction
│   │   └── app.py
│   ├── ingest/                  # bulk CSV from S3
│   │   └── app.py
│   └── queue_api/               # GET /v1/queue, POST /v1/feedback
│       └── app.py
│
├── statemachines/
│   └── clarification.asl.json   # Step Functions definition
│
├── scripts/
│   ├── resolve_one.py           # CLI: run the pipeline on one string, no cloud
│   ├── load_pincodes.py         # seed the gazetteer into DynamoDB
│   ├── warm_landmarks.py        # build the landmark index from the corpus
│   ├── create_index.py          # OpenSearch index with knn + geo_point mapping
│   └── teardown.sh              # ★ kill OpenSearch Serverless overnight
│
├── eval/
│   ├── corpus_gen.py            # perturb seeds → 2,000 addresses
│   ├── label_tool.py            # tiny CLI for hand-labelling the gold set
│   ├── run_ablation.py          # configurations A–E on the test split
│   ├── reliability.py           # calibration fit + reliability diagram
│   ├── learning_curve.py        # resolution rate vs addresses seen
│   ├── metrics.py               # F1, exact match, haversine, precision@threshold
│   └── data/
│       ├── seed_addresses.jsonl # ~120 consented real addresses (anonymised)
│       ├── corpus.jsonl         # ~2,000 generated
│       ├── gold_dev.jsonl       # 150 — iterate against this
│       └── gold_test.jsonl      # 150 — TOUCH ONCE, AT THE END
│
├── data/
│   ├── pincodes.csv             # pincode → locality, district, state, centroid
│   ├── abbreviations.json       # ~120 entries
│   └── gazetteer.json           # cities, states, aliases
│
└── tests/
    ├── test_digipin.py          # ★ known (lat,lng) → code pairs. Day 1
    ├── test_normalize.py
    ├── test_parse.py
    ├── test_rrf.py
    ├── test_confidence.py
    ├── test_cedar_policies.py   # quiet hours, opt-out, second-message
    └── fixtures/
```

### The three files marked ★

| File | Why it matters |
|---|---|
| `providers.py` | Written Day 1, it makes the Build It fallback a config change instead of a rewrite. Written Day 3, it is a rewrite |
| `digipin.py` + `test_digipin.py` | The spec changed some alphabet characters after the beta. A hand-rolled encoder fails silently and invisibly — and DIGIPIN is the centrepiece of the demo |
| `scripts/teardown.sh` | OpenSearch Serverless bills on provisioned capacity by the hour, idle or not. This script is the difference between finishing the weekend with credits and without |

---

## `frontend/`

```
frontend/
├── console/                     # Ops console (desktop) — the Best UI entry
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── package.json
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/
│       │   ├── client.ts        # typed fetch wrapper
│       │   ├── types.ts         # ★ generated from the frozen contract
│       │   └── mock.ts          # ★ lets D build before the backend exists
│       ├── pages/
│       │   ├── Playground.tsx   # ★ BUILD FIRST — the video opens here
│       │   ├── ReviewQueue.tsx
│       │   ├── MapView.tsx
│       │   ├── LandmarkExplorer.tsx
│       │   └── Metrics.tsx
│       ├── components/
│       │   ├── AddressInput.tsx
│       │   ├── SpanHighlighter.tsx   # colour raw text by resolved field
│       │   ├── StructuredPanel.tsx
│       │   ├── ConfidenceBar.tsx
│       │   ├── EvidenceList.tsx
│       │   ├── DigipinBadge.tsx
│       │   ├── MapPin.tsx           # MapLibre GL
│       │   ├── QueueRow.tsx
│       │   ├── AblationTable.tsx
│       │   └── LearningCurve.tsx    # Recharts
│       ├── hooks/
│       │   ├── useResolve.ts
│       │   └── useQueue.ts
│       └── styles/
│
├── rider/                       # Rider PWA (mobile) — deliberately tiny
│   ├── manifest.webmanifest
│   ├── public/sw.js             # service worker, offline reads
│   └── src/
│       ├── App.tsx
│       ├── components/
│       │   ├── AddressCard.tsx      # pin + DIGIPIN + access notes
│       │   ├── MicButton.tsx        # ★ hold to record → presigned S3 upload
│       │   └── OutcomeButtons.tsx   # Delivered / Couldn't find
│       └── lib/digipin.ts           # client-side encoder, works offline
│
└── shared/
    └── contract.ts              # ★ the frozen /v1/resolve response shape
```

### The frontend contract

`shared/contract.ts` is **frozen on Thursday morning** and imported by both apps and by `mock.ts`. This is the single decision that lets four people work in parallel instead of blocking each other.

---

## `docs/`

```
docs/
├── architecture.png             # exported diagram for README + video
├── pipeline.png                 # S0–S7 flow
├── demo_script.md               # second-by-second, 3 minutes
├── blog_draft.md                # → publish on AWS Builder Center
├── learnings.md                 # ★ scored by judges — write it as you go
└── results/
    ├── ablation.md              # configurations A–E, filled Day 4
    ├── reliability.png          # calibration diagram
    └── learning_curve.png
```

`learnings.md` is not an afterthought. The judging criteria explicitly reward what you learned across the four days, so keep a running note from Thursday rather than reconstructing it on Sunday.

---

## `.github/workflows/`

```
.github/workflows/
├── test.yml                     # ruff + pytest on every push
└── deploy.yml                   # sam build && sam deploy on main
```

Keep CI trivial. A broken pipeline on Saturday is a distraction you cannot afford.

---

## Ownership map

| Person | Owns | Primary paths |
|---|---|---|
| **A** | Pipeline S0–S3 | `layers/common/{normalize,parse,retrieve,structure}.py`, `functions/resolver/`, `prompts/` |
| **B** | Data + infra | `template.yaml`, `layers/common/{store,search,geocode,digipin}.py`, `scripts/`, `data/` |
| **C** | Agents + policy | `functions/{clarifier,authorizer,learner}/`, `statemachines/`, `layers/common/confidence.py`, `eval/` |
| **D** | Frontend | `frontend/` entirely, `docs/demo_script.md` |

Interfaces between them: `shared/contract.ts` (D ↔ everyone), `providers.py` (B → A and C), `models.py` (shared Pydantic types).

---

## Conventions

| Convention | Rule |
|---|---|
| Branches | `main` always deployable. Feature branches `<initial>/<short-desc>`, e.g. `a/rrf-fusion` |
| Commits | Imperative mood, one concern each. `feat:`, `fix:`, `docs:`, `test:`, `chore:` |
| Python | `ruff` for lint and format. Type hints on every public function |
| TypeScript | `strict: true`. No `any` in `contract.ts` |
| Secrets | Never in the repo. `.env.example` documents the shape; real values stay local or in SSM |
| Tests | Every pure function in `layers/common/` has one. Lambda handlers tested through `resolve_one.py` |
| Logs | JSON only, with `correlation_id`. No bare `print()` |

---

## What must never be committed

```
.env
env.json
backend/data/*_raw.csv           # un-anonymised seed addresses
frontend/*/.env.local
*.pem, *.key
__pycache__/, .venv/, node_modules/, dist/, .aws-sam/
```

The raw seed addresses come from friends and family with consent. **Anonymised versions only** go in `eval/data/seed_addresses.jsonl` — names stripped, phone numbers removed, house numbers perturbed where they are identifying.
