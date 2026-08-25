from typing import Any


class FallbackMarketDataProvider:
    """Try configured market-data providers in order."""

    def __init__(self, providers):
        self.providers = providers or []
        self.last_selection: dict[str, Any] = {}

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        last_error = None

        for provider in self.providers:
            try:
                result = await provider.get_quote(symbol)
                self.last_selection = {"provider": getattr(provider, "NAME", getattr(provider, "name", result.get("provider", provider.__class__.__name__))), "source_status": result.get("source_status", "verified")}
                return result
            except Exception as exc:
                last_error = exc

        if last_error:
            raise RuntimeError(
                f"All market-data providers failed: {last_error}"
            )

        raise RuntimeError("No market-data providers configured")

    @staticmethod
    def _validate_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candles:
            raise RuntimeError("Provider returned no candles.")
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        for candle in candles:
            if not isinstance(candle, dict):
                raise RuntimeError("Provider returned malformed candle data.")
            try:
                values = [float(candle[key]) for key in ("open", "high", "low", "close")]
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("Provider returned malformed candle data.") from exc
            if not all(value == value and abs(value) != float("inf") for value in values):
                raise RuntimeError("Provider returned invalid candle prices.")
            if values[2] > values[1] or not values[2] <= values[0] <= values[1] or not values[2] <= values[3] <= values[1]:
                raise RuntimeError("Provider returned malformed candle OHLC data.")
            timestamp = candle.get("timestamp")
            if timestamp:
                try:
                    observed = __import__("datetime").datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                    if observed.tzinfo is None:
                        observed = observed.replace(tzinfo=__import__("datetime").timezone.utc)
                    if observed.astimezone(__import__("datetime").timezone.utc) > now + __import__("datetime").timedelta(seconds=60):
                        raise RuntimeError("Provider returned a future candle timestamp.")
                except RuntimeError:
                    raise
                except (TypeError, ValueError, OverflowError) as exc:
                    raise RuntimeError("Provider returned an invalid candle timestamp.") from exc
        return candles

    async def get_candles(
        self,
        symbol: str,
        timeframe_seconds: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        last_error = None

        for provider in self.providers:
            try:
                result = await provider.get_candles(
                    symbol,
                    timeframe_seconds,
                    limit,
                )
                result = self._validate_candles(result)
                self.last_selection = {"provider": getattr(provider, "NAME", getattr(provider, "name", provider.__class__.__name__)), "source_status": "verified"}
                return result
            except Exception as exc:
                last_error = exc

        if last_error:
            raise RuntimeError(
                f"All market-data providers failed: {last_error}"
            )

        raise RuntimeError("No market-data providers configured")

    async def health_check(self) -> dict[str, Any]:
        for provider in self.providers:
            try:
                result = await provider.health_check()
                if result:
                    return result
            except Exception:
                continue

        return {
            "ok": False,
            "provider": "fallback",
            "message": "No configured market-data provider is available",
        }
