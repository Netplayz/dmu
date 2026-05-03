"""
================================================================
  Dead Man's Uplink — Heartbeat Monitor
  ISS Communication Link Tracker

  Tracks all active uplink/downlink channels:
    - TDRS-East  (S-Band  2.025–2.120 GHz)
    - TDRS-West  (S-Band  2.025–2.120 GHz)
    - TDRS-Z     (Ka-Band 25.25–27.50 GHz)
    - UHF-Alpha  (437.525 MHz — crew EVA backup)
    - Ku-Band    (15.003 GHz — primary data)
    - VHF-Guard  (243.0 MHz — emergency only)

  A link is "alive" if a valid heartbeat packet was received
  within LINK_TIMEOUT_SEC. All links dark → DMU triggers.
================================================================
"""

import time
import threading
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional


class LinkState(Enum):
    NOMINAL   = "NOMINAL"
    DEGRADED  = "DEGRADED"
    LOST      = "LOST"
    BLACKOUT  = "BLACKOUT"   # expected (e.g. orbital geometry)


@dataclass
class CommLink:
    name:         str
    frequency:    str
    band:         str
    priority:     int          # 1 = highest
    timeout_sec:  float
    last_rx:      float = field(default_factory=time.time)
    state:        LinkState = LinkState.NOMINAL
    snr_db:       float = 18.0
    packet_loss:  float = 0.0  # 0.0–1.0
    rx_count:     int   = 0
    drop_count:   int   = 0

    @property
    def age(self) -> float:
        return time.time() - self.last_rx

    @property
    def is_alive(self) -> bool:
        return self.age < self.timeout_sec and self.state != LinkState.LOST

    def touch(self, snr: float = None, loss: float = None):
        self.last_rx    = time.time()
        self.rx_count  += 1
        if snr  is not None: self.snr_db      = snr
        if loss is not None: self.packet_loss = loss
        self._update_state()

    def degrade(self, reason: str = ""):
        self.state = LinkState.DEGRADED
        self.drop_count += 1

    def lose(self):
        self.state = LinkState.LOST

    def _update_state(self):
        age = self.age
        if   age < self.timeout_sec * 0.5:  self.state = LinkState.NOMINAL
        elif age < self.timeout_sec * 0.85: self.state = LinkState.DEGRADED
        else:                               self.state = LinkState.LOST


class HeartbeatMonitor:
    """
    Monitors all ISS communication links.
    Calls on_comms_dark() when ALL links go dead simultaneously.
    Calls on_comms_restored() when any link recovers.
    """

    LINKS: Dict[str, Dict] = {
        "TDRS-E": dict(frequency="2.087 GHz",  band="S-Band",  priority=1, timeout_sec=45),
        "TDRS-W": dict(frequency="2.106 GHz",  band="S-Band",  priority=1, timeout_sec=45),
        "TDRS-Z": dict(frequency="26.55 GHz",  band="Ka-Band", priority=2, timeout_sec=30),
        "KU":     dict(frequency="15.003 GHz", band="Ku-Band", priority=2, timeout_sec=60),
        "UHF-A":  dict(frequency="437.525 MHz",band="UHF",     priority=3, timeout_sec=90),
        "VHF-G":  dict(frequency="243.000 MHz",band="VHF",     priority=4, timeout_sec=300),
    }

    def __init__(self,
                 on_comms_dark:     Callable,
                 on_comms_restored: Callable,
                 on_link_change:    Callable = None):

        self.links: Dict[str, CommLink] = {
            k: CommLink(name=k, **v) for k, v in self.LINKS.items()
        }
        self.on_comms_dark     = on_comms_dark
        self.on_comms_restored = on_comms_restored
        self.on_link_change    = on_link_change or (lambda *a: None)

        self._dark       = False
        self._lock       = threading.Lock()
        self._thread     = threading.Thread(target=self._poll, daemon=True)
        self._running    = True
        self._thread.start()

    # ----------------------------------------------------------------
    def receive_heartbeat(self, link_name: str, snr: float = None, loss: float = None):
        """Called by the comms driver when a valid packet arrives."""
        with self._lock:
            if link_name not in self.links:
                return
            prev = self.links[link_name].state
            self.links[link_name].touch(snr, loss)
            new = self.links[link_name].state
            if prev != new:
                self.on_link_change(link_name, prev, new)

    def simulate_drop(self, link_name: str):
        """Force a link to LOST (for testing)."""
        with self._lock:
            if link_name in self.links:
                lnk = self.links[link_name]
                # Set last_rx far in the past so _update_state() also sees LOST
                lnk.last_rx = time.time() - lnk.timeout_sec * 2
                lnk.lose()

    def simulate_restore(self, link_name: str):
        """Restore a link (for testing)."""
        with self._lock:
            if link_name in self.links:
                self.links[link_name].touch(snr=16.0, loss=0.01)

    # ----------------------------------------------------------------
    @property
    def any_alive(self) -> bool:
        return any(l.is_alive for l in self.links.values())

    @property
    def all_lost(self) -> bool:
        return not self.any_alive

    def link_status(self) -> Dict[str, dict]:
        with self._lock:
            return {
                k: {
                    "state":    v.state.value,
                    "age_sec":  round(v.age, 1),
                    "snr_db":   round(v.snr_db, 1),
                    "pkt_loss": round(v.packet_loss * 100, 1),
                    "rx":       v.rx_count,
                    "dropped":  v.drop_count,
                }
                for k, v in self.links.items()
            }

    # ----------------------------------------------------------------
    def _poll(self):
        """Background thread — checks link health every second."""
        while self._running:
            time.sleep(1.0)
            with self._lock:
                for link in self.links.values():
                    link._update_state()
                dark_now = not self.any_alive

            if dark_now and not self._dark:
                self._dark = True
                self.on_comms_dark()
            elif not dark_now and self._dark:
                self._dark = False
                alive = [k for k, v in self.links.items() if v.is_alive]
                self.on_comms_restored(alive)

    def shutdown(self):
        self._running = False
