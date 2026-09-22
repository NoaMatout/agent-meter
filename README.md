# agent-meter

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![No dependencies](https://img.shields.io/badge/dependencies-none-lightgrey.svg)](pyproject.toml)

Measure what an autonomous agent actually spends, without modifying the agent.

A small relay sits between your agent and the model API. It forwards every
request untouched, streams the response back byte for byte, and records the
counters the provider reports itself. One port per agent, so consumption is
attributable without ever reading what is exchanged.

## Why this exists

I run a personal agent on a VPS. Looking at four days of API usage, I went
looking for the culprit and found it immediately: a job that wakes up every
fifteen minutes to check for urgent mail. Ninety-six wake-ups a day, obviously
that was it.

It cost nothing at all. Out of 370 scheduled wake-ups, exactly one reached the
model, because a cheap script decides whether waking it is worth it.

I only know that because I built this. Before it, my agent framework kept no
record of consumption: the provider returns the counters on every call, and
they were being thrown away. Measuring first turned two confident theories into
two wrong ones in about ten minutes.

## What it records, and what it never records

Recorded: input tokens, output tokens, the share served from cache, cache
writes, duration, HTTP status, requested path, and the label of the agent that
made the call.

Never recorded: request bodies, response bodies, and the API key, which only
passes through.

## Quick start

```bash
git clone https://github.com/NoaMatout/agent-meter.git
cd agent-meter
cp agent-meter.example.toml agent-meter.toml   # set your ports, upstreams, prices
python3 -m agent_meter.proxy agent-meter.toml
```

Then point each agent at its own port instead of the provider:

```
base_url: http://127.0.0.1:8787/v1      # instead of https://api.deepseek.com/v1
```

Read the meter:

```bash
python3 -m agent_meter.report agent-meter.toml day     # or week, or all
```

A systemd unit is in [`systemd/`](systemd/agent-meter.service). It runs under
`DynamicUser` with `Restart=always`.

## What the report looks like

```
last 24 hours
  agent          calls       input    output  cached     cost $   errors
  life              17      522600     21060     94%     0.0190        4
  outside            7      253000     13600     93%     0.0108        0
  total                                                  0.0298
  1 call(s) with no declared price for their model: not costed.

by hour
  22/09 02h   13 calls    543660 tokens   0.0190$ ####################################
  22/09 03h    4 calls         0 tokens   0.0000$ #
  22/09 10h    6 calls    243900 tokens   0.0108$ ################

heaviest 5 calls
  22/09 02:53:00  life       input    65400 output    2340 cached    62130    8.1s  0.0021$
  22/09 10:53:42  outside    input    61000 output    2900 cached    56730   14.0s  0.0026$
```

Three things that report made visible on my own setup, none of which I had
guessed:

- A trivial three-word question costs about 15 000 input tokens. That floor is
  the framework's system prompt and its tool schemas, and pruning installed
  skills changed it by exactly zero.
- Repeated calls run at 98 percent cache. The same call right after editing the
  agent's configuration file runs at 31 percent. Editing an agent's
  instructions costs more than running it.
- A third of the recorded calls are capability probes to endpoints the provider
  does not implement. They return 404, cost nothing, and were invisible before.

## Supported providers

Any OpenAI-compatible endpoint, plus Anthropic. The differences are handled in
[`agent_meter/usage.py`](agent_meter/usage.py):

| Provider | Input field | Cache field | Note |
|---|---|---|---|
| OpenAI and compatibles | `prompt_tokens` | `prompt_tokens_details.cached_tokens` | input includes cache |
| DeepSeek | `prompt_tokens` | `prompt_cache_hit_tokens` | reports the miss count directly |
| Anthropic | `input_tokens` | `cache_read_input_tokens` | input excludes cache, so it is added back |

## Limits, honestly

**It is on the critical path.** If the relay stops, your agent cannot reach the
model. Run it under a supervisor with automatic restart, and keep the rollback
to one line: put the provider URL back in the agent's configuration.

**It only knows what the provider reports.** A provider that returns no usage
object produces no row. The relay never estimates token counts.

**It modifies one thing.** On streaming requests to `chat/completions`, it adds
`stream_options.include_usage`, because OpenAI-compatible APIs otherwise report
nothing while streaming. Everything else passes through byte for byte, and the
end-to-end test asserts it.

**Anthropic cache writes are an approximation** unless you declare
`cache_write` in the price table. By default they are billed at the cache-miss
rate, which understates them.

**Attribution is per port, not per task.** The relay knows which agent called,
not which job. Correlating with your scheduler is up to you.

**Tokens are not an invoice.** Your provider's dashboard remains authoritative.
This tool tells you where the tokens go, not what you owe.

**Bind to localhost.** The listener speaks plain HTTP and performs no
authentication. It is meant to sit on the same machine as the agent.

## Tests

```bash
python3 tests/test_usage.py        # the usage parser, including the traps
python3 tests/test_end_to_end.py   # fake provider, relay, store, report
```

`test_nested_object` is a negative control. The naive regular expression this
code started with returns nothing for that input, and did so in production for
calls that were billed. If that test passes for the wrong reason, the check has
stopped checking.

## License

MIT.
