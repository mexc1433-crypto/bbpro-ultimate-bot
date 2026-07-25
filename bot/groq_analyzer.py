"""
Groq AI Market Analyzer — Intelligent signal analysis for BBPro Ultimate

Uses Groq's fast inference (Llama 3.3 70B) to:
- Analyze confluence signals from 8+ indicators
- Provide natural-language market commentary
- Score trade setups with AI confidence
- Generate Telegram-ready analysis summaries
"""

from __future__ import annotations
import os, logging, json
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class AIAnalysis:
    confidence: int          # 0-100 AI confidence score
    verdict: str             # BUY / SELL / WAIT
    reasoning: str            # short reasoning
    risk_note: str            # risk warning
    suggestion: str           # actionable suggestion

class GroqAnalyzer:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self.client = None
        if self.api_key:
            try:
                from groq import Groq
                self.client = Groq(api_key=self.api_key)
                logger.info("🤖 Groq AI analyzer initialized (Llama 3.3 70B)")
            except Exception as e:
                logger.warning("Groq init failed: %s", e)
        else:
            logger.info("Groq AI: no API key set — analysis disabled")

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def analyze_signal(
        self,
        symbol: str,
        direction: str,       # "buy" or "sell"
        confluence_score: int, # e.g. 4/5
        indicators: Dict,       # {RSI: 65, MACD: "bullish", BB: "above_upper", ...}
        atr: float = 0.0,
        adx: float = 0.0,
        spread_pips: float = 0.0,
    ) -> Optional[AIAnalysis]:
        """Analyze a trade signal with Groq AI and return a verdict."""
        if not self.enabled:
            return None

        prompt = f"""You are an expert forex/gold trading analyst. Analyze this trade setup:

Symbol: {symbol}
Direction: {direction.upper()}
Confluence Score: {confluence_score}/5
Indicators: {json.dumps(indicators, indent=2)}
ATR: {atr}
ADX: {adx} (trend strength)
Spread: {spread_pips} pips

Provide a concise analysis in this EXACT JSON format:
{{
  "confidence": <0-100 integer>,
  "verdict": "<BUY|SELL|WAIT>",
  "reasoning": "<2-3 sentences explaining the analysis>",
  "risk_note": "<1 sentence about key risk>",
  "suggestion": "<1 actionable sentence>"
}}

Rules:
- If confluence < 3, recommend WAIT
- If ADX < 20, note weak trend
- If spread > 3 pips, note high spread risk
- Be conservative — better to WAIT than force a bad trade
- Keep it SHORT and DIRECT"""

        try:
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": "You are a professional trading analyst. Respond ONLY with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=300,
            )
            text = response.choices[0].message.content.strip()
            # Strip markdown code blocks if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text
                text = text.rsplit("```", 1)[0] if "```" in text else text
            data = json.loads(text)
            return AIAnalysis(
                confidence=int(data.get("confidence", 50)),
                verdict=data.get("verdict", "WAIT"),
                reasoning=data.get("reasoning", ""),
                risk_note=data.get("risk_note", ""),
                suggestion=data.get("suggestion", ""),
            )
        except json.JSONDecodeError:
            logger.warning("Groq returned non-JSON response")
            return None
        except Exception as e:
            logger.warning("Groq analysis failed: %s", e)
            return None

    def daily_summary(
        self,
        total_trades: int,
        wins: int,
        losses: int,
        total_pnl: float,
        win_rate: float,
        best_trade: str,
        worst_trade: str,
        market_session: str = "London/NY",
    ) -> str:
        """Generate a natural-language daily performance summary."""
        if not self.enabled:
            return ""

        prompt = f"""Write a concise, professional daily trading summary in Arabic + English mix:

Stats:
- Total Trades: {total_trades}
- Wins: {wins} | Losses: {losses}
- Win Rate: {win_rate:.1f}%
- Total PnL: {total_pnl:+.2f} EUR
- Best Trade: {best_trade}
- Worst Trade: {worst_trade}
- Session: {market_session}

Format: 3-4 short paragraphs. Start with a headline. Be honest — if performance was bad, say so. End with one suggestion for tomorrow."""

        try:
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": "You are a professional trading analyst writing a daily summary. Be concise and honest."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.5,
                max_tokens=400,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning("Groq daily summary failed: %s", e)
            return ""

    def market_commentary(self, symbol: str, indicators: Dict) -> str:
        """Generate short market commentary for a symbol."""
        if not self.enabled:
            return ""

        prompt = f"""Write a 2-sentence market commentary for {symbol} based on:
{json.dumps(indicators, indent=2)}

Be direct and professional. No fluff."""

        try:
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": "You are a market analyst. Be extremely concise."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.4,
                max_tokens=150,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning("Groq commentary failed: %s", e)
            return ""
