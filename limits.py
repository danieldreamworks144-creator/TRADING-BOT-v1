"""Risk limits: configurable caps that PAUSE trading when hit.

Limits (all configurable, all enforced on SIMULATED activity):
  max_position_usd      -- largest single simulated position
  max_daily_loss_usd    -- max simulated loss per day before auto-pause
  max_open_positions    -- how many simulated positions may be open at once
  max_total_exposure_usd-- max combined value of open simulated positions

When any limit trips, `RiskLimits.allow(...)` returns (False, reason) and the
engine pauses. A human must call `reset_day()` / adjust limits to resume --
limits do not silently clear themselves."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class RiskLimits:
    max_position_usd: float = 500.0
    max_daily_loss_usd: float = 100.0
    max_open_positions: int = 5
    max_total_exposure_usd: float = 1500.0
    # -- state ----------------------------------------------------------
    paused: bool = False
    pause_reason: str = ""
    day: str = field(default_factory=lambda: datetime.now(timezone.utc).date().isoformat())
    day_realized_pnl: float = 0.0

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self.day:
            self.day = today
            self.day_realized_pnl = 0.0

    def record_pnl(self, pnl: float) -> None:
        """Record realized P&L; auto-pause when the daily loss limit trips."""
        self._roll_day()
        self.day_realized_pnl += pnl
        if self.day_realized_pnl <= -abs(self.max_daily_loss_usd):
            self.trip(f"daily loss limit reached ({self.day_realized_pnl:+.2f} USD "
                      f"<= -{self.max_daily_loss_usd:.2f})")

    def trip(self, reason: str) -> None:
        self.paused = True
        self.pause_reason = reason

    def resume(self) -> None:
        self.paused = False
        self.pause_reason = ""
        self._roll_day()

    def allow(self, *, position_usd: float, open_positions: int,
              total_exposure_usd: float) -> tuple[bool, str]:
        """Pre-trade check. Returns (allowed, reason)."""
        self._roll_day()
        if self.paused:
            return False, f"paused: {self.pause_reason or 'risk limit tripped'}"
        if position_usd > self.max_position_usd:
            return False, (f"position {position_usd:.2f} USD exceeds "
                           f"max_position_usd={self.max_position_usd:.2f}")
        if open_positions + 1 > self.max_open_positions:
            return False, (f"would open position #{open_positions + 1} but "
                           f"max_open_positions={self.max_open_positions}")
        if total_exposure_usd + position_usd > self.max_total_exposure_usd:
            return False, (f"exposure would hit "
                           f"{total_exposure_usd + position_usd:.2f} USD > "
                           f"max_total_exposure_usd={self.max_total_exposure_usd:.2f}")
        if self.day_realized_pnl <= -abs(self.max_daily_loss_usd):
            self.trip(f"daily loss limit reached ({self.day_realized_pnl:+.2f} USD)")
            return False, f"paused: {self.pause_reason}"
        return True, "ok"

    def as_dict(self) -> dict:
        return {
            "max_position_usd": self.max_position_usd,
            "max_daily_loss_usd": self.max_daily_loss_usd,
            "max_open_positions": self.max_open_positions,
            "max_total_exposure_usd": self.max_total_exposure_usd,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "day": self.day,
            "day_realized_pnl": self.day_realized_pnl,
        }