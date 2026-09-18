# AWS Setup — what to get and how (easy steps)

This is the checklist for tonight's deploy. Follow it top to bottom. About
30–40 minutes, plus waiting for Bedrock approval (can be hours — do that step
**first**).

**Golden rule:** never paste access keys into chat, Slack, WhatsApp, or a
file in the repo. They go into a local config file on your machine only (step
6 shows how). If a key ever leaks, delete it in the AWS console and make a new
one.

---

## Step 1 — Log in to AWS and pick the region

1. Go to https://console.aws.amazon.com and log in.
2. Top-right corner, region dropdown → choose **Asia Pacific (Mumbai)
   `ap-south-1`**.
3. Everything below happens in this region. If you see a resource missing
   later, check the region first — it is the most common mistake.

---

## Step 2 — Enable Bedrock models (do this NOW, Claude may take a little time)

The old "Model access" page is **retired**. Models now switch on automatically
the first time you call them. The only exception is Claude, which may ask for a
short use-case form the first time.

1. In the search bar type **Bedrock** → open **Amazon Bedrock**. Confirm the
   region at the top right says **Asia Pacific (Mumbai)**.
2. Left menu → **Model catalog**.
3. Search **Claude** → click the newest Claude model → **Open in playground**.
4. Type `hello` and send.
   - If a **use-case form** pops up: company = your team name, use case =
     "address resolution for delivery logistics (hackathon)", submit, then send
     `hello` again.
   - When a reply comes back, Claude is enabled for the whole account.
5. Do the same once for **Amazon Nova Lite** and **Titan Text Embeddings V2**
   (embeddings has no chat box — just opening it is enough; it enables on the
   first API call from the deploy anyway).

You are done when the Claude playground answers you.

> If a model says it is not available in Mumbai, tell me the model name — the
> deploy template already allows cross-region inference profiles, so it is a
> one-line parameter change.

## Step 3 — Check your credits and set a budget alarm

**First look at what you already have:** Search **Billing** → **Credits**. A
new AWS account comes with **$100 "AWS Free Tier" credit** already active, and
opening the Bedrock playground adds a **$20 "Explore AWS"** credit. If you see
those, you have $120 and there is nothing to redeem.

**The "Redeem credit" button is greyed out** on new accounts. That is AWS
policy: accounts on the **Free Plan** cannot redeem promo codes; only a
**Paid Plan** can. Do not upgrade just for that unless the event gave you a
*separate* promo code you actually need. Upgrading is free (it needs a card)
and your existing credits still apply first — the card is charged only if you
spend beyond them.

> **Free Plan caveat.** The Free Plan blocks a few services. If tonight's
> deploy fails with an error saying a service is not available on your plan
> (OpenSearch Serverless is the likeliest), that is the moment to click
> **Upgrade to a paid plan**, then re-run the deploy. Credits carry over.

**Then set the alarm** (works on any plan): Search **Budgets** → **Create
budget** → *Cost budget* → amount **$25** → alerts at 80% and 100% → your
email → create. The deploy template also creates a CloudWatch billing alarm,
but AWS Budgets is the one that emails you reliably.

---

## Step 4 — Create a deploy user (the credentials I need)

Do **not** use your root account keys. Make a separate user just for deploying.

1. Search **IAM** → left menu **Users** → **Create user**.
2. User name: `patasetu-deploy`. Do **not** tick "console access". Next.
3. Permissions → **Attach policies directly** → search and tick:
   - `AdministratorAccess`

   (Yes, admin. The stack creates IAM roles, an OpenSearch collection, Cognito,
   a place index, and more — a narrow policy would take an hour to get right
   and this is a 4-day project. Delete the user after the event.)
4. Next → **Create user**.
5. Click the user name → **Security credentials** tab → **Access keys** →
   **Create access key**.
6. Choose **Command Line Interface (CLI)** → tick the confirmation → Next →
   Create.
7. You will see two values. Copy both **now** — the secret is shown only once:
   - **Access key ID** — looks like `AKIA…` (20 characters)
   - **Secret access key** — long random string

Keep them somewhere private for step 6.

---

## Step 5 — Install the two command-line tools

On the machine that will run the deploy (yours, or mine via your session):

**AWS CLI**
- Windows: download and run https://awscli.amazonaws.com/AWSCLIV2.msi
- Mac: `brew install awscli`
- Check: `aws --version` → should print `aws-cli/2.x`

**SAM CLI** (builds and deploys the stack)
- Windows: download the installer from
  https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html
- Mac: `brew install aws-sam-cli`
- Check: `sam --version` → should print `SAM CLI, version 1.x`

**Docker Desktop** is *not* required — the Lambda builds are pure Python.

---

## Step 6 — Put the credentials on the machine (safely)

Open a terminal and run:

```
aws configure
```

It asks four questions. Answer:

```
AWS Access Key ID:      <paste the AKIA… key>
AWS Secret Access Key:  <paste the secret>
Default region name:    ap-south-1
Default output format:  json
```

This writes them to `~/.aws/credentials` on your machine. Nothing goes into
the repo (`.gitignore` already blocks `.env` files anyway).

**Check it worked:**

```
aws sts get-caller-identity
```

You should see your account number and `user/patasetu-deploy`. If you see an
error, the key was mistyped — run `aws configure` again.

---

## Step 7 — What to send me

Just this line, from the command above:

```
"Arn": "arn:aws:iam::123456789012:user/patasetu-deploy"
```

plus **"Claude playground: works"** (or "asked for a form / pending"). That is all I need to
confirm the environment is ready. **Do not send the keys themselves.**

If I'm running the deploy from this session on your machine, the CLI reads
`~/.aws/credentials` automatically — nothing to pass me.

If instead you want to hand credentials to a different machine, set them as
environment variables in that terminal only:

```
# PowerShell
$env:AWS_ACCESS_KEY_ID = "AKIA..."
$env:AWS_SECRET_ACCESS_KEY = "..."
$env:AWS_DEFAULT_REGION = "ap-south-1"
```

They vanish when the terminal closes.

---

## Step 8 — The deploy itself (I run this; here for reference)

```
cd backend
python -m scripts.prepare_layer          # gazetteer into the Lambda layer
sam build
sam deploy --guided                      # first time: answer the prompts below
./scripts/smoke.sh                       # must print "OK smoke passed"
python -m scripts.create_index --load --warm
```

`sam deploy --guided` prompts — answer like this:

| Prompt | Answer |
|---|---|
| Stack Name | `patasetu` |
| AWS Region | `ap-south-1` |
| Parameter Stage | `dev` |
| Parameter ServingStack | `A` tonight, `E` once Bedrock is granted |
| Parameter EnableOpenSearch | `true` |
| Confirm changes before deploy | `N` |
| Allow SAM CLI IAM role creation | `Y` |
| Disable rollback | `N` |
| ResolverFunction may not have authorization defined, Is this okay? | `Y` (the playground is public by design) |
| QueueApiFunction may not have authorization defined | `Y` (for tonight; Cognito goes on before the demo) |
| Save arguments to configuration file | `Y` |

After it finishes it prints **Outputs**. Copy `ApiUrl` — that is the public
URL, and it goes into the console's `.env.local` as `VITE_API_URL`.

---

## Budget — how $100 is spent (and how not to spend it)

The AI models are cheap for this project. OpenSearch is not.

| Item | Cost | Notes |
|---|---|---|
| Nova Lite on ~5,000 addresses | ~$1 | the cheap model handles ~90% |
| Claude Sonnet 5 on ~500 escalations | ~$3.50 | the "strong" model; Opus would be ~$9 for no gain |
| Titan embeddings | < $0.10 | |
| Lambda, DynamoDB, API Gateway, Cognito, Location | ~$1–2 | all pay-per-request |
| **OpenSearch Serverless** | **~$12 per day it is running** | bills by the hour, idle or not |

**Expected total: $25–35** if OpenSearch is torn down every night (step 9).
**Up to $60** if it is left running for the whole event. That is the one
thing to be disciplined about.

The strong model is already set to Sonnet 5 in the code and the template.
If Claude access is still pending on deploy night, the system runs on Nova
Lite alone (`ServingStack=D` uses the cascade; escalations simply fall back to
the cheap answer and say so in `evidence[]`).

---

## Step 9 — Every night before sleeping

```
sam deploy --parameter-overrides EnableOpenSearch=false
```

OpenSearch Serverless is the only thing in the stack that bills while idle
(by the hour). This removes it; everything else costs nothing at rest. Next
morning, deploy again with `EnableOpenSearch=true` and re-run
`create_index --load --warm`.

---

## Optional — let GitHub deploy automatically

Only if you want `git push` to deploy. Skip for tonight.

1. IAM → **Identity providers** → Add provider → OpenID Connect →
   URL `https://token.actions.githubusercontent.com`, audience
   `sts.amazonaws.com`.
2. IAM → Roles → Create role → *Web identity* → that provider → restrict to
   your repo → attach `AdministratorAccess` → name it `patasetu-github-deploy`.
3. Copy the role ARN into the GitHub repo: Settings → Secrets → Actions →
   new secret `AWS_ROLE_ARN`.

`.github/workflows/deploy.yml` already uses it.

---

## Quick checklist

- [ ] Region is `ap-south-1`
- [ ] Claude answered in the Bedrock playground (Nova Lite and Titan enable on first call)
- [ ] Credits visible under Billing → Credits ($100–120), $25 budget alert set
- [ ] IAM user `patasetu-deploy` created with an access key
- [ ] `aws --version` and `sam --version` both work
- [ ] `aws configure` done, `aws sts get-caller-identity` shows the user
- [ ] Sent me the ARN line and the Bedrock status — **not the keys**

---

## If something goes wrong

| You see | Do this |
|---|---|
| `Unable to locate credentials` | run `aws configure` again |
| `AccessDenied` during deploy | the user is missing `AdministratorAccess` — re-check step 4.3 |
| `AccessDeniedException` from Bedrock on Claude | the use-case form (step 2) is not approved yet — deploy with `ServingStack=A` meanwhile; Nova Lite still works |
| `is not available in ap-south-1` | tell me the model name; we switch to a cross-region profile |
| Stack stuck in `ROLLBACK_COMPLETE` | delete it in CloudFormation and deploy again (nothing in it is persistent yet) |
