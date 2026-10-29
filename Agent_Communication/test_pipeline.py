from argparse import Namespace
from unittest.mock import patch

import run_simulation
import api as simulator_api
from client_simulator import ClientSimulatorAgent


def test_simulation_sends_customer_utterance_to_advisor():
    arguments = Namespace(
        scenario="normal",
        mood=-0.65,
        language="fr",
        difficulty=0.0,
        seed=42,
        max_turns=1,
        client_llm="rules",
        client_model=None,
        advisor_url="http://127.0.0.1:8000",
        customer_id=1,
        timeout=1.0,
    )

    original_reply = ClientSimulatorAgent.reply
    with patch.object(
        run_simulation,
        "get_advisor_response",
        return_value=({"message": "Bonjour, je vais vérifier votre dossier."}, "thread-1"),
    ) as advisor, patch.object(
        ClientSimulatorAgent,
        "reply",
        autospec=True,
        side_effect=original_reply,
    ) as client_reply, patch("builtins.print"):
        assert run_simulation.run(arguments) == 0

    sent_message = advisor.call_args.args[1]
    assert sent_message
    assert advisor.call_args.args[2] is None
    assert advisor.call_args.args[3] == 1
    assert client_reply.call_count == 1


def test_pending_financial_action_is_automatically_declined():
    pending = {
        "thread_id": "thread-1",
        "pending_approval": True,
        "message": "Confirm this transfer?",
        "confirmation": {"message": "Confirm this transfer?"},
    }
    completed = {"thread_id": "thread-1", "pending_approval": False, "message": "Cancelled."}

    with patch.object(run_simulation, "request_json", side_effect=[pending, completed]) as request, \
            patch("builtins.input") as user_input, patch("builtins.print"):
        result, thread_id = run_simulation.get_advisor_response(
            "http://127.0.0.1:8000",
            "Please transfer the money.",
            None,
            customer_id=1,
            timeout=1.0,
        )

    assert result["message"] == "Cancelled."
    assert thread_id == "thread-1"
    user_input.assert_not_called()
    assert request.call_args.args[1] == {
        "thread_id": "thread-1",
        "approved": False,
        "customer_id": 1,
    }


def test_dashboard_call_uses_selected_starting_mood_and_turn_limit():
    response = simulator_api.start_call(
        simulator_api.StartCall(
            scenario="normal",
            seed=42,
            mood=-0.75,
            max_turns=3,
            language="fr",
            llm="none",
        )
    )
    sim, state = simulator_api.CALLS[response["call_id"]]

    try:
        assert state.mood == -0.75
        assert state.request.initial_mood == -0.75
        assert sim.max_turns == 3
    finally:
        simulator_api.CALLS.pop(response["call_id"], None)
