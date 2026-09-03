/* InterviewOS — Agora Voice Mode (split-screen UI) */

let voiceClient = null;
let localAudioTrack = null;
let voicePollingInterval = null;
let lastTranscriptLength = 0;
let displayedMessages = new Set();
let voiceMode = false;
let isMuted = false;

const AGENT_UID = 999;
const VOLUME_THRESHOLD = 15;
const INTERRUPT_THRESHOLD = 30;
const DEBOUNCE_TICKS = 2;

let lastCandidateSpeakTime = 0;
let candidateWasSpeaking = false;
let candidateDetectTicks = 0;
let agentDetectTicks = 0;
let thinkTimerInterval = null;
let thinkStartTime = 0;
let thinkDurationSeconds = 0;

const startVoiceButton = document.getElementById('start-voice-button');
const muteButton = document.getElementById('mute-button');

function setThinking(persona) {
  const waves = document.getElementById(persona + '-waves');
  const dots = document.getElementById(persona + '-thinking');
  if (waves) waves.classList.add('hidden');
  if (dots) dots.classList.add('active');
  updateParticipantStatus(persona, 'Thinking...');
}

function clearThinking(persona) {
  const dots = document.getElementById(persona + '-thinking');
  if (dots) dots.classList.remove('active');
}

function clearAllThinking() {
  clearThinking('maya');
  clearThinking('raj');
  clearThinking('candidate');
}

function showThinkTimer(durationSeconds) {
  thinkDurationSeconds = durationSeconds;
  thinkStartTime = Date.now();
  const timer = document.getElementById('think-timer');
  const countdown = document.getElementById('think-countdown');
  if (!timer || !countdown) return;

  timer.classList.add('active');
  updateCountdown();
  thinkTimerInterval = setInterval(updateCountdown, 1000);
}

function updateCountdown() {
  const countdown = document.getElementById('think-countdown');
  if (!countdown) return;
  const elapsed = Math.floor((Date.now() - thinkStartTime) / 1000);
  const remaining = Math.max(0, thinkDurationSeconds - elapsed);
  const mins = Math.floor(remaining / 60);
  const secs = remaining % 60;
  countdown.textContent = mins + ':' + String(secs).padStart(2, '0');
  if (remaining <= 0) {
    hideThinkTimer();
    const actualTime = Math.floor((Date.now() - thinkStartTime) / 1000);
    addMessage('Think time ended (' + formatThinkTime(actualTime) + ')', 'notice');
  }
}

function hideThinkTimer() {
  const timer = document.getElementById('think-timer');
  if (timer) timer.classList.remove('active');
  if (thinkTimerInterval) {
    clearInterval(thinkTimerInterval);
    thinkTimerInterval = null;
  }
}

function formatThinkTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m > 0 && s > 0) return m + 'm ' + s + 's';
  if (m > 0) return m + 'm';
  return s + 's';
}

async function handleReadyButton() {
  hideThinkTimer();
  const actualTime = Math.floor((Date.now() - thinkStartTime) / 1000);
  addMessage('Ready after ' + formatThinkTime(actualTime) + ' of thinking', 'notice');
  if (sessionId) {
    try {
      await api(`/voice/sessions/${sessionId}/ready`, { method: 'POST' });
    } catch (_) {}
  }
}

async function startVoiceInterview() {
  if (!sessionId || !sessionOpen) return;
  startVoiceButton.disabled = true;
  startButton.disabled = true;
  sessionInfo.textContent = 'Connecting to voice interview...';

  try {
    const data = await api(`/voice/sessions/${sessionId}/start`, { method: 'POST' });

    voiceClient = AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' });

    voiceClient.on('user-published', async (user, mediaType) => {
      await voiceClient.subscribe(user, mediaType);
      if (mediaType === 'audio') {
        user.audioTrack.play();
      }
    });

    voiceClient.on('user-unpublished', () => {
      setAllIdle();
    });

    voiceClient.on('user-left', () => {
      setAllIdle();
      addMessage('Interviewer reconnecting...', 'notice');
    });

    voiceClient.on('volume-indicator', (volumes) => {
      if (!voiceMode) return;

      let agentDetected = false;
      let candidateDetected = false;

      for (const v of volumes) {
        if (v.uid === AGENT_UID) {
          if (v.level > VOLUME_THRESHOLD) agentDetected = true;
        } else {
          if (v.level > VOLUME_THRESHOLD) candidateDetected = true;
        }
      }

      if (agentDetected) agentDetectTicks++;
      else agentDetectTicks = 0;

      if (candidateDetected) candidateDetectTicks++;
      else candidateDetectTicks = 0;

      const agentSpeaking = agentDetectTicks >= DEBOUNCE_TICKS;
      const candidateSpeaking = candidateDetectTicks >= DEBOUNCE_TICKS;

      const now = Date.now();

      if (agentSpeaking) {
        candidateWasSpeaking = false;
        clearAllThinking();
        setPersonaSpeaking(currentPersona);
        updateParticipantStatus(currentPersona, 'Speaking');
        updateParticipantStatus('candidate', 'Listening');
      } else if (candidateSpeaking) {
        lastCandidateSpeakTime = now;
        candidateWasSpeaking = true;
        clearThinking(currentPersona);
        setPersonaSpeaking('candidate');
        updateParticipantStatus('candidate', 'Speaking');
        updateParticipantStatus(currentPersona, 'Listening');
      } else {
        if (candidateWasSpeaking && (now - lastCandidateSpeakTime > 500)) {
          candidateWasSpeaking = false;
          setThinking(currentPersona);
          updateParticipantStatus('candidate', 'Listening');
        }
      }
    });

    await voiceClient.join(data.appId, data.channelName, data.token, data.uid);
    voiceClient.enableAudioVolumeIndicator();

    localAudioTrack = await AgoraRTC.createMicrophoneAudioTrack();
    await voiceClient.publish([localAudioTrack]);

    voiceMode = true;
    showInterviewScreen();
    liveTranscription.classList.remove('hidden');
    controls.classList.remove('hidden');
    endButton.disabled = false;
    sessionInfo.textContent = 'Voice interview in progress';

    setPersonaSpeaking('maya');
    updateParticipantStatus('maya', 'Speaking');
    updateParticipantStatus('candidate', 'Listening');

    addMessage('Voice interview started. Speak into your microphone.', 'notice');
    startTimer();

    lastTranscriptLength = 0;
    voicePollingInterval = setInterval(pollVoiceStatus, 2000);
  } catch (error) {
    addMessage('Failed to start voice interview: ' + error.message, 'notice');
    startVoiceButton.disabled = false;
    startButton.disabled = false;
    sessionInfo.textContent = 'Session ready';
  }
}

async function pollVoiceStatus() {
  if (!sessionId || !voiceMode) return;
  try {
    const data = await api(`/voice/sessions/${sessionId}/status`);

    const persona = data.persona || 'maya';
    if (persona !== currentPersona) {
      currentPersona = persona;
      if (persona === 'raj') {
        document.getElementById('p-raj').classList.remove('idle');
      }
    }

    if (data.swap_in_progress) {
      addMessage('Switching interviewers...', 'notice');
      liveText.textContent = 'Switching interviewers...';
    }

    // Handle think_time status
    if (data.think_mode && !thinkTimerInterval) {
      const remaining = data.think_remaining || 0;
      if (remaining > 0) {
        showThinkTimer(remaining);
        addMessage('Taking ' + formatThinkTime(data.think_duration || remaining) + ' to think...', 'notice');
      }
    }

    const transcript = data.transcript || [];
    for (const msg of transcript) {
      const content = msg.content;
      const key = msg.role + '|' + content;
      if (displayedMessages.has(key)) continue;
      displayedMessages.add(key);

      // Check for think_time control messages from callback
      if (msg.role === 'assistant' && content.startsWith('{')) {
        try {
          const ctrl = JSON.parse(content);
          if (ctrl.type === 'think_time') {
            showThinkTimer(ctrl.duration_seconds);
            addMessage('Taking ' + formatThinkTime(ctrl.duration_seconds) + ' to think...', 'notice');
            continue;
          }
        } catch (_) {}
      }

      const role = msg.role === 'user' ? 'user' : 'assistant';
      addMessage(content, role, role === 'assistant' ? persona : undefined);
      liveText.textContent = content.slice(0, 120) + (content.length > 120 ? '...' : '');
    }

    if (data.interview_ended) {
      sessionInfo.textContent = 'Interview completed';
      if (data.feedback_report) {
        renderReport(data.feedback_report);
        await stopVoiceInterview(false);
      }
      // Keep polling until report arrives — don't stop yet
    }
  } catch (_) {}
}

async function stopVoiceInterview(callApi = true) {
  voiceMode = false;
  hideThinkTimer();
  if (voicePollingInterval) {
    clearInterval(voicePollingInterval);
    voicePollingInterval = null;
  }
  if (localAudioTrack) {
    localAudioTrack.close();
    localAudioTrack = null;
  }
  if (voiceClient) {
    await voiceClient.leave().catch(() => {});
    voiceClient = null;
  }
  if (callApi && sessionId) {
    try {
      await api(`/voice/sessions/${sessionId}/stop`, { method: 'POST' });
    } catch (_) {}
  }
  controls.classList.add('hidden');
  liveTranscription.classList.add('hidden');
  sessionOpen = false;
  stopTimer();
  setInterviewActive(false);
  setAllIdle();
  clearAllThinking();
}

function toggleMute() {
  if (!localAudioTrack) return;
  isMuted = !isMuted;
  localAudioTrack.setEnabled(!isMuted);
  muteButton.innerHTML = isMuted
    ? '<span>&#x1f507;</span><span class="tooltip">Unmute ^M</span>'
    : '<span>&#x1f3a4;</span><span class="tooltip">Mute ^M</span>';
  updateParticipantStatus('candidate', isMuted ? 'Muted' : 'Listening');
}

// Wire up buttons
if (startVoiceButton) {
  startVoiceButton.addEventListener('click', startVoiceInterview);
}
if (muteButton) {
  muteButton.addEventListener('click', toggleMute);
}

const readyButton = document.getElementById('ready-button');
if (readyButton) {
  readyButton.addEventListener('click', handleReadyButton);
}

// End call button in controls bar
const endCallButton = document.getElementById('end-call-button');
if (endCallButton) {
  endCallButton.addEventListener('click', async () => {
    if (voiceMode) {
      addMessage('Session ended. Your temporary data was cleared.', 'notice');
      await stopVoiceInterview(true);
    }
  });
}
