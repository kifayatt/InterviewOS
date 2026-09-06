import asyncio
import json

import httpx

from agora_config import (
    AGORA_APP_ID,
    AGORA_BASE_URL,
    MAYA_VOICE,
    RAJ_VOICE,
    SERVER_PUBLIC_URL,
    SWAP_DELAY_SECONDS,
    get_agora_auth_header,
    generate_agora_rtc_token,
    voice_sessions,
)

AGENT_UID = 999

AGORA_JOIN_URL = f"{AGORA_BASE_URL}/api/conversational-ai-agent/v2/projects/{AGORA_APP_ID}/join"


def _agent_leave_url(agent_id: str) -> str:
    return f"{AGORA_BASE_URL}/api/conversational-ai-agent/v2/projects/{AGORA_APP_ID}/agents/{agent_id}/leave"


def _voice_for_persona(persona: str) -> str:
    return MAYA_VOICE if persona == "maya" else RAJ_VOICE


def _build_agent_payload(
    session_id: str,
    persona: str,
    channel_name: str,
    system_prompt: str,
    candidate_uid: int,
    greeting_message: str = "",
) -> dict:
    agent_token = generate_agora_rtc_token(channel_name, AGENT_UID)
    return {
        "name": f"interviewos-{persona}-{session_id[:16]}",
        "preset": "deepgram_nova_3,minimax_speech_2_8_turbo",
        "properties": {
            "channel": channel_name,
            "token": agent_token,
            "agent_rtc_uid": str(AGENT_UID),
            "remote_rtc_uids": [str(candidate_uid)],
            "idle_timeout": 300,
            "asr": {
                "vendor": "deepgram",
                "params": {
                    "language": "en-US",
                    "silence_duration_ms": 4500,
                },
            },
            "llm": {
                "vendor": "custom",
                "url": f"{SERVER_PUBLIC_URL}/agora/llm/{session_id}",
                "system_messages": [
                    {"role": "system", "content": system_prompt},
                ],
                "failure_message": "Sorry, I missed that. Could you say that again?",
                "max_history": 32,
                **({"greeting_message": greeting_message} if greeting_message else {}),
            },
            "tts": {
                "vendor": "minimax",
                "params": {
                    "voice_setting": {
                        "voice_id": _voice_for_persona(persona),
                        "speed": 1.0,
                    },
                },
            },
        },
    }


async def start_agent(
    session_id: str,
    persona: str,
    channel_name: str,
    system_prompt: str,
    candidate_uid: int,
    greeting_message: str = "",
) -> str:
    payload = _build_agent_payload(
        session_id, persona, channel_name, system_prompt, candidate_uid, greeting_message,
    )
    headers = {
        "Authorization": get_agora_auth_header(),
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(AGORA_JOIN_URL, headers=headers, json=payload)
        if resp.status_code != 200:
            print(f"[AGORA ERROR] Status={resp.status_code} Body={resp.text}")
            print(f"[AGORA REQUEST] URL={AGORA_JOIN_URL}")
            print(f"[AGORA REQUEST] Payload={json.dumps(payload, indent=2)[:2000]}")
            resp.raise_for_status()
        data = resp.json()

    agent_id = data.get("agent_id", "")
    vs = voice_sessions.get(session_id)
    if vs:
        vs.agent_id = agent_id
        vs.persona = persona
    return agent_id


async def stop_agent(agent_id: str) -> None:
    if not agent_id:
        return
    headers = {
        "Authorization": get_agora_auth_header(),
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(_agent_leave_url(agent_id), headers=headers, json={})
        if resp.status_code not in (200, 404):
            resp.raise_for_status()


async def swap_agent(
    session_id: str,
    new_persona: str,
    new_system_prompt: str,
    is_cameo: bool = False,
    greeting_message: str = "",
) -> None:
    vs = voice_sessions.get(session_id)
    if not vs:
        return

    vs.swap_in_progress = True
    if is_cameo:
        vs.pre_swap_persona = vs.persona

    await asyncio.sleep(SWAP_DELAY_SECONDS)

    if vs.agent_id:
        await stop_agent(vs.agent_id)
        vs.agent_id = None

    from main import sessions, get_session
    session = sessions.get(session_id)
    if session:
        session.current_persona = new_persona

    await start_agent(
        session_id, new_persona, vs.channel_name, new_system_prompt,
        vs.candidate_uid, greeting_message=greeting_message,
    )
    vs.swap_in_progress = False


async def swap_back_from_cameo(session_id: str, original_system_prompt: str) -> None:
    vs = voice_sessions.get(session_id)
    if not vs or not vs.pre_swap_persona:
        return

    original_persona = vs.pre_swap_persona
    vs.pre_swap_persona = None

    await swap_agent(
        session_id, original_persona, original_system_prompt, is_cameo=False,
    )

    from main import sessions
    session = sessions.get(session_id)
    if session:
        session.cameo_active = False
        session.cameo_persona = ""
