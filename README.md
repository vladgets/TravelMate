# TravelMate — Agentic Vacation Planning PoC

A conversational AI travel planner that orchestrates real-time flight, hotel, and weather data to produce complete, budget-validated itineraries. Built to demonstrate advanced agentic patterns: parallel tool execution, adaptive replanning, multi-turn memory, and transparent step visibility.

---

## Architecture

```
User message (Chainlit)
        ↓
  orchestrator.run(user_message, history)
        ↓
  1. LiteLLM call → system_prompt + tools + history
        ↓
  2. LLM returns tool_calls? ─── YES ──→  Execute tools (parallel via asyncio.gather)
        │                                           ↓
        │                               Feed results back to LLM
        │                                           ↓
        │                               LLM continues reasoning (loop)
        ↓ NO (final text)
  Stream response → Chainlit UI
        ↓
  Persist history (multi-turn memory)
```

### Agentic Patterns Demonstrated

| Pattern | Where |
|---|---|
| **Parallel tool calls** | `search_flights` + `get_weather` in same batch via `asyncio.gather` |
| **Adaptive replanning** | LLM sees empty/expensive results and retries with adjusted params |
| **Multi-turn memory** | Full conversation history maintained across messages |
| **Transparent reasoning** | Every tool call shown as expandable Chainlit Step |
| **Provider abstraction** | `HotelProvider` interface — swap Amadeus → Expedia Rapid via env var |

---

## Project Structure

```
TravelMate/
├── app.py                        # Chainlit entry point
├── agents/
│   ├── orchestrator.py           # Agentic loop: tool use + replanning
│   └── tools.py                  # 6 tool schemas + implementations
├── services/
│   ├── base_travel_provider.py   # Abstract FlightProvider / HotelProvider
│   ├── amadeus_client.py         # Amadeus sandbox (flights + hotels)
│   ├── expedia_rapid_client.py   # Expedia Rapid API (hotels; swap via HOTEL_PROVIDER=expedia)
│   └── weather_client.py         # wttr.in wrapper (no auth)
├── models/
│   └── travel_models.py          # Pydantic models
├── config/
│   └── settings.py               # Pydantic Settings (reads .env)
├── prompts/
│   └── system_prompt.txt         # Orchestrator system prompt
├── .env.example
└── requirements.txt
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Get Amadeus credentials (free, instant)

1. Register at [developers.amadeus.com](https://developers.amadeus.com) — free tier, no credit card
2. Create an app → copy `Client ID` and `Client Secret`
3. Free sandbox includes: Flight Offers Search, Hotel List, Hotel Offers

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:
```env
LLM_MODEL=claude-sonnet-4-6
ANTHROPIC_API_KEY=sk-ant-...

AMADEUS_CLIENT_ID=your_client_id
AMADEUS_CLIENT_SECRET=your_client_secret
```

### 4. Run

```bash
# With Claude (default)
chainlit run app.py

# With OpenAI
LLM_MODEL=gpt-4o chainlit run app.py

# With Expedia Rapid API for hotels (requires partner credentials)
HOTEL_PROVIDER=expedia chainlit run app.py
```

Open [http://localhost:8000](http://localhost:8000)

---

## Tools

| Tool | Data Source | Description |
|---|---|---|
| `parse_travel_request` | LLM (JSON mode) | Extract structured params from natural language |
| `search_flights` | Amadeus sandbox | Real flight offers with prices and schedules |
| `search_hotels` | Amadeus / Expedia Rapid | Hotel availability with nightly rates |
| `get_weather` | wttr.in (no auth) | Current conditions for destination |
| `calculate_budget` | Pure Python | Validate total cost against user budget |
| `format_itinerary` | LLM | Produce polished markdown trip plan |

---

## Example Queries

```
I want to take my partner to Barcelona for 7 nights in April,
flying from New York. Budget is around $4000. We love food and art.
```

**Expected agent steps:**
1. `parse_travel_request` → JFK→BCN, April 2026, 2 adults, $4000
2. `search_flights` + `get_weather` (parallel)
3. `search_hotels`
4. `calculate_budget` → validates vs $4000 → if over, LLM retries
5. `format_itinerary` → complete markdown plan

```
Make it cheaper — I can be flexible on dates.
```

**Multi-turn memory:** Agent recalls the previous Barcelona plan and searches shoulder-season dates.

---

## Model Switching

TravelMate is model-agnostic via [LiteLLM](https://docs.litellm.ai):

```bash
LLM_MODEL=claude-sonnet-4-6  # Anthropic Claude (default)
LLM_MODEL=gpt-4o             # OpenAI
LLM_MODEL=claude-opus-4-6    # Most capable, slower
```

---

## Hotel Provider Abstraction

The `HotelProvider` abstract base class allows zero-code provider swapping:

```python
class HotelProvider(ABC):
    @abstractmethod
    async def search_hotels(self, city_code, check_in, check_out, ...) -> list[HotelResult]:
        ...
```

- **`AmadeusClient`** (default) — free sandbox, immediate setup
- **`ExpediaRapidClient`** — 750K+ properties, requires EPS partner credentials

Switch via: `HOTEL_PROVIDER=expedia` in `.env`

This mirrors how Expedia's internal teams design for lodging provider flexibility.

---

## Expedia Rapid API (Optional)

The Expedia Rapid API provides production-quality hotel inventory (750K+ properties). It requires an EPS (Expedia Partner Solutions) partnership agreement.

To enable:
1. Contact your Expedia interviewer/recruiter to request sandbox credentials
2. Add to `.env`:
   ```env
   HOTEL_PROVIDER=expedia
   EXPEDIA_EPS_CLIENT_ID=your_key
   EXPEDIA_EPS_CLIENT_SECRET=your_secret
   ```

The implementation (`services/expedia_rapid_client.py`) uses the correct HMAC-SHA512 auth scheme and hits the `test.ean.com` sandbox endpoint.
