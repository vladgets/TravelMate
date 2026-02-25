"""TravelMate — Chainlit entry point.

Chainlit handles:
  - Chat UI rendering
  - Session management (per-user history + orchestrator)
  - Step visibility (each tool call shown as an expandable step)
  - Streaming responses
"""

from __future__ import annotations

import os
import tempfile

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


def _export_action() -> cl.Action:
    return cl.Action(
        name="export_html",
        label="🌐 Download HTML",
        payload={"action": "export"},
        description="Download this itinerary as a styled HTML file",
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
    cl.user_session.set("last_itinerary", None)

    await _send_welcome(settings)


@cl.action_callback("clear_history")
async def on_clear_history(action: cl.Action):
    cl.user_session.set("history", [])
    cl.user_session.set("last_itinerary", None)
    await action.remove()
    await cl.Message(
        content="🗑️ **Conversation cleared.** Start a new trip request below.",
        actions=[_clear_action()],
    ).send()


@cl.action_callback("export_html")
async def on_export_html(action: cl.Action):
    itinerary: str | None = cl.user_session.get("last_itinerary")

    if not itinerary:
        await cl.Message(content="⚠️ No itinerary to export yet. Plan a trip first!").send()
        return

    await action.remove()

    from utils.html_export import generate_itinerary_html

    tmp_path = None
    try:
        html_bytes = generate_itinerary_html(itinerary)

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            f.write(html_bytes)
            tmp_path = f.name

        await cl.Message(
            content="🌐 Your itinerary is ready to download:",
            elements=[cl.File(name="TravelMate_Itinerary.html", path=tmp_path)],
            actions=[_export_action()],
        ).send()
    except Exception as exc:
        await cl.Message(content=f"❌ HTML export failed: {exc}").send()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@cl.on_message
async def on_message(message: cl.Message):
    history: list[dict] = cl.user_session.get("history", [])
    orchestrator: TravelOrchestrator = cl.user_session.get("orchestrator")

    response_msg = cl.Message(content="")
    await response_msg.send()

    try:
        result = await orchestrator.run(
            user_message=message.content,
            history=history,
            cl_msg=response_msg,
        )
        response_msg.content = result
        response_msg.actions = [_export_action(), _clear_action()]
        await response_msg.update()

        # Store the latest itinerary for PDF export
        cl.user_session.set("last_itinerary", result)
    except Exception as exc:
        response_msg.content = (
            f"❌ **An error occurred:** {exc}\n\n"
            f"Please check your API credentials in `.env` and try again."
        )
        await response_msg.update()
        raise

    cl.user_session.set("history", history)
