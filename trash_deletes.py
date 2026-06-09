import sys
sys.stdout.reconfigure(line_buffering=True)

from dotenv import load_dotenv
load_dotenv()

from gmail_client import GmailClient

AI_DELETE_LABEL_ID = "Label_6"


def main():
    gmail = GmailClient()
    thread_ids = gmail.fetch_labeled_threads(AI_DELETE_LABEL_ID)

    if not thread_ids:
        print("No AI-Delete threads found. Nothing to trash.")
        return

    print(f"Found {len(thread_ids)} AI-Delete threads.")
    confirm = input("Trash all of them? This moves them to Gmail Trash (recoverable for 30 days). [yes/no]: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    print("Trashing...")
    for i, tid in enumerate(thread_ids):
        gmail.trash_thread(tid)
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(thread_ids)} trashed...")

    print(f"Done. {len(thread_ids)} threads moved to Trash.")


if __name__ == "__main__":
    main()
