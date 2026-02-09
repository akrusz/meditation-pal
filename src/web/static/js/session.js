/* Session page — WebSocket, voice, and conversation logic */

(function () {
    'use strict';

    const socket = io();

    // DOM refs
    const conversationEl = document.getElementById('conversation');
    const inputEl = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');
    const voiceBtn = document.getElementById('voice-btn');
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
    const synth = window.speechSynthesis || null;
    let preferredVoice = null;

    // Resolve preferred TTS voice once voices are loaded
    function resolveVoice() {
        if (!synth) return;
        var voices = synth.getVoices();
        if (voices.length === 0) return;
        preferredVoice = voices.find(function (v) {
            return v.name.includes('Samantha') || v.name.includes('Karen') ||
                   v.name.includes('Daniel') || v.name.includes('Google UK English Female');
        }) || voices[0];
    }
    if (synth) {
        resolveVoice();
        synth.addEventListener('voiceschanged', resolveVoice);
    }

    // Audio capture state
    let audioContext = null;
    let mediaStream = null;
    let scriptProcessor = null;
    let audioChunks = [];
    let silenceTimer = null;
    let speechDetected = false;
    var SILENCE_THRESHOLD = 0.015; // RMS level below which counts as silence
    var SILENCE_DURATION = 2000;   // ms of silence before auto-submitting

    // ---- Initialize ----

    function init() {
        const params = JSON.parse(sessionStorage.getItem('sessionParams') || '{}');

        // Event listeners
        sendBtn.addEventListener('click', sendMessage);
        inputEl.addEventListener('keydown', handleInputKey);
        voiceBtn.addEventListener('click', toggleVoice);
        endBtn.addEventListener('click', endSession);

        // Auto-resize textarea
        inputEl.addEventListener('input', autoResize);

        // Start session
        socket.emit('start_session', params);
        sessionActive = true;
        sessionStart = Date.now();
        startTimer();
    }

    // ---- Messaging ----

    function sendMessage() {
        const text = inputEl.value.trim();
        if (!text || !sessionActive) return;

        addMessage('user', text);
        socket.emit('user_message', { text: text });
        inputEl.value = '';
        inputEl.style.height = 'auto';
        inputEl.focus();
    }

    function handleInputKey(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
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

    function autoResize() {
        inputEl.style.height = 'auto';
        inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
    }

    // ---- Socket events ----

    socket.on('facilitator_message', function (data) {
        addMessage('facilitator', data.text);
        if (ttsToggle.checked) {
            speak(data.text);
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

    // ---- Voice Input (server-side Whisper via AudioContext) ----
    //
    // Click mic once to enter voice mode. The mic stays open and continuously
    // listens. When you stop speaking (silence for SILENCE_DURATION ms), the
    // captured audio is sent for transcription, then listening resumes
    // automatically. Click mic again to leave voice mode.

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

            voiceActive = true;
            voiceBtn.classList.add('active');

            beginListening();
        }).catch(function (err) {
            console.error('Microphone error:', err);
            if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
                inputEl.placeholder = 'Microphone access denied. Type instead.';
            } else {
                inputEl.placeholder = 'Microphone error. Type instead.';
            }
        });
    }

    function beginListening() {
        if (!voiceActive || !audioContext || !mediaStream) return;

        var source = audioContext.createMediaStreamSource(mediaStream);
        scriptProcessor = audioContext.createScriptProcessor(4096, 1, 1);
        audioChunks = [];
        speechDetected = false;
        if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }

        inputEl.placeholder = 'Listening... speak naturally';

        scriptProcessor.onaudioprocess = function (e) {
            var channelData = e.inputBuffer.getChannelData(0);
            var chunk = new Float32Array(channelData);

            // Compute RMS level
            var sum = 0;
            for (var i = 0; i < chunk.length; i++) {
                sum += chunk[i] * chunk[i];
            }
            var rms = Math.sqrt(sum / chunk.length);

            if (rms > SILENCE_THRESHOLD) {
                // Barge-in: stop TTS if the user starts speaking
                if (!speechDetected && synth && synth.speaking) {
                    synth.cancel();
                }
                // Speech detected — keep capturing
                speechDetected = true;
                audioChunks.push(chunk);
                if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
            } else if (speechDetected) {
                // Below threshold after speech — include this chunk (tail end)
                audioChunks.push(chunk);
                // Start silence countdown if not already running
                if (!silenceTimer) {
                    silenceTimer = setTimeout(function () {
                        submitUtterance();
                    }, SILENCE_DURATION);
                }
            }
            // If no speech detected yet and below threshold, discard (ambient noise)
        };

        source.connect(scriptProcessor);
        scriptProcessor.connect(audioContext.destination);
    }

    function submitUtterance() {
        silenceTimer = null;

        if (scriptProcessor) {
            scriptProcessor.disconnect();
            scriptProcessor = null;
        }

        if (audioChunks.length > 0) {
            // Combine all chunks into one Float32Array
            var totalLength = 0;
            for (var i = 0; i < audioChunks.length; i++) {
                totalLength += audioChunks[i].length;
            }
            var combined = new Float32Array(totalLength);
            var offset = 0;
            for (var i = 0; i < audioChunks.length; i++) {
                combined.set(audioChunks[i], offset);
                offset += audioChunks[i].length;
            }
            audioChunks = [];

            var sampleRate = audioContext ? audioContext.sampleRate : 16000;

            inputEl.placeholder = 'Transcribing...';
            socket.emit('audio_data', {
                audio: combined.buffer,
                sample_rate: sampleRate,
            });
        } else {
            // Nothing captured, just resume listening
            beginListening();
        }
    }

    function deactivateVoice() {
        voiceActive = false;
        voiceBtn.classList.remove('active');
        inputEl.placeholder = 'Describe what you\'re experiencing...';

        if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
        if (scriptProcessor) { scriptProcessor.disconnect(); scriptProcessor = null; }
        if (mediaStream) {
            mediaStream.getTracks().forEach(function (t) { t.stop(); });
            mediaStream = null;
        }
        if (audioContext) {
            audioContext.close();
            audioContext = null;
        }
        audioChunks = [];
        speechDetected = false;
    }

    socket.on('transcription', function (data) {
        var text = (data.text || '').trim();
        if (text) {
            inputEl.value = text;
            autoResize();
            sendMessage();
        } else {
            inputEl.placeholder = 'No speech detected. Try again.';
        }
        // Resume listening if still in voice mode
        if (voiceActive) {
            beginListening();
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
