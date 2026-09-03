# InterviewOS

An adaptive, voice-native AI interviewer for Product Manager interviews. InterviewOS conducts realistic mock interviews with two AI personas, builds a live evidence map from candidate answers, and generates competency-based feedback reports.

Built for the **EchoSphere Agora Conversational AI Hackathon** (Round 3 Sprint).

## What It Does

InterviewOS runs a full-length PM interview with two AI interviewers -- Maya (warm, probing Senior PM) and Raj (blunt, skeptical Hiring Manager). Unlike scripted interview bots, the system dynamically decides what to ask based on what the candidate has (and hasn't) demonstrated, using a live evidence map that tracks claims, evidence, gaps, vague statements, and contradictions across 6 competency areas.

## Key Features

- **Real-time voice interviews** via Agora Conversational AI v2 (Deepgram STT + MiniMax TTS)
- **Two distinct interviewer personas** with separate voices, personalities, and focus areas
- **Live evidence map** -- claims, evidence, gaps, vague statements, contradictions tracked per turn
- **9-action orchestrator** -- follow-up, probe, challenge, advance, cameo, hand-off, wrap-up
- **Cameo system** -- one interviewer can jump in with a quick question during the other's section
- **Think-time support** -- candidates can request time to think with a visible countdown timer
- **Competency-based feedback report** -- scores, strengths, improvements, hire recommendation
- **Echo suppression** -- agent-first priority with debounced volume detection
- **Barge-in support** -- candidates can interrupt naturally during the interview
- **Split-screen meeting UI** -- participant cards with live speaking/listening/thinking indicators

## Architecture

```
Candidate (browser)
    |
    v
Agora Web SDK (RTC audio)  <-->  Agora Cloud (STT/TTS pipeline)
    |                                    |
    v                                    v
Split-screen UI (voice.js)     LLM Callback (agora_llm_callback.py)
                                         |
                                    +---------+
                                    |         |
                              Evidence    Orchestrator
                              Extractor   (decide action)
                                    |         |
                                    +---------+
                                         |
                                    Groq LLM API
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python FastAPI |
| LLM | Groq API (`openai/gpt-oss-20b`) |
| Voice/RTC | Agora Conversational AI v2 |
| STT | Deepgram Nova 3 (managed by Agora) |
| TTS | MiniMax Speech 2.8 Turbo (managed by Agora) |
| Frontend | Vanilla HTML/CSS/JS + Agora Web SDK 4.x |
| Tunnel | ngrok (reserved domain for Agora callbacks) |

## Setup

### Prerequisites

- Python 3.9+
- ngrok account (with a reserved domain)
- Agora account with Conversational AI enabled
- Groq API key

### Environment Variables

Create a `.env` file:

```
GROQ_API_KEY=your_groq_api_key
AGORA_APP_ID=your_agora_app_id
AGORA_APP_CERTIFICATE=your_agora_app_certificate
AGORA_CUSTOMER_ID=your_agora_customer_id
AGORA_CUSTOMER_SECRET=your_agora_customer_secret
SERVER_PUBLIC_URL=https://your-domain.ngrok-free.dev
```

### Install and Run

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Terminal 1: Start the server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Start ngrok tunnel
ngrok http 8000 --domain=your-reserved-domain.ngrok-free.dev
```

Open `http://localhost:8000` in your browser.

## How an Interview Works

1. **Enter your name/email** on the landing page
2. **Upload your resume** (PDF or TXT) -- optional but recommended
3. **Click "Start Voice Interview"** -- Maya greets you and introduces Raj
4. **Maya interviews first** -- covers Product Sense, Customer Understanding, Metrics, Prioritization
5. **Evidence map builds in real-time** -- the AI tracks what you claim vs. what you prove
6. **Raj takes over** -- covers Execution, Leadership, Communication with his direct style
7. **Cameos** -- either interviewer can jump in with a quick question during the other's section
8. **Interview wraps up** -- when competency areas are sufficiently covered
9. **Feedback report** -- competency scores, strengths, areas for improvement, hire recommendation

## Interview Flow

```
Greeting -> Intro (1-2 turns) -> Deep Dive (evidence-driven) -> Hand-off -> Deep Dive -> Wrap-up -> Report
                                      |                              |
                                  Cameo (optional)              Cameo (optional)
```

## Project Structure

```
InterviewOS/
  main.py                 # Core interview logic -- sessions, prompts, evidence, orchestration
  agora_agent.py          # Agora agent lifecycle -- start/stop/swap agents
  agora_config.py         # Voice session state + Agora config constants
  agora_llm_callback.py   # Custom LLM callback -- dedup, think time, answer pipeline
  agora_routes.py         # Voice HTTP endpoints -- start/stop/status/ready
  requirements.txt        # Python dependencies
  PROGRESS.md             # Detailed development progress
  static/
    index.html            # Split-screen meeting UI -- lobby, participants, transcript
    voice.js              # Agora RTC client, volume indicator, think timer
```

## License

MIT
