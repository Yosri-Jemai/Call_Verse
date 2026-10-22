from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

from src.agent.state import AgentState
from src.agent.nodes import call_model, should_continue
from src.tools import all_tools


def create_agent(checkpointer=None):
    """Create and compile the Cashy agent graph.

    Args:
        checkpointer: Optional LangGraph checkpointer for conversation persistence.

    Returns:
        Compiled LangGraph agent.
    """
    tool_node = ToolNode(all_tools)

    builder = StateGraph(AgentState)
    builder.add_node("agent", call_model)
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=checkpointer)
