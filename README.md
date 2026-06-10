# agent-email-cleaner

Three modules, one repo:

| Module | Folder | What it does |
|--------|--------|--------------|
| **1 — Labeling** | `labeling/` | Fetch inbox → Claude classifies → apply AI-Keep / AI-Delete labels |
| **2 — Refine** | `refine/` | WhatsApp / Agent Inbox loop to review AI-Keep pile and trash by preference |
| **3 — Ops** | `ops/` | Manual scripts (bulk trash AI-Delete, fix duplicate labels) |

Shared Gmail client and label IDs live in `shared/`.

## Setup

1. Copy `.env.example` → `.env` and add `ANTHROPIC_API_KEY`
2. Put `credentials.json` in the repo root (from Google Cloud Console)
3. Run once: `py -m labeling.setup_labels` → paste IDs into `shared/labels.py`
4. Edit personal rules in `labeling/context.md`

## Run (from repo root)

```bash
# Module 1 — label inbox
py -m labeling

# Module 1 — utilities
py -m labeling.setup_labels
py -m labeling.verify_labels

# Module 2 — refine loop
py refine/refine.py
langgraph dev   # uses refine/agent.py via langgraph.json

# Module 3 — ops
py ops/trash_deletes.py
py ops/cleanup_labels.py

# Smoke test Gmail auth
py test_gmail_smoke.py
```
