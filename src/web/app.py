"""Flask web application for the meditation facilitator."""

import asyncio
import time
from pathlib import Path

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
            model=config.llm.model,
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
    app.web_sessions = {}
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

    @socketio.on("connect")
    def handle_connect():
        pass

    @socketio.on("disconnect")
    def handle_disconnect():
        sid = request.sid
        if sid in app.web_sessions:
            session_data = app.web_sessions[sid].end()
            if session_data and app.meditation_config.session.auto_save:
                app.transcript_logger.save_session(session_data)
                app.transcript_logger.save_session_text(session_data)
            del app.web_sessions[sid]

    @socketio.on("start_session")
    def handle_start_session(data):
        sid = request.sid
        config = app.meditation_config

        web_session = WebMeditationSession(
            config=config,
            intention=data.get("intention", ""),
            style=data.get("style", "jhourney"),
            directiveness=data.get("directiveness", 3),
            pleasant_emphasis=data.get("pleasant_emphasis", True),
            verbosity=data.get("verbosity", "low"),
            custom_instructions=data.get("custom_instructions", ""),
        )

        app.web_sessions[sid] = web_session

        opener = web_session.get_opener()
        emit("facilitator_message", {"text": opener, "type": "opener"})

    @socketio.on("user_message")
    def handle_user_message(data):
        sid = request.sid
        if sid not in app.web_sessions:
            emit("error", {"message": "No active session"})
            return

        text = data.get("text", "").strip()
        if not text:
            return

        web_session = app.web_sessions[sid]

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
        if sid not in app.web_sessions:
            return

        web_session = app.web_sessions[sid]

        closer = web_session.prompts.get_session_closer()
        web_session.session.add_assistant_message(closer)

        session_data = web_session.end()
        session_id = None
        if session_data and app.meditation_config.session.auto_save:
            app.transcript_logger.save_session(session_data)
            app.transcript_logger.save_session_text(session_data)
            session_id = session_data.get("session_id")

        del app.web_sessions[sid]

        emit("session_ended", {
            "closer": closer,
            "session_id": session_id,
        })

    @socketio.on("audio_data")
    def handle_audio_data(data):
        """Receive raw PCM float32 audio and transcribe with Whisper."""
        try:
            audio_bytes = data.get("audio")
            sample_rate = data.get("sample_rate", 16000)

            audio = np.frombuffer(audio_bytes, dtype=np.float32)

            result = app.whisper_stt.transcribe(audio, sample_rate=sample_rate)
            text = result.text.strip()

            emit("transcription", {"text": text})
        except Exception as e:
            emit("transcription", {"text": "", "error": str(e)})


def run_web(
    config_path: str | None = None,
    host: str = "0.0.0.0",
    port: int = 5555,
    debug: bool = False,
) -> None:
    """Run the web application."""
    config = load_config(config_path)
    app, socketio = create_app(config)

    print(f"\n{'=' * 50}")
    print("  Meditation Pal")
    print(f"  http://localhost:{port}")
    print(f"{'=' * 50}\n")

    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)
