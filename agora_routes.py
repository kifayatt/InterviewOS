import random

from fastapi import APIRouter, HTTPException

from agora_config import (
    AGORA_APP_ID,
    CANDIDATE_UID,
    generate_agora_rtc_token,
    voice_sessions,
    VoiceSessionState,
)
from agora_agent import start_agent, stop_agent

voice_router = APIRouter(prefix="/voice")

_GREETINGS = [
    "Hi! I'm Maya, a Senior Product Manager here, and Raj will hop in shortly. How are you doing today?",
    "Hey there! I'm Maya -- I lead product here. Raj, our hiring manager, will join us a bit later. How's your day going?",
    "Hi! Maya here, Senior PM. Raj will be joining shortly. Before we dive in, how are you doing?",
    "Hey! I'm Maya, a Senior PM on the team. Raj will pop in later. How are things on your end?",
]

_GREETINGS_WITH_NAME = [
    "Hi {name}! I'm Maya, a Senior Product Manager here, and Raj will hop in shortly. How are you doing today?",
    "Hey {name}! I'm Maya -- I lead product here. Raj, our hiring manager, will join us a bit later. How's your day going?",
    "Hi {name}! Maya here, Senior PM. Raj will be joining shortly. Before we dive in, how are you doing?",
    "Hey {name}! I'm Maya, a Senior PM on the team. Raj will pop in later. How are things on your end?",
]


@voice_router.post("/sessions/{session_id}/start")
async def start_voice_interview(session_id: str):
    from main import (
        sessions,
        get_session,
        build_maya_prompt,
        build_scenario,
        empty_evidence_map,
        COMPETENCY_AREAS,
    )

    session = get_session(session_id)

    if session.interview_active:
        raise HTTPException(status_code=409, detail="This interview has already started.")

    if session_id in voice_sessions:
        raise HTTPException(status_code=409, detail="A voice session is already active.")

    if not AGORA_APP_ID:
        raise HTTPException(status_code=500, detail="Agora credentials not configured. Set AGORA_APP_ID in .env.")

    channel_name = f"interview-{session_id[:12]}"
    token = generate_agora_rtc_token(channel_name, CANDIDATE_UID)

    # Initialize interview state (same as text-mode start)
    import time
    session.interview_active = True
    session.interview_start_time = time.time()
    session.off_topic_attempts = 0
    session.current_persona = "maya"
    session.turn_count = 0
    session.evidence_map = empty_evidence_map()
    session.interview_phase = "intro"
    session.maya_cameo_used = False
    session.raj_cameo_used = False
    session.cameo_active = False
    session.questions_per_area = {a: 0 for a in COMPETENCY_AREAS}

    # Create voice session state
    vs = VoiceSessionState(session_id=session_id, channel_name=channel_name)
    voice_sessions[session_id] = vs

    # Build Maya's system prompt and generate a varied greeting
    has_resume = bool(session.resume_text)
    maya_prompt = build_maya_prompt(session.resume_text, has_resume, scenario=build_scenario(session))

    if session.candidate_name:
        greeting_text = random.choice(_GREETINGS_WITH_NAME).format(name=session.candidate_name)
    else:
        greeting_text = random.choice(_GREETINGS)

    kickoff = "Start the interview. Greet the candidate, introduce yourself and mention Raj."

    session.conversation_history = [
        {"role": "system", "content": maya_prompt},
        {"role": "user", "content": kickoff},
        {"role": "assistant", "content": greeting_text},
    ]

    try:
        await start_agent(
            session_id, "maya", channel_name, maya_prompt, CANDIDATE_UID,
            greeting_message=greeting_text,
        )
    except Exception as e:
        voice_sessions.pop(session_id, None)
        session.interview_active = False
        print(f"[VOICE START ERROR] {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start Agora agent: {e}")

    return {
        "appId": AGORA_APP_ID,
        "token": token,
        "channelName": channel_name,
        "uid": CANDIDATE_UID,
    }


@voice_router.post("/sessions/{session_id}/stop")
async def stop_voice_interview(session_id: str):
    import asyncio
    from main import sessions, generate_feedback_report, _auto_cleanup

    vs = voice_sessions.get(session_id)
    if not vs:
        raise HTTPException(status_code=404, detail="No active voice session found.")

    if vs.agent_id:
        await stop_agent(vs.agent_id)
        vs.agent_id = None

    session = sessions.get(session_id)
    report = None
    if session:
        session.interview_active = False
        try:
            report = await generate_feedback_report(session)
        except Exception as e:
            print(f"[STOP] Report generation failed: {e}")

    voice_sessions.pop(session_id, None)

    asyncio.create_task(_auto_cleanup(session_id))

    return {"message": "Voice interview stopped.", "feedback_report": report}


@voice_router.post("/sessions/{session_id}/ready")
async def candidate_ready(session_id: str):
    import time
    from main import sessions

    vs = voice_sessions.get(session_id)
    session = sessions.get(session_id)

    if not vs or not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    if not vs.think_mode:
        return {"message": "Not in think mode."}

    vs.think_mode = False
    think_duration = int(time.time() - vs.think_start)
    vs.think_requested_count = 0
    session.conversation_history.append(
        {"role": "user", "content": f"[Candidate took {think_duration}s to think]"}
    )
    return {"message": "Ready acknowledged.", "think_duration": think_duration}


@voice_router.get("/sessions/{session_id}/status")
async def voice_session_status(session_id: str):
    from main import sessions

    vs = voice_sessions.get(session_id)
    session = sessions.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    transcript = []
    for msg in session.conversation_history[-20:]:
        if msg["role"] == "system":
            continue
        content = msg.get("content", "").strip()
        if not content:
            continue
        if msg["role"] == "user" and "Start the interview." in content:
            continue
        if msg["role"] == "user" and content.startswith("[Candidate took"):
            transcript.append({"role": "notice", "content": content})
            continue
        if msg["role"] in ("user", "assistant"):
            transcript.append({"role": msg["role"], "content": content})

    import time
    think_remaining = 0
    think_duration = 0
    if vs and vs.think_mode:
        think_remaining = max(0, int(vs.think_deadline - time.time()))
        think_duration = int(vs.think_deadline - vs.think_start)

    return {
        "persona": session.current_persona,
        "phase": session.interview_phase,
        "interview_active": session.interview_active,
        "swap_in_progress": vs.swap_in_progress if vs else False,
        "interview_ended": vs.interview_ended if vs else not session.interview_active,
        "transcript": transcript,
        "evidence_map": session.evidence_map,
        "feedback_report": vs.feedback_report if vs else None,
        "think_mode": vs.think_mode if vs else False,
        "think_remaining": think_remaining,
        "think_duration": think_duration,
    }
