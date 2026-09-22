"""Reading back what the agents consumed."""

from __future__ import annotations

import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

from .pricing import Prices
from .store import Store
from .usage import Usage


def _usage(row) -> Usage:
    return Usage(row["input_total"], row["cache_read"], row["cache_write"],
                 row["input_fresh"], row["output"])


def _total_cost(rows, prices) -> tuple[float, int]:
    """Return the priceable total and how many calls had no known price.

    A call that consumed nothing is not an unpriced call. Capability probes and
    refused requests land here in numbers, and counting them as missing prices
    would bury the one model that genuinely has no price declared.
    """
    total, unknown = 0.0, 0
    for r in rows:
        usage = _usage(r)
        if usage.is_empty():
            continue
        c = prices.cost(usage, r["ts"], r["model"])
        if c is None:
            unknown += 1
        else:
            total += c
    return total, unknown


def summary(title: str, rows, prices) -> None:
    print(f"\n{title}")
    if not rows:
        print("  no calls")
        return
    per = {}
    for r in rows:
        a = per.setdefault(r["label"], {"n": 0, "inp": 0, "out": 0, "cache": 0, "err": 0, "rows": []})
        a["n"] += 1
        a["inp"] += r["input_total"]
        a["out"] += r["output"]
        a["cache"] += r["cache_read"]
        a["err"] += 1 if (r["status"] or 0) >= 400 else 0
        a["rows"].append(r)
    print(f"  {'agent':12} {'calls':>7} {'input':>11} {'output':>9} {'cached':>7} {'cost $':>10} {'errors':>8}")
    total, unknown = 0.0, 0
    for name, a in sorted(per.items()):
        cost, unk = _total_cost(a["rows"], prices)
        total += cost
        unknown += unk
        share = 100 * a["cache"] / a["inp"] if a["inp"] else 0
        print(f"  {name:12} {a['n']:7} {a['inp']:11} {a['out']:9} {share:6.0f}% {cost:10.4f} {a['err']:8}")
    print(f"  {'total':12} {'':7} {'':11} {'':9} {'':7} {total:10.4f}")
    if unknown:
        print(f"  {unknown} call(s) with no declared price for their model: not costed.")


def by_hour(rows, prices) -> None:
    if not rows:
        return
    buckets = {}
    for r in rows:
        key = datetime.fromtimestamp(r["ts"]).strftime("%d/%m %Hh")
        b = buckets.setdefault(key, [0, 0, 0.0])
        b[0] += 1
        b[1] += r["input_total"] + r["output"]
        b[2] += prices.cost(_usage(r), r["ts"], r["model"]) or 0.0
    print("\nby hour")
    peak = max(b[1] for b in buckets.values()) or 1
    for key in sorted(buckets):
        n, tokens, cost = buckets[key]
        print(f"  {key:9} {n:4} calls {tokens:9} tokens {cost:8.4f}$ {'#' * max(1, round(36 * tokens / peak))}")


def heaviest(rows, prices, n: int = 5) -> None:
    rows = sorted(rows, key=lambda r: -(r["input_total"] + r["output"]))[:n]
    if not rows:
        return
    print(f"\nheaviest {len(rows)} calls")
    for r in rows:
        when = datetime.fromtimestamp(r["ts"]).strftime("%d/%m %H:%M:%S")
        c = prices.cost(_usage(r), r["ts"], r["model"])
        cost = f"{c:.4f}$" if c is not None else "price unknown"
        print(f"  {when}  {r['label']:10} input {r['input_total']:8} output {r['output']:7}"
              f" cached {r['cache_read']:8} {r['duration']:6.1f}s  {cost}")


def refusals(rows) -> None:
    """What the provider actually said when it refused."""
    refused = {}
    for r in rows:
        if (r["status"] or 0) >= 400 and (r["error"] if "error" in r.keys() else None):
            key = (r["status"], r["error"])
            refused[key] = refused.get(key, 0) + 1
    if not refused:
        return
    print("\nrefusals, as the provider explained them")
    for (status, message), n in sorted(refused.items(), key=lambda kv: -kv[1])[:6]:
        print(f"  {n:4} x  {status}  {message}")


def main() -> None:
    args = list(sys.argv[1:])
    config = "agent-meter.toml"
    if args and args[0].endswith(".toml"):
        config = args.pop(0)
    window = args[0] if args else "day"

    if not Path(config).exists():
        sys.exit(f"configuration not found: {config}")
    with open(config, "rb") as f:
        raw = tomllib.load(f)
    store = Store(raw.get("database", "./usage.db"))
    prices = Prices(raw)

    now = time.time()
    spans = {"day": 86400, "week": 7 * 86400, "all": now}
    if window not in spans:
        sys.exit("usage: agent-meter-report [config.toml] [day|week|all]")

    rows = store.read(now - spans[window])
    titles = {"day": "last 24 hours", "week": "last 7 days", "all": "since metering started"}
    summary(titles[window], rows, prices)

    if window == "day":
        by_hour(rows, prices)
    elif window == "week":
        print("\nper day")
        for i in range(6, -1, -1):
            start = now - (i + 1) * 86400
            day = [r for r in rows if start <= r["ts"] < start + 86400]
            cost, _ = _total_cost(day, prices)
            tokens = sum(r["input_total"] + r["output"] for r in day)
            label = datetime.fromtimestamp(start + 43200).strftime("%d/%m")
            print(f"  {label}  {len(day):4} calls {tokens:10} tokens {cost:9.4f}$")
    refusals(rows)
    heaviest(rows, prices, 8 if window == "all" else 5)


if __name__ == "__main__":
    main()
