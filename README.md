# PataSetu

**An address resolution engine for India.** Messy free-text address in → structured address, geocode, DIGIPIN, and a calibrated confidence score out. When it genuinely doesn't know, it asks the customer exactly one targeted question instead of guessing.

Built for **First Commit**, event 01 of the Bharat Builds Tour (WeMakeDevs × AWS), 17–20 September 2026.

---

## The problem

An Indian delivery address is usually not an address. It is a set of directions given to a human being.

```
"h no 14 behind shiv mandir near gupta general store opp water tank
 ramesh nagar delhi 110015 call before coming"
```

Three things follow, and all three cost money:

1. **The rider phones the customer.** Almost every order.
2. **The first delivery attempt fails.** Fuel, rider time, a second dispatch.
3. **The knowledge evaporates.** The rider eventually learns "gate 3, ask the guard, lift on the left" — and that dies in their head. The next rider starts from zero.

Landmark-based addressing isn't laziness. Large parts of India lack systematic street numbering. The system has to work *with* landmarks, not against them.

---

## What PataSetu does

```http
POST /v1/resolve
{
  "raw": "h no 14 behind shiv mandir near gupta store ramesh nagar delhi 110015",
  "hint": { "lat": 28.6519, "lng": 77.1200 }
}
```

```json
{
  "status": "RESOLVED",
  "confidence": 0.91,
  "structured": {
    "building": "H.No. 14",
    "sub_locality": "Ramesh Nagar",
    "city": "New Delhi",
    "state": "Delhi",
    "pincode": "110015",
    "landmarks": [
      { "name": "Shiv Mandir", "relation": "behind", "matched_id": "LMK#DL#4412" },
      { "name": "Gupta General Store", "relation": "near", "matched_id": null }
    ]
  },
  "geo": { "lat": 28.65213, "lng": 77.11987, "source": "landmark_graph" },
  "digipin": "39JJTT4565",
  "evidence": [
    "pincode 110015 matched locality 'Ramesh Nagar' (exact)",
    "landmark 'Shiv Mandir' matched LMK#DL#4412 at 63m (vector 0.89)"
  ],
  "clarification": null
}
```

---

## Why this is different from an "address cleaner"

### 1. DIGIPIN as the canonical output

DIGIPIN is India Post's open-source national addressing grid, built with IIT Hyderabad and ISRO's NRSC. It divides the country into roughly 4 m × 4 m cells and assigns each a unique 10-character alphanumeric code derived from its latitude and longitude. It was finalised in March 2025 as the foundation layer of India's addressing Digital Public Infrastructure.

> **Format note.** The canonical DIGIPIN is a **continuous 10-character string with no hyphens** — the official India Post encoder (`INDIAPOST-gov/digipin`, revision 2026-05-04) dropped the `XXX-XXX-XXXX` separators and its decoder now *rejects* them. We store and transmit `39JJTT4565`; the hyphenated `39J-JTT-4565` is a display-only form produced by `format_digipin`. Most third-party write-ups still show the old format.

Two engineering consequences:

- It is a **pure function of (lat, lng)**. No API call, no rate limit, no cost. Runs inside Lambda in microseconds, and offline on a rider's phone with no connectivity.
- It gives us a **deduplication key**. Ten differently-worded addresses that resolve to the same 4 m cell are provably the same doorstep.

### 2. The landmark graph learns

Every resolved address and every rider voice note writes back into a landmark index. The 200th address in a locality resolves far better than the 1st — and we measure and plot that curve.

### 3. We ask questions rarely, and precisely

Not "please confirm your address." Instead: *"Sunrise Apartments mil gaya. Flat number 4B hai — kaunsa floor aur kaunsa block?"* Question selection is driven by which field is missing and how much confidence it would recover.

### 4. Cedar guards every outbound message

An automated system that texts customers needs a hard boundary, and "we'll be careful" is not one. Every outreach is authorised by a Cedar policy before it is sent — quiet hours, one message per order, opt-out lists. Denials route to a human queue with the reason attached.

### 5. A real evaluation harness

Gold set, four metrics, reliability diagram, and an ablation table showing what each pipeline stage actually contributes. See [`eval/`](./eval) and [ARCHITECTURE.md § Evaluation](./ARCHITECTURE.md#12-evaluation-harness).

---

## Architecture at a glance

```
Client ──▶ API Gateway ──▶ Lambda: resolver ──▶ Response
                                 │
     ┌───────────────────────────┼───────────────────────────┐
     │                           │                           │
  S0 Normalise             S2 Retrieval                S4 Geocode
  S1 Parse                 (OpenSearch:               S5 DIGIPIN
  (regex, gazetteer,        BM25 + kNN + geo)         S6 Confidence
   cache probe)                  │                    S7 Decide
                            S3 Structure
                            (Bedrock: Nova Lite
                             ──▶ Claude on escalate)

Async:  EventBridge ──▶ Step Functions ──▶ Cedar ──▶ SNS
        Rider voice ──▶ Transcribe ──▶ Bedrock ──▶ Landmark graph
```

Full detail in [ARCHITECTURE.md](./ARCHITECTURE.md).

---

## Tracks

The same codebase satisfies both hackathon tracks:

| Track | How |
|---|---|
| **Ship It** (primary) | Deployed on AWS in `ap-south-1`. Architecture is part of the score. |
| **Build It** (fallback) | Local OpenSearch, Ollama via Strands' model-provider switch, SAM Local + LocalStack. All swaps confined to `layers/common/providers.py`. |
| **Best UI** | The ops console — playground, review queue, map, metrics. |

---

## Quickstart

### Prerequisites

- Python 3.12, Node.js 20+
- AWS CLI configured for `ap-south-1`
- AWS SAM CLI
- Bedrock model access granted for Amazon Nova Lite, Claude, and Titan Text Embeddings v2 — **request this first, it can take hours**
- Docker (for SAM local and LocalStack)

### Local development

```bash
git clone <repo> && cd patasetu

# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export PROVIDER=local          # everything below runs with no cloud at all

# Data: the gazetteer is built from the All-India Pincode Directory (23 MB,
# not committed). One download, then everything is reproducible.
curl -sL -o data/pincode_raw.csv \
  https://raw.githubusercontent.com/harshvardhaniimi/IndiaPIN/main/data-raw/pincode.csv
python -m scripts.build_gazetteer --src data/pincode_raw.csv
python -m scripts.build_seeds --n 120
python -m eval.corpus_gen --n 2000
python -m scripts.make_gold --n 300
python -m scripts.warm_landmarks                 # cold landmark graph
python -m scripts.warm_landmarks --observations --out eval/data/landmarks_warm.jsonl

# Verify the DIGIPIN encoder before anything else
pytest tests/test_digipin.py -v

# Run the pipeline on a single address
python -m scripts.resolve_one "h no 14 behind shiv mandir ramesh nagar delhi 110015"
python -m scripts.resolve_one --stack R2 "..."   # with in-process retrieval

# The whole suite: 376 tests, no network
pytest
```

### Deploy

```bash
cd backend
python -m scripts.prepare_layer   # copies the gazetteer into the Lambda layer
sam build
sam deploy --guided               # first time only
sam deploy                        # subsequent

./scripts/smoke.sh                # after EVERY deploy
python -m scripts.create_index --load --warm   # OpenSearch: create + bulk-load
```

`EnableOpenSearch=false` on a deploy tears the collection down overnight (it
bills by the hour, idle or not); the rest of the stack is untouched.

Note the API URL from the stack outputs, then:

```bash
cd ../frontend/console
npm install
echo "VITE_API_URL=<your-api-url>" > .env.local
npm run dev                    # local
npm run build                  # Amplify picks this up on push
```

### Seed data

See the local-development block above: seeds, corpus, gold splits and the
landmark graph are all built by the scripts under `scripts/` and `eval/`.

---

## Evaluation

```bash
python -m eval.run_ablation --split dev --stacks A R1 R2     # no model needed
python -m eval.run_ablation --split dev --stacks B C D E     # needs Bedrock or Ollama
python -m eval.run_ablation --split test --stacks A B C D E  # ONCE, at the end
```

`R1`/`R2` are diagnostic rows — retrieval with no LLM — that isolate what S2
contributes to geocoding. Configurations that cannot run report **NOT RUN**
with the reason; the table is never filled with a weaker configuration's
numbers.

Targets:

| Metric | Target |
|---|---|
| Field-level F1 | > 0.90 |
| Full-address exact match | > 0.75 |
| Median geocode error | < 150 m |
| Auto-resolution rate | > 80% |
| Clarification rate | < 15% |
| Precision @ threshold | > 95% |
| p95 latency | < 1.2 s |

---

## Running it on your laptop

Step-by-step guides, no AWS needed:
[Windows](docs/run-backend.md) · [Mac](docs/run-backend-mac.md) ·
[AWS setup for the deploy](docs/aws-setup.md) ·
[How it works, in plain language](docs/how-it-works.md)

## Repository layout

See [REPO_STRUCTURE.md](./REPO_STRUCTURE.md).

## Requirements and acceptance criteria

See [REQUIREMENTS.md](./REQUIREMENTS.md).

## Task board and four-day plan

See [TASKS.md](./TASKS.md).

---

## Limitations — stated openly

- Evaluated on a corpus generated from **120 real, publicly listed addresses** (India Post's All-India Pincode Directory — real localities, real pincodes, published coordinates, no personal data), **not** on production courier data. Those seed addresses are *better formed* than real landmark addresses; all the mess is introduced by `corpus_gen.py`, which labels what it perturbed.
- Gold-set labels are **derived** from the source directory, not hand-typed. They are verifiable rather than guessed, but they inherit any error in the source; `eval/label_tool.py` exists for the human pass, and the count of verified rows travels with every reported number.
- The landmark graph was warmed from the same directory the addresses came from. A 0 m median geocode error says the graph works *when it knows the place*, not that it knows every place.
- In local mode the vector signal is a character-trigram hashing embedder, not Titan; local retrieval numbers are a lower bound on the cloud configuration.
- The landmark graph is warmed only for localities in our corpus. Cold localities perform closer to the baseline configuration.
- Indic-script coverage is tested for Hindi and Devanagari transliteration. Other scripts are structurally supported but unmeasured.
- Phone numbers are stripped at stage S1 and never embedded or sent to a model. No PII is retained beyond the demo dataset.
- This is a four-day hackathon build. It is a working prototype with measurements, not a production system.

---

## Team

| Role | Owns |
|---|---|
| A | Pipeline S0–S3 — normalise, parse, retrieval, structuring |
| B | Data + infra — DynamoDB, OpenSearch, SAM, deploy, geocoding, DIGIPIN |
| C | Agents + policy — Strands clarifier, Cedar, Step Functions, learner |
| D | Frontend — ops console, rider PWA |

## Acknowledgements

Built with [Strands Agents SDK](https://strandsagents.com), [Cedar](https://www.cedarpolicy.com), Amazon Bedrock, and OpenSearch. DIGIPIN encoder vendored from the Department of Posts' open-source implementation.

## License

MIT
