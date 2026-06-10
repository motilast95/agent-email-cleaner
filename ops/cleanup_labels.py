"""Remove duplicate AI labels from emails that have more than one applied."""
from shared.gmail_client import GmailClient

AI_LABEL_NAMES = ["AI-Delete", "AI-Keep"]


def main():
    gmail = GmailClient()

    all_labels = gmail.service.users().labels().list(userId="me").execute().get("labels", [])
    ai_label_ids = {l["id"] for l in all_labels if l["name"] in AI_LABEL_NAMES}
    id_to_name = {l["id"]: l["name"] for l in all_labels if l["name"] in AI_LABEL_NAMES}

    fixed = 0
    for label_id in ai_label_ids:
        page_token = None
        while True:
            resp = gmail.service.users().messages().list(
                userId="me", labelIds=[label_id], maxResults=500,
                pageToken=page_token
            ).execute()
            for msg in resp.get("messages", []):
                detail = gmail.service.users().messages().get(
                    userId="me", id=msg["id"], format="minimal"
                ).execute()
                applied = [l for l in detail.get("labelIds", []) if l in ai_label_ids]
                if len(applied) > 1:
                    keep = sorted(applied, key=lambda l: id_to_name[l])[0]
                    remove = [l for l in applied if l != keep]
                    gmail.service.users().messages().modify(
                        userId="me", id=msg["id"],
                        body={"removeLabelIds": remove}
                    ).execute()
                    print(f"Fixed {msg['id']}: kept {id_to_name[keep]}, removed {[id_to_name[l] for l in remove]}")
                    fixed += 1
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    print(f"\nDone. Fixed {fixed} emails with conflicting AI labels.")


if __name__ == "__main__":
    main()
