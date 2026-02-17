/* Session page — WebSocket, voice, and conversation logic */

(function () {
    'use strict';

    const socket = io();

    // DOM refs
    const conversationEl = document.getElementById('conversation');
    const voiceBtn = document.getElementById('voice-btn');
    const voiceStatus = document.getElementById('voice-status');
    const ttsToggle = document.getElementById('tts-toggle');
    const endBtn = document.getElementById('end-btn');
    const typingEl = document.getElementById('typing-indicator');
    const timerEl = document.getElementById('timer');
    const orbEl = document.getElementById('orb');
    const endedOverlay = document.getElementById('session-ended');
    const closerText = document.getElementById('closer-text');

    // State
    let sessionActive = false;
    let voiceActive = false;
    let timerInterval = null;
    let sessionStart = null;
    let sessionId = null;          // stable ID that survives socket reconnections
    let initialConnectDone = false; // distinguishes first connect from reconnects
    let queuedSpeech = null;       // opener TTS queued until user gesture (mic permission)
    const synth = window.speechSynthesis || null;
    let preferredVoice = null;

    // Resolve preferred TTS voice once voices are loaded
    // Preferred voices in order — premium macOS voices first, then standard fallbacks
    var VOICE_PREFERENCES = ['Zoe', 'Ava', 'Samantha', 'Karen'];

    function resolveVoice() {
        if (!synth) return;
        var voices = synth.getVoices();
        if (voices.length === 0) return;
        console.log('Available TTS voices:', voices.map(function (v) { return v.name; }));
        for (var i = 0; i < VOICE_PREFERENCES.length; i++) {
            var name = VOICE_PREFERENCES[i];
            var match = voices.find(function (v) {
                return v.name.includes(name) && v.lang.startsWith('en');
            });
            if (match) {
                preferredVoice = match;
                console.log('Selected TTS voice:', match.name);
                return;
            }
        }
        preferredVoice = voices.find(function (v) { return v.lang.startsWith('en'); }) || voices[0];
        console.log('Fallback TTS voice:', preferredVoice.name);
    }
    if (synth) {
        resolveVoice();
        synth.addEventListener('voiceschanged', resolveVoice);
    }

    // Audio capture state
    let audioContext = null;
    let mediaStream = null;
    let sourceNode = null;         // MediaStreamSource — created once, reused
    let scriptProcessor = null;
    let audioChunks = [];
    let listening = false;         // true when actively detecting speech
    let ttsSpeaking = false;       // true while TTS is playing — ignore mic input
    let ttsMismatchStart = 0;      // timestamp when ttsSpeaking/synth.speaking diverged
    let preBuffer = [];            // rolling buffer of recent chunks before speech detected
    let pendingTranscriptions = 0;  // count of in-flight transcription requests

    // VAD state machine (mirrors src/audio/vad.py)
    let vadState = 'silence';      // 'silence' | 'speech_started' | 'speaking'
    let speechStartTime = 0;       // Date.now() when speech onset detected
    let lastSpeechTime = 0;        // Date.now() of last above-threshold chunk
    let noiseFloor = 0.005;        // adaptive noise floor (EMA)
    let noiseSamples = 0;          // count for EMA alpha selection
    let bargeInCount = 0;          // consecutive high-energy chunks during TTS

    var SILENCE_THRESHOLD = 0.015; // RMS level below which counts as silence
    var SILENCE_DURATION = 2000;   // ms of silence before auto-submitting
    var PRE_BUFFER_CHUNKS = 20;    // ~2s of audio to keep before speech onset
    var MIN_SPEECH_DURATION = 500; // ms — reject sounds shorter than this
    var NOISE_REJECT_MS = 200;     // ms — abort speech_started if silence exceeds this
    var TTS_COOLDOWN_MS = 800;     // ignore mic for this long after TTS ends
    var TTS_WATCHDOG_MS = 1500;    // force-reset ttsSpeaking if synth stopped this long ago
    var BARGE_IN_THRESHOLD = 0.04; // RMS energy to detect user speaking over TTS
    var BARGE_IN_CHUNKS = 3;       // consecutive chunks required (~280ms at 44.1kHz)
    var TRANSCRIPTION_TIMEOUT_MS = 15000; // warn if transcription takes too long

    // ---- Initialize ----

    function init() {
        const params = JSON.parse(sessionStorage.getItem('sessionParams') || '{}');

        // Generate a stable session ID that survives socket reconnections
        sessionId = 'ses-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
        params.session_id = sessionId;

        // Event listeners
        voiceBtn.addEventListener('click', toggleVoice);
        endBtn.addEventListener('click', endSession);

        // Start session
        socket.emit('start_session', params);
        sessionActive = true;
        sessionStart = Date.now();
        startTimer();

        // Auto-activate voice — the mic permission prompt acts as a user
        // gesture, which unlocks speechSynthesis for TTS.
        activateVoice();
    }

    // Handle reconnection — only fires on reconnects, not the initial connect.
    // Re-registers the session so the server re-maps the new socket sid to
    // our existing session (preserving conversation history).
    socket.on('connect', function () {
        if (!initialConnectDone) {
            initialConnectDone = true;
            return;
        }
        if (sessionActive && sessionId) {
            console.log('Socket reconnected — re-registering session', sessionId);
            socket.emit('start_session', { session_id: sessionId });
        }
    });

    // ---- Messaging ----

    function sendText(text) {
        if (!text || !sessionActive) return;
        addMessage('user', text);
        socket.emit('user_message', { text: text });
    }

    function addMessage(role, text) {
        const msg = document.createElement('div');
        msg.className = 'message ' + role;

        const content = document.createElement('div');
        content.className = 'message-content';
        content.textContent = text;
        msg.appendChild(content);

        conversationEl.appendChild(msg);
        scrollToBottom();
    }

    function scrollToBottom() {
        requestAnimationFrame(function () {
            conversationEl.scrollTop = conversationEl.scrollHeight;
        });
    }

    // ---- Socket events ----

    socket.on('facilitator_message', function (data) {
        addMessage('facilitator', data.text);
        if (ttsToggle.checked) {
            // If voice isn't active yet (e.g. opener arrives before mic
            // permission is granted), queue the speech for later.
            if (voiceActive) {
                speak(data.text);
            } else {
                queuedSpeech = data.text;
            }
        }
    });

    socket.on('facilitator_typing', function (data) {
        if (data.typing) {
            typingEl.classList.add('visible');
        } else {
            typingEl.classList.remove('visible');
        }
    });

    socket.on('session_ended', function (data) {
        sessionActive = false;
        stopTimer();

        if (data.closer) {
            closerText.textContent = data.closer;
        }

        if (ttsToggle.checked && data.closer) {
            speak(data.closer);
        }

        endedOverlay.style.display = 'flex';
    });

    socket.on('error', function (data) {
        console.error('Server error:', data.message);
    });

    // ---- Audio helpers ----

    function downsampleTo16k(buffer, fromRate) {
        if (fromRate === 16000) return buffer;
        var ratio = fromRate / 16000;
        var newLength = Math.round(buffer.length / ratio);
        var result = new Float32Array(newLength);
        for (var i = 0; i < newLength; i++) {
            // Linear interpolation for decent quality
            var srcIndex = i * ratio;
            var low = Math.floor(srcIndex);
            var high = Math.min(low + 1, buffer.length - 1);
            var frac = srcIndex - low;
            result[i] = buffer[low] * (1 - frac) + buffer[high] * frac;
        }
        return result;
    }

    // ---- VAD helpers ----

    function updateNoiseFloor(energy) {
        var alpha = noiseSamples < 100 ? 0.1 : 0.01;
        noiseFloor = (1 - alpha) * noiseFloor + alpha * energy;
        noiseSamples++;
    }

    // ---- Voice Input (server-side Whisper via AudioContext) ----
    //
    // Voice mode activates automatically when the session starts.
    // The mic stays open and continuously listens. When you stop speaking
    // (silence for SILENCE_DURATION ms), the captured audio is sent for
    // transcription, then listening resumes automatically. Click mic to
    // mute/unmute.

    function setStatus(text) {
        voiceStatus.textContent = text;
    }

    function toggleVoice() {
        if (voiceActive) {
            deactivateVoice();
        } else {
            activateVoice();
        }
    }

    function activateVoice() {
        navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
            mediaStream = stream;
            audioContext = new (window.AudioContext || window.webkitAudioContext)();

            // Build the audio pipeline once — it stays connected for the
            // entire voice-active session.  This avoids the startup gap that
            // was eating the first fraction of each utterance.
            sourceNode = audioContext.createMediaStreamSource(mediaStream);
            scriptProcessor = audioContext.createScriptProcessor(4096, 1, 1);

            scriptProcessor.onaudioprocess = function (e) {
                // ---- TTS watchdog ----
                // Chrome sometimes fails to fire onend, leaving ttsSpeaking
                // stuck at true.  If synth has actually stopped for longer
                // than TTS_WATCHDOG_MS, force-reset the flag.
                if (ttsSpeaking) {
                    if (synth && !synth.speaking) {
                        if (ttsMismatchStart === 0) {
                            ttsMismatchStart = Date.now();
                        } else if (Date.now() - ttsMismatchStart > TTS_WATCHDOG_MS) {
                            console.warn('TTS watchdog: resetting stuck ttsSpeaking flag');
                            ttsSpeaking = false;
                            ttsMismatchStart = 0;
                        }
                    } else {
                        ttsMismatchStart = 0;
                    }
                }

                var channelData = e.inputBuffer.getChannelData(0);
                var chunk = new Float32Array(channelData);

                // Compute RMS energy (used for both barge-in and VAD)
                var sum = 0;
                for (var i = 0; i < chunk.length; i++) {
                    sum += chunk[i] * chunk[i];
                }
                var energy = Math.sqrt(sum / chunk.length);

                // ---- Barge-in detection during TTS ----
                // Instead of ignoring all audio while TTS plays, monitor for
                // the user speaking over it.  If energy stays above a higher
                // threshold for a few consecutive chunks, cancel TTS and
                // start capturing immediately.
                var synthActive = ttsSpeaking || (synth && synth.speaking);
                if (synthActive) {
                    if (energy > BARGE_IN_THRESHOLD) {
                        bargeInCount++;
                        if (bargeInCount >= BARGE_IN_CHUNKS) {
                            // User is speaking — cancel TTS and resume capture
                            console.log('Barge-in detected, cancelling TTS');
                            synth.cancel();
                            ttsSpeaking = false;
                            ttsMismatchStart = 0;
                            bargeInCount = 0;
                            // Start fresh — don't seed from pre-buffer since
                            // it's contaminated with TTS speaker audio.
                            preBuffer = [chunk];
                            // Fall through to normal VAD below
                        } else {
                            return;
                        }
                    } else {
                        bargeInCount = 0;
                        preBuffer = [];
                        return;
                    }
                }

                // If not actively listening, just maintain the rolling
                // pre-buffer so the onset of the next utterance is captured.
                if (!listening) {
                    preBuffer.push(chunk);
                    if (preBuffer.length > PRE_BUFFER_CHUNKS) {
                        preBuffer.shift();
                    }
                    return;
                }

                var now = Date.now();

                // Adaptive threshold: at least SILENCE_THRESHOLD, or 3x noise floor
                var threshold = Math.max(SILENCE_THRESHOLD, noiseFloor * 3);
                var isSpeech = energy > threshold;

                if (vadState === 'silence') {
                    if (isSpeech) {
                        vadState = 'speech_started';
                        speechStartTime = now;
                        lastSpeechTime = now;
                        // Seed audio buffer from pre-buffer so onset isn't lost
                        for (var i = 0; i < preBuffer.length; i++) {
                            audioChunks.push(preBuffer[i]);
                        }
                        preBuffer = [];
                        audioChunks.push(chunk);
                    } else {
                        updateNoiseFloor(energy);
                        preBuffer.push(chunk);
                        if (preBuffer.length > PRE_BUFFER_CHUNKS) {
                            preBuffer.shift();
                        }
                    }
                } else if (vadState === 'speech_started') {
                    // Always capture audio during onset (including brief pauses)
                    audioChunks.push(chunk);
                    if (isSpeech) {
                        lastSpeechTime = now;
                        if (now - speechStartTime >= MIN_SPEECH_DURATION) {
                            vadState = 'speaking';
                            setStatus('Listening...');
                        }
                    } else {
                        // Short silence — noise reject if too long
                        if (now - lastSpeechTime > NOISE_REJECT_MS) {
                            // Was just a noise spike, discard
                            vadState = 'silence';
                            audioChunks = [];
                            speechStartTime = 0;
                            lastSpeechTime = 0;
                        }
                    }
                } else if (vadState === 'speaking') {
                    audioChunks.push(chunk);
                    if (isSpeech) {
                        lastSpeechTime = now;
                    } else {
                        if (now - lastSpeechTime >= SILENCE_DURATION) {
                            submitUtterance();
                        }
                    }
                }
            };

            sourceNode.connect(scriptProcessor);
            scriptProcessor.connect(audioContext.destination);

            voiceActive = true;
            voiceBtn.classList.add('active');

            // Speak any opener that was queued before mic permission was granted
            if (queuedSpeech && ttsToggle.checked) {
                speak(queuedSpeech);
                queuedSpeech = null;
            }

            beginListening();
        }).catch(function (err) {
            console.error('Microphone error:', err);
            if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
                setStatus('Microphone access denied. Click mic to retry.');
            } else {
                setStatus('Microphone error. Click mic to retry.');
            }
        });
    }

    function beginListening() {
        if (!voiceActive || !audioContext || !mediaStream) return;

        // Reset VAD state fully (including noise floor) so each exchange
        // starts clean — prevents TTS residue from inflating the threshold.
        audioChunks = [];
        listening = true;
        vadState = 'silence';
        speechStartTime = 0;
        lastSpeechTime = 0;
        noiseFloor = 0.005;
        noiseSamples = 0;
        pendingTranscriptions = 0;
        bargeInCount = 0;

        setStatus('Listening... speak naturally');
    }

    function submitUtterance() {
        // Grab the current audio and reset VAD, but keep listening — the
        // transcription happens asynchronously in the background so the
        // user is never blocked from speaking.
        var chunks = audioChunks;
        audioChunks = [];
        vadState = 'silence';
        speechStartTime = 0;
        lastSpeechTime = 0;

        if (chunks.length === 0) return;

        // Combine all chunks into one Float32Array
        var totalLength = 0;
        for (var i = 0; i < chunks.length; i++) {
            totalLength += chunks[i].length;
        }
        var combined = new Float32Array(totalLength);
        var offset = 0;
        for (var i = 0; i < chunks.length; i++) {
            combined.set(chunks[i], offset);
            offset += chunks[i].length;
        }

        var nativeSampleRate = audioContext ? audioContext.sampleRate : 16000;

        // Downsample to 16kHz on the client — sends 2-3x less data over the
        // socket and eliminates server-side resampling entirely.
        if (nativeSampleRate !== 16000) {
            combined = downsampleTo16k(combined, nativeSampleRate);
        }

        var durationSec = (combined.length / 16000).toFixed(1);

        pendingTranscriptions++;
        console.log('Submitting audio: ' + combined.length + ' samples @ 16kHz, ~' + durationSec + 's (' + pendingTranscriptions + ' pending)');

        // Safety timeout — log a warning if transcription is very slow
        setTimeout(function () {
            if (pendingTranscriptions > 0) {
                console.warn('Transcription still pending after ' + TRANSCRIPTION_TIMEOUT_MS + 'ms');
            }
        }, TRANSCRIPTION_TIMEOUT_MS);

        socket.emit('audio_data', {
            audio: combined.buffer,
            sample_rate: 16000,
        });

        // Listening continues uninterrupted — VAD was reset above,
        // noise floor will re-calibrate from the next silent chunks.
    }

    function deactivateVoice() {
        voiceActive = false;
        listening = false;
        voiceBtn.classList.remove('active');
        setStatus('Microphone off. Click mic to resume.');

        pendingTranscriptions = 0;
        if (scriptProcessor) { scriptProcessor.disconnect(); scriptProcessor = null; }
        if (sourceNode) { sourceNode.disconnect(); sourceNode = null; }
        if (mediaStream) {
            mediaStream.getTracks().forEach(function (t) { t.stop(); });
            mediaStream = null;
        }
        if (audioContext) {
            audioContext.close();
            audioContext = null;
        }
        audioChunks = [];
        preBuffer = [];
        vadState = 'silence';
        speechStartTime = 0;
        lastSpeechTime = 0;
        noiseFloor = 0.005;
        noiseSamples = 0;
        ttsSpeaking = false;
        ttsMismatchStart = 0;
        bargeInCount = 0;
    }

    socket.on('transcription', function (data) {
        pendingTranscriptions = Math.max(0, pendingTranscriptions - 1);

        var text = (data.text || '').trim();
        console.log('Transcription received:', text || '(empty)',
            data.error ? 'error: ' + data.error : '',
            '(' + pendingTranscriptions + ' still pending)');

        if (text) {
            sendText(text);
        }
    });

    // ---- Voice Output (Speech Synthesis) ----

    function speak(text) {
        if (!synth) return;
        synth.cancel(); // cancel any current speech

        var utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 0.9;
        utterance.pitch = 0.95;

        if (preferredVoice) {
            utterance.voice = preferredVoice;
        }

        ttsSpeaking = true;
        utterance.onend = function () {
            // Cooldown: mic stays muted briefly so it doesn't pick up speaker echo
            setTimeout(function () { ttsSpeaking = false; }, TTS_COOLDOWN_MS);
        };
        utterance.onerror = function () {
            setTimeout(function () { ttsSpeaking = false; }, TTS_COOLDOWN_MS);
        };

        synth.speak(utterance);
    }

    // ---- Timer ----

    function startTimer() {
        timerInterval = setInterval(updateTimer, 1000);
    }

    function stopTimer() {
        if (timerInterval) {
            clearInterval(timerInterval);
            timerInterval = null;
        }
    }

    function updateTimer() {
        if (!sessionStart) return;
        var elapsed = Math.floor((Date.now() - sessionStart) / 1000);
        var minutes = Math.floor(elapsed / 60);
        var seconds = elapsed % 60;
        timerEl.textContent = minutes + ':' + (seconds < 10 ? '0' : '') + seconds;
    }

    // ---- End Session ----

    function endSession() {
        if (!sessionActive) return;

        if (voiceActive) {
            deactivateVoice();
        }

        socket.emit('end_session');
    }

    // ---- Start ----

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
