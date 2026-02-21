# TravelMate — Design & Architecture

## 1. Problem Statement

Build a conversational AI agent that takes a natural-language vacation request ("I want a beach trip to Lisbon for 2 people in March, budget $3000") and autonomously orchestrates multiple external APIs to produce a complete, budget-validated itinerary — without the user filling in any forms.

The secondary goal is to showcase agentic engineering patterns relevant to travel industry AI: parallel data gathering, adaptive replanning when constraints aren't met, multi-turn conversational memory, and full reasoning transparency.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Chainlit UI                         │
│         (chat interface + expandable step log)          │
└───────────────────────┬─────────────────────────────────┘
                        │ user message
                        ▼
┌─────────────────────────────────────────────────────────┐
│                TravelOrchestrator                       │
│   ┌─────────────────────────────────────────────────┐   │
│   │              Agentic Loop                       │   │
│   │  LiteLLM call → tool_calls? → execute → loop   │   │
│   └─────────────────────────────────────────────────┘   │
└────┬───────────┬───────────┬────────────┬───────────────┘
     │           │           │            │
     ▼           ▼           ▼            ▼
  Amadeus     Amadeus     wttr.in      LiteLLM
  Flights     Hotels      Weather     (formatting)
```

The system has four logical layers:

| Layer | Responsibility |
|---|---|
| **UI** (Chainlit) | Render chat, session state, step visibility |
| **Orchestrator** | Drive the agentic loop, manage history |
| **Tools** | Schema definitions + dispatch + Chainlit step wrapping |
| **Services** | External API clients with provider abstraction |

---

## 3. Agentic Loop Design

The core pattern is a **ReAct-style loop** (Reason → Act → Observe → Reason) implemented natively using LLM function calling, without any agent framework:

```
while True:
    response = LLM(system_prompt + history + tool_schemas)

    if response.finish_reason == "tool_calls":
        results = await execute_tools_in_parallel(response.tool_calls)
        history.append(assistant_tool_use_message)
        history.append(tool_results)
        # loop — LLM reasons over results

    else:
        return response.content  # final answer
```

### Why native tool use over a framework (LangChain, CrewAI, etc.)?

Frameworks like LangChain abstract the tool-use loop behind convenience methods, which obscures what's actually happening. For a PoC designed to demonstrate agentic mastery, owning the loop directly means:

- Full control over how tool results are injected into history
- Ability to implement custom parallel execution logic
- No hidden prompts or implicit behavior from the framework
- Easier to explain every decision in an interview context

The tradeoff is more boilerplate — about 60 lines in `orchestrator.py` — which is acceptable.

### Safety Limit

The loop has `MAX_ITERATIONS = 10` to prevent runaway tool chains. In practice, a full trip plan requires 4–6 LLM turns.

---

## 4. Tool Design

### Schema Format

All 6 tools use the OpenAI function-calling schema, which LiteLLM transparently translates for any underlying model:

```python
{
    "type": "function",
    "function": {
        "name": "search_flights",
        "description": "...",
        "parameters": {
            "type": "object",
            "properties": { ... },
            "required": [...]
        }
    }
}
```

### The 6 Tools and Why They're Separate

| Tool | Why a distinct tool |
|---|---|
| `parse_travel_request` | Separates NLU from action — the LLM explicitly commits to structured params before calling any API |
| `search_flights` | Stateless external I/O — cacheable, retryable, independently parallelizable |
| `search_hotels` | Same as above; routed through provider abstraction |
| `get_weather` | Independent of flights/hotels — always safe to run in parallel |
| `calculate_budget` | Pure computation — separating it makes the budget check an explicit reasoning step the LLM must perform |
| `format_itinerary` | Separates data gathering from presentation — LLM only formats once all data is confirmed |

The key principle: **each tool maps to one unit of external state change or I/O**. This makes parallel execution safe (no shared mutable state between tools in the same batch).

### Tool Dispatch

`execute_tool_call()` wraps each tool in a `cl.Step`, giving full input/output visibility in the UI. The dispatch function checks the tool name and routes to the correct implementation, passing `cl_msg` for step parenting where needed.

---

## 5. Parallel Tool Execution

When the LLM decides to call `search_flights` and `get_weather` in the same response, those two calls have no data dependency on each other. Executing them sequentially would waste time.

```python
async def execute_tool_calls_parallel(tool_calls, cl_msg):
    tasks = [execute_tool_call(tc, cl_msg) for tc in tool_calls]
    return await asyncio.gather(*tasks)
```

`asyncio.gather` launches all tasks concurrently on the same event loop. Since both `search_flights` (Amadeus HTTP) and `get_weather` (wttr.in HTTP) are I/O-bound operations using `httpx.AsyncClient`, they genuinely overlap — a 2-second flight search and a 1-second weather call take ~2 seconds total, not 3.

The LLM naturally groups independent calls in the same turn when the system prompt instructs it to — this is prompt-engineered behavior, not hard-coded orchestration logic.

---

## 6. Adaptive Replanning

The agentic loop enables adaptive replanning without any special code. When the LLM sees tool results that don't satisfy the user's constraints (e.g. all flights are above budget), it can autonomously:

1. Call `search_flights` again with a different origin airport (JFK → EWR)
2. Adjust dates to shoulder season
3. Call `search_hotels` with tighter price filters
4. Recalculate budget with the new numbers

This works because the LLM receives the full tool result history and the system prompt instructs it to retry rather than give up. No replanning code needs to be written — the intelligence is in the model and the prompt.

The system prompt key instruction:
> *"If flights exceed budget: adjust search params and retry. Do NOT just say 'it's over budget' — find an alternative."*

---

## 7. Provider Abstraction Pattern

Hotel data sources are abstracted behind a `HotelProvider` interface:

```python
class HotelProvider(ABC):
    @abstractmethod
    async def search_hotels(
        self, city_code, check_in, check_out, num_adults, max_results
    ) -> list[HotelResult]:
        ...
```

Two implementations exist:

| Implementation | Data | Auth | Status |
|---|---|---|---|
| `AmadeusClient` | Sandbox (test data) | OAuth2 bearer | Default, works immediately |
| `ExpediaRapidClient` | 750K+ real properties | HMAC-SHA512 | Requires EPS partner agreement |

The routing logic in `tools.py` checks `settings.hotel_provider` at call time:

```python
if settings.hotel_provider == "expedia" and settings.expedia_eps_client_id:
    client = ExpediaRapidClient(...)
else:
    client = AmadeusClient(...)
```

The orchestrator and tools layer are completely unaware of which provider is active — they only see `list[HotelResult]`. This is the same provider-agnostic pattern used in production travel platforms to swap between lodging inventory sources (Expedia, Booking.com, direct hotel chains) without changing business logic.

### Why this matters for Expedia

Expedia Group operates across multiple inventory sources — Rapid API for hotels, various GDS systems for flights, affiliate networks for activities. Designing services with provider interfaces rather than concrete implementations is standard practice at this scale. This PoC demonstrates that design sensibility from the start.

---

## 8. Multi-Turn Conversational Memory

History is maintained as a plain `list[dict]` in Chainlit's user session:

```python
history = cl.user_session.get("history", [])
```

Each turn appends:
1. The user message
2. The assistant's tool-use message (with `tool_calls` array)
3. Each tool result (role: `"tool"`)
4. The final assistant text response

The full history is prepended with the system prompt on every LLM call. This means:
- The LLM always has complete context of what was searched and what was found
- Follow-up requests ("make it cheaper", "I prefer boutique hotels") work without re-explaining the trip
- The LLM can reference specific flight prices or hotel names from earlier in the conversation

The tradeoff is context window growth over many turns. For a PoC, this is acceptable. A production system would implement sliding window summarization.

---

## 9. LLM Abstraction via LiteLLM

All LLM calls go through LiteLLM, which provides a single interface for 100+ model providers:

```python
response = await litellm.acompletion(
    model="claude-sonnet-4-6",  # or "gpt-4o", "gemini/gemini-pro", etc.
    messages=messages,
    tools=TOOL_SCHEMAS,
    tool_choice="auto",
)
```

LiteLLM normalizes the response format so the orchestrator loop works identically regardless of whether Claude or GPT-4o is under the hood. The model is configurable at runtime:

```bash
LLM_MODEL=gpt-4o chainlit run app.py
```

Two LLM calls have specific requirements:
- `parse_travel_request` uses `response_format={"type": "json_object"}` (JSON mode) to guarantee structured output
- `format_itinerary` is a free-form generation call with a writing-focused system prompt

---

## 10. Chainlit Integration

Chainlit was chosen over alternatives (Streamlit, Gradio, custom FastAPI + React) for one reason: **native agent step visibility**.

`cl.Step` creates collapsible UI elements that show tool name, inputs, and outputs without any custom frontend code:

```python
async with cl.Step(name=f"🔧 {tool_name}", type="tool", parent_id=cl_msg.id) as step:
    step.input = json.dumps(args, indent=2)
    result = await execute(args)
    step.output = json.dumps(result, indent=2)
```

This gives the user (and demo audience) full transparency into the agent's reasoning process — a key requirement for an interview demo where showing *how* the system thinks matters as much as the final output.

Session state (`cl.user_session`) is used to maintain the orchestrator instance and conversation history per user, enabling concurrent users without shared state.

---

## 11. Data Flow: Full Example

**Query:** *"7 nights in Barcelona from New York, April, budget $4000, 2 people who love food and art"*

```
Turn 1:
  LLM → tool_call: parse_travel_request(user_message)
  Result: {origin: "JFK", destination: "BCN", depart_date: "2026-04-05",
           return_date: "2026-04-12", num_adults: 2, budget_usd: 4000,
           preferences: ["food", "art"]}

Turn 2 (parallel):
  LLM → tool_calls: [search_flights(JFK→BCN), get_weather(Barcelona, April)]
  Both fire simultaneously via asyncio.gather
  Results: [{airline: "IB", price: 1240, stops: 0, ...}],
           {condition: "sunny", temp_c: 18, ...}

Turn 3:
  LLM → tool_call: search_hotels(BCN, check_in: Apr 5, check_out: Apr 12)
  Result: [{name: "Hotel Arts", stars: 5, price_per_night: 380, ...}, ...]

Turn 4:
  LLM → tool_call: calculate_budget(flight: 1240, hotel: 220, nights: 7,
                                    people: 2, daily_expenses: 120)
  Result: {total: 4120, within_budget: false, breakdown: {...}}
  → Over budget by $120

Turn 5 (adaptive replanning):
  LLM → tool_call: search_hotels(BCN, ...) with implicit cheaper filter
  Result: [{name: "Barceló Raval", stars: 4, price_per_night: 195, ...}]

Turn 6:
  LLM → tool_call: calculate_budget(flight: 1240, hotel: 195, ...)
  Result: {total: 3610, within_budget: true}

Turn 7:
  LLM → tool_call: format_itinerary({...all data...})
  Result: {markdown: "# Your Barcelona Adventure..."}

Final: LLM streams formatted itinerary to Chainlit UI
```

7 LLM turns, 2 external APIs called in parallel, 1 adaptive retry — all transparent in the step log.

---

## 12. Design Decisions & Trade-offs

| Decision | Alternative Considered | Reason Chosen |
|---|---|---|
| Native tool use loop | LangChain / LlamaIndex | Full control, easier to explain, no hidden prompts |
| LiteLLM | Direct Anthropic SDK | Model-agnostic; one env var to switch providers |
| Chainlit | Streamlit / Gradio | Native step visibility without custom frontend |
| Amadeus sandbox | Mock data / fixtures | Real API calls make the demo credible |
| Provider ABC | Hard-coded Amadeus | Demonstrates production-grade extensibility |
| Plain list history | Vector store / summary | Sufficient for PoC; simpler to reason about |
| asyncio.gather | Sequential tool calls | Realistic performance; demonstrates async mastery |
| Pydantic models | TypedDict / raw dicts | Runtime validation; self-documenting data contracts |

---

## 13. What Would Change in Production

| Concern | PoC Approach | Production Approach |
|---|---|---|
| **Secrets** | `.env` file | Secrets manager (AWS SSM, Vault) |
| **History storage** | In-memory session | Redis / PostgreSQL with TTL |
| **Context limits** | Full history | Sliding window + summarization |
| **Hotel provider** | Amadeus sandbox | Expedia Rapid API (750K properties) |
| **Caching** | None | Redis cache for repeated city/date lookups |
| **Error handling** | Propagate to UI | Retry with exponential backoff, fallback providers |
| **Observability** | Chainlit steps | LangSmith / Langfuse traces |
| **Auth** | None | OAuth2 for user accounts, saved itineraries |
| **Testing** | Manual | Pytest with Amadeus sandbox fixtures |
