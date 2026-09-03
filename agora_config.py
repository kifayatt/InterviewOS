from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

AGORA_APP_ID = os.getenv("AGORA_APP_ID", "")
AGORA_APP_CERTIFICATE = os.getenv("AGORA_APP_CERTIFICATE", "")
AGORA_CUSTOMER_ID = os.getenv("AGORA_CUSTOMER_ID", "")
AGORA_CUSTOMER_SECRET = os.getenv("AGORA_CUSTOMER_SECRET", "")
AGORA_BASE_URL = os.getenv("AGORA_BASE_URL", "https://api.agora.io")
SERVER_PUBLIC_URL = os.getenv("SERVER_PUBLIC_URL", "")

MAYA_VOICE = "English_captivating_female1"
RAJ_VOICE = "English_confident_male1"

CANDIDATE_UID = 1000
TOKEN_EXPIRE_SECONDS = 3600
SWAP_DELAY_SECONDS = 4


def get_agora_auth_header() -> str:
    credentials = f"{AGORA_CUSTOMER_ID}:{AGORA_CUSTOMER_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


def generate_agora_rtc_token(channel: str, uid: int, expire_seconds: int = TOKEN_EXPIRE_SECONDS) -> str:
    from agora_token_builder import RtcTokenBuilder

    role = 1  # publisher
    privilege_expired_ts = int(time.time()) + expire_seconds
    return RtcTokenBuilder.buildTokenWithUid(
        AGORA_APP_ID, AGORA_APP_CERTIFICATE, channel, uid, role, privilege_expired_ts,
    )


@dataclass
class VoiceSessionState:
    session_id: str
    channel_name: str
    agent_id: Optional[str] = None
    candidate_uid: int = CANDIDATE_UID
    persona: str = "maya"
    swap_in_progress: bool = False
    pre_swap_persona: Optional[str] = None
    interview_ended: bool = False
    feedback_report: Optional[dict] = None
    # Message merging (Fix 2)
    pending_transcript: str = ""
    last_transcript_time: float = 0.0
    # Think time (Fix 4)
    think_mode: bool = False
    think_deadline: float = 0.0
    think_start: float = 0.0
    think_requested_count: int = 0


voice_sessions: dict[str, VoiceSessionState] = {}
