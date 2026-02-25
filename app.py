"""TravelMate — Chainlit entry point.

Chainlit handles:
  - Chat UI rendering
  - Session management (per-user history + orchestrator)
  - Step visibility (each tool call shown as an expandable step)
  - Streaming responses
"""

from __future__ import annotations

import chainlit as cl

from agents.orchestrator import TravelOrchestrator
from config.settings import get_settings


def _clear_action() -> cl.Action:
    return cl.Action(
        name="clear_history",
        label="🗑️ New Conversation",
        payload={"action": "clear"},
        description="Clear conversation history to start a fresh request",
    )


async def _send_welcome(settings):
    provider = settings.hotel_provider.upper()
    model_display = settings.llm_model

    await cl.Message(
        content=(
            f"# ✈️ Welcome to TravelMate\n\n"
            f"I'm your AI vacation planner. Tell me where you want to go and I'll handle everything — "
            f"flights, hotels, weather, and a complete itinerary within your budget.\n\n"
            f"**Try something like:**\n"
            f"> *I want to take my partner to Barcelona for 7 nights in April, flying from New York. "
            f"Budget is around $4000. We love food and art.*\n\n"
            f"---\n"
            f"🤖 **Model:** `{model_display}` &nbsp;|&nbsp; "
            f"🏨 **Hotel data:** `{provider}` &nbsp;|&nbsp; "
            f"✈️ **Flights:** `Amadeus sandbox`"
        ),
        actions=[_clear_action()],
    ).send()


@cl.on_chat_start
async def on_chat_start():
    settings = get_settings()
    orchestrator = TravelOrchestrator(settings)

    cl.user_session.set("orchestrator", orchestrator)
    cl.user_session.set("history", [])

    await _send_welcome(settings)


@cl.action_callback("clear_history")
async def on_clear_history(action: cl.Action):
    cl.user_session.set("history", [])
    await action.remove()
    await cl.Message(
        content="🗑️ **Conversation cleared.** Start a new trip request below.",
        actions=[_clear_action()],
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    history: list[dict] = cl.user_session.get("history", [])
    orchestrator: TravelOrchestrator = cl.user_session.get("orchestrator")

    # Create the response message first so we have its ID for Step parent_id
    response_msg = cl.Message(content="")
    await response_msg.send()

    try:
        result = await orchestrator.run(
            user_message=message.content,
            history=history,
            cl_msg=response_msg,
        )
        response_msg.content = result
        response_msg.actions = [_clear_action()]
        await response_msg.update()
    except Exception as exc:
        response_msg.content = (
            f"❌ **An error occurred:** {exc}\n\n"
            f"Please check your API credentials in `.env` and try again."
        )
        await response_msg.update()
        raise

    cl.user_session.set("history", history)
