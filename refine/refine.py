import sys

sys.stdout.reconfigure(line_buffering=True)

from dotenv import load_dotenv

load_dotenv()

from langgraph.errors import GraphInterrupt

from shared.gmail_client import GmailClient
from refine.refine_graph import THREAD_ID, build_refine_graph, make_sqlite_checkpointer
from refine.refine_nodes import RefineState

gmail = GmailClient()
checkpointer = make_sqlite_checkpointer()
graph = build_refine_graph(gmail, checkpointer=checkpointer)

initial_state: RefineState = {
    "keep_threads": [],
    "clusters": [],
    "current_idx": 0,
    "current_question": "",
    "preference_profile": "",
    "trash_queue": [],
    "_reply": "",
}
config = {"configurable": {"thread_id": THREAD_ID}}

print("Starting refinement session...")
try:
    graph.invoke(initial_state, config=config)
except GraphInterrupt:
    print("Graph paused — waiting for reply. Use Agent Inbox or webhook to continue.")
