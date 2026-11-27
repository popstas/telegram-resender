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
from .telegram_utils import mark_quotes


def _chat_id(msg: dict) -> int | None:
    peer = msg.get("peer_id") or {}
    for key in ("channel_id", "chat_id", "user_id"):
        if peer.get(key) is not None:
            return int(peer[key])
    return None


def load_export(paths: list[str]) -> list[dict]:
    """Read export files into ``{"chat_id", "message_id", "text"}`` rows, skipping empty messages.

    A reply also gets ``reply_to``: the parent text when the parent is in the exports.
    Quotes (``MessageEntityBlockquote`` in ``entities``) are marked with ``"> "``,
    the way the live gate sees them.
    """
    rows: list[dict] = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for msg in data:
            text = mark_quotes(msg.get("message"), msg.get("entities"))
            if not text:
                continue
            row = {"chat_id": _chat_id(msg), "message_id": int(msg["id"]), "text": text}
            parent = categories.reply_parent_id(msg.get("reply_to"))
            if parent is not None:
                row["reply_to_msg_id"] = parent
            rows.append(row)
    texts = {(r["chat_id"], r["message_id"]): r["text"] for r in rows}
    for row in rows:
        parent = row.pop("reply_to_msg_id", None)
        if parent is not None and (row["chat_id"], parent) in texts:
            row["reply_to"] = texts[(row["chat_id"], parent)]
    return rows


def load_rows(path: str) -> list[dict]:
    """Read JSONL ``{"id", "text", "label", "owner_previous"?}`` rows (blank lines skipped)."""
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            missing = [k for k in ("id", "text", "label") if k not in raw]
            if missing:
                raise SystemExit(f"{path}:{n}: missing keys: {', '.join(missing)}")
            rows.append(
                {
                    "id": str(raw["id"]),
                    "text": raw["text"],
                    "label": raw["label"],
                    "owner_previous": list(raw.get("owner_previous") or []),
                }
            )
    return rows


def _ref(item: dict) -> dict:
    """Row identity: ``id`` in --rows mode, chat_id/message_id for exports."""
    if "id" in item:
        return {"id": item["id"]}
    return {"chat_id": item.get("chat_id"), "message_id": item.get("message_id")}


def _ref_str(item: dict) -> str:
    return item["id"] if "id" in item else f"{item['chat_id']}:{item['message_id']}"


def _should_forward(label: str, gate: CategoriesGate, binary: bool) -> bool:
    return label in gate.forward or (binary and label == "forward")


def compute_metrics(
    rows: list[dict], gate: CategoriesGate, binary: bool = False
) -> dict:
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
            **_ref(row),
            "label": label,
            "predicted": row.get("top_category") or predicted,
            "p_forward": row.get("p_forward"),
        }
        should = _should_forward(label, gate, binary)
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

    def share_ok(name: str, limit: float, upper: bool) -> bool:
        fwd, total = get(name)
        if total == 0:
            return False
        return fwd / total <= limit if upper else fwd / total >= limit

    return {
        "no_errors": metrics.get("errors", 0) == 0,
        "lead_direct_all": get("lead_direct")[1] > 0 and all_forwarded("lead_direct"),
        "reco_letters_all": get("reco_letters")[1] > 0
        and all_forwarded("reco_letters"),
        "lead_weak_recall": share_ok("lead_weak", 0.7, upper=False),
        "competitor_forwarded": get("competitor")[0] <= 3,
        "other_share": share_ok("other", 0.10, upper=True),
    }


def acceptance_rows(
    metrics: dict, rows: list[dict], gate: CategoriesGate
) -> dict[str, bool]:
    """Spec criterion for --rows: no errors, at most one missed forward, >=60% of rejects cut."""
    rejects = [r for r in rows if not _should_forward(r["label"], gate, True)]
    cut = sum(1 for r in rejects if not r["forward"])
    return {
        "no_errors": metrics.get("errors", 0) == 0,
        "forward_recall": len(metrics["misses"]) <= 1,
        "reject_cut_60": bool(rejects) and cut / len(rejects) >= 0.6,
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
    parser.add_argument("--export", action="append", default=[])
    parser.add_argument("--gold")
    parser.add_argument(
        "--rows", help="JSONL rows with owner_previous (instead of --export/--gold)"
    )
    parser.add_argument("--out", default="data/evals")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--label")
    args = parser.parse_args(argv)
    if args.rows:
        if args.export or args.gold:
            parser.error("--rows cannot be combined with --export/--gold")
    elif not (args.export and args.gold):
        parser.error("either --rows or both --export and --gold are required")
    return args


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

    known = set(gate.forward) | set(gate.reject)
    if args.rows:
        known |= {"forward", "reject"}
        loaded = load_rows(args.rows)
        labelled = [(r["label"], r) for r in loaded]
    else:
        with open(args.gold, "r", encoding="utf-8") as f:
            gold = {(int(g["chat_id"]), int(g["message_id"])): g for g in json.load(f)}
        labelled = [(g["label"], g) for g in gold.values()]
    unknown: dict[str, int] = {}
    for label, _ in labelled:
        if label not in known:
            unknown[label] = unknown.get(label, 0) + 1
    if unknown:
        where = "Row" if args.rows else "Gold"
        detail = ", ".join(f"{k!r} ({v} rows)" for k, v in sorted(unknown.items()))
        raise SystemExit(f"{where} labels not in the gate's categories: {detail}")

    unlabeled = skipped_bots = 0
    todo: list[dict] = []
    if args.rows:
        todo = loaded
    else:
        for msg in load_export(args.export):
            g = gold.get((msg["chat_id"], msg["message_id"]))
            if g is None:
                unlabeled += 1
            elif g.get("is_bot"):
                skipped_bots += 1
            else:
                reply_to = msg.get("reply_to") if gate.reply_context else None
                todo.append({**msg, "label": g["label"], "reply_to": reply_to})

    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def classify(item: dict) -> dict:
        extra = {}
        if item.get("owner_previous"):
            extra["owner_previous"] = item["owner_previous"]
        async with sem:
            match = await categories.match_categories(
                gate,
                item["text"],
                reply_to=item.get("reply_to"),
                client=client,
                **extra,
            )
        row = {**_ref(item), "text": item["text"], "label": item["label"]}
        if args.rows:
            row["owner_previous"] = item["owner_previous"]
        if match is None:
            return {**row, "error": True}
        return {
            **row,
            "error": False,
            "category": _argmax(match.probabilities),
            "top_category": match.category,
            "forward": match.forward,
            "p_forward": match.p_forward,
            "probabilities": match.probabilities,
            "model": match.model,
        }

    results = await asyncio.gather(*(classify(i) for i in todo))
    ok = [r for r in results if not r["error"]]
    errors = len(results) - len(ok)
    metrics = compute_metrics(ok, gate, binary=bool(args.rows))
    metrics["errors"] = errors
    error_ids = [_ref(r) for r in results if r["error"]]
    metrics["error_ids"] = error_ids
    accept = acceptance_rows(metrics, ok, gate) if args.rows else acceptance(metrics)
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
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"model: {report['response_model']}")
    print("label: forwarded/total")
    for name, (fwd, total) in sorted(metrics["forwarded_by_label"].items()):
        print(f"  {name}: {fwd}/{total}")
    print(
        f"precision={metrics['forward_precision']:.3f} "
        f"recall={metrics['forward_recall']:.3f}"
    )
    print("counts: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    for name, passed in accept.items():
        print(f"  {'PASS' if passed else 'FAIL'} {name}")
    for title, items in (
        ("misses", metrics["misses"]),
        ("false_forwards", metrics["false_forwards"]),
    ):
        print(f"{title}:")
        for it in items:
            p = it["p_forward"]
            print(f"  {_ref_str(it)} {it['label']}→{it['predicted']} {p:.2f}")
    print("errors:")
    for e in error_ids:
        print(f"  {_ref_str(e)}")
    print(f"report: {path}")
    return path


def main() -> None:
    asyncio.run(run_eval(sys.argv[1:]))


if __name__ == "__main__":
    main()
