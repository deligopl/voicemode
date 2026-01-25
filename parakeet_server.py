#!/usr/bin/env python3
"""OpenAI-compatible STT server using Parakeet MLX.

Security:
    Set PARAKEET_API_KEY environment variable to require authentication.
    Clients must send: Authorization: Bearer <key>

    If PARAKEET_API_KEY is not set, the server runs without authentication
    (suitable for local-only access behind a firewall).
"""

import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, Header, HTTPException, Depends
from fastapi.responses import JSONResponse
import uvicorn

# API key from environment (optional - if not set, no auth required)
API_KEY = os.getenv("PARAKEET_API_KEY")

app = FastAPI(title="Parakeet STT Server")

# Global model instance
_model = None


def verify_api_key(authorization: Optional[str] = Header(None)):
    """Verify API key if configured."""
    if not API_KEY:
        # No API key configured - allow all requests
        return True

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # Expect "Bearer <key>" format
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format. Use: Bearer <api_key>",
            headers={"WWW-Authenticate": "Bearer"}
        )

    provided_key = parts[1]

    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(provided_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return True


def get_model():
    """Load model lazily."""
    global _model
    if _model is None:
        from parakeet_mlx import from_pretrained
        print("Loading Parakeet v3 model...")
        start = time.time()
        _model = from_pretrained('mlx-community/parakeet-tdt-0.6b-v3')
        print(f"Model loaded in {time.time()-start:.1f}s")
    return _model


@app.on_event("startup")
async def startup():
    """Pre-load model on startup."""
    if API_KEY:
        print(f"🔐 API key authentication enabled")
    else:
        print("⚠️  No API key configured - running without authentication")
        print("   Set PARAKEET_API_KEY environment variable to enable auth")
    get_model()


@app.get("/")
async def root():
    return {
        "status": "ok",
        "model": "parakeet-tdt-0.6b-v3",
        "auth_required": bool(API_KEY)
    }


@app.get("/v1/models")
async def list_models(authorized: bool = Depends(verify_api_key)):
    """List available models (OpenAI-compatible)."""
    return {
        "object": "list",
        "data": [
            {
                "id": "parakeet-tdt-0.6b-v3",
                "object": "model",
                "created": 1700000000,
                "owned_by": "nvidia"
            }
        ]
    }


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default="parakeet-tdt-0.6b-v3"),
    language: Optional[str] = Form(default=None),
    response_format: str = Form(default="json"),
    temperature: float = Form(default=0.0),
    authorized: bool = Depends(verify_api_key),
):
    """Transcribe audio file (OpenAI Whisper-compatible API)."""
    start_time = time.time()

    # Save uploaded file to temp
    suffix = Path(file.filename).suffix if file.filename else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Transcribe
        model_instance = get_model()
        result = model_instance.transcribe(tmp_path)

        # Get text from result
        text = result.text if hasattr(result, 'text') else str(result)

        elapsed = time.time() - start_time

        if response_format == "text":
            return text
        elif response_format == "verbose_json":
            return {
                "task": "transcribe",
                "language": language or "en",
                "duration": getattr(result, 'duration', 0),
                "text": text,
                "segments": [
                    {
                        "id": i,
                        "start": getattr(s, 'start', 0),
                        "end": getattr(s, 'end', 0),
                        "text": getattr(s, 'text', str(s)),
                    }
                    for i, s in enumerate(getattr(result, 'sentences', []))
                ],
                "processing_time": elapsed
            }
        else:  # json
            return {"text": text}

    finally:
        # Cleanup temp file
        Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 2022
    # Default to localhost only for security - use --host 0.0.0.0 to expose
    host = "127.0.0.1"

    for i, arg in enumerate(sys.argv):
        if arg == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]

    print(f"Starting Parakeet STT server on {host}:{port}...")
    uvicorn.run(app, host=host, port=port)
