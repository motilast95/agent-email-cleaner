"""Run once to create Gmail labels and print their IDs."""
from gmail_client import GmailClient

LABEL_NAMES = ["AI-Delete", "AI-Keep"]

def main():
    gmail = GmailClient()

    existing = {l["name"]: l["id"] for l in gmail.service.users().labels().list(userId="me").execute().get("labels", [])}

    label_ids = {}
    for name in LABEL_NAMES:
        if name in existing:
            label_ids[name] = existing[name]
            print(f"Already exists — {name}: {existing[name]}")
        else:
            result = gmail.service.users().labels().create(
                userId="me",
                body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
            ).execute()
            label_ids[name] = result["id"]
            print(f"Created — {name}: {result['id']}")

    print("\nPaste this into main.py:")
    print("LABEL_IDS = {")
    for name, lid in label_ids.items():
        print(f'    "{name}": "{lid}",')
    print("}")

if __name__ == "__main__":
    main()
