import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from labeling.prompts import (
    BATCH_PROMPT_TEMPLATE,
    RECLASSIFY_PROMPT_TEMPLATE,
    RECLASSIFY_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
)
from shared.paths import PROJECT_ROOT

BATCH_SIZE = 25
HAIKU_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 8192
CONTEXT_FILE = Path(__file__).parent / "context.md"
CHECKPOINT_FILE = PROJECT_ROOT / "classifications_cache.json"
BODY_TRUNCATE = 2000


def _load_context() -> str:
    if CONTEXT_FILE.exists():
        with open(CONTEXT_FILE) as f:
            content = f.read().strip()
        if content:
            return f"\n\n## User-provided context (takes priority over default rules)\n{content}"
    return ""


def _parse_json(text: str) -> list:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    start = text.find("[")
    if start == -1:
        return json.loads(text)
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    return json.loads(text[start:])


class EmailState(TypedDict):
    emails: list[dict]
    classifications: list[dict]
    bodies: dict[str, str]
    label_ids: dict[str, str]


def make_fetch_node(gmail_client):
    def fetch_emails(state: EmailState) -> dict:
        emails = gmail_client.fetch_metadata(max_results=5000)
        return {"emails": emails}
    return fetch_emails


def make_fetch_bodies_node(gmail_client):
    def fetch_bodies(state: EmailState) -> dict:
        ambiguous_ids = [
            c["id"] for c in state["classifications"] if c.get("confidence") == "low"
        ]
        bodies = {}
        for email_id in ambiguous_ids:
            bodies[email_id] = gmail_client.fetch_body(email_id)
        return {"bodies": bodies}
    return fetch_bodies


def make_apply_labels_node(gmail_client):
    def apply_labels(state: EmailState) -> dict:
        if CHECKPOINT_FILE.exists():
            CHECKPOINT_FILE.unlink()

        label_ids = state["label_ids"]
        all_ai_label_ids = list(label_ids.values())

        email_meta = {e["id"]: e for e in state["emails"]}
        groups: dict[str, list[str]] = {}
        for c in state["classifications"]:
            label = c["label"]
            if label in label_ids:
                thread_id = email_meta.get(c["id"], {}).get("thread_id", c["id"])
                groups.setdefault(label, []).append(thread_id)

        for label_name, message_ids in groups.items():
            label_id = label_ids[label_name]
            remove_ids = [l for l in all_ai_label_ids if l != label_id]
            gmail_client.batch_apply_label(message_ids, label_id, remove_ids)
            print(f"  Applied {label_name} to {len(message_ids)} emails.")

        return {}
    return apply_labels


MAX_WORKERS = 4


def classify_metadata(state: EmailState) -> dict:
    llm = ChatAnthropic(model=HAIKU_MODEL, max_tokens=MAX_TOKENS)
    emails = state["emails"]
    system_prompt = SYSTEM_PROMPT + _load_context()

    known_labels = {"AI-Delete", "AI-Keep"}
    cached: dict[str, dict] = {}
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            raw = json.load(f)
        cached = {c["id"]: c for c in raw if c.get("label") in known_labels}
        dropped = len(raw) - len(cached)
        msg = f"  Resuming from checkpoint: {len(cached)} emails already classified."
        if dropped:
            msg += f" Dropped {dropped} stale entries with old labels."
        print(msg)

    to_classify = [e for e in emails if e["id"] not in cached]
    total = len(to_classify)

    if total == 0:
        if not emails:
            print("  Nothing to do — inbox fully labeled.")
        else:
            print("  All emails already classified from checkpoint.")
        return {"classifications": list(cached.values())}

    batches = [to_classify[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
    all_classifications = list(cached.values())
    lock = threading.Lock()
    completed = 0

    def classify_batch(batch):
        email_text = "\n\n".join(
            f"[{j+1}] ID: {e['id']}\nFrom: {e['sender']}\nSubject: {e['subject']}\nSnippet: {e['snippet']}"
            for j, e in enumerate(batch)
        )
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=BATCH_PROMPT_TEMPLATE.format(n=len(batch), emails=email_text)),
        ])
        return _parse_json(response.content)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(classify_batch, batch): batch for batch in batches}
        for future in as_completed(futures):
            results = future.result()
            with lock:
                all_classifications.extend(results)
                completed += len(results)
                with open(CHECKPOINT_FILE, "w") as f:
                    json.dump(all_classifications, f)
                print(f"  Classified {completed}/{total} new emails...")

    return {"classifications": all_classifications}


def reclassify_ambiguous(state: EmailState) -> dict:
    llm = ChatAnthropic(model=HAIKU_MODEL, max_tokens=MAX_TOKENS)
    bodies = state["bodies"]
    ambiguous = [c for c in state["classifications"] if c.get("confidence") == "low"]

    if not ambiguous:
        return {}

    email_meta = {e["id"]: e for e in state["emails"]}
    updated: dict[str, dict] = {}
    total = len(ambiguous)

    for i in range(0, total, BATCH_SIZE):
        batch = ambiguous[i:i + BATCH_SIZE]
        email_text = "\n\n".join(
            f"[{j+1}] ID: {c['id']}\nFrom: {email_meta.get(c['id'], {}).get('sender', '')}\nSubject: {email_meta.get(c['id'], {}).get('subject', '')}\nBody:\n{bodies.get(c['id'], '(no body)')[:BODY_TRUNCATE]}"
            for j, c in enumerate(batch)
        )
        response = llm.invoke([
            SystemMessage(content=RECLASSIFY_SYSTEM_PROMPT + _load_context()),
            HumanMessage(content=RECLASSIFY_PROMPT_TEMPLATE.format(n=len(batch), emails=email_text)),
        ])
        updated.update({c["id"]: c for c in _parse_json(response.content)})
        print(f"  Reclassified {min(i + BATCH_SIZE, total)}/{total} ambiguous emails...")

    merged = [
        updated.get(c["id"], c) if c.get("confidence") == "low" else c
        for c in state["classifications"]
    ]

    return {"classifications": merged}


def route_after_classify(state: EmailState) -> str:
    ambiguous = [c for c in state["classifications"] if c.get("confidence") == "low"]
    return "fetch_bodies" if ambiguous else "apply_labels"
