from langgraph.graph import StateGraph, END
from nodes import (
    EmailState,
    make_fetch_node,
    make_fetch_bodies_node,
    make_apply_labels_node,
    classify_metadata,
    reclassify_ambiguous,
    route_after_classify,
)


def build_graph(gmail_client):
    graph = StateGraph(EmailState)

    graph.add_node("fetch_emails", make_fetch_node(gmail_client))
    graph.add_node("classify_metadata", classify_metadata)
    graph.add_node("fetch_bodies", make_fetch_bodies_node(gmail_client))
    graph.add_node("reclassify_ambiguous", reclassify_ambiguous)
    graph.add_node("apply_labels", make_apply_labels_node(gmail_client))

    graph.set_entry_point("fetch_emails")
    graph.add_edge("fetch_emails", "classify_metadata")
    graph.add_conditional_edges(
        "classify_metadata",
        route_after_classify,
        {
            "fetch_bodies": "fetch_bodies",
            "apply_labels": "apply_labels",
        },
    )
    graph.add_edge("fetch_bodies", "reclassify_ambiguous")
    graph.add_edge("reclassify_ambiguous", "apply_labels")
    graph.add_edge("apply_labels", END)

    return graph.compile()
