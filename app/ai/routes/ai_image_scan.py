import cv2
import numpy as np
from PIL import Image

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException
)

from datetime import datetime
import os
import shutil

from app.ai.services.ai_chat_engine import analyze_market


router = APIRouter(
    prefix="/api/ai",
    tags=["AI Image Scanner"]
)

# =========================
# CREATE UPLOAD FOLDER
# =========================

UPLOAD_FOLDER = "uploaded_charts"

os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)


# =========================
# IMAGE SCANNER
# =========================

@router.post("/scan-image")
async def scan_image(
    image: UploadFile = File(...)
):

    try:

        # =========================
        # VALIDATE IMAGE
        # =========================

        allowed_extensions = [
            ".png",
            ".jpg",
            ".jpeg",
            ".webp"
        ]

        file_extension = os.path.splitext(
            image.filename
        )[1].lower()

        if file_extension not in allowed_extensions:

            raise HTTPException(
                status_code=400,
                detail="Invalid image format"
            )

        # =========================
        # SAVE IMAGE
        # =========================

        timestamp = datetime.utcnow().strftime(
            "%Y%m%d%H%M%S"
        )

        filename = (
            f"{timestamp}_{image.filename}"
        )

        file_path = os.path.join(
            UPLOAD_FOLDER,
            filename
        )

        with open(file_path, "wb") as buffer:

            shutil.copyfileobj(
                image.file,
                buffer
            )

        # =========================
        # READ IMAGE
        # =========================

        img = cv2.imread(file_path)

        height, width, channels = img.shape

        gray = cv2.cvtColor(
            img,
            cv2.COLOR_BGR2GRAY
        )

        # =========================
        # THRESHOLD DETECTION
        # =========================

        _, threshold = cv2.threshold(
            gray,
            120,
            255,
            cv2.THRESH_BINARY
        )

        white_pixels = np.sum(
            threshold == 255
        )

        black_pixels = np.sum(
            threshold == 0
        )

        bullish_pressure = bool(
            white_pixels > black_pixels
        )

        bearish_pressure = bool(
            black_pixels > white_pixels
        )

        brightness = np.mean(gray)

        edges = cv2.Canny(
            gray,
            100,
            200
        )

        edge_strength = np.mean(edges)

        # =========================
        # TREND DETECTION
        # =========================

        left_side = gray[:, :width // 2]
        right_side = gray[:, width // 2:]

        left_brightness = np.mean(left_side)
        right_brightness = np.mean(right_side)

        uptrend_detected = bool(
            right_brightness > left_brightness
        )

        downtrend_detected = bool(
            left_brightness > right_brightness
        )

        sideways_market = bool(
            abs(
                right_brightness - left_brightness
            ) < 3
        )

        # =========================
        # COLOR ANALYSIS
        # =========================

        blue_channel = img[:, :, 0]
        green_channel = img[:, :, 1]
        red_channel = img[:, :, 2]

        green_strength = np.mean(green_channel)
        red_strength = np.mean(red_channel)

        bullish_chart = bool(
            green_strength > red_strength
        )

        bearish_chart = bool(
            red_strength > green_strength
        )

        # =========================
        # MARKET ACTIVITY
        # =========================

        high_activity = bool(
            edge_strength > 25
        )

        low_activity = bool(
            edge_strength <= 25
        )

        # =========================
        # SUPPORT / RESISTANCE
        # =========================

        top_zone = gray[:height // 4, :]
        bottom_zone = gray[height - (height // 4):, :]

        resistance_strength = float(
            np.mean(top_zone)
        )

        support_strength = float(
            np.mean(bottom_zone)
        )

        strong_support = bool(
            support_strength > resistance_strength
        )

        strong_resistance = bool(
            resistance_strength > support_strength
        )

        # =========================
        # AI ANALYSIS
        # =========================

        # FOR NOW:
        # DEFAULT SYMBOL = XAUUSD
        # LATER:
        # AI WILL DETECT SYMBOL
        # FROM IMAGE

        symbol = "XAUUSD"

        # SIMPLE AI DETECTION

        if brightness < 80:
            detected_session = "NIGHT"

        else:
            detected_session = "DAY"

        analysis = analyze_market(symbol)

        # =========================
        # CANDLE PATTERN DETECTION
        # =========================

        bullish_engulfing = bool(
            bullish_chart
            and bullish_pressure
            and uptrend_detected
        )

        bearish_engulfing = bool(
            bearish_chart
            and bearish_pressure
            and downtrend_detected
        )

        doji_detected = bool(
            abs(
                green_strength - red_strength
            ) < 5
        )

        # =========================
        # FAKE BREAKOUT DETECTION
        # =========================

        fake_breakout_up = bool(
            strong_resistance
            and bullish_pressure
            and bearish_chart
        )

        fake_breakout_down = bool(
            strong_support
            and bearish_pressure
            and bullish_chart
        )

        # =========================
        # BUILD AI REASONS
        # =========================

        ai_reasons = []

        if bullish_chart:

            ai_reasons.append(
            "Bullish chart structure detected"
        )

        if bearish_chart:

            ai_reasons.append(
                "Bearish chart structure detected"
            )

        if high_activity:

            ai_reasons.append(
                "High market activity detected"
            )

        if low_activity:

            ai_reasons.append(
                "Low volatility market detected"
        )

        if analysis["signal"] == "BUY":

            ai_reasons.append(
                "Bullish market momentum detected"
            )

        elif analysis["signal"] == "SELL":

            ai_reasons.append(
                "Bearish market pressure detected"
            )

        if analysis["bullish_bos"]:

            ai_reasons.append(
                "Bullish BOS detected"
            )

        if analysis["bearish_bos"]:

            ai_reasons.append(
                "Bearish BOS detected"
            )

        if analysis["bullish_choch"]:

            ai_reasons.append(
                "Bullish CHOCH detected"
            )

        if analysis["bearish_choch"]:

            ai_reasons.append(
                "Bearish CHOCH detected"
            )

        if analysis["bullish_liquidity_sweep"]:

            ai_reasons.append(
                "Bullish liquidity sweep detected"
            )

        if analysis["bearish_liquidity_sweep"]:

            ai_reasons.append(
                "Bearish liquidity sweep detected"
            )

        if bullish_pressure:

            ai_reasons.append(
                "Bullish candle pressure detected"
            )

        if bearish_pressure:

            ai_reasons.append(
                "Bearish candle pressure detected"
            )

        if uptrend_detected:

            ai_reasons.append(
                "Uptrend detected from chart image"
            )

        if downtrend_detected:

            ai_reasons.append(
                "Downtrend detected from chart image"
            )

        if sideways_market:

            ai_reasons.append(
                "Sideways market detected"
            )

        if strong_support:

            ai_reasons.append(
                "Strong support zone detected"
            )

        if strong_resistance:

            ai_reasons.append(
                "Strong resistance zone detected"
            )

        if bullish_engulfing:

            ai_reasons.append(
                "Bullish engulfing pattern detected"
            )

        if bearish_engulfing:

            ai_reasons.append(
                "Bearish engulfing pattern detected"
            )

        if doji_detected:

            ai_reasons.append(
                "Doji candle detected"
            )

        if fake_breakout_up:

            ai_reasons.append(
               "Possible fake bullish breakout detected"
            )

        if fake_breakout_down:

            ai_reasons.append(
               "Possible fake bearish breakout detected"
        )    

        # =========================
        # AI CONFIDENCE MESSAGE
        # =========================

        confidence_level = "Moderate"

        if analysis["confidence"] >= 90:

            confidence_level = "Very Strong"

        elif analysis["confidence"] >= 75:

            confidence_level = "Strong"

        elif analysis["confidence"] >= 60:

            confidence_level = "Good"


        # =========================
        # VISUAL CONFIDENCE BOOST
        # =========================

        if bullish_pressure and analysis["signal"] == "BUY":

            analysis["confidence"] += 5

        if bearish_pressure and analysis["signal"] == "SELL":

            analysis["confidence"] += 5

        if analysis["confidence"] > 100:

            analysis["confidence"] = 100

        if bullish_engulfing:

            analysis["confidence"] += 4

        if bearish_engulfing:

            analysis["confidence"] += 4

        if strong_support and analysis["signal"] == "BUY":

            analysis["confidence"] += 3

        if strong_resistance and analysis["signal"] == "SELL":

            analysis["confidence"] += 3

        if fake_breakout_up:

            analysis["confidence"] -= 5

        if fake_breakout_down:

            analysis["confidence"] -= 5

        # =========================
        # FINAL RESPONSE
        # =========================

        return {

            "message":
                "AI chart analysis completed successfully",

            "uploaded_image":
                str(filename),

            "image_width":
                int(width),

            "image_height":
                int(height),

            "brightness":
                float(brightness),

            "edge_strength":
                float(edge_strength),

            "detected_session":
                detected_session,

            "symbol":
                str(symbol),

            "signal":
                str(analysis["signal"]),

            "trend":
                str(analysis["trend"]),

            "confidence":
                int(analysis["confidence"]),

            "confidence_level":
                str(confidence_level),

            "entry":
                float(analysis["current_price"]),

            "stop_loss":
                float(analysis["stop_loss"]),

            "take_profit":
                float(analysis["take_profit"]),

            "support":
                float(analysis["support"]),

            "resistance":
                float(analysis["resistance"]),

            "rsi":
                float(analysis["rsi"]),

            "volatility":
                float(analysis["volatility"]),

            "bullish_bos":
                bool(analysis["bullish_bos"]),

            "bearish_bos":
                bool(analysis["bearish_bos"]),

            "bullish_choch":
                bool(analysis["bullish_choch"]),

            "bearish_choch":
                bool(analysis["bearish_choch"]),

            "bullish_chart":
                bullish_chart,

            "bearish_chart":
                bearish_chart,

            "high_activity":
                high_activity,

            "low_activity":
                low_activity,

            "green_strength":
                float(green_strength),

            "red_strength":
                float(red_strength),

            "bullish_pressure":
                bullish_pressure,

            "bearish_pressure":
                bearish_pressure,

            "white_pixels":
                int(white_pixels),

            "black_pixels":
                int(black_pixels),

            "uptrend_detected":
                uptrend_detected,

            "downtrend_detected":
                downtrend_detected,

            "sideways_market":
                sideways_market,

            "strong_support":
                strong_support,

            "strong_resistance":
                strong_resistance,

            "support_strength":
                support_strength,

            "resistance_strength":
                resistance_strength,

            "bullish_engulfing":
                bullish_engulfing,

            "bearish_engulfing":
                bearish_engulfing,

            "doji_detected":
                doji_detected,

            "fake_breakout_up":
                fake_breakout_up,

            "fake_breakout_down":
                fake_breakout_down,

            "bullish_liquidity_sweep":
                bool(
                    analysis[
                        "bullish_liquidity_sweep"
                    ]
                ),

            "bearish_liquidity_sweep":
                bool(
                    analysis[
                        "bearish_liquidity_sweep"
                    ]
                ),

            "analysis":
                ai_reasons,

            "assistant_response":
                str(
                    analysis[
                       "assistant_response"
                    ]
                )
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )