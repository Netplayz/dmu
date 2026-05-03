"""
================================================================
  Dead Man's Uplink — Timer Engine
  Multi-Stage Countdown to Autonomous Action

  Thresholds (configurable):
    T+0:00  Comms lost detected
    T+2:00  ALERT-1  Log, crew notification
    T+5:00  ALERT-2  Begin diagnostic cascade
    T+8:00  ALERT-3  Safe mode pre-arm
    T+12:00 TRIGGER  Safe mode execute + distress burst

  Timer resets instantly on any heartbeat restore.
  Each stage fires exactly once and logs to the event ledger.
================================================================
"""

import time
import threading
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Callable, List, Optional


class TimerStage(Enum):
    STANDBY      = auto()   # nominal — comms alive
    ALERT_1      = auto()   # 2 min dark
    ALERT_2      = auto()   # 5 min dark
    ALERT_3      = auto()   # 8 min dark
    TRIGGER      = auto()   # 12 min dark — EXECUTE
    ABORTED      = auto()   # comms restored before trigger


@dataclass
class StageEvent:
    stage:      TimerStage
    elapsed_s:  float
    timestamp:  float = field(default_factory=time.time)
    note:       str   = ""


# Thresholds in seconds
STAGE_THRESHOLDS = {
    TimerStage.ALERT_1: 120,
    TimerStage.ALERT_2: 300,
    TimerStage.ALERT_3: 480,
    TimerStage.TRIGGER: 720,
}


class DeadMansTimer:
    """
    Starts counting the moment comms go dark.
    Fires stage callbacks at configured thresholds.
    Resets completely if comms are restored before TRIGGER.
    """

    def __init__(self,
                 on_alert_1: Callable,
                 on_alert_2: Callable,
                 on_alert_3: Callable,
                 on_trigger: Callable,
                 on_abort:   Callable,
                 thresholds: dict = None):

        self.callbacks = {
            TimerStage.ALERT_1: on_alert_1,
            TimerStage.ALERT_2: on_alert_2,
            TimerStage.ALERT_3: on_alert_3,
            TimerStage.TRIGGER: on_trigger,
            TimerStage.ABORTED: on_abort,
        }
        self.thresholds    = thresholds or STAGE_THRESHOLDS
        self.stage         = TimerStage.STANDBY
        self.dark_since: Optional[float] = None
        self.fired_stages: List[TimerStage] = []
        self.event_log:    List[StageEvent]  = []
        self._lock         = threading.Lock()
        self._thread       = threading.Thread(target=self._run, daemon=True)
        self._running      = True
        self._thread.start()

    # ----------------------------------------------------------------
    def start(self):
        """Comms just went dark — begin countdown."""
        with self._lock:
            if self.dark_since is not None:
                return  # already counting
            self.dark_since   = time.time()
            self.fired_stages = []
            self.stage        = TimerStage.STANDBY
            self._log(TimerStage.STANDBY, 0, "Countdown started — all links dark")
            print(f"[DMU-TIMER] ⚠  Countdown started  T+00:00")

    def reset(self, restored_links: List[str]):
        """Comms restored — abort and reset."""
        with self._lock:
            if self.dark_since is None:
                return
            elapsed = time.time() - self.dark_since
            self.dark_since   = None
            prev_stage        = self.stage
            self.stage        = TimerStage.ABORTED
            self._log(TimerStage.ABORTED, elapsed,
                      f"Restored via {', '.join(restored_links)}")
            print(f"[DMU-TIMER] ✓  Comms restored after "
                  f"{self._fmt(elapsed)} — ABORT (was {prev_stage.name})")

        self.callbacks[TimerStage.ABORTED](elapsed, restored_links)

        with self._lock:
            self.stage        = TimerStage.STANDBY
            self.fired_stages = []

    # ----------------------------------------------------------------
    @property
    def elapsed(self) -> float:
        with self._lock:
            if self.dark_since is None:
                return 0.0
            return time.time() - self.dark_since

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self.dark_since is not None

    @property
    def time_to_trigger(self) -> float:
        return max(0.0, self.thresholds[TimerStage.TRIGGER] - self.elapsed)

    def status(self) -> dict:
        return {
            "stage":            self.stage.name,
            "elapsed_s":        round(self.elapsed, 1),
            "time_to_trigger_s": round(self.time_to_trigger, 1),
            "is_active":        self.is_active,
            "events":           [(e.stage.name, round(e.elapsed_s,1), e.note)
                                 for e in self.event_log],
        }

    # ----------------------------------------------------------------
    def _run(self):
        ordered = sorted(self.thresholds.items(), key=lambda x: x[1])
        while self._running:
            time.sleep(0.5)
            with self._lock:
                if self.dark_since is None:
                    continue
                elapsed = time.time() - self.dark_since

            for stage, threshold in ordered:
                if elapsed >= threshold and stage not in self.fired_stages:
                    with self._lock:
                        self.fired_stages.append(stage)
                        self.stage = stage
                        self._log(stage, elapsed,
                                  f"Threshold T+{self._fmt(threshold)} reached")
                    print(f"[DMU-TIMER] ◆ STAGE {stage.name}  "
                          f"elapsed={self._fmt(elapsed)}")
                    self.callbacks[stage](elapsed)

    def _log(self, stage: TimerStage, elapsed: float, note: str = ""):
        self.event_log.append(StageEvent(stage, elapsed, time.time(), note))

    @staticmethod
    def _fmt(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def shutdown(self):
        self._running = False
