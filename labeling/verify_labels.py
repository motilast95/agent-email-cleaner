"""
Diagnostic: fetch the last 50 inbox emails and print their actual Gmail labelIds.
"""
from dotenv import load_dotenv

from shared.gmail_client import GmailClient
from shared.labels import LABEL_IDS

load_dotenv()

AI_LABEL_IDS = {v: k for k, v in LABEL_IDS.items()}


def main():
    gmail = GmailClient()
    results = gmail.service.users().messages().list(
        userId="me", maxResults=50
    ).execute()

    messages = results.get("messages", [])
    print(f"Checking {len(messages)} messages...\n")

    labeled = 0
    unlabeled = 0

    for msg in messages:
        detail = gmail.service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["Subject"],
        ).execute()

        label_ids = detail.get("labelIds", [])
        subject = next(
            (h["value"] for h in detail["payload"]["headers"] if h["name"] == "Subject"),
            "(no subject)"
        )

        ai_labels = [AI_LABEL_IDS[l] for l in label_ids if l in AI_LABEL_IDS]

        subject_safe = subject[:60].encode("ascii", errors="replace").decode("ascii")
        if ai_labels:
            labeled += 1
            print(f"[LABELED]   {msg['id'][:12]}  {', '.join(ai_labels):<22}  {subject_safe}")
        else:
            unlabeled += 1
            print(f"[NO AI LBL] {msg['id'][:12]}  {'':22}  {subject_safe}")

    print(f"\nSummary: {labeled} with AI label, {unlabeled} without AI label (out of {len(messages)})")


if __name__ == "__main__":
    main()
