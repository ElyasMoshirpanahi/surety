# surety

**Statistical guarantees for System One decision models.**

Decision models such as Jev (TypeSafe AI) and Laya (Convai Innovations) return
typed, probabilistic decisions. Both vendors tell you to validate confidence
thresholds on your own labelled data, but nothing ships a guarantee. `surety`
fills that gap. From human-labelled real traffic it certifies a confidence
threshold per question, model and slice so that, **with probability at least
1 − δ, the error rate among automated decisions is at most α**. At runtime it
gates every decision and fails closed to human review. It watches audited
decisions with an anytime-valid drift detector and writes evidence to a
hash-chained ledger. It works with any backend that speaks `/v1/systemone`, and
it has zero runtime dependencies.

## 60-second quickstart

This runs fully offline on the bundled synthetic dataset, using a simulated backend.

```bash
git clone https://github.com/ElyasMoshirpanahi/surety && cd surety
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

surety plan --alpha 0.1
surety collect examples/jev_vs_laya/triage.jsonl --questions examples/jev_vs_laya/questions.json \
    --backend fake --model sim-1.0.0 -o calib.jsonl
surety certify calib.jsonl --questions examples/jev_vs_laya/questions.json --alpha 0.1 -o certs.json
surety report calib.jsonl --questions examples/jev_vs_laya/questions.json --alpha 0.1 -o report.md
```

Then gate decisions at runtime:

```python
import json
from surety import FakeBackend, Gate, Ledger

questions = json.load(open("examples/jev_vs_laya/questions.json"))
backend = FakeBackend("sim-1.0.0")  # live: HttpBackend(model="jev-1.13.0")
gate = Gate.load("certs.json", ledger=Ledger("decisions.jsonl"))

state = {"text": "I was charged twice this month. Please refund the charge."}
res = backend.predict(state, questions)
d = gate.decide("department", questions["department"], res["answers"]["department"], model=backend.model, state=state)
print(d.action, d.decision, round(d.confidence, 3), "-", d.reason)
```

Finally, check the evidence chain and print the hash to publish somewhere you don't control alone:

```bash
surety verify decisions.jsonl
surety ledger anchor decisions.jsonl
```

To use a real backend, swap `--backend fake --model sim-1.0.0` for one of these:

| Backend | Flags | Notes |
|---|---|---|
| Hosted Jev | `--backend http --model jev-1.13.0` | Reads `TYPESAFE_API_KEY` and sends it only to `https://api.typesafe.ai`. |
| Official SDK | `--backend sdk --model jev-1.13.0` | `pip install "surety-gate[sdk]"`. |
| laya-serve | `--backend http --base-url http://localhost:8000 --model <laya model>` | Same wire protocol. No key is sent. |
| Laya in-process | `--backend laya --model <laya model>` | `pip install "surety-gate[laya]"`. The pinned model is checked on every response. |

`collect` runs requests concurrently. It retries 429, 5xx and network errors
with backoff, and `--resume` continues an interrupted run.

To compare Jev and Laya on the same data, run `python examples/jev_vs_laya/run.py --fake`.

## What the guarantee means

For each certificate, with probability at least 1 − δ over the calibration sample,
the error rate among decisions the gate automates is at most α. "Error" means
the argmax decision differs from the human label. `--simultaneous` splits δ
across all certificates, so they all hold at once. The method is exact and
finite-sample: an exact binomial test at each threshold on a grid fixed in
advance, with a Bonferroni correction. See [docs/method.md](docs/method.md).

## What it does *not* mean

- **It assumes exchangeability.** Future traffic must look like the calibration
  sample. Sample calibration rows at random from real traffic, not from a
  convenient subset. When traffic shifts, the guarantee no longer applies. That's
  what the drift monitor is for. Audit a uniformly random sample of automated
  decisions and call `gate.audit(...)`. An alarm suspends the certificate until
  you re-certify.
- **Labels must come from humans.** Labels produced by the model you're
  certifying, or by a sibling model, make the guarantee meaningless.
- **Guarantees hold per slice, not for every subgroup.** A certificate for slice
  `eu` says nothing about Germans in `eu`. Certify the slices you care about
  separately.
- **Score questions are certified on exact-match argmax.** An urgency of 2 when
  the label is 3 counts as an error, the same as 0 would. The expected `score`
  field is never used.
- **It is not about calibration.** A model can be badly calibrated and still
  certify at some threshold. A well-calibrated model with few labelled rows can
  fail to certify.

## FAQ

**Why not just use ECE or a reliability diagram?**
Expected calibration error averages over all confidence levels and is itself a
noisy estimate. A low ECE doesn't bound the error rate above your threshold, and
it gives no probability that the bound holds. `surety report` still shows
reliability bins, because they help you understand a model. They just don't
decide anything.

**Why don't thresholds transfer between Jev and Laya?**
The models are different, so their errors are too. Their `confidence` fields
are also defined differently. Jev uses (n·p_max − 1)/(n − 1). Laya uses 1 − normalized
entropy. `surety` ignores both and uses the probability of the argmax
decision, but even on that common scale each model needs its own certificate.

**Why must I pin model versions?**
A certificate holds for one model. Aliases like `jev-latest` and `jev-preview`
can move to a new model without warning. `collect` and `certify` refuse aliases.
`collect` also records the model the server says it actually used. The gate
sends every decision to review if the model string differs from the certified
one.

**How many labelled rows do I need?**
Run `surety plan --alpha 0.05`. That's the minimum with zero errors. In practice,
budget 2–3× that per question and slice.

## Development

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy
pytest -m "not slow"        # fast, offline
pytest -m slow              # Monte Carlo checks of the guarantee and the drift detector
```

## License

Apache-2.0. See [LICENSE](LICENSE).

This project is not affiliated with, or endorsed by, TypeSafe AI or Convai
Innovations. Jev and Laya are named only to describe compatibility.
