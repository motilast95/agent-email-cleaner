import sys

sys.stdout.reconfigure(line_buffering=True)

from shared.gmail_client import GmailClient

print("Creating GmailClient...")
gmail = GmailClient()

print("Testing auth (getProfile)...")
profile = gmail.service.users().getProfile(userId="me").execute()
print(f"  Auth OK: {profile['emailAddress']}")

print("Testing fetch_metadata(max_results=3)...")
emails = gmail.fetch_metadata(max_results=3)
print(f"  fetch_metadata OK: {len(emails)} emails returned")

if emails:
    e = emails[0]
    print(f"  Sample: From={e['sender'][:50]!r}  Subject={e['subject'][:50]!r}")
else:
    print("  (No unlabeled inbox emails to fetch — still OK)")

print("All checks passed.")
