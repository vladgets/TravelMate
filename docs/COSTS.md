## Token Breakdown — Single Barcelona-style ### Request                                                                                                                                             
                                                                                                                                                                                               
### Main Orchestrator Loop (7 turns)                                                                                                                                                             

  Each turn re-sends the full history + system prompt (~1,336 tokens fixed overhead). History grows as tool results accumulate:

  ┌───────┬────────────────────────────────────────────────────────┬──────────────────┬────────┐
  │ Turn  │                      What happens                      │ Cumulative input │ Output │
  ├───────┼────────────────────────────────────────────────────────┼──────────────────┼────────┤
  │ 1     │ user message → parse_travel_request call               │ ~1,400           │ ~60    │
  ├───────┼────────────────────────────────────────────────────────┼──────────────────┼────────┤
  │ 2     │ parse result → search_flights + get_weather (parallel) │ ~1,600           │ ~120   │
  ├───────┼────────────────────────────────────────────────────────┼──────────────────┼────────┤
  │ 3     │ flights + weather → search_hotels                      │ ~2,220           │ ~60    │
  ├───────┼────────────────────────────────────────────────────────┼──────────────────┼────────┤
  │ 4     │ hotels → search_hotel_details                          │ ~2,480           │ ~60    │
  ├───────┼────────────────────────────────────────────────────────┼──────────────────┼────────┤
  │ 5     │ hotel details → calculate_budget                       │ ~2,940           │ ~80    │
  ├───────┼────────────────────────────────────────────────────────┼──────────────────┼────────┤
  │ 6     │ budget → format_itinerary                              │ ~3,120           │ ~150   │
  ├───────┼────────────────────────────────────────────────────────┼──────────────────┼────────┤
  │ 7     │ itinerary result (stubbed) → final answer              │ ~3,320           │ ~200   │
  ├───────┼────────────────────────────────────────────────────────┼──────────────────┼────────┤
  │ Total │                                                        │ ~17,000          │ ~730   │
  └───────┴────────────────────────────────────────────────────────┴──────────────────┴────────┘

  ### Sub-calls (outside the loop)

  ┌──────────────────────┬──────────────────────────────────────────────────────┬────────┬────────┐
  │         Call         │                       Purpose                        │ Input  │ Output │
  ├──────────────────────┼──────────────────────────────────────────────────────┼────────┼────────┤
  │ parse_travel_request │ JSON extraction LLM call                             │ ~300   │ ~150   │
  ├──────────────────────┼──────────────────────────────────────────────────────┼────────┼────────┤
  │ search_hotel_details │ Anthropic web_search call (search results are heavy) │ ~2,500 │ ~800   │
  ├──────────────────────┼──────────────────────────────────────────────────────┼────────┼────────┤
  │ format_itinerary     │ Markdown generation LLM call                         │ ~1,300 │ ~700   │
  ├──────────────────────┼──────────────────────────────────────────────────────┼────────┼────────┤
  │ Total                │                                                      │ ~4,100 │ ~1,650 │
  └──────────────────────┴──────────────────────────────────────────────────────┴────────┴────────┘

  ---
  ### Cost Calculation

  ┌──────────────┬─────────┬────────┬─────────┐
  │              │ Tokens  │  Rate  │  Cost   │
  ├──────────────┼─────────┼────────┼─────────┤
  │ Input total  │ ~21,000 │ $3/1M  │ ~$0.063 │
  ├──────────────┼─────────┼────────┼─────────┤
  │ Output total │ ~2,400  │ $15/1M │ ~$0.036 │
  ├──────────────┼─────────┼────────┼─────────┤
  │ Grand total  │         │        │ ~$0.10  │
  └──────────────┴─────────┴────────┴─────────┘

  ---
  ### Key Observations

  - Output is the expensive half despite being 9× fewer tokens — $15/1M vs $3/1M means output costs ~3× more per token
  - search_hotel_details alone costs ~$0.02 — the web search results returned by Anthropic are token-heavy (~2K input). That's why we capped it at 3 hotels and max_uses=3
  - History repetition is the main cost driver — system prompt + tool schemas (~1,336 tokens) are re-sent on every one of the 7 main loop turns, totaling ~9,300 tokens of pure repetition
  - For multi-turn follow-ups ("make it cheaper"), cost drops to ~$0.02-0.04 since the agent only re-runs 1-2 tools instead of the full 7-step flow



## Real price comparison
**Model	        Input	Output	Relative cost**
GPT-4.1 (full)	$2	    $8	    baseline
Claude Sonnet	$3	    $15	    expensive
Claude Haiku	$1	    $5	    cheap
GPT-4.1 mini	$0.40	$1.60	very cheap
GPT-4.1 nano	$0.10	$0.40	ultra cheap

(all per 1M tokens)