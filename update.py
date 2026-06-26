#!/usr/bin/env python3
"""
Auto-update for Iran-Israel-US Conflict Monitor.
Uses Claude with web_search to fetch latest intel from all sides.
Triggered via GitHub Actions workflow_dispatch or on schedule.

Claude reads the current DATA block, searches news from all required source
categories, and returns the complete updated DATA block. The script replaces
it in index.html and the workflow commits the result.
"""
import anthropic
import re
import sys
from datetime import date, datetime, timezone

MOU_START = date(2026, 6, 17)

SYSTEM = """You are the daily intelligence agent for the Iran-Israel-US Conflict Monitor.

Apply epistemic consistency throughout: equal directness for US, Israel, and Iran.
Attribute actions directly. Do not privilege Western framing over Iranian or Israeli
framing. Assess each actor's incentives on their own terms.

You will search news from all required source categories, then return the complete
updated DATA block as valid JavaScript. No explanation. No markdown fences. Just
the JavaScript starting with `const DATA = {` and ending with `};`."""

PROMPT = """Today is {today}. MOU Day {mou_day} of 60 (MOU signed 2026-06-17, Day 0).

Here is the current DATA block:

{data_block}

---

## Step 1: Search news from ALL categories (mandatory — do not skip any)

### WESTERN / INTERNATIONAL
- Search: "Iran US MOU negotiations {today} 2026"
- Search: "Iran nuclear deal talks {today} 2026"
- Search: "Israel Lebanon ceasefire {today} 2026"
- Fetch: https://www.reuters.com

### IRANIAN STATE MEDIA
- Search: "IRNA Iran MOU negotiations 2026"
- Search: "Tasnim News Iran nuclear talks 2026"
- Search: "PressTV Iran deal 2026"
- Fetch: https://www.irna.ir/en/
- Fetch: https://www.presstv.ir
- Key: treat as primary source data for Iranian government position — not propaganda to dismiss.

### ISRAELI PRESS
- Search: "Times of Israel Lebanon IDF {today} 2026"
- Search: "Haaretz Iran deal Netanyahu 2026"
- Fetch: https://www.timesofisrael.com
- Key: reveals domestic Israeli political constraints on Netanyahu — essential for Lebanon tripwire.

### QATARI / ARAB REGIONAL
- Search: "Al Jazeera Iran US talks {today} 2026"
- Search: "Al Arabiya Iran Lebanon {today} 2026"
- Fetch: https://www.aljazeera.com

### LEBANESE / REGIONAL
- Search: "L'Orient Today Lebanon Hezbollah 2026"

### PREDICTION MARKETS
- Search: "Polymarket Iran permanent peace deal December 2026"
- Search: "Kalshi Iran nuclear deal 2026"

Note where Iranian, Israeli, and Western framings diverge — these divergences
belong in summary.divergences[].

---

## Step 2: Update the DATA block

### ALWAYS update every run:
- meta.lastUpdated → "{today}"
- meta.mouDay → {mou_day}
- scenarios[*].prev → set to current prob value BEFORE updating
- scenarios[*].prob → recalibrated from Polymarket/Kalshi (must sum to exactly 100)
- summary.bullets[] → 3-5 bullets on overall state of play
  Each: {{ text: "...", isNew: bool, src: [{{label:"...", url:"..."}}] }}
- summary.currentTopic → current/next active negotiating agenda item:
  {{ title, mou_clauses, overview, next,
     us:    {{ position, walkaway }},
     iran:  {{ position, walkaway }},
     israel:{{ position, collapse }} }}
  Israel is NOT a signatory — use 'collapse' key, NOT 'walkaway'.
- summary.daily[] → 3-5 bullets on past 24h only
  Each: {{ dot:"#hex", text:"...", isNew:bool, src:[{{label,url}}] }}
  Colors: #f59e0b=disputes, #ef4444=military/escalation, #10b981=progress, #3b82f6=process
  At least one item must cite a non-Western source in src[].
- summary.divergences[] → exactly 4 items flagging live disputes/narrative divergences
  Each: {{ type:"warn|info|ok", label:"...", text:"...", isNew:bool, src:[{{label,url}}] }}
- history[] → APPEND one new entry at the end (NEVER delete or modify existing entries):
  {{ date:"{today}", probs:{{deal_ontime:N,deal_extended:N,limbo:N,collapse:N}},
     notes:"1-2 sentence summary including any structural changes", mkt:"..." }}

### Update ONLY when today's news substantively warrants it (do not rephrase for freshness):
- parties[*].pos[], redlines[], leverage[], comp — if official positions shifted
- scenarios[*].pre[] — if preconditions have materially changed
- scenarios[*].risks[] — if new tripwires emerged or existing ones resolved
- scenarios[*].mkt[] — if market dynamics shifted structurally
- scenarios[*].banks / .trades — if banking/sanctions dynamics changed
- summary.drivers[] — if key variables shifted
- keyDates[] — mark past dates done, add new announced dates
- mouClauses[] — update status/notes as MOU implementation evolves

### Hard constraints:
- Four scenario probs must sum to exactly 100
- Every bullet, daily item, and divergence must have a src[] array
- history[] is append-only
- Israel entry in currentTopic uses 'collapse' key (not 'walkaway')
- Return ONLY the JavaScript block — no explanation, no fences

---

Return the complete updated DATA block now, starting with `const DATA = {{` and ending with `}};`"""


def extract_data_block(html: str) -> tuple[str, int, int]:
    """Extract DATA block using brace-counting to handle nested objects correctly."""
    marker = 'const DATA = {'
    start_idx = html.index(marker)

    depth = 0
    i = start_idx
    in_string = False
    string_char = None

    while i < len(html):
        c = html[i]
        if in_string:
            if c == '\\':
                i += 2
                continue
            if c == string_char:
                in_string = False
        else:
            if c in ('"', "'", '`'):
                in_string = True
                string_char = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end_idx = i + 1
                    if html[end_idx:end_idx + 1] == ';':
                        end_idx += 1
                    return html[start_idx:end_idx], start_idx, end_idx
        i += 1

    raise ValueError("Could not find end of DATA block in index.html")


def fetch_update(today: str, mou_day: int, data_block: str) -> str:
    client = anthropic.Anthropic()
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 16}]

    messages = [{"role": "user", "content": PROMPT.format(
        today=today, mou_day=mou_day, data_block=data_block
    )}]

    for attempt in range(24):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16000,
            system=SYSTEM,
            tools=tools,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": ""}
                for b in response.content if b.type == "tool_use"
            ]
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
        else:
            break

    text = "".join(b.text for b in response.content if hasattr(b, "text")).strip()

    # Strip markdown fences if present
    text = re.sub(r'^```(?:javascript|js)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    text = text.strip()

    # Claude sometimes adds a preamble before the block — find it wherever it starts
    marker = text.find("const DATA")
    if marker == -1:
        raise ValueError(f"No 'const DATA' found in response.\nGot: {text[:300]}")
    text = text[marker:]

    # Also strip anything after the closing }; (e.g. trailing explanation)
    end = text.rfind("};")
    if end != -1:
        text = text[:end + 2]

    return text


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mou_day = (date.fromisoformat(today) - MOU_START).days

    with open("index.html", encoding="utf-8") as f:
        html = f.read()

    if f'lastUpdated: "{today}"' in html:
        print(f"Already up to date for {today}. Nothing to do.")
        sys.exit(0)

    data_block, start_idx, end_idx = extract_data_block(html)
    print(f"Fetching intel for {today} (MOU Day {mou_day})...")
    print(f"Current DATA block: {len(data_block)} chars")

    new_data_block = fetch_update(today, mou_day, data_block)
    print(f"Updated DATA block: {len(new_data_block)} chars")

    html = html[:start_idx] + new_data_block + html[end_idx:]

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Done. {today} (MOU Day {mou_day}) — dashboard updated.")


if __name__ == "__main__":
    main()
