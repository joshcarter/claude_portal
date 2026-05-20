from dataclasses import dataclass
from typing import Optional


@dataclass
class Snapshot:
    five_hour_pct: Optional[float] = None
    five_hour_resets_at: Optional[int] = None
    seven_day_pct: Optional[float] = None
    seven_day_resets_at: Optional[int] = None
    seven_day_opus_pct: Optional[float] = None
    seven_day_opus_resets_at: Optional[int] = None
    burn_rate: float = 0.0
    projected_full_at: Optional[int] = None
    stale: bool = True
    auth_failed: bool = False
    last_update: int = 0


snapshot = Snapshot()
org_id: Optional[str] = None
