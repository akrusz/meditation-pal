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
    let recognition = null;
    let voiceActive = false;
    let timerInterval = null;
    let sessionStart = null;
    const synth = window.speechSynthesis || null;

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

    // ---- Voice Input (Web Speech API) ----

    function toggleVoice() {
        if (voiceActive) {
            stopVoice();
        } else {
            startVoice();
        }
    }

    function startVoice() {
        var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            inputEl.placeholder = 'Voice not supported in this browser. Type instead.';
            return;
        }

        recognition = new SpeechRecognition();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = 'en-US';

        var finalTranscript = '';

        recognition.onresult = function (event) {
            var interim = '';
            for (var i = event.resultIndex; i < event.results.length; i++) {
                if (event.results[i].isFinal) {
                    finalTranscript += event.results[i][0].transcript;
                } else {
                    interim += event.results[i][0].transcript;
                }
            }
            inputEl.value = finalTranscript + interim;
            autoResize();
        };

        recognition.onend = function () {
            // If voice is still active, send what we have and restart
            if (voiceActive) {
                if (finalTranscript.trim()) {
                    inputEl.value = finalTranscript.trim();
                    sendMessage();
                    finalTranscript = '';
                }
                // Restart if still in voice mode
                try {
                    recognition.start();
                } catch (e) {
                    // Already started
                }
            }
        };

        recognition.onerror = function (event) {
            if (event.error === 'not-allowed') {
                inputEl.placeholder = 'Microphone access denied. Type instead.';
                stopVoice();
            }
        };

        try {
            recognition.start();
            voiceActive = true;
            voiceBtn.classList.add('active');
            inputEl.placeholder = 'Listening... speak naturally';
        } catch (e) {
            console.error('Speech recognition error:', e);
        }
    }

    function stopVoice() {
        voiceActive = false;
        voiceBtn.classList.remove('active');
        inputEl.placeholder = 'Describe what you\'re experiencing...';

        if (recognition) {
            recognition.onend = null; // prevent restart
            try {
                recognition.stop();
            } catch (e) {
                // ignore
            }
            recognition = null;
        }

        // Send any remaining text
        if (inputEl.value.trim()) {
            sendMessage();
        }
    }

    // ---- Voice Output (Speech Synthesis) ----

    function speak(text) {
        if (!synth) return;
        synth.cancel(); // cancel any current speech

        var utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 0.9;
        utterance.pitch = 0.95;

        // Try to find a calm-sounding voice
        var voices = synth.getVoices();
        var preferred = voices.find(function (v) {
            return v.name.includes('Samantha') || v.name.includes('Karen') ||
                   v.name.includes('Daniel') || v.name.includes('Google UK English Female');
        });
        if (preferred) {
            utterance.voice = preferred;
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
            stopVoice();
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
