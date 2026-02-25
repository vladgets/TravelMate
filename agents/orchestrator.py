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


class TravelOrchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.llm_model
        self.system_prompt = _load_system_prompt()
        self._configure_litellm()

    async def _completion_with_retry(self, history: list[dict]):
        """Call LiteLLM with automatic retry on rate limit errors."""
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await litellm.acompletion(
                    model=self.model,
                    messages=[{"role": "system", "content": self.system_prompt}] + history,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto",
                )
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
            response = await self._completion_with_retry(history)

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

                # Continue loop — LLM will reason over results
                continue

            else:
                # Final answer reached
                final_text = assistant_message.content or ""
                history.append({"role": "assistant", "content": final_text})
                return final_text

        # Safety fallback if loop limit hit
        fallback = "I've gathered all the information but ran into complexity limits. Please try a simpler request."
        history.append({"role": "assistant", "content": fallback})
        return fallback
