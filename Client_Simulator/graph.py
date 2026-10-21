"""
LangGraph agent for ONE call: the Client Simulator as an explicit state machine.

    START -> init -+-> advisor -> perceive -> update -> speak -+-> advisor ... (loop)
                   |                  |          |             |
                   +-> finish <-------+----------+-------------+   (call over)
                                                                 -> END

  init      the client arrives (wait in queue, opening message). May already be "abandoned".
  advisor   the Customer Advisor answers. ANY function AdvisorView -> str (scripted, LLM, human, other graph).
            It only receives an AdvisorView: never the profile, mood or is_fraudster.
  perceive  the judge (LLM, rules as fallback) reads the advisor's message: empathy, identity check,
            resolution, refusal, secret-code request...
  update    deterministic: mood, fraud pressure / strategy switch, end-of-call conditions.
  speak     the client LLM writes the next reply (hidden goal, secret CRM facts, emotion);
            rules take over if the LLM fails. Identity claims are then checked against the CRM in code.
  finish    KPIs and ground truth (summary) for the Quality Analyst / Workforce Manager.

Every node is a plain function on a typed state, so the graph is easy to test, trace and extend.
If `langgraph` is not installed, a tiny runner with the same API executes the very same nodes
(see GRAPH_RUNTIME), so the agent never depends on the package to work.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, TypedDict

from advisors import baseline_advisor
from client_simulator import AdvisorView, ClientRequest, ClientSimulatorAgent, advisor_view

try:
    from langgraph.graph import END, StateGraph
    GRAPH_RUNTIME = "langgraph"
except ImportError:                                     # pragma: no cover - depends on the environment
    GRAPH_RUNTIME = "builtin"
    END = "__end__"

    class StateGraph:                                   # minimal subset of the LangGraph API
        def __init__(self, _state_type=None):
            self.nodes, self.edges, self.cond, self.entry = {}, {}, {}, None

        def add_node(self, name, fn):
            self.nodes[name] = fn

        def set_entry_point(self, name):
            self.entry = name

        def add_edge(self, a, b):
            self.edges[a] = b

        def add_conditional_edges(self, a, router, mapping):
            self.cond[a] = (router, mapping)

        def compile(self):
            g = self

            class _App:
                def invoke(self, state, config=None):
                    limit = (config or {}).get("recursion_limit", 25)
                    state, node = dict(state or {}), g.entry
                    for _ in range(limit):
                        if node == END:
                            return state
                        state.update(g.nodes[node](state) or {})
                        if node in g.cond:
                            router, mapping = g.cond[node]
                            node = mapping[router(state)]
                        else:
                            node = g.edges[node]
                    raise RuntimeError(f"Recursion limit of {limit} reached without hitting END")
            return _App()


class CallState(TypedDict, total=False):
    conv: Any             # ConversationState (hidden ground truth lives here)
    advisor_msg: str
    features: Optional[dict]   # the judge's verdict on the last advisor message
    summary: dict
    trace: list           # node names in execution order (debug / architecture proof)


def _t(state: CallState, name: str) -> list:
    return list(state.get("trace", [])) + [name]


def build_call_graph(sim: ClientSimulatorAgent, request: ClientRequest,
                     advisor_fn: Callable[[AdvisorView], str] = baseline_advisor,
                     wait_seconds: float = 0.0):
    def init(state: CallState):
        return {"conv": sim.start(request, wait_seconds), "trace": _t(state, "init")}

    def advisor(state: CallState):
        msg = advisor_fn(advisor_view(state["conv"], sim.crm.get))
        return {"advisor_msg": msg, "trace": _t(state, "advisor")}

    def perceive(state: CallState):
        return {"features": sim.perceive(state["conv"], state["advisor_msg"]), "trace": _t(state, "perceive")}

    def update(state: CallState):
        if state.get("features") is not None:
            sim.update(state["conv"], state["features"])
        return {"trace": _t(state, "update")}

    def speak(state: CallState):
        if state.get("features") is not None:
            sim.speak(state["conv"], state["features"])
        return {"trace": _t(state, "speak")}

    def finish(state: CallState):
        return {"summary": sim.summary(state["conv"]), "trace": _t(state, "finish")}

    def active(next_node: str):
        return lambda s: next_node if s["conv"].status == "active" else "finish"

    g = StateGraph(CallState)
    for name, fn in (("init", init), ("advisor", advisor), ("perceive", perceive),
                     ("update", update), ("speak", speak), ("finish", finish)):
        g.add_node(name, fn)
    g.set_entry_point("init")
    g.add_conditional_edges("init", active("advisor"), {"advisor": "advisor", "finish": "finish"})
    g.add_edge("advisor", "perceive")
    g.add_conditional_edges("perceive", active("update"), {"update": "update", "finish": "finish"})
    g.add_edge("update", "speak")                       # speak also runs on the last turn (final words)
    g.add_conditional_edges("speak", active("advisor"), {"advisor": "advisor", "finish": "finish"})
    g.add_edge("finish", END)
    return g.compile()


def run_call(sim: ClientSimulatorAgent, request: ClientRequest,
             advisor_fn: Callable[[AdvisorView], str] = baseline_advisor, wait_seconds: float = 0.0) -> dict:
    """Play a whole call through the graph. Returns the final state (conv, summary, trace)."""
    app = build_call_graph(sim, request, advisor_fn, wait_seconds)
    # 5 nodes per turn x max_turns, plus init and finish
    return app.invoke({}, config={"recursion_limit": 5 * sim.max_turns + 10})


if __name__ == "__main__":
    import sys

    from advisors import llm_advisor
    from llm_backends import LLMError, auto_llm

    use_llm = "--rules" not in sys.argv
    llm, note = None, "rules (--rules)"
    if use_llm:
        try:
            llm = auto_llm()
            note = f"{llm.backend} / {llm.model}"
        except LLMError as e:
            note = f"rules (no LLM: {e})"
    sim = ClientSimulatorAgent(seed=3, llm=llm)
    req = sim.generate_requests("normal", 5)[0]
    advisor = llm_advisor(llm) if llm and "--llm-advisor" in sys.argv else baseline_advisor
    out = run_call(sim, req, advisor)
    print(f"graph runtime: {GRAPH_RUNTIME} | client engine: {note}\n")
    for who, text in out["conv"].history:
        print(f"{who:>10}: {text}")
    print("\ntrace  :", " > ".join(out["trace"]))
    print("engine :", sim.engine_report(out["conv"]))
    print("summary:", out["summary"])
