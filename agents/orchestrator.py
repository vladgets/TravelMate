"""Core agentic loop — multi-step reasoning with parallel tool execution.

Architecture:
  1. Send user message + history to LLM with tool schemas
  2. If LLM requests tool calls → execute in parallel → feed results back → loop
  3. If LLM produces final text → stream to Chainlit → done

Key agentic behaviors demonstrated:
  - Parallel tool calls (asyncio.gather)
  - Adaptive replanning (LLM sees tool errors/empty results and adjusts)
  - Multi-turn memory (full conversation history maintained)
  - Transparent step visibility (each tool in a Chainlit Step)
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import chainlit as cl
import litellm
from litellm.exceptions import RateLimitError

from agents.tools import TOOL_SCHEMAS, execute_tool_calls_parallel
from config.settings import Settings

MAX_ITERATIONS = 10  # Safety limit on agentic loop depth
_RETRY_DELAYS = [5, 15, 30]  # Seconds to wait on successive rate-limit hits


def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent.parent / "prompts" / "system_prompt.txt"
    if prompt_path.exists():
        return prompt_path.read_text()
    return "You are TravelMate, an expert AI travel planner."


def _stub_format_itinerary(history: list[dict]) -> None:
    """Replace the format_itinerary tool result's markdown with a stub.

    The full markdown is already in the subsequent assistant message, so
    carrying it again in the tool result only wastes tokens on future turns.
    """
    for msg in history:
        if msg.get("role") == "tool" and msg.get("name") == "format_itinerary":
            try:
                content = json.loads(msg["content"])
                if "markdown" in content:
                    content["markdown"] = "[rendered]"
                    msg["content"] = json.dumps(content)
            except (json.JSONDecodeError, KeyError):
                pass


class TravelOrchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.llm_model
        self.system_prompt = _load_system_prompt()
        self._configure_litellm()

    async def _completion_with_retry(self, history: list[dict], cl_msg: cl.Message):
        """Call LiteLLM with streaming and automatic retry on rate limit errors.

        All completions use stream=True. For tool-call iterations the model
        emits no content tokens, so the UI sees nothing. For the final answer,
        tokens are forwarded to cl_msg in real-time via stream_token().
        """
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                model = cl.user_session.get("active_model") or self.model
                stream = await litellm.acompletion(
                    model=model,
                    messages=[{"role": "system", "content": self.system_prompt}] + history,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto",
                    stream=True,
                )
                chunks = []
                async for chunk in stream:
                    chunks.append(chunk)
                    delta = chunk.choices[0].delta
                    if delta.content:
                        await cl_msg.stream_token(delta.content)
                return litellm.stream_chunk_builder(chunks)
            except RateLimitError:
                if attempt == len(_RETRY_DELAYS):
                    raise
        raise RuntimeError("Unreachable")

    def _configure_litellm(self):
        """Set API keys for whichever provider is being used."""
        if self.settings.anthropic_api_key:
            os.environ["ANTHROPIC_API_KEY"] = self.settings.anthropic_api_key
        if self.settings.openai_api_key:
            os.environ["OPENAI_API_KEY"] = self.settings.openai_api_key

    async def run(
        self,
        user_message: str,
        history: list[dict],
        cl_msg: cl.Message,
    ) -> str:
        """
        Main agentic loop. Mutates `history` in place so the caller
        can persist it for multi-turn conversations.
        """
        history.append({"role": "user", "content": user_message})

        for iteration in range(MAX_ITERATIONS):
            response = await self._completion_with_retry(history, cl_msg)

            choice = response.choices[0]
            assistant_message = choice.message

            if choice.finish_reason == "tool_calls":
                tool_calls = assistant_message.tool_calls

                # Append assistant's tool-use message to history
                history.append(
                    {
                        "role": "assistant",
                        "content": assistant_message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )

                # Execute all tool calls in parallel
                tool_results = await execute_tool_calls_parallel(tool_calls, cl_msg)

                # Append each tool result to history
                for result in tool_results:
                    history.append(result)

                # Short-circuit: format_itinerary already streamed the final
                # itinerary directly to cl_msg — skip the redundant echo call.
                for r in tool_results:
                    if r["name"] == "format_itinerary":
                        content = json.loads(r["content"])
                        markdown = content.get("markdown", "")
                        if markdown:
                            history.append({"role": "assistant", "content": markdown})
                            _stub_format_itinerary(history)
                            return markdown

                # Continue loop — LLM will reason over results
                continue

            else:
                # Final answer reached
                final_text = assistant_message.content or ""
                history.append({"role": "assistant", "content": final_text})
                # Level-1 context pruning: the full itinerary markdown is now
                # in the assistant message above, so stub the tool result to
                # avoid carrying ~600 duplicate tokens into future turns.
                _stub_format_itinerary(history)
                return final_text

        # Safety fallback if loop limit hit
        fallback = "I've gathered all the information but ran into complexity limits. Please try a simpler request."
        history.append({"role": "assistant", "content": fallback})
        return fallback
