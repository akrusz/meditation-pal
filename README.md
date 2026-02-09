# Meditation Pal

An AI meditation facilitator that supports live somatic exploration practice. You describe your moment-to-moment sensory experience and the AI asks gentle questions, helping you explore sensation, emotion, and absorption at your own pace.

Two interfaces: a **web UI** (recommended for getting started) and a **voice CLI** for hands-free sessions.

## Quick Start (Web Interface)

```bash
uv venv
uv pip install -r requirements.txt
uv run python -m src.web
```

Open [http://localhost:5555](http://localhost:5555) in your browser. Set an intention (or don't), pick a facilitation style, and begin.

You type what you're experiencing. The facilitator responds. That's it.

Optional: click the microphone button to use voice dictation (uses server-side Whisper transcription — works in all browsers). Toggle "Voice" to have responses read aloud.

## Quick Start (Voice CLI)

The CLI mode uses your computer's microphone and speaker for a fully hands-free session. Requires audio dependencies (pyaudio, sounddevice) and a working mic.

```bash
pip install -r requirements.txt
python -m src
```

Speak naturally. The app listens, transcribes with Whisper, and the facilitator responds via text-to-speech. Press Ctrl+C to end the session.

## Setup

### Requirements

- Python 3.10+
- An LLM provider (see below)

### Install

```bash
git clone <this-repo>
cd meditation-pal
pip install -r requirements.txt
```

For Apple Silicon Macs, optionally install the optimized Whisper:
```bash
pip install mlx-whisper
```

### LLM Provider

You need one of these configured for the AI facilitator to work:

**Claude via CLI proxy** (default — if you're running [Claude Code](https://docs.anthropic.com/en/docs/claude-code) or similar):
```yaml
# config/default.yaml
llm:
  provider: claude_proxy
  proxy_url: http://127.0.0.1:8317
```

**Anthropic API directly:**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
```
```yaml
llm:
  provider: anthropic
  api_key: ${ANTHROPIC_API_KEY}
  model: claude-sonnet-4-5-20250929
```

**OpenAI:**
```bash
export OPENAI_API_KEY=sk-...
```
```yaml
llm:
  provider: openai
  api_key: ${OPENAI_API_KEY}
  model: gpt-4o
```

**Ollama (fully local/offline):**
```bash
ollama pull llama3
```
```yaml
llm:
  provider: ollama
  ollama_url: http://localhost:11434
  ollama_model: llama3
```

## Configuration

All settings live in `config/default.yaml`. You can also pass a custom config:

```bash
python -m src --config path/to/my-config.yaml
python -m src --web --config path/to/my-config.yaml
```

### Facilitation Styles

Choose in the web UI setup page, or set in config:

| Style | What it does |
|---|---|
| **Jhourney** | Gentle arc toward pleasant sensations and meditative absorption (jhana). Understands piti, sukha, and the jhana factors. Encourages letting go and enjoying what's here. |
| **Adaptive** | Flows with whatever arises — no fixed framework. Companion, not director. |
| **Non-directive** | Pure presence. Only reflects and asks "What's here now?" |
| **Somatic** | Body-focused exploration of texture, temperature, movement, density. |
| **Open** | Minimal facilitation. Mostly holds space. Long silences welcome. |

### Key Config Options

```yaml
facilitation:
  directiveness: 3          # 0 (pure following) to 10 (active guidance)
  pleasant_emphasis: true   # orient toward pleasant sensations
  verbosity: low            # low, medium, high

llm:
  context:
    strategy: rolling       # rolling (last N exchanges) or full (entire session)
    window_size: 10         # how many exchanges to keep in context
    max_tokens: 300         # max length of each facilitator response
```

### Text-to-Speech (CLI mode)

The CLI uses TTS for spoken responses. Options in `config/default.yaml`:

```yaml
tts:
  engine: macos             # default on Mac — uses system 'say' command
  voice: Samantha
  rate: 180
```

Other engines (install separately):

| Engine | Install | Notes |
|---|---|---|
| `macos` | built-in | System voices, zero latency, decent quality |
| `piper` | `pip install piper-tts` | Fast local neural TTS, good quality |
| `parakeet` | `pip install transformers torch` | NVIDIA neural TTS, high quality |
| `elevenlabs` | `pip install elevenlabs` | Cloud API, best quality, requires API key |

For ElevenLabs:
```bash
export ELEVENLABS_API_KEY=your-key
```
```yaml
tts:
  engine: elevenlabs
  api_key: ${ELEVENLABS_API_KEY}
  voice_id: 21m00Tcm4TlvDq8ikWAM   # Rachel — calm, warm
```

### Speech-to-Text (CLI mode)

Uses OpenAI Whisper locally. Model sizes trade speed for accuracy:

```yaml
stt:
  model: small    # tiny (fastest), base, small (good balance), medium, large (most accurate)
  language: en
  device: auto    # auto, cpu, cuda, mps
```

## Usage Tips

- **Set an intention loosely.** "Explore pleasant sensations" or "just be present" — the facilitator holds it lightly.
- **Describe raw sensation.** "Warmth in my chest" works better than "I feel happy." The facilitator will help you go deeper.
- **It's okay to drift.** There's no wrong direction. If you wander, the facilitator follows.
- **Say "going quiet" or "just listen"** to enter silence mode. The facilitator will hold space without interrupting. Say "I'm back" to resume.
- **You can release all expectations.** The facilitator supports whatever happens, including nothing.

## Session History

Sessions auto-save to the `sessions/` folder as both JSON and readable text files.

**Web:** Visit [http://localhost:5555/history](http://localhost:5555/history) to browse past sessions.

**CLI:**
```bash
python -m src --list-sessions
python -m src --view-session 2026-02-09-143022
```

## Project Structure

```
meditation-pal/
  config/default.yaml       # all settings
  src/
    main.py                 # CLI entry point
    web/                    # web interface (Flask + SocketIO)
    audio/                  # mic input, voice activity detection
    stt/                    # speech-to-text (Whisper)
    tts/                    # text-to-speech (macOS, Piper, Parakeet, ElevenLabs)
    llm/                    # LLM providers (Claude, OpenAI, Ollama)
    facilitation/           # prompts, session state, pacing/turn-taking
    logging/                # session transcript saving
  sessions/                 # saved session transcripts
```
