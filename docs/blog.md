# The address stops being a phone call

*Draft 1 — written Saturday night, not published. Numbers are from the local
evaluation on the dev split; the cloud numbers replace them on Sunday.*

---

An Indian delivery address is usually not an address. It is a set of
directions given to a human being:

```
h no 14 behind shiv mandir near gupta general store opp water tank
ramesh nagar delhi 110015 call before coming
```

Three things follow from that, and all three cost money. The rider phones the
customer on almost every order. The first delivery attempt still fails a lot.
And whatever the rider learns — gate 3, ask the guard, lift on the left — dies
in their head; the next rider starts from zero.

We spent four days at First Commit building PataSetu, an address-resolution
engine that takes that text and returns a structured address, a coordinate, a
DIGIPIN, and a confidence score that means what it says. When it genuinely
does not know, it asks the customer exactly one question. And it learns: every
confirmed delivery makes the next address in that neighbourhood easier.

This post is about the four things we got wrong on the way, because they were
more interesting than the things we got right.

## 1. The government's own geocode format had changed, and nobody's blog knew

DIGIPIN is India Post's official 10-character geocode. Every secondary source
we found — tutorials, GitHub gists, a couple of npm packages — writes it with
hyphens: `39J-JTT-4565`. The official encoder does not. It emits a continuous
string, and the hyphens are a display convention that some sources promoted to
a data format.

We vendored the official encoder, wrote a test that checks the format against
India Post's published examples, and that test is the first thing the README
tells you to run. The lesson generalises: when a standard has an official
implementation, read the implementation, not the posts about it.

## 2. Real data was available, and it had junk in it

We planned to scrape 120 addresses. Instead we found the All-India Pincode
Directory — every post office in the country, with coordinates, published as
open data. Real addresses, no personal information, and a gazetteer for free.

It also contains a post office called `TEST OFFICE` at pincode `999999`,
coordinates placed in the sea, and offices whose coordinates belong to a
different state. None of this fails loudly. A pincode lookup on junk simply
returns junk, with full confidence.

So the gazetteer build now rejects rows by rule — placeholder and
non-civilian offices, coordinates outside the DIGIPIN national bounding box —
and takes the *median* of a pincode's offices as its centroid, so one office
geocoded into the wrong district cannot drag the centroid kilometres off. It
prints what it dropped. "The data is real" is only worth saying if you can
also say "and here is what we threw out".

## 3. A fixed 3 km radius is a metro assumption

Landmark retrieval fuses three signals: BM25 on names, vector similarity, and
geography — candidates near the pincode's centroid. We set the geographic
radius to 3 km, which felt generous.

It excluded the right answer for 29% of localities. Delhi pincodes are small;
rural pincodes are not. The radius now scales with the pincode's own extent,
measured from the gazetteer, and the retrieval diagnostics went from "the
graph is somehow not helping" to a median geocode error of **0 m** when the
graph knows the place.

That number deserves its caveat, which we print next to it: the landmark graph
was warmed from the same directory the addresses came from. Zero metres says
the mechanism works when the place is known, not that every place is known.

## 4. The learning curve is a step, not a slope

The claim we most wanted to make was *the system gets better at your
neighbourhood the more it sees of it*. We expected a gentle climb. We built the
measurement anyway: start with an empty landmark graph, feed six hundred
addresses across thirty-six localities one at a time, resolve each against the
graph as it stands, then "confirm the delivery" and let the graph learn.

![Learning curve](./results/learning_curve.svg)

With zero prior deliveries in a locality, 5.6% of addresses geocode from the
graph, and the median error is 1.2 km — a pincode centroid. After **one**
confirmed delivery: 80.6%, and 0 m. Then flat.

One delivery teaches the temple. The next address "behind the same temple" is
placed exactly. The remaining fifth are addresses that share no landmark with
any earlier one, and those still fall through to a geocoder. It is a more
useful result than the slope we expected, and it changed how we describe the
product: not "it learns over time", but "one delivery is enough".

## The part that says no

The clarifier — a Strands agent on Amazon Nova Lite — writes one question, in
the script the customer wrote in, targeting the field that unlocks the most.
Before that question goes anywhere, Cedar decides whether we may contact this
customer at all: not above 80% confidence, not twice for one order, not
outside 09:00–20:00 local time, and never if they opted out.

Our first draft of the opt-out rule was scoped to the agent. A test — "even an
operator cannot contact an opted-out customer" — failed, because a human
operator was allowed through by a different permit. The forbid now names no
principal at all. A human reviewer approved the first draft; the test read the
actual semantics. That is the argument for policy tests over policy review.

A denial is never a silent drop. The case goes to a person, with the policy id
and the reason on the row.

## What we would tell someone starting tomorrow

- Find the real data before you scrape. It usually exists, and it comes with a
  gazetteer.
- Build the measurement before the feature. The learning curve took an
  afternoon and changed the pitch.
- Print the caveat next to the number. A 0 m median with its caveat is more
  credible than a 40 m median without one.
- Give the guardrail a test, not a reviewer.

The code, the evaluation harness, and a journal of everything that went wrong
are at [github.com/ShashankVaish/address-simplfy](https://github.com/ShashankVaish/address-simplfy).

---

*To fill in on Sunday: cloud ablation rows B–E with real Bedrock; the
calibrated threshold on stack E; p95 latency from the dashboard; the video
link.*
