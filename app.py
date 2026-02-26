"""TravelMate — Chainlit entry point.

Chainlit handles:
  - Chat UI rendering
  - Session management (per-user history + orchestrator)
  - Step visibility (each tool call shown as an expandable step)
  - Streaming responses
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

import chainlit as cl
from chainlit.input_widget import Select, TextInput

from agents.orchestrator import TravelOrchestrator
from config.settings import SUPPORTED_MODELS, get_settings
from services.profile_store import load_profile, save_profile


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


async def _send_welcome(settings, profile: str):
    provider = settings.hotel_provider.upper()
    model_display = settings.llm_model

    profile_line = (
        f"\n📋 **Your profile:** {profile}"
        if profile
        else "\n📋 **No profile set** — add your preferences in ⚙️ Settings for personalised results."
    )

    await cl.Message(
        content=(
            f"# ✈️ Welcome to TravelMate\n\n"
            f"I'm your AI vacation planner. Tell me where you want to go and I'll handle everything — "
            f"flights, hotels, weather, and a complete itinerary within your budget.\n\n"
            f"**Try something like:**\n"
            f"> *Barcelona for a week in April, budget $4000*\n\n"
            f"---\n"
            f"🤖 **Model:** `{model_display}` &nbsp;|&nbsp; "
            f"🏨 **Hotels:** `{provider}` &nbsp;|&nbsp; "
            f"✈️ **Flights:** `Amadeus sandbox`\n"
            f"{profile_line}"
        ),
        actions=[_clear_action()],
    ).send()


@cl.on_chat_start
async def on_chat_start():
    settings = get_settings()
    orchestrator = TravelOrchestrator(settings)
    profile = load_profile()

    cl.user_session.set("orchestrator", orchestrator)
    cl.user_session.set("history", [])
    cl.user_session.set("last_itinerary", None)
    cl.user_session.set("active_model", settings.llm_model)
    cl.user_session.set("profile", profile)

    await cl.ChatSettings([
        Select(
            id="active_model",
            label="LLM Model",
            initial_value=settings.llm_model,
            items={label: model_id for label, model_id in SUPPORTED_MODELS.items()},
        ),
        TextInput(
            id="profile",
            label="Your Travel Profile",
            initial=profile,
            placeholder="e.g. I usually fly from JFK, prefer luxury hotels, love food and art",
        ),
    ]).send()

    await _send_welcome(settings, profile)


@cl.on_settings_update
async def on_settings_update(new_settings: dict):
    model = new_settings.get("active_model")
    if model:
        cl.user_session.set("active_model", model)
        label = next((k for k, v in SUPPORTED_MODELS.items() if v == model), model)
        await cl.Message(content=f"✅ Switched to **{label}**").send()

    profile = new_settings.get("profile", "").strip()
    cl.user_session.set("profile", profile)
    save_profile(profile)
    if profile:
        await cl.Message(content=f"✅ Profile saved: *{profile[:80]}{'...' if len(profile) > 80 else ''}*").send()


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

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"TravelMate_Itinerary_{timestamp}.html"
        await cl.Message(
            content="🌐 Your itinerary is ready to download:",
            elements=[cl.File(name=filename, path=tmp_path)],
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

        # Store the latest itinerary for HTML export
        cl.user_session.set("last_itinerary", result)


    except Exception as exc:
        response_msg.content = (
            f"❌ **An error occurred:** {exc}\n\n"
            f"Please check your API credentials in `.env` and try again."
        )
        await response_msg.update()
        raise

    cl.user_session.set("history", history)
