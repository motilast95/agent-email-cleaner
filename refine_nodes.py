import os
import re
from typing import Literal, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.types import interrupt
from pydantic import BaseModel

HAIKU_MODEL = "claude-haiku-4-5-20251001"


class RefineState(TypedDict):
    keep_threads: list[dict]    # [{thread_id, sender, subject, snippet}]
    clusters: list[dict]        # [{domain, thread_ids, count, example_subjects}]
    current_idx: int            # index into clusters
    current_question: str       # question text for the current cluster
    preference_profile: str     # accumulated preference rules, appended each round
    trash_queue: list[str]      # thread_ids confirmed for trash
    _reply: str                 # transient: reply from await_reply, read by process_reply


class ProcessReplyOutput(BaseModel):
    action: Literal["trash_all", "keep_all", "partial"]
    rule: str                   # short preference rule to remember, or empty string
    trash_ids: list[str]        # only populated for action=partial; ignored otherwise


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
    # Only sends if Twilio env vars are configured — skipped when using Agent Inbox
    to = os.environ.get("TWILIO_WHATSAPP_TO", "")
    if not to or to == "whatsapp:+1XXXXXXXXXX":
        return
    from twilio.rest import Client
    client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    client.messages.create(
        from_=os.environ["TWILIO_WHATSAPP_FROM"],
        to=to,
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
        f"Write a single question asking whether to trash emails from {cluster['domain']}. "
        f"Count: {cluster['count']}. Example subjects: {'; '.join(cluster['example_subjects'])}.{profile_hint}\n\n"
        "Rules: plain text only, no markdown, no headers, no bullet points, no character counts, no notes. "
        "One sentence, under 160 chars. "
        "Example output: 23 emails from substack.com — trash all? (yes/no/partial rule)"
    )
    question = llm.invoke([HumanMessage(content=prompt)]).content.strip()
    _send_whatsapp(question)
    idx = state["current_idx"]
    total = len(state["clusters"])
    print(f"  [{idx+1}/{total}] Question for {cluster['domain']}: {question}")
    return {"current_question": question}


def await_reply(state: RefineState) -> dict:
    cluster = state["clusters"][state["current_idx"]]
    reply = interrupt({
        "question": state["current_question"],
        "domain": cluster["domain"],
        "count": cluster["count"],
        "example_subjects": cluster["example_subjects"],
    })
    return {"_reply": reply}


def process_reply(state: RefineState) -> dict:
    cluster = state["clusters"][state["current_idx"]]
    reply = state["_reply"]
    llm = ChatAnthropic(model=HAIKU_MODEL, max_tokens=1024).with_structured_output(ProcessReplyOutput)

    profile_hint = (
        f"\n\nKnown preferences so far:\n{state['preference_profile']}"
        if state["preference_profile"] else ""
    )
    prompt = (
        f"The user was asked: \"{state['current_question']}\"\n"
        f"They replied: \"{reply}\"\n\n"
        f"Cluster: {cluster['count']} emails from {cluster['domain']}.\n"
        f"Available thread_ids: {cluster['thread_ids']}{profile_hint}\n\n"
        "Determine the action:\n"
        "- trash_all: user wants to trash all emails in this cluster\n"
        "- keep_all: user wants to keep all emails in this cluster\n"
        "- partial: user wants to trash some — populate trash_ids with the relevant subset\n\n"
        "Also extract a short preference rule if the user expressed one (e.g. 'always keep emails from Goldman'). Leave rule empty if none."
    )
    result: ProcessReplyOutput = llm.invoke([HumanMessage(content=prompt)])

    # Resolve trash_ids from action — don't rely on LLM to enumerate all IDs for trash_all
    if result.action == "trash_all":
        new_trash = cluster["thread_ids"]
    elif result.action == "keep_all":
        new_trash = []
    else:
        new_trash = result.trash_ids

    rule = result.rule.strip()
    profile = state["preference_profile"]
    if rule:
        profile = (profile + "\n" + rule).strip()

    print(f"  Reply parsed: action={result.action}, trashing {len(new_trash)} threads. Rule: {rule or '(none)'}")
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
    msg = f"Refinement complete! Reviewed {len(state['clusters'])} clusters. Check Gmail Trash for moved emails."
    _send_whatsapp(msg)
    print("  All clusters reviewed. Session complete.")
    return {}
