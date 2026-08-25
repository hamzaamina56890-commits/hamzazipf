from datetime import datetime, timezone

SUPPORTED_TIMEFRAMES = [60, 300, 900, 1800, 3600]


class Scanner:
    def __init__(self, market_provider, audit_log=None):
        self.market_provider = market_provider
        self.audit_log = audit_log
        self.mode = "manual"
        self.running = False

    @property
    def execution(self):
        return self

    @property
    def available(self):
        return False

    def start(self, mode="MANUAL"):
        requested = str(mode or "MANUAL").upper()
        if requested == "AUTO":
            return {
                "enabled": False,
                "mode": "manual",
                "reason": "Authorized execution/trading access is required for AUTO mode.",
            }
        self.mode = "manual"
        self.running = requested != "STOPPED"
        return {"enabled": self.running, "mode": self.mode, "reason": "Manual scanner mode enabled."}

    def stop(self):
        self.running = False
        self.mode = "manual"
        return {"enabled": False, "mode": self.mode, "reason": "Scanner stopped."}

    @staticmethod
    def _provider_name(provider, fallback="unknown"):
        return (
            getattr(provider, "NAME", None)
            or getattr(provider, "name", None)
            or getattr(provider, "__class__", type(provider)).__name__
            or fallback
        )

    @staticmethod
    def _data_status(timestamp, timeframe_seconds):
        if timestamp in (None, ""):
            return {"timestamp": None, "fresh": False, "closed": False, "status": "missing"}
        try:
            value = str(timestamp).replace("Z", "+00:00")
            observed = datetime.fromisoformat(value)
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return {"timestamp": str(timestamp), "fresh": False, "closed": False, "status": "invalid"}
        max_age = max(120, int(timeframe_seconds) * 3)
        fresh = -60 <= age <= max_age
        closed = age >= int(timeframe_seconds)
        return {
            "timestamp": str(timestamp),
            "fresh": fresh,
            "closed": closed,
            "age_seconds": round(age, 1),
            "status": "fresh" if fresh else "stale",
        }

    async def scan(self, symbol, timeframe_seconds, limit=100):
        if timeframe_seconds not in SUPPORTED_TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe: {timeframe_seconds} seconds.")

        provider_name = self._provider_name(self.market_provider)
        candles = None
        last_error = None

        # Give providers one retry when their newest candle is temporarily
        # in the future (a common feed synchronization condition).
        for _ in range(2):
            try:
                candles = await self.market_provider.get_candles(symbol, timeframe_seconds, limit)
                last_error = None
            except Exception as exc:
                last_error = exc
                candles = None
                break
            if candles:
                status = self._data_status(candles[-1].get("timestamp"), timeframe_seconds)
                if status["closed"] and status["fresh"]:
                    break

        if not candles:
            return {
                "ok": False, "status": "unavailable", "source_status": "unavailable",
                "symbol": symbol, "timeframe_seconds": timeframe_seconds,
                "signal": "WAIT", "confidence": 0, "price": None,
                "provider": provider_name, "candle_timestamp": None,
                "reason": str(last_error or "No verified market candles available."),
                "candles": [],
            }

        data_status = self._data_status(candles[-1].get("timestamp"), timeframe_seconds)
        if not data_status["fresh"] or not data_status["closed"]:
            reason = (
                "Latest market candle timestamp is in the future."
                if data_status.get("age_seconds", 0) < 0
                else "Verified market data is stale or the latest candle is not closed yet."
            )
            return {
                "ok": True, "status": "ready", "source_status": "verified",
                "symbol": symbol, "timeframe_seconds": timeframe_seconds,
                "signal": "WAIT", "confidence": 0,
                "price": candles[-1].get("close"),
                "provider": provider_name,
                "candle_timestamp": candles[-1].get("timestamp"),
                "data_status": data_status, "reason": reason,
                "trade_decision": "NO TRADE", "candles": candles,
            }

        from analysis.signal_engine import analyze

        payload = {
            "symbol": symbol,
            "timeframe": timeframe_seconds,
            "timeframe_seconds": timeframe_seconds,
            "candles": candles,
            "source_status": "verified",
            "data_source": provider_name,
        }
        result = analyze(payload)
        result.update({
            "ok": True, "status": "ready", "source_status": "verified",
            "provider": provider_name,
            "candle_timestamp": candles[-1].get("timestamp"),
            "data_status": {**result.get("data_status", {}), **data_status},
            "candles": candles,
        })
        return result
