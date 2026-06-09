"""
Entrypoint for langgraph dev / Agent Inbox.
Run with: langgraph dev
Then connect Agent Inbox at https://dev.agentinbox.ai to http://127.0.0.1:2024
"""
from dotenv import load_dotenv
load_dotenv()

from gmail_client import GmailClient
from refine_graph import build_refine_graph

# No checkpointer passed — langgraph dev injects its own
graph = build_refine_graph(GmailClient())
