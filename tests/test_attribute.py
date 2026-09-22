"""Attribution has to be exact: every call lands in one group, and only one.

`test_no_call_is_lost` is the negative control here. An implementation that
looks up the first matching window, or that skips calls outside every window,
passes the other tests and quietly drops spend.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_meter.attribute import INTERACTIVE, Window, _epoch, attribute


def calls(*timestamps):
    return [{"ts": t, "input_total": 10, "output": 1} for t in timestamps]


def test_call_inside_one_window():
    w = [Window("brief", 100, 200)]
    assert list(attribute(calls(150), w)) == ["brief"]


def test_call_outside_every_window_is_interactive():
    w = [Window("brief", 100, 200)]
    assert list(attribute(calls(50, 250), w)) == [INTERACTIVE]


def test_shortest_window_wins_when_they_overlap():
    """A long parent job must not absorb a short child's cost."""
    w = [Window("parent", 100, 400), Window("child", 180, 200)]
    grouped = attribute(calls(190), w)
    assert list(grouped) == ["child"], grouped


def test_no_call_is_lost():
    """Negative control: totals per job must equal the overall total."""
    w = [Window("a", 0, 10), Window("b", 5, 30), Window("c", 100, 110)]
    everything = calls(-5, 1, 7, 20, 50, 105, 200)
    grouped = attribute(everything, w)
    placed = sum(len(v) for v in grouped.values())
    assert placed == len(everything), f"{placed} calls placed out of {len(everything)}"


def test_no_call_is_counted_twice():
    w = [Window("a", 0, 100), Window("b", 10, 90), Window("c", 20, 80)]
    grouped = attribute(calls(50), w)
    assert sum(len(v) for v in grouped.values()) == 1


def test_boundaries_are_inclusive():
    w = [Window("a", 100, 200)]
    assert list(attribute(calls(100), w)) == ["a"]
    assert list(attribute(calls(200), w)) == ["a"]


def test_timestamps_parse_both_ways():
    assert _epoch(1700000000.5) == 1700000000.5
    assert _epoch("2026-09-22T07:30:00+02:00") is not None
    assert _epoch("pas une date") is None
    assert _epoch(None) is None


def test_a_call_is_never_attributed_to_another_agents_job():
    """Two agents on one host: each scheduler explains only its own agent."""
    w = [Window("vie-brief", 100, 200, "vie"), Window("dehors-watch", 100, 200, "dehors")]
    call = [{"ts": 150, "label": "dehors", "input_total": 10, "output": 1}]
    assert list(attribute(call, w)) == ["dehors-watch"]


def test_unlabelled_window_matches_any_agent():
    w = [Window("job", 100, 200)]
    call = [{"ts": 150, "label": "whoever", "input_total": 10, "output": 1}]
    assert list(attribute(call, w)) == ["job"]


def test_no_windows_at_all():
    grouped = attribute(calls(1, 2, 3), [])
    assert list(grouped) == [INTERACTIVE] and len(grouped[INTERACTIVE]) == 3


if __name__ == "__main__":
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as e:
                failures.append(name)
                print(f"  FAIL {name}: {e}")
    print()
    print("all checks pass" if not failures else f"{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
