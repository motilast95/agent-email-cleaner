"""
Gmail adapter — talks to Gmail API only. No AI logic here.

Login files (both gitignored, live in project root):
  credentials.json  — app identity from Google Cloud Console
  token.json        — your personal session (created on first browser login)

After GmailClient() runs, self.service is the Gmail API client used for all calls.

fetch_metadata() does two steps internally:
  Step 1 — search inbox (list API) → message IDs only
  Step 2 — batch-fetch From/Subject/snippet per ID (get API) → email dicts

Safe to treat as black boxes for now: pagination (page_token), batch callbacks.

Smoke test:
  python -c "from gmail_client import GmailClient; g=GmailClient(); print(g.fetch_metadata(max_results=3))"
"""

import os
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailClient:
    """
    Public methods (input → output → used by):

      GmailClient()           → logged-in client          → every script
      fetch_metadata(n)       → [{id, thread_id, sender, subject, snippet}] → Phase 1 (main.py)
      fetch_body(id)          → full text string          → Phase 1, low-confidence emails only
      batch_apply_label(...)  → (writes to Gmail)         → Phase 1 end (nodes.py)
      fetch_labeled_threads() → [thread_id, ...]          → Phase 2, trash_deletes.py
      trash_thread(id)        → (moves to Gmail Trash)    → Phase 2, trash_deletes.py

    Come back to this file when changing: inbox query, auth, labels, or trash — not Claude rules.
    """

    def __init__(self):
        self.service = self._authenticate()

    def _authenticate(self):
        """Load or refresh token.json; fall back to browser login. Returns Gmail API client."""
        creds = None
        if os.path.exists("token.json"):  
            creds = Credentials.from_authorized_user_file("token.json", SCOPES) 
        if not creds or not creds.valid: 
            if creds and creds.expired and creds.refresh_token: 
                creds.refresh(Request()) 
            else:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES) 
                creds = flow.run_local_server(port=0) 
            with open("token.json", "w") as f: 
                f.write(creds.to_json()) 
        return build("gmail", "v1", credentials=creds)



    def fetch_metadata(self, max_results=50):
        """Return metadata for unlabeled inbox emails (sender, subject, snippet — not full body)."""
        query = "in:inbox -label:AI-Keep -label:AI-Delete"  # Gmail search syntax; same as inbox search bar
        # Step 1: collect all message IDs via paginated list calls (up to 500 per page)
        message_ids = []
        page_token = None
        while len(message_ids) < max_results:
            page_size = min(500, max_results - len(message_ids))
            kwargs = {"userId": "me", "maxResults": page_size, "q": query}
            if page_token:
                kwargs["pageToken"] = page_token
            results = self.service.users().messages().list(**kwargs).execute()
            msgs = results.get("messages", [])
            if not msgs:
                break
            message_ids.extend(msg["id"] for msg in msgs)
            page_token = results.get("nextPageToken")
            if not page_token:
                break

        message_ids = message_ids[:max_results]
        print(f"Found {len(message_ids)} unlabeled emails to process. Fetching metadata...")

        # Step 2: fetch metadata in batches of 100 using Gmail batch API
        emails = []
        BATCH_SIZE = 100
        for i in range(0, len(message_ids), BATCH_SIZE):
            chunk = message_ids[i:i + BATCH_SIZE]
            batch_results = {}

            def make_callback(msg_id):
                def callback(request_id, response, exception):
                    if exception is None:
                        batch_results[msg_id] = response
                return callback

            batch = self.service.new_batch_http_request()
            for msg_id in chunk:
                batch.add(
                    self.service.users().messages().get(
                        userId="me", id=msg_id, format="metadata",
                        metadataHeaders=["From", "Subject"],
                    ),
                    callback=make_callback(msg_id),
                )
            batch.execute()

            for msg_id in chunk:
                detail = batch_results.get(msg_id)
                if detail:
                    headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
                    emails.append({
                        "id": msg_id,
                        "thread_id": detail.get("threadId", msg_id),
                        "sender": headers.get("From", ""),
                        "subject": headers.get("Subject", ""),
                        "snippet": detail.get("snippet", ""),
                    })

            print(f"  Fetched {min(i + BATCH_SIZE, len(message_ids))}/{len(message_ids)} metadata records...")

        return emails

    def fetch_body(self, email_id):
        """Return plain-text body for one message (used when metadata-only classification is uncertain)."""
        detail = self.service.users().messages().get(
            userId="me", id=email_id, format="full"
        ).execute()
        return self._extract_text(detail.get("payload", {}))

    def _extract_text(self, payload):
        if "parts" in payload:
            for part in payload["parts"]:
                if part["mimeType"] == "text/plain":
                    data = part["body"].get("data", "")
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        return ""

    def fetch_labeled_threads(self, label_id: str) -> list:
        """Return all thread IDs that have the given Gmail label ID."""
        thread_ids = []
        page_token = None
        while True:
            kwargs = {"userId": "me", "labelIds": [label_id], "maxResults": 500}
            if page_token:
                kwargs["pageToken"] = page_token
            results = self.service.users().threads().list(**kwargs).execute()
            thread_ids.extend(t["id"] for t in results.get("threads", []))
            page_token = results.get("nextPageToken")
            if not page_token:
                break
        return thread_ids

    def trash_thread(self, thread_id: str) -> None:
        """Move one thread to Gmail Trash (recoverable ~30 days)."""
        import time
        for attempt in range(3):
            try:
                self.service.users().threads().trash(userId="me", id=thread_id).execute()
                break
            except Exception as e:
                if attempt == 2:
                    print(f"Warning: failed to trash thread {thread_id}: {e}")
                time.sleep(1.5 * (attempt + 1))
        time.sleep(0.1)

    def batch_apply_label(self, thread_ids: list, label_id: str, remove_label_ids: list):
        """Add one label to threads and remove conflicting AI labels."""
        import time
        for thread_id in thread_ids:
            for attempt in range(3):
                try:
                    self.service.users().threads().modify(
                        userId="me",
                        id=thread_id,
                        body={
                            "addLabelIds": [label_id],
                            "removeLabelIds": remove_label_ids,
                        },
                    ).execute()
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"Warning: failed to label thread {thread_id}: {e}")
                    time.sleep(1.5 * (attempt + 1))
            time.sleep(0.1)  # stay under Gmail quota (10 calls/sec = 6,000 units/min)
