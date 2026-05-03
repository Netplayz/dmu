"""
================================================================
  Dead Man's Uplink — Safe Mode Engine + Diagnostics
  Autonomous ISS System Response

  Safe Mode Stages (NASA-inspired):
    SAFE-1  Non-essential systems shed (payload experiments off)
    SAFE-2  Attitude control to sun-pointing (power positive)
    SAFE-3  Life support to standalone autonomous mode
    SAFE-4  Communications hardware reset + antenna slew

  Diagnostics runs in parallel, checking:
    - Power (solar array current, battery SoC)
    - Thermal (node temps, radiator output)
    - Life support (O₂ ppm, CO₂ scrubber, cabin pressure)
    - Attitude (gyroscope health, CMG torque availability)
    - Software (watchdog, memory ECC counters, process health)
================================================================
"""

import time
import random
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Callable, Tuple


# ----------------------------------------------------------------
#  Diagnostic Framework
# ----------------------------------------------------------------

class CheckStatus(Enum):
    PASS    = "PASS"
    WARN    = "WARN"
    FAIL    = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass
class DiagnosticResult:
    subsystem:   str
    check:       str
    status:      CheckStatus
    value:       str
    note:        str = ""
    timestamp:   float = field(default_factory=time.time)


def _check(subsystem: str, check: str,
           value_fn: Callable[[], Tuple[str, CheckStatus, str]]) -> DiagnosticResult:
    try:
        val, status, note = value_fn()
    except Exception as e:
        val, status, note = "ERROR", CheckStatus.FAIL, str(e)
    return DiagnosticResult(subsystem, check, status, val, note)


def run_diagnostics() -> List[DiagnosticResult]:
    """
    Runs all diagnostic checks.
    In production: reads telemetry from MDM (Multiplexer/Demultiplexer).
    Here: simulated with realistic ISS operating ranges.
    """
    results = []

    # ── Power ────────────────────────────────────────────────────
    def _solar_current():
        amps = random.uniform(130.0, 165.0)
        s = CheckStatus.PASS if amps > 140 else CheckStatus.WARN
        return f"{amps:.1f} A", s, "8-channel SARJ nominal" if s == CheckStatus.PASS else "Reduced insolation"

    def _battery_soc():
        soc = random.uniform(78.0, 98.0)
        s = CheckStatus.PASS if soc > 50 else (CheckStatus.WARN if soc > 30 else CheckStatus.FAIL)
        return f"{soc:.1f}%", s, ""

    results += [
        _check("POWER", "Solar Array Current",   _solar_current),
        _check("POWER", "Battery State-of-Charge", _battery_soc),
        _check("POWER", "Main Bus Voltage", lambda: (
            f"{random.uniform(126.0, 130.0):.1f} V", CheckStatus.PASS, "28V bus nominal")),
    ]

    # ── Thermal ──────────────────────────────────────────────────
    def _node_temps():
        temps = [random.uniform(19.0, 27.0) for _ in range(3)]
        ok    = all(18 < t < 29 for t in temps)
        t_str = ", ".join(f"{t:.1f}°C" for t in temps)
        return t_str, CheckStatus.PASS if ok else CheckStatus.WARN, "Nodes 1/2/3"

    results += [
        _check("THERMAL", "Node Temperatures",     _node_temps),
        _check("THERMAL", "Radiator Output",  lambda: (
            f"{random.uniform(68.0, 84.0):.1f} kW",  CheckStatus.PASS, "")),
        _check("THERMAL", "ITCS Loop A/B",    lambda: (
            "NOMINAL", CheckStatus.PASS, "Internal Thermal Control System")),
    ]

    # ── Life Support ─────────────────────────────────────────────
    def _co2():
        ppm = random.uniform(3.2, 5.8)
        s   = CheckStatus.PASS if ppm < 5.3 else CheckStatus.WARN
        return f"{ppm:.2f} mmHg", s, "CDRA active" if s == CheckStatus.PASS else "CDRA cycle slow"

    def _cabin_pressure():
        psi = random.uniform(14.4, 14.9)
        s   = CheckStatus.PASS if 14.2 < psi < 15.0 else CheckStatus.FAIL
        return f"{psi:.2f} psi", s, ""

    results += [
        _check("ECLSS",   "O₂ Partial Pressure",  lambda: (
            f"{random.uniform(20.5, 21.1):.1f} kPa", CheckStatus.PASS, "OGA nominal")),
        _check("ECLSS",   "CO₂ Level",             _co2),
        _check("ECLSS",   "Cabin Pressure",         _cabin_pressure),
        _check("ECLSS",   "Water Recovery System", lambda: (
            f"{random.uniform(90.0,96.0):.1f}%", CheckStatus.PASS, "WRS-2 recovery rate")),
    ]

    # ── Attitude Control ─────────────────────────────────────────
    def _cmg():
        rpm   = random.uniform(6560.0, 6600.0)
        alive = random.random() > 0.05   # 5% chance one CMG is off
        s     = CheckStatus.PASS if alive else CheckStatus.WARN
        return f"{rpm:.0f} RPM", s, "4 CMGs" if alive else "CMG-3 offline, 3-CMG config"

    results += [
        _check("ADCS", "Control Moment Gyros",  _cmg),
        _check("ADCS", "Star Tracker",          lambda: (
            "LOCKED", CheckStatus.PASS, "2/2 trackers nominal")),
        _check("ADCS", "Attitude Error",        lambda: (
            f"{random.uniform(0.01, 0.12):.3f}°", CheckStatus.PASS, "")),
    ]

    # ── Software / Compute ───────────────────────────────────────
    def _ecc():
        singlebit = random.randint(0, 12)
        multibit  = random.randint(0, 1)
        s = CheckStatus.PASS if multibit == 0 else CheckStatus.WARN
        return f"SBE={singlebit} MBE={multibit}", s, "Since last scrub"

    results += [
        _check("SOFTWARE", "Memory ECC Counters",  _ecc),
        _check("SOFTWARE", "Watchdog Heartbeats",  lambda: (
            "NOMINAL", CheckStatus.PASS, "All 14 processes reporting")),
        _check("SOFTWARE", "Uptime",               lambda: (
            f"{random.randint(180,600)} days", CheckStatus.PASS, "")),
    ]

    return results


def print_diagnostics(results: List[DiagnosticResult]):
    icons = {CheckStatus.PASS: "✓", CheckStatus.WARN: "⚠", CheckStatus.FAIL: "✗", CheckStatus.UNKNOWN: "?"}
    print("\n┌─── AUTOMATED DIAGNOSTICS ─────────────────────────────────┐")
    subsys = None
    for r in results:
        if r.subsystem != subsys:
            subsys = r.subsystem
            print(f"│  [{subsys}]")
        note = f"  — {r.note}" if r.note else ""
        print(f"│    {icons[r.status]} {r.check:<28} {r.value:<16} {note}")
    fails = sum(1 for r in results if r.status == CheckStatus.FAIL)
    warns = sum(1 for r in results if r.status == CheckStatus.WARN)
    print(f"└─── PASS={len(results)-fails-warns}  WARN={warns}  FAIL={fails} {'── ALL NOMINAL ──' if fails==0 and warns==0 else ''} ─┘\n")


# ----------------------------------------------------------------
#  Safe Mode Engine
# ----------------------------------------------------------------

class SafeModeStage(Enum):
    IDLE    = auto()
    SAFE_1  = auto()   # Shed non-essential loads
    SAFE_2  = auto()   # Sun-pointing attitude
    SAFE_3  = auto()   # ECLSS standalone
    SAFE_4  = auto()   # Comms hardware reset
    COMPLETE= auto()


@dataclass
class SafeModeAction:
    name:        str
    description: str
    stage:       SafeModeStage
    duration_s:  float = 2.0
    completed:   bool  = False
    timestamp:   float = 0.0
    success:     bool  = True


class SafeModeEngine:
    """
    Executes ISS safe-mode procedures autonomously.
    Modeled on NASA's Power-Down Sequence and Safe Haven protocol.
    """

    PROCEDURES: List[dict] = [
        # SAFE-1 — Non-essential loads
        dict(name="Payload Power Off",   description="Commanding EXPRESS racks to standby",
             stage=SafeModeStage.SAFE_1, duration_s=3.0),
        dict(name="Robotic Arm Park",    description="Parking SSRMS to 'canbus' configuration",
             stage=SafeModeStage.SAFE_1, duration_s=2.0),
        dict(name="Crew Notification",   description="Activating crew PA + caution/warning tones",
             stage=SafeModeStage.SAFE_1, duration_s=1.0),
        dict(name="Lighting Reduction",  description="Dimming non-essential module lighting 50%",
             stage=SafeModeStage.SAFE_1, duration_s=1.0),

        # SAFE-2 — Attitude
        dict(name="Attitude Mode Switch",description="Commanding LVLH → Sun-pointing (XPOP)",
             stage=SafeModeStage.SAFE_2, duration_s=4.0),
        dict(name="Solar Array Tracking",description="Releasing SARJ to free-drift sun acquisition",
             stage=SafeModeStage.SAFE_2, duration_s=3.0),

        # SAFE-3 — Life Support
        dict(name="ECLSS Autonomous",    description="Switching ECLSS to standalone loop control",
             stage=SafeModeStage.SAFE_3, duration_s=2.5),
        dict(name="Fire Suppress Arm",   description="Arming automatic fire detection/suppression",
             stage=SafeModeStage.SAFE_3, duration_s=1.0),
        dict(name="O₂ Reserve Valve",    description="Opening O₂ reserve manifold valve",
             stage=SafeModeStage.SAFE_3, duration_s=1.5),

        # SAFE-4 — Comms hardware reset
        dict(name="Antenna Slew",        description="Slewing SGANT to TDRS acquisition search",
             stage=SafeModeStage.SAFE_4, duration_s=5.0),
        dict(name="Transponder Reset",   description="Cold-rebooting S-Band and Ku-Band transponders",
             stage=SafeModeStage.SAFE_4, duration_s=4.0),
        dict(name="UHF Beacon Enable",   description="Enabling continuous UHF carrier beacon",
             stage=SafeModeStage.SAFE_4, duration_s=1.0),
        dict(name="CCSDS Beacon Tx",     description="Transmitting distress CCSDS packet on all bands",
             stage=SafeModeStage.SAFE_4, duration_s=2.0),
    ]

    def __init__(self, on_complete: Callable = None):
        self.on_complete = on_complete or (lambda actions: None)
        self.actions: List[SafeModeAction] = [
            SafeModeAction(**p) for p in self.PROCEDURES
        ]
        self.stage   = SafeModeStage.IDLE
        self._thread: threading.Thread = None
        self._abort  = False

    def execute(self):
        """Begin safe mode execution in a background thread."""
        if self._thread and self._thread.is_alive():
            print("[SAFE-MODE] Already executing.")
            return
        self._abort  = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def abort(self):
        """Comms restored — abort remaining procedures."""
        self._abort = True
        print("[SAFE-MODE] Abort signal received — halting remaining procedures.")

    def _run(self):
        print("\n╔══════════════════════════════════════════════════╗")
        print("║       ISS AUTONOMOUS SAFE MODE — EXECUTING       ║")
        print("╚══════════════════════════════════════════════════╝")

        current_stage = None
        for action in self.actions:
            if self._abort:
                print("[SAFE-MODE] Aborted during execution.")
                break

            if action.stage != current_stage:
                current_stage = action.stage
                print(f"\n  ── {current_stage.name} ──────────────────────")

            print(f"  → {action.name}")
            print(f"     {action.description}")
            time.sleep(action.duration_s)

            action.completed = True
            action.timestamp = time.time()
            print(f"     ✓ Done")

        if not self._abort:
            self.stage = SafeModeStage.COMPLETE
            completed  = [a for a in self.actions if a.completed]
            print(f"\n[SAFE-MODE] ✓ Complete — {len(completed)}/{len(self.actions)} procedures executed.")
            self.on_complete(completed)
        else:
            passed = [a for a in self.actions if a.completed]
            print(f"[SAFE-MODE] Aborted — {len(passed)}/{len(self.actions)} procedures executed before abort.")

    def health_summary(self) -> dict:
        """Returns dict suitable for embedding in distress packet."""
        results = run_diagnostics()
        return {
            r.subsystem + "." + r.check.replace(" ", "_"): r.status.value
            for r in results
        }
