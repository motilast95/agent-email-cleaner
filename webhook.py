import sys
sys.stdout.reconfigure(line_buffering=True)

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Form
from langgraph.types import Command

from gmail_client import GmailClient
from refine_graph import build_refine_graph, make_sqlite_checkpointer, THREAD_ID

app = FastAPI()
checkpointer = make_sqlite_checkpointer()
graph = build_refine_graph(GmailClient(), checkpointer=checkpointer)


@app.post("/twilio/reply")
async def receive_reply(Body: str = Form(...)):
    config = {"configurable": {"thread_id": THREAD_ID}}
    graph.invoke(Command(resume=Body.strip()), config=config)
    return {"status": "ok"}
