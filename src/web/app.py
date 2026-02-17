"""Flask web application for the meditation facilitator."""

import asyncio
import os
import signal
import sys
import threading
import time
from pathlib import Path

import httpx
import numpy as np
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

from ..config import load_config, Config
from ..llm.ollama import create_llm_provider
from ..llm.base import Message
from ..facilitation.prompts import PromptBuilder, PromptConfig, FacilitationStyle
from ..facilitation.session import SessionManager
from ..logging.transcript import TranscriptLogger
from ..stt.whisper import WhisperSTT


class WebMeditationSession:
    """Manages a single meditation session via the web interface."""

    def __init__(
        self,
        config: Config,
        intention: str = "",
        style: str = "jhourney",
        directiveness: int = 3,
        pleasant_emphasis: bool = True,
        verbosity: str = "low",
        custom_instructions: str = "",
        model: str | None = None,
    ):
        self.config = config
        self.intention = intention
        self.start_time = time.time()

        # Map style string to enum
        style_map = {
            "jhourney": FacilitationStyle.JHOURNEY,
            "non_directive": FacilitationStyle.NON_DIRECTIVE,
            "somatic": FacilitationStyle.SOMATIC,
            "open": FacilitationStyle.OPEN,
            "adaptive": FacilitationStyle.ADAPTIVE,
        }

        prompt_config = PromptConfig(
            directiveness=directiveness,
            pleasant_emphasis=pleasant_emphasis,
            verbosity=verbosity,
            custom_instructions=custom_instructions,
            style=style_map.get(style),
        )
        self.prompts = PromptBuilder(prompt_config)

        self.session = SessionManager(
            context_strategy=config.llm.context_strategy,
            window_size=config.llm.window_size,
        )

        self.llm = create_llm_provider(
            provider=config.llm.provider,
            model=model or config.llm.model,
            proxy_url=config.llm.proxy_url,
            ollama_url=config.llm.ollama_url,
            api_key=config.llm.api_key,
            max_tokens=config.llm.max_tokens,
        )

        self.session.start_session()

    def build_system_prompt(self) -> str:
        """Build system prompt, incorporating the meditator's intention."""
        base = self.prompts.build_system_prompt()
        if self.intention:
            base += (
                f"\n\nThe meditator's intention for this session: \"{self.intention}\"\n"
                "Hold this lightly. Follow their process rather than forcing toward the goal. "
                "The intention is a compass, not a cage."
            )
        return base

    async def generate_response(self, user_text: str) -> str:
        """Generate a facilitator response to user input."""
        self.session.add_user_message(user_text)

        messages = self.session.get_context_messages()
        llm_messages = [Message(role=m["role"], content=m["content"]) for m in messages]

        try:
            result = await self.llm.complete(
                messages=llm_messages,
                system=self.build_system_prompt(),
            )
            response = result.text.strip()
        except Exception as e:
            response = "Mmm. What do you notice now?"

        self.session.add_assistant_message(response)
        return response

    def get_opener(self) -> str:
        """Get a session opening message."""
        opener = self.prompts.get_session_opener()
        self.session.add_assistant_message(opener)
        return opener

    def end(self) -> dict | None:
        """End the session and return serialized data."""
        self.session.end_session()
        return self.session.to_dict()


def create_app(config: Config | None = None) -> tuple[Flask, SocketIO]:
    """Create and configure the Flask application."""
    if config is None:
        config = load_config()

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["SECRET_KEY"] = "meditation-pal-local"

    socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

    app.meditation_config = config
    app.web_sessions = {}      # session_id → WebMeditationSession
    app.sid_to_session = {}    # socket sid → session_id
    app.session_to_sid = {}    # session_id → current socket sid
    app.transcript_logger = TranscriptLogger(
        save_directory=config.session.save_directory,
        include_timestamps=config.session.include_timestamps,
    )

    # Initialize Whisper STT and pre-load model for fast first transcription
    app.whisper_stt = WhisperSTT(
        model=config.stt.model,
        language=config.stt.language,
        device=config.stt.device,
    )
    app.whisper_stt._load_model()
    app.whisper_lock = threading.Lock()

    _register_routes(app)
    _register_socketio_events(socketio, app)

    return app, socketio


def _register_routes(app: Flask) -> None:
    """Register HTTP routes."""

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/session")
    def session_page():
        return render_template("session.html")

    @app.route("/history")
    def history_page():
        sessions = app.transcript_logger.list_sessions()
        return render_template("history.html", sessions=sessions)

    @app.route("/api/sessions")
    def api_sessions():
        sessions = app.transcript_logger.list_sessions()
        return jsonify(sessions)

    @app.route("/api/sessions/<session_id>")
    def api_session_detail(session_id):
        session = app.transcript_logger.load_session(session_id)
        if session is None:
            return jsonify({"error": "Session not found"}), 404
        return jsonify(session)

    @app.route("/api/sessions/<session_id>", methods=["DELETE"])
    def api_session_delete(session_id):
        deleted = app.transcript_logger.delete_session(session_id)
        return jsonify({"deleted": deleted})


def _register_socketio_events(socketio: SocketIO, app: Flask) -> None:
    """Register WebSocket event handlers."""

    def _get_session(sid):
        """Look up a WebMeditationSession by socket sid."""
        session_id = app.sid_to_session.get(sid)
        if session_id:
            return app.web_sessions.get(session_id)
        return None

    @socketio.on("connect")
    def handle_connect():
        pass

    @socketio.on("disconnect")
    def handle_disconnect():
        sid = request.sid
        # Only unmap the socket — keep the session alive so a reconnect
        # can pick it back up with full conversation history.
        app.sid_to_session.pop(sid, None)

    @socketio.on("start_session")
    def handle_start_session(data):
        sid = request.sid
        session_id = data.get("session_id")

        # Reconnection: session already exists, just re-map the new socket
        if session_id and session_id in app.web_sessions:
            app.sid_to_session[sid] = session_id
            app.session_to_sid[session_id] = sid
            print(f"  [Session] Reconnected sid={sid[:8]}… to session {session_id[:12]}…", flush=True)
            return

        config = app.meditation_config

        web_session = WebMeditationSession(
            config=config,
            intention=data.get("intention", ""),
            style=data.get("style", "jhourney"),
            directiveness=data.get("directiveness", 3),
            pleasant_emphasis=data.get("pleasant_emphasis", True),
            verbosity=data.get("verbosity", "low"),
            custom_instructions=data.get("custom_instructions", ""),
            model=data.get("model"),
        )

        if not session_id:
            session_id = sid  # fallback
        app.web_sessions[session_id] = web_session
        app.sid_to_session[sid] = session_id
        app.session_to_sid[session_id] = sid
        print(f"  [Session] New session {session_id[:12]}… for sid={sid[:8]}…", flush=True)

        opener = web_session.get_opener()
        emit("facilitator_message", {"text": opener, "type": "opener"})

    @socketio.on("user_message")
    def handle_user_message(data):
        sid = request.sid
        web_session = _get_session(sid)
        if not web_session:
            emit("error", {"message": "No active session"})
            return

        text = data.get("text", "").strip()
        if not text:
            return

        emit("facilitator_typing", {"typing": True})

        try:
            response = asyncio.run(web_session.generate_response(text))
            emit("facilitator_message", {"text": response, "type": "response"})
        except Exception:
            emit("facilitator_message", {
                "text": "Mmm. What do you notice now?",
                "type": "response",
            })
        finally:
            emit("facilitator_typing", {"typing": False})

    @socketio.on("end_session")
    def handle_end_session():
        sid = request.sid
        session_id = app.sid_to_session.pop(sid, None)
        if not session_id or session_id not in app.web_sessions:
            return

        app.session_to_sid.pop(session_id, None)
        web_session = app.web_sessions.pop(session_id)

        closer = web_session.prompts.get_session_closer()
        web_session.session.add_assistant_message(closer)

        session_data = web_session.end()
        saved_id = None
        if session_data and app.meditation_config.session.auto_save:
            app.transcript_logger.save_session(session_data)
            app.transcript_logger.save_session_text(session_data)
            saved_id = session_data.get("session_id")

        emit("session_ended", {
            "closer": closer,
            "session_id": saved_id,
        })

    @socketio.on("audio_data")
    def handle_audio_data(data):
        """Receive raw PCM float32 audio and transcribe with Whisper.

        Runs transcription in a background task so the event handler
        returns immediately — this keeps the socket alive during slow
        Whisper inference.
        """
        try:
            audio_bytes = data.get("audio")
            sample_rate = data.get("sample_rate", 16000)
            audio = np.frombuffer(audio_bytes, dtype=np.float32)
            duration = len(audio) / sample_rate
            print(f"  [STT] Received {len(audio)} samples @ {sample_rate}Hz ({duration:.1f}s)", flush=True)
        except Exception as e:
            print(f"  [STT] Error parsing audio: {e}", flush=True)
            emit("transcription", {"text": "", "error": str(e)})
            return

        # Look up session so we can emit to the right socket even after
        # a reconnection changes the sid.
        session_id = app.sid_to_session.get(request.sid)

        def _transcribe():
            try:
                if not app.whisper_lock.acquire(timeout=15):
                    print("  [STT] Whisper busy, dropping audio", flush=True)
                    target_sid = app.session_to_sid.get(session_id)
                    if target_sid:
                        socketio.emit("transcription", {"text": "", "error": "busy"}, to=target_sid)
                    return

                try:
                    t0 = time.time()
                    result = app.whisper_stt.transcribe(audio, sample_rate=sample_rate)
                    elapsed = time.time() - t0
                    text = result.text.strip()
                    print(f"  [STT] Transcribed in {elapsed:.1f}s: \"{text}\"", flush=True)
                finally:
                    app.whisper_lock.release()

                # Emit to whatever socket is currently mapped to this session
                # (may have changed due to reconnection during transcription).
                target_sid = app.session_to_sid.get(session_id)
                if target_sid:
                    socketio.emit("transcription", {"text": text}, to=target_sid)
                else:
                    print("  [STT] No active socket for session, dropping result", flush=True)
            except Exception as e:
                print(f"  [STT] Error: {e}", flush=True)
                target_sid = app.session_to_sid.get(session_id)
                if target_sid:
                    socketio.emit("transcription", {"text": "", "error": str(e)}, to=target_sid)

        socketio.start_background_task(_transcribe)


def run_web(
    config_path: str | None = None,
    host: str = "0.0.0.0",
    port: int = 5555,
    debug: bool = False,
) -> None:
    """Run the web application."""
    config = load_config(config_path)

    # Check if LLM proxy is reachable when using claude_proxy provider
    if config.llm.provider == "claude_proxy":
        proxy_url = config.llm.proxy_url or "http://127.0.0.1:8317"
        headers = {}
        if config.llm.api_key:
            headers["Authorization"] = f"Bearer {config.llm.api_key}"
        try:
            resp = httpx.get(
                f"{proxy_url.rstrip('/')}/v1/models",
                headers=headers,
                timeout=3.0,
            )
            if resp.status_code == 401:
                print(f"\n  *** CLIProxyAPI at {proxy_url} rejected our API key ***")
                print(f"  Check api-keys in ~/.cli-proxy-api/config.yaml")
                print(f"  and llm.api_key in config/default.yaml\n")
                return
        except (httpx.ConnectError, httpx.TimeoutException):
            print(f"\n  *** CLIProxyAPI is not running at {proxy_url} ***")
            print(f"  Start it with: CLIProxyAPI")
            print(f"  Then restart this server.\n")
            return

    print(f"\n{'=' * 50}")
    print("  Meditation Pal — starting up...")
    print(f"{'=' * 50}")

    app, socketio = create_app(config)

    print(f"\n  Ready: http://localhost:{port}")
    print(f"  Press Ctrl+C to stop.\n")

    # Ensure Ctrl+C actually exits — threading mode can swallow KeyboardInterrupt
    def _shutdown(*_):
        print("\n  Shutting down...", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)

    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)
