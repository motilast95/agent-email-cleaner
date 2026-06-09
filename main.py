import sys
sys.stdout.reconfigure(line_buffering=True)

from dotenv import load_dotenv
load_dotenv()  # must run before langchain imports so tracing env vars are set

from gmail_client import GmailClient
from graph import build_graph

LABEL_IDS = {
    "AI-Delete": "Label_6",
    "AI-Keep": "Label_5",
}


def main():
    gmail = GmailClient()
    graph = build_graph(gmail)

    initial_state = {
        "emails": [],
        "classifications": [],
        "bodies": {},
        "label_ids": LABEL_IDS,
    }

    print("Running email triage agent...")
    result = graph.invoke(initial_state)

    counts = {}
    for c in result["classifications"]:
        counts[c["label"]] = counts.get(c["label"], 0) + 1
    print("\nLabel breakdown:")
    for label, count in sorted(counts.items()):
        print(f"  {label}: {count}")


if __name__ == "__main__":
    main()
