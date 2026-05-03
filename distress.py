"""
================================================================
  Dead Man's Uplink — Distress Transmitter
  Multi-Frequency Emergency Burst

  On trigger, broadcasts simultaneously on:
    - S-Band  (2.025 GHz) — NASA TDRS primary uplink
    - UHF     (437.525 MHz) — crew/EVA emergency
    - VHF     (243.000 MHz) — international guard channel
    - L-Band  (1.544 GHz) — Cospas-Sarsat EPIRB equivalent
    - Ka-Band (26.550 GHz) — TDRS high-rate backup

  Each burst contains a signed CCSDS distress packet:
    - ISS identifier
    - Orbital TLE snapshot
    - System health summary
    - Last known ground contact timestamp
    - Timestamp + sequence number
================================================================
"""

import time
import json
import hashlib
import base64
import threading
from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class DistressBurst:
    frequency:   str
    band:        str
    power_watts: float
    packet:      dict
    sent_at:     float = field(default_factory=time.time)
    ack_received: bool = False
    retries:     int   = 0


@dataclass
class CCSDSDistressPacket:
    """
    Simplified CCSDS (Consultative Committee for Space Data Systems)
    distress packet structure — used by NASA/ESA spacecraft.
    """
    spacecraft_id:       str   = "ISS-ZARYA-00001"
    packet_version:      int   = 1
    sequence_number:     int   = 0
    timestamp_utc:       float = field(default_factory=time.time)
    last_contact_utc:    float = 0.0
    dark_duration_sec:   float = 0.0
    orbital_altitude_km: float = 408.0
    orbital_inclination: float = 51.6
    orbital_period_min:  float = 92.68
    crew_count:          int   = 7
    o2_partial_pressure: float = 20.9   # kPa
    co2_ppm:             float = 0.38
    cabin_temp_c:        float = 21.5
    power_nominal:       bool  = True
    attitude_nominal:    bool  = True
    safe_mode_active:    bool  = False
    diagnostics_summary: dict  = field(default_factory=dict)
    checksum:            str   = ""

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        # compute checksum over content
        payload = json.dumps({k: v for k, v in d.items() if k != "checksum"},
                             sort_keys=True).encode()
        d["checksum"] = hashlib.sha256(payload).hexdigest()[:16]
        return d

    def to_bytes(self) -> bytes:
        return json.dumps(self.to_dict()).encode("utf-8")

    def to_base64(self) -> str:
        return base64.b64encode(self.to_bytes()).decode("ascii")


class DistressTransmitter:
    """
    Fires distress bursts on all available frequencies.
    Retries every RETRY_INTERVAL_SEC until ACK or shutdown.
    """

    CHANNELS = [
        dict(frequency="2.025 GHz",  band="S-Band",  power_watts=40.0),
        dict(frequency="437.525 MHz",band="UHF",     power_watts=5.0),
        dict(frequency="243.000 MHz",band="VHF",     power_watts=10.0),
        dict(frequency="1.544 GHz",  band="L-Band",  power_watts=25.0),
        dict(frequency="26.550 GHz", band="Ka-Band", power_watts=100.0),
    ]

    RETRY_INTERVAL_SEC = 30
    MAX_RETRIES        = 20   # ~10 minutes of retries

    def __init__(self, on_ack: callable = None):
        self.on_ack    = on_ack or (lambda burst: None)
        self.bursts:   List[DistressBurst] = []
        self.seq       = 0
        self._lock     = threading.Lock()
        self._running  = False
        self._thread:  threading.Thread = None

    def fire(self, dark_duration_sec: float, health_summary: dict,
             last_contact_utc: float):
        """
        Immediately broadcast distress on all channels.
        Starts retry loop in background.
        """
        self.seq += 1
        packet = CCSDSDistressPacket(
            sequence_number    = self.seq,
            timestamp_utc      = time.time(),
            last_contact_utc   = last_contact_utc,
            dark_duration_sec  = dark_duration_sec,
            safe_mode_active   = True,
            diagnostics_summary= health_summary,
        )

        print("\n" + "═" * 60)
        print("  ██████  DISTRESS TRANSMITTER ACTIVATED  ██████")
        print("═" * 60)
        print(f"  Spacecraft : {packet.spacecraft_id}")
        print(f"  Seq #      : {packet.sequence_number}")
        print(f"  Dark Time  : {dark_duration_sec:.1f}s")
        print(f"  Checksum   : {packet.to_dict()['checksum']}")
        print("═" * 60)

        for ch in self.CHANNELS:
            burst = DistressBurst(
                frequency  = ch["frequency"],
                band       = ch["band"],
                power_watts= ch["power_watts"],
                packet     = packet.to_dict(),
            )
            self._transmit(burst)
            with self._lock:
                self.bursts.append(burst)

        self._running = True
        self._thread  = threading.Thread(target=self._retry_loop, daemon=True)
        self._thread.start()

    def _transmit(self, burst: DistressBurst):
        """Simulate RF transmission — replace with actual driver call."""
        burst.retries += 1
        print(f"  [TX] {burst.band:<8} {burst.frequency:<12} "
              f"{burst.power_watts:>6.1f}W  "
              f"retry={burst.retries}  "
              f"{'✓ ACK' if burst.ack_received else '…'}")
        # In real hardware: write packet to RF driver FIFO here

    def _retry_loop(self):
        """Re-transmit until ACK or MAX_RETRIES."""
        while self._running:
            time.sleep(self.RETRY_INTERVAL_SEC)
            with self._lock:
                pending = [b for b in self.bursts
                           if not b.ack_received and b.retries < self.MAX_RETRIES]

            if not pending:
                print("[TX] All channels exhausted or acknowledged.")
                break

            print(f"\n[TX] Retry burst ({len(pending)} channel(s) pending)...")
            for burst in pending:
                self._transmit(burst)

    def acknowledge(self, band: str):
        """Call this when ground sends ACK on a specific band."""
        with self._lock:
            for burst in self.bursts:
                if burst.band == band and not burst.ack_received:
                    burst.ack_received = True
                    print(f"[TX] ✓ ACK received on {band}")
                    self.on_ack(burst)

        if all(b.ack_received for b in self.bursts):
            self._running = False

    def stop(self):
        self._running = False

    def status(self) -> List[dict]:
        with self._lock:
            return [
                {
                    "band":      b.band,
                    "frequency": b.frequency,
                    "retries":   b.retries,
                    "ack":       b.ack_received,
                    "sent_at":   b.sent_at,
                }
                for b in self.bursts
            ]
