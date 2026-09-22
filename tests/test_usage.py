"""Each test is a shape met for real, or a specific trap.

`test_nested_object` is this module's negative control: a naive regular
expression fails it, and used to return nothing for calls that were billed.
If it ever goes green for the wrong reason, the check stops checking anything.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_meter.usage import extract


def test_deepseek_non_streaming():
    body = b'''{"choices":[],"usage":{"prompt_tokens":14950,"completion_tokens":146,
      "total_tokens":15096,"prompt_cache_hit_tokens":14720,"prompt_cache_miss_tokens":230}}'''
    u = extract(body)
    assert u.input_total == 14950
    assert u.cache_read == 14720
    assert u.input_fresh == 230
    assert u.output == 146


def test_nested_object():
    """Negative control: usage holds a sub-object before its closing brace."""
    body = b'''{"usage":{"prompt_tokens":1000,"completion_tokens":50,
      "prompt_tokens_details":{"cached_tokens":800,"audio_tokens":0},
      "completion_tokens_details":{"reasoning_tokens":10}}}'''
    u = extract(body)
    assert u is not None, "a naive regular expression returns None here"
    assert u.input_total == 1000
    assert u.cache_read == 800
    assert u.input_fresh == 200
    assert u.output == 50


def test_anthropic_streaming():
    """Input arrives first, output last. Taking the last event loses the input."""
    body = b'''event: message_start
data: {"message":{"usage":{"input_tokens":300,"cache_read_input_tokens":2000,
  "cache_creation_input_tokens":100,"output_tokens":1}}}

event: message_delta
data: {"usage":{"output_tokens":420}}

data: [DONE]
'''
    u = extract(body)
    assert u.input_total == 2400, "Anthropic excludes cached tokens from input_tokens"
    assert u.cache_read == 2000
    assert u.cache_write == 100
    assert u.output == 420, "the last event carries the real output"


def test_brace_inside_a_string():
    body = b'{"model":"odd}{name","usage":{"prompt_tokens":10,"completion_tokens":2}}'
    u = extract(body)
    assert u.input_total == 10 and u.output == 2


def test_no_usage():
    assert extract(b'{"error":{"message":"invalid key"}}') is None
    assert extract(b"") is None


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
