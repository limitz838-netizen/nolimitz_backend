"""
================================================================================
  AI CHART SCANNER  (RENDER / LINUX)
================================================================================
  Endpoint that powers the "AI Chart Scanner" hero feature: a user uploads a
  screenshot of a chart, and Claude's vision model reads it and returns a
  structured analysis (detected symbol/timeframe, bias, key levels, patterns,
  indicators, a plain-English summary, and an explicit risk caveat).

  HONEST SCOPE — what this does and does NOT do:
    • DOES: describe what is *visible* in the screenshot — trend, support /
      resistance, candlestick & chart patterns, and any indicators shown
      (RSI, MACD, EMAs) — and explain what they typically imply.
    • DOES NOT: see beyond the image (no live price, no off-screen history),
      know the timeframe unless it's labeled, or predict the future. It is an
      educational reading tool, not a guaranteed signal.

  This is a STANDALONE feature. It does not touch the execution worker, the
  watcher, or any trading. It only reads an image and returns text.

  SETUP (Render):
    1. pip install anthropic            (add `anthropic` to requirements.txt)
       (python-multipart is also required for file uploads — usually already
        installed with FastAPI; add `python-multipart` if not.)
    2. Set env var:  ANTHROPIC_API_KEY = sk-ant-...
       (optional)    CHART_SCAN_MODEL  = claude-haiku-4-5-20251001   # default
                      CHART_SCAN_DAILY_LIMIT = 15                      # per license/day
    3. In your FastAPI app:  app.include_router(chart_scanner.router)

  To swap to a different vision provider later, replace ONLY the body of
  `_call_vision_model()` — the rest of the endpoint is provider-agnostic.
================================================================================
"""

import os
import json
import base64
import logging
from datetime import datetime, timezone, date
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import License

logger = logging.getLogger("chart_scanner")

router = APIRouter(prefix="/api/client", tags=["AI Chart Scanner"])

# ── Config ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Cost-safe default for a free feature. Bump to "claude-sonnet-4-6" or
# "claude-opus-4-8" for deeper reads (higher cost per scan).
CHART_SCAN_MODEL  = os.environ.get("CHART_SCAN_MODEL", "claude-haiku-4-5-20251001")
MAX_IMAGE_BYTES   = int(os.environ.get("CHART_SCAN_MAX_BYTES", str(5 * 1024 * 1024)))  # 5 MB
# Simple per-license daily cap to stop a single user spamming paid vision calls.
# NOTE: this counter is in-memory (per process). If you run more than one
# backend instance, move this to the DB or Redis for an accurate global cap.
DAILY_LIMIT       = int(os.environ.get("CHART_SCAN_DAILY_LIMIT", "15"))

ALLOWED_MEDIA = {
    "image/png":  "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg":  "image/jpeg",
    "image/webp": "image/webp",
    "image/gif":  "image/gif",
}

# (license_key_or_ip, date) -> count
_scan_counts: dict = defaultdict(int)


# ── The analysis instructions handed to the vision model ────────────────────
_SYSTEM_PROMPT = (
    "You are a professional technical-analysis assistant for the NolimitzBots "
    "AI platform. You read a single screenshot of a price chart and describe "
    "ONLY what is visibly present in the image. You never invent data you "
    "cannot see, never claim to know live prices or off-screen history, and "
    "never promise profit or give guaranteed buy/sell calls. You are an "
    "educational reading tool, not financial advice. Be concise, specific, and "
    "honest about uncertainty (e.g. if the timeframe or symbol is not labeled, "
    "say so)."
)

_USER_PROMPT = (
    "Analyse this trading chart screenshot and respond with ONLY a JSON object "
    "(no markdown, no code fences, no commentary) in exactly this shape:\n"
    "{\n"
    '  "is_chart": true,\n'
    '  "detected_symbol": "string or null if not visible",\n'
    '  "detected_timeframe": "string or null if not visible",\n'
    '  "bias": "BULLISH | BEARISH | NEUTRAL",\n'
    '  "confidence": 0-100,\n'
    '  "trend": "short description of the visible trend",\n'
    '  "key_levels": { "support": ["..."], "resistance": ["..."] },\n'
    '  "patterns": ["candlestick or chart patterns you can actually see"],\n'
    '  "indicators": "what any visible indicators (RSI/MACD/EMA/etc) are showing, or null",\n'
    '  "summary": "2-4 sentence plain-English reading of the setup",\n'
    '  "what_to_watch": ["1-3 concrete things a trader would watch next"],\n'
    '  "caveat": "one short sentence reminding this is a reading of the image only, not advice"\n'
    "}\n"
    "If the image is NOT a price chart, return {\"is_chart\": false, \"summary\": "
    "\"This doesn't look like a price chart — please upload a screenshot of a "
    "trading chart.\"} and nothing else. confidence reflects how clearly the "
    "VISIBLE evidence supports the bias, not a prediction of the future."
)


def _rate_limit_key(license_key: Optional[str]) -> str:
    return (license_key or "anon").strip().lower()


def _check_and_bump_limit(key: str) -> bool:
    """Return True if allowed, False if the daily cap is exceeded."""
    today = date.today().isoformat()
    bucket = (key, today)
    if _scan_counts[bucket] >= DAILY_LIMIT:
        return False
    _scan_counts[bucket] += 1
    return True


def _call_vision_model(image_b64: str, media_type: str, user_hint: str) -> str:
    """
    Send the image to the vision model and return the raw text response.

    PROVIDER-SPECIFIC: this is the ONLY function to change if you swap away
    from Anthropic. Everything else in this file is provider-agnostic.
    """
    try:
        import anthropic
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Server missing 'anthropic' package. Run: pip install anthropic",
        )

    if not ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Chart scanner not configured (ANTHROPIC_API_KEY not set).",
        )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = _USER_PROMPT
    if user_hint:
        prompt += f"\n\nUser hint about the chart (may be wrong, verify against the image): {user_hint}"

    try:
        message = client.messages.create(
            model=CHART_SCAN_MODEL,
            max_tokens=900,
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": media_type, "data": image_b64,
                    }},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        # Concatenate any text blocks in the response
        parts = [b.text for b in message.content if getattr(b, "type", "") == "text"]
        return "\n".join(parts).strip()
    except Exception as e:
        logger.error("Vision model call failed: %s", e)
        raise HTTPException(status_code=502, detail="AI analysis failed. Please try again.")


def _parse_analysis(raw: str) -> dict:
    """Parse the model's JSON. Defensive: strip code fences, fall back to text."""
    text = (raw or "").strip()
    if text.startswith("```"):
        # remove ```json ... ``` fences if the model added them
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    # Fallback: hand back the text so the user still sees something useful
    return {"is_chart": True, "summary": raw.strip(),
            "caveat": "This is a reading of the uploaded image only, not financial advice."}


@router.post("/chart-scan")
async def chart_scan(
    file: UploadFile = File(...),
    license_key: Optional[str] = Form(default=None),
    symbol_hint: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """
    Accept a chart screenshot (multipart 'file') and return structured analysis.

    Optional form fields:
      - license_key: used for the daily rate limit (and could gate access later)
      - symbol_hint: a symbol the user thinks the chart is (passed to the model
                     as a hint it must verify against the image)
    """
    # ── Validate file type ──────────────────────────────────────────────────
    media_type = ALLOWED_MEDIA.get((file.content_type or "").lower())
    if not media_type:
        raise HTTPException(
            status_code=415,
            detail="Please upload a PNG, JPEG, WebP, or GIF image of a chart.",
        )

    # ── Read + size-check ─────────────────────────────────────────────────────
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(raw_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large (max {MAX_IMAGE_BYTES // (1024*1024)} MB).",
        )

    # ── Rate limit (per license/day) ─────────────────────────────────────────
    rl_key = _rate_limit_key(license_key)
    if not _check_and_bump_limit(rl_key):
        raise HTTPException(
            status_code=429,
            detail=f"Daily scan limit reached ({DAILY_LIMIT}/day). Try again tomorrow.",
        )

    # Optional: confirm the license exists (kept soft — scanner is a free feature).
    if license_key:
        lic = db.query(License).filter(License.license_key == license_key).first()
        if not lic:
            logger.info("chart-scan with unknown license_key=%s (allowed, free feature)", license_key)

    image_b64 = base64.standard_b64encode(raw_bytes).decode("utf-8")

    raw = _call_vision_model(image_b64, media_type, (symbol_hint or "").strip())
    analysis = _parse_analysis(raw)

    # Not a chart → friendly message
    if analysis.get("is_chart") is False:
        return {
            "success": False,
            "is_chart": False,
            "message": analysis.get("summary",
                       "That doesn't look like a price chart. Please upload a trading chart."),
        }

    # Ensure a caveat is always present
    analysis.setdefault(
        "caveat",
        "This is an AI reading of the uploaded image only — not financial advice. "
        "It cannot see live price or off-screen history.",
    )

    return {
        "success": True,
        "is_chart": True,
        "model": CHART_SCAN_MODEL,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "analysis": analysis,
    }