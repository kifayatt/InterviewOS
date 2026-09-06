from __future__ import annotations

import asyncio
import json
import re
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from agora_config import voice_sessions

llm_callback_router = APIRouter()

MERGE_WINDOW_SECONDS = 2.0

THINK_PATTERN = re.compile(
    r"(?:give me|let me (?:think|have)|need|can i (?:have|get)|i need)\s*"
    r"(?:(?:a |like )?(\d+)\s*(?:min(?:ute)?s?|sec(?:ond)?s?)|a (?:minute|moment|second))",
    re.IGNORECASE,
)


def _sse_response(text: str) -> StreamingResponse:
    chunk_id = str(uuid.uuid4())[:8]

    async def generate():
        content_chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(content_chunk)}\n\n"

        done_chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(done_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _sse_control(control_data: dict) -> StreamingResponse:
    """Send a control message the frontend can parse (e.g. think_time)."""
    text = json.dumps(control_data)
    return _sse_response(text)


def _extract_latest_user_message(messages: list) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                return " ".join(parts).strip()
            return str(content).strip()
    return ""


def _parse_think_duration(transcript: str) -> int | None:
    """Return requested think seconds, or None if not a think request."""
    m = THINK_PATTERN.search(transcript)
    if not m:
        lower = transcript.lower().strip()
        if any(p in lower for p in ("let me think", "give me a moment", "need a moment", "one moment", "hold on")):
            return 60
        return None
    if m.group(1):
        num = int(m.group(1))
        if "sec" in m.group(0).lower():
            return min(num, 180)
        return min(num * 60, 180)
    return 60


@llm_callback_router.post("/agora/llm/{session_id}")
async def agora_llm_callback(session_id: str, request: Request):
    try:
        return await _agora_llm_callback_inner(session_id, request)
    except Exception as e:
        print(f"[LLM CALLBACK UNHANDLED] {e}")
        return _sse_response("Could you tell me more about that?")


async def _agora_llm_callback_inner(session_id: str, request: Request):
    from main import (
        sessions,
        get_session,
        build_maya_prompt,
        build_raj_prompt,
        build_scenario,
        build_evidence_summary,
        extract_evidence_async,
        decide_next_action_async,
        generate_feedback_report,
        clean_interviewer_text,
        is_off_topic_message,
        _llm_call,
        SHARED_RULES,
        MAYA_COMPETENCIES,
        RAJ_COMPETENCIES,
    )
    from agora_agent import swap_agent, swap_back_from_cameo, stop_agent

    vs = voice_sessions.get(session_id)
    session = sessions.get(session_id)

    if not session or not vs:
        return _sse_response("Interview session not found.")

    if vs.swap_in_progress:
        return _sse_response("One moment, switching interviewers.")

    body = await request.json()
    agora_messages = body.get("messages", [])
    transcript = _extract_latest_user_message(agora_messages)

    # Dedup: if Agora retries with the same transcript, return last response
    if transcript:
        last_user_msg = ""
        last_assistant_msg = ""
        for msg in reversed(session.conversation_history):
            if msg["role"] == "user" and not last_user_msg:
                last_user_msg = msg["content"]
            if msg["role"] == "assistant" and not last_assistant_msg:
                last_assistant_msg = msg["content"]
            if last_user_msg and last_assistant_msg:
                break
        if transcript == last_user_msg:
            return _sse_response(last_assistant_msg or "I'm listening, go on.")

    # If no user message, this might be the initial greeting call from Agora.
    if not transcript:
        if not session.conversation_history:
            has_resume = bool(session.resume_text)
            name_part = f"Address the candidate by name — their name is {session.candidate_name}. " if session.candidate_name else ""
            kickoff = (
                f"Start the interview. {name_part}"
                "Greet the candidate, introduce yourself and mention Raj. "
                "You may ask how they're doing, or ask them to introduce themselves and share "
                "a key achievement — pick one or two, not all. Vary your wording. "
                "Stay professional and candidate-focused. No hypothetical or creative questions."
            )
            session.conversation_history = [
                {"role": "system", "content": build_maya_prompt(session.resume_text, has_resume, scenario=build_scenario(session))},
                {"role": "user", "content": kickoff},
            ]
            raw = await _llm_call(session.conversation_history, temperature=0.7)
            greeting = clean_interviewer_text(raw)
            if not greeting:
                greeting = "Hi! I'm Maya. How are you doing today?"
            session.conversation_history.append({"role": "assistant", "content": greeting})
            return _sse_response(greeting)

        # Raj just started after a handoff — generate his opening
        if session.current_persona == "raj" and not any(
            m["role"] == "user" for m in session.conversation_history
            if m.get("content", "").strip() and m != session.conversation_history[1]
        ):
            raw = await _llm_call(session.conversation_history)
            opening = clean_interviewer_text(raw)
            if not opening:
                opening = "Alright, let's continue. Tell me about your approach."
            session.conversation_history.append({"role": "assistant", "content": opening})
            return _sse_response(opening)

        last_assistant = ""
        for m in reversed(session.conversation_history):
            if m["role"] == "assistant":
                last_assistant = m["content"]
                break
        return _sse_response(last_assistant or "Hi there.")

    # --- Think mode: handle timer expiry ---
    if vs.think_mode:
        now = time.time()
        if now > vs.think_deadline:
            vs.think_mode = False
            think_duration = int(now - vs.think_start)
            vs.think_requested_count = 0
            session.conversation_history.append(
                {"role": "user", "content": f"[Candidate took {think_duration}s to think]"}
            )
            nudge = "Alright, are you ready with your answer? Go ahead whenever you are."
            session.conversation_history.append({"role": "assistant", "content": nudge})
            return _sse_response(nudge)

        # Check if asking for more time while already thinking
        think_seconds = _parse_think_duration(transcript)
        if think_seconds is not None:
            vs.think_requested_count += 1
            if vs.think_requested_count >= 2:
                response = (
                    "Instead of waiting, why don't you share your thought process with me? "
                    "Walk me through what you're considering — that's just as valuable as a polished answer."
                )
                vs.think_mode = False
                think_duration = int(time.time() - vs.think_start)
                vs.think_requested_count = 0
                session.conversation_history.append(
                    {"role": "user", "content": f"[Candidate took {think_duration}s to think]"}
                )
                session.conversation_history.append({"role": "assistant", "content": response})
                return _sse_response(response)
            return _sse_response("Mm-hmm.")

        # Candidate answered during think time — process normally
        vs.think_mode = False
        think_duration = int(time.time() - vs.think_start)
        vs.think_requested_count = 0
        session.conversation_history.append(
            {"role": "user", "content": f"[Candidate took {think_duration}s to think]"}
        )
        # Fall through to normal processing with this transcript

    # --- Fix 4: Detect think time requests ---
    think_seconds = _parse_think_duration(transcript)
    if think_seconds is not None and not vs.think_mode:
        vs.think_mode = True
        vs.think_start = time.time()
        vs.think_deadline = vs.think_start + think_seconds
        vs.think_requested_count = 1
        mins = think_seconds // 60
        secs = think_seconds % 60
        if mins > 0 and secs > 0:
            time_str = f"{mins} minute{'s' if mins > 1 else ''} and {secs} seconds"
        elif mins > 0:
            time_str = f"{mins} minute{'s' if mins > 1 else ''}"
        else:
            time_str = f"{secs} seconds"
        message = f"Take your time — you've got {time_str}."
        control = json.dumps({
            "type": "think_time",
            "duration_seconds": think_seconds,
            "message": message,
        })
        return _sse_response(control)

    # --- Fix 2: Merge consecutive user messages (cumulative-aware) ---
    now = time.time()
    time_since_last = now - vs.last_transcript_time if vs.last_transcript_time else float("inf")
    vs.last_transcript_time = now

    if not session.interview_active:
        return _sse_response("This interview session has ended. Thank you for your time.")

    def _merge_user_content(existing: str, new: str) -> str:
        if new.startswith(existing):
            return new
        if existing.startswith(new):
            return existing
        return (existing + " " + new).strip()

    last_msg = session.conversation_history[-1] if session.conversation_history else None

    if last_msg and last_msg.get("role") == "assistant" and time_since_last < MERGE_WINDOW_SECONDS:
        prev_msg = session.conversation_history[-2] if len(session.conversation_history) >= 2 else None
        if prev_msg and prev_msg.get("role") == "user":
            prev_msg["content"] = _merge_user_content(prev_msg["content"], transcript)
            session.conversation_history.pop()
        else:
            session.conversation_history.append({"role": "user", "content": transcript})
    elif last_msg and last_msg.get("role") == "user":
        last_msg["content"] = _merge_user_content(last_msg["content"], transcript)
    else:
        session.conversation_history.append({"role": "user", "content": transcript})

    # Off-topic check
    if is_off_topic_message(session, transcript):
        session.off_topic_attempts += 1
        if session.off_topic_attempts >= 2:
            closing = (
                "This interview session has ended because we need to stay focused on the "
                "interview. Thank you for your time."
            )
            session.conversation_history.append({"role": "assistant", "content": closing})
            session.interview_active = False
            vs.interview_ended = True
            asyncio.create_task(_stop_agent_delayed(vs))
            return _sse_response(closing)
    else:
        session.off_topic_attempts = 0

    session.turn_count += 1

    # --- Cameo return: candidate answered the cameo persona's question ---
    if session.cameo_active:
        session.cameo_active = False
        cameo_name = session.cameo_persona.capitalize()
        take_back = f"Thanks, {cameo_name}. I'm good — carry on."
        session.conversation_history.append({"role": "assistant", "content": take_back})
        session.cameo_persona = ""

        # Swap back to original persona
        original_persona = vs.pre_swap_persona or "maya"
        if original_persona == "maya":
            prompt = build_maya_prompt(session.resume_text, bool(session.resume_text), scenario=build_scenario(session))
        else:
            summary = build_evidence_summary(session.evidence_map)
            prompt = build_raj_prompt(session.resume_text, summary, scenario=build_scenario(session))

        asyncio.create_task(swap_back_from_cameo(session_id, prompt))
        return _sse_response(take_back)

    # --- Evidence extraction + orchestration in parallel ---
    try:
        _, decision = await asyncio.gather(
            extract_evidence_async(session, transcript),
            decide_next_action_async(session),
        )
    except Exception as e:
        print(f"[LLM CALLBACK ERROR] Evidence/orchestration failed: {e}")
        fallback = "Could you tell me more about that?"
        session.conversation_history.append({"role": "assistant", "content": fallback})
        return _sse_response(fallback)

    action = decision.get("action", "follow_up")
    orchestrated_question = decision.get("question", "")

    # --- Handle actions ---

    if action == "wrap_up":
        closing = orchestrated_question or (
            "That wraps up our interview. Thank you for your time today — we really "
            "appreciate you walking us through your experience. We'll be in touch soon "
            "with next steps. Take care!"
        )
        closing = clean_interviewer_text(closing)
        session.conversation_history.append({"role": "assistant", "content": closing})
        session.interview_phase = "closing"
        session.interview_active = False
        vs.interview_ended = True
        asyncio.create_task(_handle_wrap_up(session_id, session, vs))
        return _sse_response(closing)

    if action == "cameo":
        lead_persona = session.current_persona
        cameo_persona = "raj" if lead_persona == "maya" else "maya"

        lead_invite = orchestrated_question or (
            f"{cameo_persona.capitalize()}, anything you want to ask about this?"
        )
        lead_invite = clean_interviewer_text(lead_invite)
        session.conversation_history.append({"role": "assistant", "content": lead_invite})

        # Build cameo agent's system prompt
        if cameo_persona == "raj":
            summary = build_evidence_summary(session.evidence_map)
            cameo_prompt = (
                f"You are Raj doing a quick cameo during Maya's section. "
                f"Ask ONE sharp, blunt question about what the candidate just discussed. "
                f"Keep it to 1 sentence. Stay in Raj's voice — direct and skeptical.\n"
                f"Evidence so far: {summary}\n{SHARED_RULES}"
            )
            session.raj_cameo_used = True
        else:
            cameo_prompt = (
                f"You are Maya doing a quick cameo during Raj's section. "
                f"Ask ONE warm but sharp question about what the candidate just discussed. "
                f"Keep it to 1 sentence. Stay in Maya's voice.\n{SHARED_RULES}"
            )
            session.maya_cameo_used = True

        session.cameo_active = True
        session.cameo_persona = cameo_persona

        # Swap to the cameo persona's agent
        asyncio.create_task(swap_agent(
            session_id, cameo_persona, cameo_prompt, is_cameo=True,
        ))
        return _sse_response(lead_invite)

    if action == "hand_off":
        handoff_line = orchestrated_question or (
            "That's really helpful context. Let me bring Raj in from the hiring side to continue."
        )
        handoff_line = clean_interviewer_text(handoff_line)
        session.conversation_history.append({"role": "assistant", "content": handoff_line})

        # Build Raj's system prompt with evidence summary
        summary = build_evidence_summary(session.evidence_map)
        raj_prompt = build_raj_prompt(session.resume_text, summary, scenario=build_scenario(session))

        # Update conversation history system prompt for Raj
        session.conversation_history[0]["content"] = raj_prompt

        asyncio.create_task(swap_agent(session_id, "raj", raj_prompt))
        return _sse_response(handoff_line)

    # Normal actions: follow_up, probe, challenge, advance, respond, intro, begin_scenario
    if action == "begin_scenario":
        session.interview_phase = "deep_dive"

    if orchestrated_question:
        ai_message = clean_interviewer_text(orchestrated_question)
    else:
        raw = await _llm_call(session.conversation_history)
        ai_message = clean_interviewer_text(raw)

    if not ai_message:
        ai_message = "Could you tell me more about that?"

    session.conversation_history.append({"role": "assistant", "content": ai_message})
    return _sse_response(ai_message)


async def _stop_agent_delayed(vs: "VoiceSessionState") -> None:
    from agora_agent import stop_agent
    await asyncio.sleep(5)
    if vs.agent_id:
        await stop_agent(vs.agent_id)
        vs.agent_id = None


async def _handle_wrap_up(session_id: str, session, vs) -> None:
    from main import generate_feedback_report
    from agora_agent import stop_agent

    await asyncio.sleep(5)
    if vs.agent_id:
        await stop_agent(vs.agent_id)
        vs.agent_id = None

    report = await generate_feedback_report(session)
    vs.feedback_report = report
