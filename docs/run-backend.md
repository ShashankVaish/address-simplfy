# How to run the backend on your laptop — Windows (easy steps)

No AWS account needed for any of this. Everything runs offline with
`PROVIDER=local`. Commands are for **PowerShell on Windows**.

> **On a Mac?** Use [run-backend-mac.md](run-backend-mac.md) instead — same
> steps, Mac commands.

---

## Step 0 — One-time setup (5 minutes)

Open a terminal in the project and go into the backend folder:

```powershell
cd g:\coding\goo\address-simplify\backend
```

Create a Python environment and install the tools:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # (Mac/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
```

Tell the backend to run offline. **Do this in every new terminal:**

```powershell
$env:PROVIDER = "local"               # (Mac/Linux: export PROVIDER=local)
```

> If PowerShell refuses to run `Activate.ps1`, run once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` and try again.

---

## Step 1 — Build the data (only if the files are missing)

Check whether the data already exists:

```powershell
dir data\pincodes.csv, eval\data\seed_addresses.jsonl, eval\data\landmarks.jsonl
```

If all three are listed, **skip to Step 2.** If any is missing, build them —
one download (23 MB), then five commands, about 2 minutes total:

```powershell
curl.exe -L -o data\pincode_raw.csv https://raw.githubusercontent.com/harshvardhaniimi/IndiaPIN/main/data-raw/pincode.csv
python -m scripts.build_gazetteer --src data\pincode_raw.csv
python -m scripts.build_seeds --n 120
python -m eval.corpus_gen --n 2000
python -m scripts.make_gold --n 300
python -m scripts.warm_landmarks
python -m scripts.warm_landmarks --observations --out eval\data\landmarks_warm.jsonl
```

What each does, in one line:

| Command | Makes |
|---|---|
| `build_gazetteer` | the pincode table (19,300 pincodes) from India Post data |
| `build_seeds` | 120 real seed addresses |
| `corpus_gen` | 2,040 messy test addresses from those seeds |
| `make_gold` | the 150 + 150 answer key |
| `warm_landmarks` | the landmark index (263 real places) |

---

## Step 2 — Resolve one address (the quickest check)

```powershell
python -m scripts.resolve_one "h no 14 behind shiv mandir near gupta store ramesh nagar delhi 110015"
```

You should see the structured fields, a DIGIPIN, the confidence, and the
list of reasons. Try the full pipeline with landmark search:

```powershell
python -m scripts.resolve_one --stack R2 "h no 14 behind shiv mandir near gupta store ramesh nagar delhi 110015"
```

Now `geo` should say `landmark_graph` instead of `pincode_centroid`.

Try Hindi:

```powershell
python -m scripts.resolve_one --stack R2 "शिव मंदिर के पीछे, रमेश नगर, दिल्ली 110015"
```

> If Hindi text prints as boxes, run `$env:PYTHONIOENCODING = "utf-8"` first.

---

## Step 3 — Run the tests (proves nothing is broken)

```powershell
python -m pytest
```

Expect: **`376 passed`** in a few seconds. If something fails, copy the last
20 lines and send them to me.

Only the DIGIPIN test (the important one):

```powershell
python -m pytest tests\test_digipin.py -v
```

---

## Step 4 — Run the API as a web server (for the console)

```powershell
python -m scripts.serve
```

You will see:

```
landmark index: 263 records loaded
PataSetu API on http://localhost:8000  (stack R2, PROVIDER=local)
```

Leave this terminal open. Test it from a **second** terminal:

```powershell
curl.exe http://localhost:8000/health
curl.exe -X POST http://localhost:8000/v1/resolve -H "content-type: application/json" -d "{\"raw\":\"h no 14 behind shiv mandir ramesh nagar delhi 110015\"}"
curl.exe http://localhost:8000/v1/queue
```

The same four endpoints the cloud version has: `/health`, `/v1/resolve`,
`/v1/queue`, `/v1/feedback`. Press **Ctrl+C** to stop.

Pick a different pipeline configuration if you want:

```powershell
python -m scripts.serve --stack A        # rules only, no landmark search
python -m scripts.serve --stack R2       # rules + landmark search (default)
python -m scripts.serve --stack D        # with the AI model (needs Ollama, see below)
```

---

## Step 5 — Run the console against it

In a **third** terminal:

```powershell
cd g:\coding\goo\address-simplify\frontend\console
echo VITE_API_URL=http://localhost:8000 > .env.local
npm install                              # first time only
npm run dev
```

Open **http://localhost:5173**. Paste an address, click Resolve. The
**Review queue** tab shows every case that needed a human.

To use the built-in fake data instead of the local API, make `.env.local`
empty (or delete it) and restart `npm run dev` — the page shows a "mock mode"
badge.

---

## Step 6 — Measure it (the ablation table)

```powershell
python -m eval.run_ablation --split dev --stacks A R1 R2
```

Prints the metrics for each configuration and writes
`docs\results\ablation.md`. Takes about 5 seconds.

---

## Optional — the AI model on your laptop (Ollama)

Configurations **B, C, D, E** call a language model. Without one they print
`NOT RUN` and the reason. To run them locally:

1. Install Ollama from https://ollama.com (Windows/Mac installer).
2. In a terminal: `ollama pull llama3.2` (about 2 GB, once).
3. Ollama runs in the background on port 11434. Then:

```powershell
python -m eval.run_ablation --split dev --stacks B C D E
python -m scripts.serve --stack D
```

This is for testing the wiring. The **real** numbers come from Bedrock after
the deploy.

---

## Cheat sheet

| I want to… | Run |
|---|---|
| resolve one address | `python -m scripts.resolve_one "…"` |
| …with landmark search | `python -m scripts.resolve_one --stack R2 "…"` |
| run all tests | `python -m pytest` |
| start the local API | `python -m scripts.serve` |
| start the console | `cd ..\frontend\console; npm run dev` |
| measure accuracy | `python -m eval.run_ablation --split dev --stacks A R1 R2` |
| rebuild the landmark index | `python -m scripts.warm_landmarks` |
| check code style | `python -m ruff check .` |

---

## If something goes wrong

| You see | Do this |
|---|---|
| `No module named patasetu` | you are not in the `backend` folder, or the venv is not activated |
| `PROVIDER must be 'aws' or 'local'` | you typed the env var wrong — `$env:PROVIDER = "local"` |
| `pincodes.csv not found` | Step 1 |
| `landmarks.jsonl missing` | `python -m scripts.warm_landmarks` |
| `Ollama is not reachable` | expected without Ollama — use `--stack R2` or A |
| Hindi shows as `????` | `$env:PYTHONIOENCODING = "utf-8"` |
| port 8000 already in use | `python -m scripts.serve --port 8001` and update `.env.local` |
