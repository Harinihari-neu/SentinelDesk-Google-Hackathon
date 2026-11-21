# SentinelDesk-Google-Hackathon

Multilingual incident triage and grounded resolution copilot (GDG Cloud Boston x Northeastern Hackathon).

**Start here:** [docs/PLAN.md](docs/PLAN.md). AI engineering handoff: [docs/AI_ENG_ACTION_PLAN.md](docs/AI_ENG_ACTION_PLAN.md). has the problem, architecture diagram, metrics, team options and timeline.
Interfaces: [docs/CONTRACTS.md](docs/CONTRACTS.md). Team rules: [AGENTS.md](AGENTS.md).

## Layout
| Folder | What it holds |
|---|---|
| `app/` | Pipeline and API: triage, retrieval, gate, drafting, checks |
| `ui/` | Operator console |
| `data/` | Split script and loaders (raw data is gitignored) |
| `eval/` | Eval harness, adversarial set, Trust Report |
| `docs/` | Plan and architecture |

## Setup
_TBD once the stack is chosen._ Copy `.env.example` to `.env` and fill in keys.

## Mocked for the demo
_List anything mocked here. Never present a mock as real._
