"""Generate triage.jsonl: 400 SYNTHETIC support tickets with labels.

Every row is invented from templates with a fixed seed. Names come from a short
made-up list and every email address is on example.com. There is no real
customer data in this file. Re-run to regenerate the same file byte for byte:

    python examples/jev_vs_laya/make_data.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

HERE = Path(__file__).parent
N = 400
SEED = 20261005

NAMES = ["Avery", "Brook", "Casey", "Devon", "Emery", "Finley", "Harper", "Jules", "Kai", "Morgan", "Quinn", "Rowan"]
PRODUCTS = ["Starter plan", "Team plan", "mobile app", "desktop app", "API", "invoice export"]

TEMPLATES: dict[str, list[str]] = {
    "billing": [
        "I was charged twice for the {product} this month. Order {order}.",
        "My card was declined when renewing the {product}, can you check?",
        "The invoice for order {order} shows the wrong VAT number.",
        "Why did my {product} price go up without notice?",
    ],
    "technical": [
        "The {product} crashes every time I open the settings page.",
        "Sync in the {product} has been stuck at 99% since yesterday.",
        "Getting a 500 error from the {product} when uploading files.",
        "Login on the {product} loops back to the start screen.",
    ],
    "account": [
        "Please change the owner of our workspace to {name2}.",
        "I can't reset my password, the email never arrives at {email}.",
        "How do I delete my account and all my data?",
        "Can you merge my two accounts? Both use {email}.",
    ],
    "sales": [
        "We're a team of {seats}, is there a discount on the {product}?",
        "Do you offer an annual contract for the {product}?",
        "Can I get a quote for {seats} seats with SSO?",
        "Is there a nonprofit price for the {product}?",
    ],
}
REFUND_TAILS = [" I'd like a refund please.", " Please refund the charge.", " I want my money back."]
URGENCY_TAILS = {
    0: ["", " No rush."],
    1: [" When you get a chance.", ""],
    2: [" This is blocking my work today.", " Please help soon."],
    3: [" URGENT: production is down for all our users!", " This is critical, we are losing sales right now."],
}
URGENCY_PRIOR = {"billing": [3, 4, 2, 1], "technical": [1, 3, 4, 2], "account": [3, 4, 2, 1], "sales": [5, 4, 1, 0]}


def make(rng: random.Random, i: int) -> dict[str, object]:
    dept = rng.choice(list(TEMPLATES))
    name, name2 = rng.sample(NAMES, 2)
    text = rng.choice(TEMPLATES[dept]).format(
        product=rng.choice(PRODUCTS),
        order=f"SYN-{rng.randint(10000, 99999)}",
        name2=name2,
        email=f"{name.lower()}{rng.randint(1, 99)}@example.com",
        seats=rng.choice([5, 12, 40, 250]),
    )
    refund = dept == "billing" and rng.random() < 0.45
    if refund:
        text += rng.choice(REFUND_TAILS)
    urgency = rng.choices(range(4), weights=URGENCY_PRIOR[dept])[0]
    text += rng.choice(URGENCY_TAILS[urgency])
    return {
        "id": f"syn-{i:04d}",
        "synthetic": True,
        "state": {"from": name, "channel": rng.choice(["email", "chat"]), "text": text},
        "labels": {"department": dept, "urgency": urgency, "is_refund": refund},
    }


def main() -> None:
    rng = random.Random(SEED)
    with (HERE / "triage.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for i in range(N):
            f.write(json.dumps(make(rng, i), ensure_ascii=False) + "\n")
    print(f"wrote {N} synthetic rows to {HERE / 'triage.jsonl'}")


if __name__ == "__main__":
    main()
