import sqlite3

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from refine_nodes import (
    RefineState,
    make_fetch_keep_pile_node,
    cluster_ambiguous,
    build_and_send_question,
    await_reply,
    process_reply,
    make_trash_confirmed_node,
    check_termination,
    notify_done,
)

THREAD_ID = "refine-session-1"


def make_sqlite_checkpointer(db_path: str = "refine_state.db"):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(conn)


def build_refine_graph(gmail_client, checkpointer=None):
    builder = StateGraph(RefineState)

    builder.add_node("fetch_keep_pile", make_fetch_keep_pile_node(gmail_client))
    builder.add_node("cluster_ambiguous", cluster_ambiguous)
    builder.add_node("build_and_send_question", build_and_send_question)
    builder.add_node("await_reply", await_reply)
    builder.add_node("process_reply", process_reply)
    builder.add_node("trash_confirmed", make_trash_confirmed_node(gmail_client))
    builder.add_node("notify_done", notify_done)

    builder.set_entry_point("fetch_keep_pile")
    builder.add_edge("fetch_keep_pile", "cluster_ambiguous")
    builder.add_edge("cluster_ambiguous", "build_and_send_question")
    builder.add_edge("build_and_send_question", "await_reply")
    builder.add_edge("await_reply", "process_reply")
    builder.add_edge("process_reply", "trash_confirmed")
    builder.add_conditional_edges(
        "trash_confirmed",
        check_termination,
        {"ask": "build_and_send_question", "done": "notify_done"},
    )
    builder.add_edge("notify_done", END)

    return builder.compile(checkpointer=checkpointer)
