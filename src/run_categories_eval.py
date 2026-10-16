"""Run the categories gate over telegram-download-chat exports and compare with gold labels."""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from . import categories
from .config import CategoriesGate, _parse_categories, load_config


def _chat_id(msg: dict) -> int | None:
    peer = msg.get("peer_id") or {}
    for key in ("channel_id", "chat_id", "user_id"):
        if peer.get(key) is not None:
            return int(peer[key])
    return None


def load_export(paths: list[str]) -> list[dict]:
    """Read export files into ``{"chat_id", "message_id", "text"}`` rows, skipping empty messages."""
    rows: list[dict] = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for msg in data:
            text = msg.get("message")
            if not text:
                continue
            rows.append(
                {"chat_id": _chat_id(msg), "message_id": int(msg["id"]), "text": text}
            )
    return rows


def compute_metrics(rows: list[dict], gate: CategoriesGate) -> dict:
    confusion: dict[str, dict[str, int]] = {}
    by_label: dict[str, list[int]] = {}
    misses: list[dict] = []
    false_forwards: list[dict] = []
    tp = fp = fn = 0
    for row in rows:
        label = row["label"]
        predicted = row.get("category") or "none"
        forwarded = bool(row["forward"])
        confusion.setdefault(label, {}).setdefault(predicted, 0)
        confusion[label][predicted] += 1
        counts = by_label.setdefault(label, [0, 0])
        counts[1] += 1
        counts[0] += int(forwarded)
        brief = {
            "chat_id": row.get("chat_id"),
            "message_id": row.get("message_id"),
            "label": label,
            "predicted": predicted,
            "p_forward": row.get("p_forward"),
        }
        should = label in gate.forward
        if forwarded and should:
            tp += 1
        elif forwarded:
            fp += 1
            false_forwards.append(brief)
        elif should:
            fn += 1
            misses.append(brief)
    return {
        "confusion": confusion,
        "forwarded_by_label": {k: (v[0], v[1]) for k, v in by_label.items()},
        "forward_precision": tp / (tp + fp) if tp + fp else 0.0,
        "forward_recall": tp / (tp + fn) if tp + fn else 0.0,
        "misses": misses,
        "false_forwards": false_forwards,
    }


def acceptance(metrics: dict) -> dict[str, bool]:
    by_label = metrics["forwarded_by_label"]

    def get(name: str) -> tuple[int, int]:
        return tuple(by_label.get(name, (0, 0)))  # type: ignore[return-value]

    def all_forwarded(name: str) -> bool:
        fwd, total = get(name)
        return fwd == total

    weak_fwd, weak_total = get("lead_weak")
    other_fwd, other_total = get("other")
    return {
        "lead_direct_all": all_forwarded("lead_direct"),
        "reco_letters_all": all_forwarded("reco_letters"),
        "lead_weak_recall": weak_total == 0 or weak_fwd / weak_total >= 0.7,
        "competitor_forwarded": get("competitor")[0] <= 3,
        "other_share": other_total == 0 or other_fwd / other_total <= 0.10,
    }


def _load_gate(args: argparse.Namespace) -> CategoriesGate:
    if args.categories:
        with open(args.categories, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    else:
        instances = load_config().get("instances", [])
        found = [i for i in instances if i.get("name") == args.instance]
        if not found:
            raise SystemExit(f"Instance not found: {args.instance}")
        raw = found[0].get("categories")
    gate = _parse_categories(raw)
    if gate is None:
        raise SystemExit("No categories gate configured")
    return gate


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--instance", help="Instance name in data/config.yml")
    src.add_argument("--categories", help="YAML file with a categories gate")
    parser.add_argument("--export", action="append", required=True, default=[])
    parser.add_argument("--gold", required=True)
    parser.add_argument("--out", default="data/evals")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--label")
    return parser.parse_args(argv)


def _argmax(probabilities: dict[str, float]) -> str | None:
    if not probabilities:
        return None
    return max(probabilities, key=lambda n: probabilities[n])


async def run_eval(argv: list[str] | None = None, client=None) -> Path:
    """Run the evaluation and return the path of the written JSON report."""
    args = _parse_args(argv)
    try:
        categories.config.update(load_config())
    except FileNotFoundError:
        if args.instance:
            raise SystemExit("data/config.yml not found, cannot resolve --instance")
    gate = _load_gate(args)
    if client is None:
        client = categories.build_client()
    if client is None:
        raise SystemExit("TYPESAFE_API_KEY is not set (env or typesafe_api_key)")

    with open(args.gold, "r", encoding="utf-8") as f:
        gold = {
            (int(g["chat_id"]), int(g["message_id"])): g for g in json.load(f)
        }
    messages = load_export(args.export)

    todo: list[tuple[dict, dict]] = []
    unlabeled = skipped_bots = 0
    for msg in messages:
        g = gold.get((msg["chat_id"], msg["message_id"]))
        if g is None:
            unlabeled += 1
        elif g.get("is_bot"):
            skipped_bots += 1
        else:
            todo.append((msg, g))

    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def classify(msg: dict, g: dict) -> dict:
        async with sem:
            match = await categories.match_categories(
                gate, msg["text"], client=client
            )
        row = {
            "chat_id": msg["chat_id"],
            "message_id": msg["message_id"],
            "text": msg["text"],
            "label": g["label"],
        }
        if match is None:
            return {**row, "error": True}
        return {
            **row,
            "error": False,
            "category": _argmax(match.probabilities) or match.category,
            "forward": match.forward,
            "p_forward": match.p_forward,
            "probabilities": match.probabilities,
            "model": match.model,
        }

    results = await asyncio.gather(*(classify(m, g) for m, g in todo))
    ok = [r for r in results if not r["error"]]
    errors = len(results) - len(ok)
    metrics = compute_metrics(ok, gate)
    accept = acceptance(metrics)
    models = sorted({r["model"] for r in ok if r.get("model")})
    counts = {
        "evaluated": len(ok),
        "errors": errors,
        "unlabeled": unlabeled,
        "skipped_bots": skipped_bots,
    }

    label = args.label or datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"categories-eval-{label}.json"
    report = {
        "label": label,
        "gate": asdict(gate),
        "response_model": models[0] if len(models) == 1 else models,
        "counts": counts,
        "metrics": metrics,
        "acceptance": accept,
        "rows": results,
    }
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"model: {report['response_model']}")
    print("label: forwarded/total")
    for name, (fwd, total) in sorted(metrics["forwarded_by_label"].items()):
        print(f"  {name}: {fwd}/{total}")
    print(
        f"precision={metrics['forward_precision']:.3f} "
        f"recall={metrics['forward_recall']:.3f}"
    )
    print(
        "counts: " + " ".join(f"{k}={v}" for k, v in counts.items())
    )
    for name, passed in accept.items():
        print(f"  {'PASS' if passed else 'FAIL'} {name}")
    for title, items in (
        ("misses", metrics["misses"]),
        ("false_forwards", metrics["false_forwards"]),
    ):
        print(f"{title}:")
        for it in items:
            p = it["p_forward"]
            print(
                f"  {it['chat_id']}:{it['message_id']} {it['label']}"
                f"→{it['predicted']} {p:.2f}"
            )
    print(f"report: {path}")
    return path


def main() -> None:
    asyncio.run(run_eval(sys.argv[1:]))


if __name__ == "__main__":
    main()
