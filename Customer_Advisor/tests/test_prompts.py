from src.agent.prompts import SYSTEM_PROMPT_DEMO, SYSTEM_PROMPT_PERSONAL


LANGUAGE_RULE = (
    "Always reply in the same language as the user's latest message: French if the user "
    "writes in French, English if the user writes in English. If the message is ambiguous, "
    "keep the language of the conversation so far, or French if there is none. This applies "
    "to every kind of question (accounts, transactions, spending, policies)."
)


def test_both_prompts_use_the_same_language_rule():
    for prompt in (SYSTEM_PROMPT_DEMO, SYSTEM_PROMPT_PERSONAL):
        assert "Respond in English by default" not in prompt
        assert LANGUAGE_RULE in prompt
