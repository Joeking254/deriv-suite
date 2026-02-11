from datetime import datetime, timezone


class RiskManager:
    def __init__(self, max_daily_loss: float, max_consecutive_losses: int):
        self.max_daily_loss = max_daily_loss
        self.max_consecutive_losses = max_consecutive_losses
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.day = self._today()

    def _today(self):
        return datetime.now(timezone.utc).date()

    def _roll_day(self):
        today = self._today()
        if today != self.day:
            self.day = today
            self.daily_pnl = 0.0
            self.consecutive_losses = 0

    def can_trade(self) -> bool:
        self._roll_day()
        if self.max_daily_loss > 0 and self.daily_pnl <= -self.max_daily_loss:
            return False
        if self.max_consecutive_losses > 0 and self.consecutive_losses >= self.max_consecutive_losses:
            return False
        return True

    def record_trade(self, profit: float) -> None:
        self._roll_day()
        self.daily_pnl += profit
        if profit < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
