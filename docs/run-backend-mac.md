# How to run PataSetu on a Mac (easy steps)

Everything runs offline. No AWS account needed. All commands go in the
**Terminal** app (Cmd+Space, type "Terminal").

---

## Step 0 — One-time setup (10 minutes)

### Install the tools

If you don't have Homebrew yet (the Mac package manager):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Then:

```bash
brew install python@3.12 node git
```

Check they work:

```bash
python3.12 --version      # Python 3.12.x
node --version            # v20 or newer
```

### Get the code

```bash
git clone <repo-url> address-simplify
cd address-simplify/backend
```

(If your friend already sent you the folder, just `cd` into
`address-simplify/backend`.)

### Create the Python environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

You should now see `(.venv)` at the start of your prompt.

### Tell it to run offline — **do this in every new terminal window**

```bash
export PROVIDER=local
```

> Tip: to make it permanent, add that line to `~/.zshrc`:
> `echo 'export PROVIDER=local' >> ~/.zshrc`

---

## Step 1 — Build the data (only if the files are missing)

Check first:

```bash
ls data/pincodes.csv eval/data/seed_addresses.jsonl eval/data/landmarks.jsonl
```

If all three print without "No such file", **skip to Step 2.** Otherwise:

```bash
curl -L -o data/pincode_raw.csv https://raw.githubusercontent.com/harshvardhaniimi/IndiaPIN/main/data-raw/pincode.csv
python -m scripts.build_gazetteer --src data/pincode_raw.csv
python -m scripts.build_seeds --n 120
python -m eval.corpus_gen --n 2000
python -m scripts.make_gold --n 300
python -m scripts.warm_landmarks
python -m scripts.warm_landmarks --observations --out eval/data/landmarks_warm.jsonl
```

About 2 minutes. What each does:

| Command | Makes |
|---|---|
| `build_gazetteer` | the pincode table (19,300 pincodes) from India Post data |
| `build_seeds` | 120 real seed addresses |
| `corpus_gen` | 2,040 messy test addresses from those seeds |
| `make_gold` | the 150 + 150 answer key |
| `warm_landmarks` | the landmark index (263 real places) |

---

## Step 2 — Resolve one address (the quickest check)

```bash
python -m scripts.resolve_one "h no 14 behind shiv mandir near gupta store ramesh nagar delhi 110015"
```

You should see structured fields, a DIGIPIN, a confidence and a list of
reasons. Now with landmark search:

```bash
python -m scripts.resolve_one --stack R2 "h no 14 behind shiv mandir near gupta store ramesh nagar delhi 110015"
```

`geo` should now say `landmark_graph`. Hindi works too (Mac terminals show
Devanagari fine):

```bash
python -m scripts.resolve_one --stack R2 "शिव मंदिर के पीछे, रमेश नगर, दिल्ली 110015"
```

---

## Step 3 — Run the tests

```bash
python -m pytest
```

Expect **`376 passed`**. Just the DIGIPIN test:

```bash
python -m pytest tests/test_digipin.py -v
```

---

## Step 4 — Run the API as a web server

```bash
python -m scripts.serve
```

You'll see:

```
landmark index: 263 records loaded
PataSetu API on http://localhost:8000  (stack R2, PROVIDER=local)
```

Leave this window open. Test from a **second** Terminal window (Cmd+N):

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/v1/resolve -H 'content-type: application/json' -d '{"raw":"h no 14 behind shiv mandir ramesh nagar delhi 110015"}'
curl http://localhost:8000/v1/queue
```

Stop it with **Ctrl+C**. Other configurations:

```bash
python -m scripts.serve --stack A     # rules only
python -m scripts.serve --stack R2    # rules + landmark search (default)
python -m scripts.serve --stack D     # with the AI model (needs Ollama, below)
```

---

## Step 5 — Run the console (the web page)

In a **third** Terminal window:

```bash
cd ~/address-simplify/frontend/console      # adjust the path if different
echo 'VITE_API_URL=http://localhost:8000' > .env.local
npm install                                  # first time only
npm run dev
```

Open **http://localhost:5173** in your browser.

**Check the badge:** if you see an orange **mock mode** badge next to the
Resolve button, the page is using fake built-in data instead of your backend.
That means `.env.local` is missing or you didn't restart `npm run dev` after
creating it. Fix it, press Ctrl+C, run `npm run dev` again.

---

## Step 6 — Measure it

```bash
python -m eval.run_ablation --split dev --stacks A R1 R2
```

Prints the metrics per configuration and writes `docs/results/ablation.md`.

---

## Optional — the AI model on your Mac (Ollama)

Configurations **B, C, D, E** call a language model. Without one they print
`NOT RUN` and why. To run them locally:

```bash
brew install ollama
brew services start ollama          # runs in the background
ollama pull llama3.2                # ~2 GB, once
```

Then:

```bash
python -m eval.run_ablation --split dev --stacks B C D E
python -m scripts.serve --stack D
```

Apple Silicon Macs (M1/M2/M3/M4) run this well. The **real** numbers still
come from Bedrock after the deploy.

---

## Cheat sheet

| I want to… | Run |
|---|---|
| activate the environment (new window) | `cd address-simplify/backend && source .venv/bin/activate && export PROVIDER=local` |
| resolve one address | `python -m scripts.resolve_one "…"` |
| …with landmark search | `python -m scripts.resolve_one --stack R2 "…"` |
| run all tests | `python -m pytest` |
| start the local API | `python -m scripts.serve` |
| start the console | `cd ../frontend/console && npm run dev` |
| measure accuracy | `python -m eval.run_ablation --split dev --stacks A R1 R2` |
| rebuild the landmark index | `python -m scripts.warm_landmarks` |
| check code style | `python -m ruff check .` |

---

## If something goes wrong

| You see | Do this |
|---|---|
| `command not found: python` | use `python3.12`, or activate the venv (`source .venv/bin/activate`) |
| `No module named patasetu` | you're not in the `backend` folder, or the venv isn't active |
| `PROVIDER must be 'aws' or 'local'` | `export PROVIDER=local` |
| `pincodes.csv not found` | Step 1 |
| `landmarks.jsonl missing` | `python -m scripts.warm_landmarks` |
| `Ollama is not reachable` | expected without Ollama — use `--stack R2` or `A` |
| `Address already in use` on port 8000 | `python -m scripts.serve --port 8001` and change `.env.local` to match |
| `zsh: permission denied` on a script | `chmod +x scripts/*.sh` |
| Homebrew says "not in PATH" | run the two `eval` lines it prints, then reopen Terminal |
