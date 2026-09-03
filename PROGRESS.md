# InterviewOS — Development Progress

**Hackathon:** EchoSphere Agora Conversational AI Hackathon (Round 3 Sprint)
**Deadline:** Sep 4, 11:59 PM IST
**Category:** Conversational AI / Future of Work
**Builder:** Solo build

---

## What InterviewOS Is

An adaptive, voice-native AI interviewer for Product Manager interviews. The system builds a
live "evidence map" from candidate answers (claims, gaps, vague statements, contradictions)
and uses it to decide the most useful next question — rather than following a fixed script.

Two interviewer personas (Maya and Raj) share the evidence map and hand off mid-interview.
At the end, the candidate receives competency-specific feedback with evidence citations.

**Target architecture (from build plan):**
- Agora Conversational AI for real-time voice (barge-in, interruptibility)
- FastAPI backend with in-memory session state
- React + Agora Web SDK frontend
- LLM-powered evidence extraction, orchestration, and report generation

**Current approach:** Building the orchestrator + interview logic first (text-based), then
layering Agora voice integration on top once the logic is solid.

---

## Tech Stack (Current)

| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend | Python FastAPI | `main.py` (~1304 lines) + 4 Agora modules (~805 lines) |
| LLM | Groq API | Model: `openai/gpt-oss-20b` (free tier; build plan specified GPT-4o but cost prohibitive) |
| Voice/RTC | Agora Conversational AI v2 | Managed pipeline: Deepgram Nova 3 (STT) + MiniMax Speech 2.8 Turbo (TTS) |
| Voice SDK | Agora Web SDK 4.x | Client-side RTC for voice, volume indicator for speaker detection |
| Resume parsing | `pypdf` + plain text | PDF and TXT support |
| Frontend | Vanilla HTML/CSS/JS | `static/index.html` (~633 lines) + `voice.js` (~321 lines), split-screen meeting UI |
| State | In-memory Python dicts | One dict per session_id, no database |
| Auth/identity | Email or 10-digit phone | Normalized to `email:` or `phone:` prefix keys |
| HTTP client | httpx | Async calls to Agora REST API |
| Token generation | agora-token-builder | RTC tokens for Agora channels |
| Tunnel | ngrok | Reserved domain for Agora webhook callbacks |

**Dependencies** (`requirements.txt`):
```
fastapi
uvicorn
groq
python-dotenv
pypdf
python-multipart
httpx
agora-token-builder
```

**Environment** (`.env`):
```
GROQ_API_KEY=<required>
AGORA_APP_ID=<required>
AGORA_APP_CERTIFICATE=<required>
AGORA_CUSTOMER_ID=<required>
AGORA_CUSTOMER_SECRET=<required>
SERVER_PUBLIC_URL=<ngrok URL, e.g. https://your-domain.ngrok-free.dev>
```

**Run command:**
```bash
cd InterviewOS && source venv/bin/activate && uvicorn main:app --reload
# In a separate terminal, start ngrok:
ngrok http 8000 --url=your-reserved-domain.ngrok-free.dev
```

---

## What Has Been Built (as of 2026-09-03)

### 1. Session Management

**Files:** `main.py:62-123`, `main.py:471-490`

- `InterviewSession` dataclass holds all per-session state including: `interview_phase`,
  `maya_cameo_used`, `raj_cameo_used`, `cameo_active`, `cameo_persona`, `topics_asked`
- Sessions keyed by opaque `secrets.token_urlsafe(32)` IDs
- One active session per identifier (email/phone), enforced via `active_sessions_by_identifier`
- Thread-safe with `threading.Lock` per session + global `sessions_lock`
- Clean teardown: `remove_session()` wipes resume text, conversation history, evidence map,
  turn count, persona, and off-topic counter

**Endpoints:**
- `POST /sessions` — create session with email/phone identifier
- `DELETE /sessions/{session_id}` — end session, clear all data

### 2. Resume Upload (One-Shot)

**Files:** `main.py:225-233`, `main.py:493-557`

- Accepts PDF (parsed via `pypdf`) or TXT files
- Extracts text, truncated to 12,000 chars
- **One upload per session** — re-upload returns HTTP 409
- Resume text injected into the active persona's system prompt
- **Mid-interview upload:** If the interview is already active and Maya is the current
  persona, an automatic LLM call generates Maya's acknowledgment of the resume (references
  a specific detail from it). Returned as `maya_response` in the API response.
- Frontend disables the upload button and file input permanently after successful upload

**Endpoint:**
- `POST /sessions/{session_id}/upload-resume` — multipart file upload

### 3. Two Interviewer Personas (Maya and Raj)

**Files:** `main.py:126-222`

**Maya** (`build_maya_prompt`, line 154):
- PM / Product Interviewer — goes first
- **Personality:** Sharp, warm Senior PM with 10+ years experience. Genuinely wants the
  candidate to show their best but won't let vague answers slide. Think: the senior PM who
  mentors you but also calls you out in a product review.
- **Speech style:** Direct but warm. Short acknowledgments ("Got it.", "Okay.", "Interesting.")
  then her question. Example phrases: "Walk me through that.", "What made you pick that over X?",
  "Okay, but what did the data say?", "You mentioned 15% lift — lift in what, exactly?"
- Focus areas: Product Sense, Customer Understanding, Metrics, Prioritization
- **Opening:** Always starts with a warm greeting + introduces Raj. Asks a simple ice-breaker.
  Does NOT jump to scenario or background questions — the orchestrator controls that.
- **Cameo:** Can be invited by Raj for 1 quick question during his section (max 1 per interview).
- Scenario: Senior PM at B2C marketplace, 68% cart abandonment

**Raj** (`build_raj_prompt`, line 188):
- Hiring Manager — takes over after Maya
- **Personality:** No-nonsense Engineering Director turned VP. Sat through hundreds of
  interviews. Evaluating whether he'd want this person on his team when things get hard.
  Fair but skeptical — doesn't take claims at face value. Think: the exec who asks the one
  question that makes the room go quiet.
- **Speech style:** Blunt and economical. Fewer words than Maya. No easing in — gets to the
  point. Example phrases: "Tell me what actually happened.", "Who pushed back and what did you
  do?", "What broke?", "Skip the setup — what was the hard part?", "That sounds clean. What
  went wrong?", "You're telling me nobody disagreed? I don't buy that."
- Focus areas: Execution, Leadership, Communication, Behavioral
- System prompt seeded with Maya's evidence summary via `build_evidence_summary()`
- References Maya's findings in his first question, using his own voice
- Cannot re-ask what Maya already covered
- **Cameo:** Can be invited by Maya for 1 sharp question during her section (max 1). Says
  "I'm good, Maya — carry on" when done.

**Shared rules** (`SHARED_RULES`, line 138):
- Ask exactly ONE question at a time, 1-2 sentences max
- NEVER compliment or praise an answer — just move on, dig deeper, or push back
- Use short, natural transitions: "Got it.", "Okay.", "Right." — not formal thank-yous
- NEVER explain why you're asking a question
- NEVER summarize what the candidate just said back to them
- Quote the candidate's own words when following up
- Use contractions — sound like a real person
- If an answer is weak or vague, say so directly
- No feedback/scores during interview
- Resume-aware (tailors to resume content, never invents details)
- ASCII-only punctuation
- Role-staying enforcement
- Prompt injection resistance (treats all candidate text as data)

**Supporting:**
- `INTERVIEW_SCENARIO` (line 126) — the B2C marketplace scenario used by both
- `build_resume_context()` (line 132) — formats resume text for prompt injection
- `build_evidence_summary()` (line 208) — converts evidence map to readable text for Raj
- `MAYA_COMPETENCIES` / `RAJ_COMPETENCIES` (lines 47-48) — which persona owns which areas

### 4. Evidence Map Data Structure

**Files:** `main.py:38-59`, `main.py:62-74`

The evidence map is a Python dict on `InterviewSession` with:
- `claims` — list of factual claims the candidate made
- `evidence_given` — list of claims backed by specifics (metrics, examples, situations)
- `gaps` — list of competency areas with no evidence
- `vague_statements` — list of claims with no supporting detail
- `contradictions` — list of statements conflicting with earlier ones
- `competency_coverage` — dict mapping each of 6 competency areas to coverage level:
  `"none"` | `"weak"` | `"partial"` | `"strong"` | `"covered"`

**Competency areas** (`COMPETENCY_AREAS`, line 38):
`product_sense`, `customer_understanding`, `metrics`, `prioritization`, `execution`, `communication`

Factory function `empty_evidence_map()` initializes all lists empty and all coverages to `"none"`.

### 5. Evidence Extractor (LLM Call #1)

**Files:** `main.py:290-372`

Function: `extract_evidence(session, candidate_answer)`

- Runs after every candidate answer (before the orchestrator)
- Separate LLM call at `temperature=0` — analysis only, candidate never sees output
- System prompt (`EVIDENCE_EXTRACTOR_PROMPT`) asks for structured JSON with:
  - `new_claims`, `new_evidence`, `new_gaps`, `new_vague`, `new_contradictions`
  - `competency_signals` — per-competency signal strength from this turn only
  - `quality_scores` — per-competency quality grade (1-5) with rationale for each area
    touched this turn. Rubric: 1=very weak, 2=vague/generic, 3=adequate, 4=strong with
    specifics, 5=exceptional with deep insight
- **Receives:** interviewer question, candidate answer, existing evidence map, recent
  conversation (last 3 turns for contradiction detection), candidate resume (for claim
  verification against resume — flags contradictions)
- Strips markdown code fences if model wraps JSON
- Merges new findings into existing evidence map (appends to lists)
- Quality scores appended to `evidence_map["quality_scores"]` as `{turn, competency, score, rationale}`
- Increments `session.questions_per_area` for each competency touched (non-"none" signal)
- Coverage ratcheting via `COVERAGE_RANK` (line 318) — coverage only goes up, never down
- Fails silently on JSON parse error (doesn't interrupt the interview)

### 6. Orchestrator (LLM Call #2)

**Files:** `main.py:375-468`

Function: `decide_next_action(session)` → `{action, reasoning, question}`

- Runs after evidence extraction, before the interviewer responds
- LLM call at `temperature=0.3` (slightly warmer for question variety)
- System prompt (`ORCHESTRATOR_PROMPT`) includes: interview scenario, current persona, interview
  phase, competency coverage, turn count, topics already asked, cameo availability, and (for
  Raj) Maya's findings
- **Receives (user message):** full evidence map, candidate resume (truncated to 2000 chars),
  recent conversation (last 4 turns, non-system messages)
- Persona-aware question generation with voice/style instructions per persona
- Resume-aware: references specific roles/companies from resume during intro phase
- Enforces 1-2 sentence max for generated questions
- **Phase-aware**: orchestrator knows the current phase and restricts available actions accordingly
- **Topic diversity**: tracks topics asked, enforces max 3-4 questions per competency area,
  prevents same question type more than 2 times consecutively

**Interview phases** (managed by orchestrator):
| Phase | Description |
|-------|-------------|
| `greeting` | Maya's opening — greeting + introduce Raj (kickoff, no orchestrator) |
| `intro` | Turns 1-2 — orchestrator picks `intro` or `begin_scenario` |
| `deep_dive` | Main interview — all deep-dive actions available |
| `closing` | `wrap_up` triggered — interview ends |

**9 possible actions:**
| Action | Phase | When |
|--------|-------|------|
| `intro` | intro | Ask about background/experience |
| `begin_scenario` | intro | Transition to deep dive scenario (after 1-2 intro turns) |
| `follow_up` | deep_dive | Dig deeper into something the candidate just said |
| `probe` | deep_dive | Candidate made a claim with no evidence — ask for specifics |
| `challenge` | deep_dive | Statement is vague or contradicts something earlier |
| `advance` | deep_dive | Current competency area covered enough, move to next gap |
| `cameo` | deep_dive | Invite the other interviewer for 1 quick question (max 1 per lead) |
| `hand_off` | deep_dive | Maya's areas covered — invite Raj to take over (Maya only) |
| `wrap_up` | deep_dive | All areas covered — closing note, interview ends |

**Safety guards:**
- `intro`/`begin_scenario` forced during intro phase (blocks premature deep-dive actions)
- `cameo` blocked if already used in this lead section → downgrades to `advance`
- `wrap_up` blocked if any persona areas below "partial" → downgrades to `advance`
- `hand_off` blocked for Raj → downgrades to `advance`
- Falls back to `follow_up` with empty question on JSON parse failure

**Cameo flow:**
1. Orchestrator picks `cameo` → lead says "Raj/Maya, anything on this?"
2. Separate LLM call generates 1 question from the cameo interviewer
3. Candidate responds to cameo question
4. Lead automatically takes back over with a "Thanks, [name]" + continues

### 7. Answer Pipeline (Wired Together)

**Files:** `main.py:595-673`

The `POST /sessions/{session_id}/answer` endpoint runs this pipeline per turn:

```
1. If cameo_active: handle cameo return (lead takes back, continues with orchestrator)
2. Off-topic check — blocks non-interview messages
3. Increment turn_count
4. LLM call #1: extract_evidence() — updates evidence map
5. LLM call #2: decide_next_action() — picks action + question
6. Track topic in topics_asked for diversity
7. Handle action:
   - wrap_up: closing note, interview_ended=True
   - cameo: lead invites other, LLM generates cameo question, cameo_active=True
   - hand_off: Maya says handoff line, system prompt swaps to Raj, Raj opens
   - begin_scenario: phase set to deep_dive, question used as transition
   - intro/follow_up/probe/challenge/advance: question used directly
7. If orchestrator returned empty: fallback to conversation-based LLM call
```

**Response includes:**
- `next_question` — the interviewer's response text
- `persona` — current persona ("maya" or "raj")
- `action` — what the orchestrator decided (shown as a notice in the UI)
- `evidence_map` — full current evidence map snapshot
- `interview_ended` — boolean

### 8. Off-Topic Guardrail

**Files:** `main.py` — `is_off_topic_message()`

- Keyword-based pattern check (replaced LLM classifier to cut ~1-2s per turn)
- Catches prompt injection attempts, joke requests, code help, etc.
- Resume-related messages always pass (keyword shortcut)
- Short messages (≤3 words) always pass — likely answers or clarifications
- 2 consecutive off-topic messages → session auto-ends
- Fails open: defaults to on-topic

### 9. Text Cleanup

**Files:** `main.py:279-287`

`clean_interviewer_text()` — replaces typographic punctuation (curly quotes, em dashes,
UTF-8 encoding artifacts) with plain ASCII equivalents.

### 10. Report Generator (LLM Call #3)

Function: `generate_feedback_report(session)` → JSON report dict

- Runs once at interview end when `wrap_up` is triggered
- LLM call at `temperature=0` (deterministic)
- Takes full evidence map (with quality scores), questions_per_area, candidate resume (for
  experience-level contextualization), and conversation summary
- Returns structured JSON: `overall_score`, `competency_scores` (per area with summary),
  `strengths` (2-3 with evidence citations), `areas_for_improvement` (2-3 with citations),
  `recommendation` (hire/lean_hire/lean_no_hire/no_hire), `recommendation_rationale`
- Scoring rubric: 1=very weak to 5=exceptional
- `overall_score` is weighted average (product_sense and execution weighted 1.5x)
- Falls back to computed averages from quality_scores if LLM call fails

### 11. Hard Topic Limits

- `MAX_QUESTIONS_PER_AREA = 6` — code-enforced constant
- `session.questions_per_area` — dict tracking question count per competency area
- Incremented in `extract_evidence()` when a competency signal is non-"none"
- Orchestrator prompt receives the counts and is told exhausted areas (at 6) are off-limits
- Hard guard in `decide_next_action()`: if ALL areas for the current persona are exhausted,
  forces `hand_off` (Maya) or `wrap_up` (Raj)
- Flow remains smooth — orchestrator advances naturally when quality scores are strong (4-5
  after 2-3 questions), doesn't need to use all 6

### 12. Frontend (Chat UI)

**Files:** `static/index.html` (197 lines, single file)

- Dark-themed, responsive chat interface
- No build step, no framework — vanilla HTML/CSS/JS + `fetch`
- Landing page: "Meet Maya & Raj" — enter email/phone to start
- Chat view:
  - Top bar: brand, persona label (updates on handoff), session status
  - Resume bar: file input + upload button (permanently disabled after upload) + start button
  - Messages area: assistant (left), user (right), notices (center)
  - Composer: text input + send button
- Shows orchestrator action as a small `[follow up]` / `[probe]` / `[hand off]` notice
  between messages
- Persona label in header updates when handoff happens
- Mid-interview resume upload: if API returns `maya_response`, displays it as assistant message
- **Feedback report card**: displayed when interview ends, shows overall score (large number),
  per-competency scores (color-coded: red 1-2, yellow 3, green 4-5), strengths list, areas
  for improvement list, recommendation badge (hire/lean_hire/lean_no_hire/no_hire with color),
  and recommendation rationale. Dark-themed card matching existing chat UI.

### 13. Agora Voice Integration

**Files:** `agora_agent.py`, `agora_config.py`, `agora_llm_callback.py`, `agora_routes.py`

- **Architecture:** Agora Conversational AI v2 with a custom LLM callback. Agora manages the
  full voice pipeline — Deepgram Nova 3 handles STT, MiniMax Speech 2.8 Turbo handles TTS.
  For every candidate utterance, Agora sends the transcript to our custom callback endpoint
  (`POST /agora/llm/{session_id}`), which runs the same evidence extraction + orchestration
  pipeline as text mode and returns the interviewer's response as SSE chunks.
- **Agent UIDs:** Agent = 999, Candidate = 1000. RTC tokens generated via `agora-token-builder`.
- **Greeting:** An LLM call (`temperature=0.7`) generates a varied greeting before the Agora
  agent starts. The greeting is passed as `greeting_message` to the Agora agent payload, so
  the agent speaks it via TTS immediately upon joining — no awkward silence.
- **Persona swap:** Agora v2 doesn't support mid-session system prompt changes. Persona
  handoff (Maya → Raj) and cameos work by stopping the current agent and starting a new one
  with the new persona's system prompt and voice. 4-second swap delay.
- **Voice session state:** `VoiceSessionState` dataclass in `agora_config.py` tracks per-session
  voice state: agent_id, channel, persona, swap status, think mode, message merging buffers.

### 14. Split-Screen Meeting UI

**Files:** `static/index.html`, `static/voice.js`

- **Lobby:** "Meet Maya & Raj" landing page with name/email input, resume upload, and two
  start buttons: voice-primary (large teal button) and text-secondary (smaller bordered button).
- **Left panel — Meeting room:** Three participant cards (Maya, Raj, Candidate) with SVG avatars,
  name/role labels, status text ("Speaking"/"Listening"/"Thinking..."/"Muted"), animated audio
  waves (5-bar animation when speaking), and thinking dots (3-dot pulsing animation when
  processing). Resume upload row available during interview (not just lobby).
- **Right panel — Transcript:** Scrolling chat showing the conversation as it happens. Messages
  polled from `/voice/sessions/{id}/status` every 2 seconds.
- **Bottom controls:** Mute/unmute button + end call button. Keyboard shortcut Ctrl+M for mute.
- **Live transcription bar:** Below participants, shows the latest transcript snippet.

### 15. Real-Time Speaker Detection

**Files:** `static/voice.js`

- Uses Agora's `enableAudioVolumeIndicator()` which fires `volume-indicator` events every ~200ms
  with per-user audio levels.
- Volume threshold: level > 15 = speaking (raised from 5 to suppress echo). Only checks remote
  users by UID — removed raw `localAudioTrack.getVolumeLevel()` check (was picking up TTS echo).
- **Debounce:** Requires 2 consecutive ticks above threshold before changing state (`DEBOUNCE_TICKS = 2`).
- **Agent-first priority:** When both agent and candidate are detected speaking, agent wins
  (any candidate detection during TTS playback is echo, not real speech).
- Replaces the previous approach of inferring speaker state from transcript polling (which had
  5-10 second delay). Now speaker highlighting switches in ~400ms (200ms per tick x 2 ticks).

### 16. Fragmented Speech Fixes

**Files:** `agora_agent.py`, `agora_llm_callback.py`

- **Primary fix:** Deepgram `silence_duration_ms: 3000` in ASR config. Deepgram's default VAD
  cuts transcripts on very short pauses (~300ms), fragmenting natural speech into multiple
  messages. 3 seconds gives candidates room to pause mid-sentence without triggering a cut.
- **Fallback fix:** Message merging in the LLM callback. If a new transcript arrives within 2
  seconds of the previous one, it's appended to a pending buffer and an empty SSE is returned
  (no LLM call). Once 2+ seconds pass, the merged buffer is processed as one complete message.
  Controlled via `pending_transcript` and `last_transcript_time` on `VoiceSessionState`.

### 17. Thinking/Processing Indicators

**Files:** `static/index.html` (CSS + HTML), `static/voice.js`

- Three-dot pulsing animation (CSS `@keyframes thinking`) replaces audio waves when a
  participant is processing/waiting.
- **Interviewer thinking:** When the candidate pauses speaking for > 0.5 seconds, the
  interviewer's card switches from audio waves to thinking dots with "Thinking..." status.
  This gives visual feedback that the AI is processing.
- **State transitions:** Candidate starts speaking → clear interviewer thinking. Agent starts
  speaking → clear all thinking. Both silent > 0.5s after candidate was speaking → show
  interviewer thinking.

### 18. Think-Time Countdown

**Files:** `agora_llm_callback.py`, `agora_routes.py`, `static/index.html`, `static/voice.js`

- **Detection:** Regex in the LLM callback catches phrases like "give me 2 minutes", "let me
  think", "need a moment", "can I have 30 seconds". Extracts duration (default 60s, max 180s).
- **Countdown UI:** Timer overlay appears on the candidate's participant card showing remaining
  time (e.g. "1:42") with a "Ready to answer" button. JavaScript countdown updates every second.
- **Ready button:** Clicking it hides the timer and sends `POST /voice/sessions/{id}/ready` to
  clear think mode on the server. Think time is logged in the transcript as a notice.
- **Timer expiry:** When countdown reaches 0, the interviewer politely asks "Are you ready with
  your answer? Go ahead whenever you are."
- **Repeated requests:** If the candidate asks for more time while already thinking (second
  request), the interviewer instead says "Why don't you share your thought process with me?
  Walk me through what you're considering."
- **Transcript logging:** Think time is recorded in conversation history as
  `[Candidate took Xs to think]` for the feedback report.

---

## What Is NOT Built Yet

### Not started:
- **Evidence map in results** — the evidence map powers the AI brain during the interview
  (deciding what to ask, when to probe, when to advance) but is not shown to the candidate.
  Plan: display it in the final results screen alongside the feedback report as a "behind the
  scenes" view — competency coverage bars, claims/evidence/gaps lists, quality scores. Gives
  the candidate transparency into how the AI evaluated them, post-interview only.
- **Demo video / README** — required for hackathon submission.

### Fixed (2026-08-26):
- **Persona personality** — DONE. Maya and Raj now have distinct characters, speech styles,
  and example phrases.
- **Question quality** — DONE. Anti-chatbot rules in `SHARED_RULES`.
- **Orchestrator-driven interview flow** — DONE. 9 actions, 4 phases, cameo system.
- **Per-answer quality scoring** — DONE. 1-5 grades per competency per turn.
- **Hard topic limits** — DONE. `MAX_QUESTIONS_PER_AREA = 6`.
- **Feedback report** — DONE. Competency scores, strengths, improvements, hire recommendation.
- **Full data flow to brain components** — DONE. All LLM calls receive resume + context.
- **Async parallel execution** — DONE. Evidence extraction + orchestration run in parallel.

### Fixed (2026-08-31 to 2026-09-02):
- **Agora voice integration** — DONE. Full voice pipeline with custom LLM callback.
- **Split-screen meeting UI** — DONE. Participant cards, transcript, controls.
- **LLM-generated greeting** — DONE. Maya speaks a varied greeting on join.
- **Real-time speaker detection** — DONE. Volume indicator replaces transcript polling.
- **In-interview resume upload** — DONE. Upload row visible during active interview.
- **Fragmented speech fixes** — DONE. Deepgram VAD tuning + message merging.
- **Thinking indicators** — DONE. Pulsing dots on processing state.
- **Think-time countdown** — DONE. Timer, ready button, interviewer nudges.

### Fixed (2026-09-03):
- **Echo suppression (Fix E)** — DONE. Agent-first priority in volume-indicator handler,
  threshold raised 5→15, debounce requiring 2 consecutive ticks. Removed raw
  `localAudioTrack.getVolumeLevel()` check that was picking up TTS audio from speakers.
- **Thinking dots fix (Fix E)** — DONE. `candidateWasSpeaking` now resets in silence branch
  so thinking fires once. Removed `setAllIdle()` from silence branch so thinking state persists.
- **Speech fragmentation fix (Fix F)** — DONE. `silence_duration_ms` increased 1500→3000 to
  prevent mid-sentence transcript cuts.
- **Server-side message dedup (Fix G1)** — DONE. LLM callback checks if incoming transcript
  matches last user message in conversation history. If duplicate (Agora retry), returns cached
  last assistant response instead of re-processing.
- **Client-side message dedup (Fix G2)** — DONE. `displayedMessages` Set in voice.js tracks
  `role|content` keys, prevents re-rendering the same message in the transcript panel.

### Known quality issues:
- None critical remaining. All voice fixes applied, pending live verification.

---

## File Structure

```
InterviewOS/
  .env                      # API keys: Groq + Agora + ngrok URL
  main.py                   # Core interview logic — sessions, prompts, evidence, orchestration (~1304 lines)
  agora_agent.py            # Agora agent lifecycle — start/stop/swap agents (~173 lines)
  agora_config.py           # Voice session state dataclass + Agora config constants (~65 lines)
  agora_llm_callback.py     # Custom LLM callback — message merging, think time, answer pipeline (~380 lines)
  agora_routes.py           # Voice HTTP endpoints — start/stop/status/ready (~187 lines)
  requirements.txt          # Python dependencies (8 packages)
  PROGRESS.md               # This file
  static/
    index.html              # Split-screen meeting UI — lobby, participants, transcript (~633 lines)
    voice.js                # Agora RTC client, volume indicator, think timer (~321 lines)
  venv/                     # Python virtual environment (not tracked)
```

---

## API Endpoints

### Text Mode
| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Serves `static/index.html` |
| `POST` | `/sessions` | Create session (body: `{identifier}`) |
| `POST` | `/sessions/{id}/upload-resume` | Upload PDF/TXT resume (multipart, one-shot) |
| `POST` | `/sessions/{id}/start-interview` | Start text interview, returns Maya's opening |
| `POST` | `/sessions/{id}/answer` | Submit answer, returns next question + evidence map |
| `DELETE` | `/sessions/{id}` | End session, clear all data |

### Voice Mode
| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/voice/sessions/{id}/start` | Start voice interview — generates greeting, starts Agora agent |
| `POST` | `/voice/sessions/{id}/stop` | Stop voice interview — leaves Agora channel, stops agent |
| `GET` | `/voice/sessions/{id}/status` | Poll transcript, persona, think mode, evidence map |
| `POST` | `/voice/sessions/{id}/ready` | Signal candidate is ready after think time |
| `POST` | `/agora/llm/{session_id}` | Agora's LLM callback (not user-facing — Agora calls this) |

---

## Key Design Decisions

1. **Groq instead of GPT-4o** — cost (GPT-4o not free, solo self-funded build)
2. **Orchestrator-first build order** — logic before voice (inverted from build plan's Day 1)
3. **In-memory state only** — no DB/Redis, a dict per session. Server restart loses sessions.
4. **Two-agent swap** — Agora v2 doesn't support changing system prompt mid-session, so
   persona swap (Maya → Raj, cameos) = stop old agent + start new agent with new prompt/voice.
   4-second delay covers the transition.
5. **Custom LLM callback** — Agora calls our server for every LLM turn. Runs the same
   evidence extraction + orchestration pipeline as text mode — no separate voice logic.
6. **LLM-generated greeting** — `_llm_call(temperature=0.7)` generates a varied greeting
   before the Agora agent starts. Passed as `greeting_message` so the agent speaks it
   immediately via TTS. No fixed greeting script.
7. **Volume-based speaker detection** — Agora's `enableAudioVolumeIndicator()` over
   transcript-based polling. ~200ms vs 5-10s for speaker state changes.
8. **Deepgram VAD tuning** — `silence_duration_ms: 3000` prevents premature transcript cuts.
   Message merging (2s window) as fallback for any remaining fragmentation.
9. **ngrok reserved domain** — paid plan for stable webhook URL that survives restarts
10. **One-shot resume** — uploaded once per session, cannot be replaced
11. **Mid-interview resume upload** — Maya auto-acknowledges and pivots
12. **Evidence coverage ratcheting** — coverage only goes up, never down
13. **Evidence map is internal** — powers the AI brain during the interview but is not shown
    to the candidate live. Will be displayed post-interview in the results screen.
