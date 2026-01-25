#!/usr/bin/env python3
"""OpenAI-compatible STT server using Parakeet MLX."""

import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="Parakeet STT Server")

# Global model instance
_model = None


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
    get_model()


@app.get("/")
async def root():
    return {"status": "ok", "model": "parakeet-tdt-0.6b-v3"}


@app.get("/v1/models")
async def list_models():
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
    print(f"Starting Parakeet STT server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
