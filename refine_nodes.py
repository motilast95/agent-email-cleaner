import os
import re
from typing import TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.types import interrupt
from twilio.rest import Client

HAIKU_MODEL = "claude-haiku-4-5-20251001"


class RefineState(TypedDict):
    keep_threads: list[dict]    # [{thread_id, sender, subject, snippet}]
    clusters: list[dict]        # [{domain, thread_ids, count, example_subjects}]
    current_idx: int            # index into clusters
    current_question: str       # WhatsApp question text for the current cluster
    preference_profile: str     # accumulated preference rules, appended each round
    trash_queue: list[str]      # thread_ids confirmed for trash
    _reply: str                 # transient: reply from await_reply, read by process_reply


def make_fetch_keep_pile_node(gmail_client):
    def fetch_keep_pile(state: RefineState) -> dict:
        label_id = "Label_5"  # AI-Keep label ID (matches main.py LABEL_IDS)
        thread_ids = gmail_client.fetch_labeled_threads(label_id)
        print(f"Found {len(thread_ids)} AI-Keep threads. Fetching metadata...")

        threads = []
        BATCH = 100
        for i in range(0, len(thread_ids), BATCH):
            chunk = thread_ids[i:i + BATCH]
            batch_results = {}

            def make_cb(tid):
                def cb(req_id, resp, exc):
                    if exc is None:
                        batch_results[tid] = resp
                return cb

            batch = gmail_client.service.new_batch_http_request()
            for tid in chunk:
                batch.add(
                    gmail_client.service.users().threads().get(
                        userId="me", id=tid, format="metadata",
                        metadataHeaders=["From", "Subject"],
                    ),
                    callback=make_cb(tid),
                )
            batch.execute()

            for tid in chunk:
                detail = batch_results.get(tid)
                if detail:
                    msgs = detail.get("messages", [])
                    headers = {}
                    if msgs:
                        headers = {h["name"]: h["value"] for h in msgs[0]["payload"]["headers"]}
                    threads.append({
                        "thread_id": tid,
                        "sender": headers.get("From", ""),
                        "subject": headers.get("Subject", ""),
                        "snippet": msgs[0].get("snippet", "") if msgs else "",
                    })

            print(f"  Fetched metadata for {min(i + BATCH, len(thread_ids))}/{len(thread_ids)}...")

        return {
            "keep_threads": threads,
            "clusters": [],
            "current_idx": 0,
            "preference_profile": "",
            "trash_queue": [],
            "_reply": "",
        }
    return fetch_keep_pile


def cluster_ambiguous(state: RefineState) -> dict:
    domain_map: dict[str, list] = {}
    for t in state["keep_threads"]:
        match = re.search(r"@([\w.\-]+)", t["sender"])
        domain = match.group(1).lower() if match else "unknown"
        domain_map.setdefault(domain, []).append(t)

    clusters = []
    for domain, threads in sorted(domain_map.items(), key=lambda x: -len(x[1])):
        if len(threads) >= 3:
            clusters.append({
                "domain": domain,
                "thread_ids": [t["thread_id"] for t in threads],
                "count": len(threads),
                "example_subjects": [t["subject"] for t in threads[:3]],
            })
    print(f"Found {len(clusters)} clusters with ≥3 threads to review.")
    return {"clusters": clusters}


def _send_whatsapp(message: str) -> None:
    client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    client.messages.create(
        from_=os.environ["TWILIO_WHATSAPP_FROM"],
        to=os.environ["TWILIO_WHATSAPP_TO"],
        body=message,
    )


def build_and_send_question(state: RefineState) -> dict:
    cluster = state["clusters"][state["current_idx"]]
    llm = ChatAnthropic(model=HAIKU_MODEL, max_tokens=256)
    profile_hint = (
        f"\n\nKnown preferences:\n{state['preference_profile']}"
        if state["preference_profile"] else ""
    )
    prompt = (
        f"Draft a short WhatsApp question (≤160 chars) asking whether to trash emails from {cluster['domain']}. "
        f"Count: {cluster['count']}. Example subjects: {'; '.join(cluster['example_subjects'])}.{profile_hint} "
        "Be direct, e.g. '23 emails from substack.com — trash all? (yes/no/partial rule)'"
    )
    question = llm.invoke([HumanMessage(content=prompt)]).content.strip()
    _send_whatsapp(question)
    idx = state["current_idx"]
    total = len(state["clusters"])
    print(f"  [{idx+1}/{total}] Sent question for {cluster['domain']}: {question}")
    return {"current_question": question}


def await_reply(state: RefineState) -> dict:
    cluster = state["clusters"][state["current_idx"]]
    reply = interrupt({"question": state["current_question"], "domain": cluster["domain"]})
    return {"_reply": reply}


def process_reply(state: RefineState) -> dict:
    cluster = state["clusters"][state["current_idx"]]
    reply = state["_reply"]
    llm = ChatAnthropic(model=HAIKU_MODEL, max_tokens=512)

    prompt = (
        f"The user was asked: \"{state['current_question']}\"\n"
        f"They replied: \"{reply}\"\n\n"
        f"Cluster: {cluster['count']} emails from {cluster['domain']}.\n\n"
        "Return JSON with keys:\n"
        "  action: 'trash_all' | 'keep_all' | 'partial'\n"
        "  rule: short preference rule to remember (1 sentence, or '' if none)\n"
        "  trash_ids: list of thread_ids to trash (all of them if trash_all, [] if keep_all, subset if partial)\n\n"
        f"Available thread_ids: {cluster['thread_ids']}\n"
        "Respond with only JSON, no markdown."
    )
    raw = llm.invoke([HumanMessage(content=prompt)]).content.strip()

    import json, re as _re
    raw = _re.sub(r"^```(?:json)?\s*", "", raw)
    raw = _re.sub(r"\s*```$", "", raw)
    parsed = json.loads(raw)

    new_trash = parsed.get("trash_ids", [])
    rule = parsed.get("rule", "").strip()

    profile = state["preference_profile"]
    if rule:
        profile = (profile + "\n" + rule).strip()

    print(f"  Reply parsed: action={parsed.get('action')}, trashing {len(new_trash)} threads. Rule: {rule or '(none)'}")
    return {
        "trash_queue": state["trash_queue"] + new_trash,
        "preference_profile": profile,
        "_reply": "",
    }


def make_trash_confirmed_node(gmail_client):
    def trash_confirmed(state: RefineState) -> dict:
        queue = state["trash_queue"]
        for tid in queue:
            gmail_client.trash_thread(tid)
        if queue:
            print(f"  Trashed {len(queue)} confirmed threads.")
        return {"trash_queue": [], "current_idx": state["current_idx"] + 1}
    return trash_confirmed


def check_termination(state: RefineState) -> str:
    return "ask" if state["current_idx"] < len(state["clusters"]) else "done"


def notify_done(state: RefineState) -> dict:
    total_trashed = sum(
        c["count"] for c in state["clusters"]
    )  # approximation; actual count was in trash_queue before clearing
    msg = f"Refinement complete! Reviewed {len(state['clusters'])} clusters. Check Gmail Trash for moved emails."
    _send_whatsapp(msg)
    print("  All clusters reviewed. Session complete.")
    return {}
