"""
================================================================
  Dead Man's Uplink (DMU) — Master Orchestrator
  ISS Autonomous Comms Loss Response System

  NetByte  v1.0

  Usage:
    python3 main.py [--demo]   Run with auto scenario
    python3 main.py            Interactive mode

  Architecture:
    HeartbeatMonitor  ──→ DeadMansTimer ──→ SafeModeEngine
                                        ──→ DistressTransmitter
                                        ──→ DiagnosticsEngine
================================================================
"""

import sys
import time
import threading
import argparse
import signal
from datetime import datetime

from heartbeat  import HeartbeatMonitor
from timer      import DeadMansTimer
from distress   import DistressTransmitter
from safemode   import SafeModeEngine, run_diagnostics, print_diagnostics


def ts() -> str:
    return datetime.utcnow().strftime("%H:%M:%S UTC")


BANNER = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║        💀  DEAD MAN'S UPLINK  —  DMU v1.0                   ║
║                                                              ║
║   ISS Autonomous Communications Loss Response System         ║
║   NetByte                                 ║
║                                                              ║
║   "If the silence lasts too long — we speak for ourselves."  ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""


class DeadMansUplink:

    def __init__(self, fast_mode: bool = False):
        """
        fast_mode: compress timeouts to seconds for demo purposes.
        In real deployment fast_mode=False and STAGE_THRESHOLDS govern timing.
        """
        self.fast_mode     = fast_mode
        self._running      = True
        self._last_contact = time.time()
        self._triggered    = False

        # ── Thresholds ──────────────────────────────────────────
        if fast_mode:
            from timer import TimerStage
            thresholds = {
                TimerStage.ALERT_1: 5,
                TimerStage.ALERT_2: 10,
                TimerStage.ALERT_3: 16,
                TimerStage.TRIGGER: 22,
            }
        else:
            thresholds = None   # use defaults (minutes-scale)

        # ── Sub-systems ─────────────────────────────────────────
        self.transmitter = DistressTransmitter(on_ack=self._on_ack)

        self.safe_mode   = SafeModeEngine(on_complete=self._on_safe_complete)

        self.timer       = DeadMansTimer(
            on_alert_1 = self._alert_1,
            on_alert_2 = self._alert_2,
            on_alert_3 = self._alert_3,
            on_trigger = self._trigger,
            on_abort   = self._abort,
            thresholds = thresholds,
        )

        self.monitor     = HeartbeatMonitor(
            on_comms_dark     = self._comms_dark,
            on_comms_restored = self._comms_restored,
            on_link_change    = self._link_changed,
        )

    # ═══════════════════════════════════════════════════════════
    #  HeartbeatMonitor callbacks
    # ═══════════════════════════════════════════════════════════

    def _comms_dark(self):
        self._last_contact = time.time()
        print(f"\n{'▓'*62}")
        print(f"  [{ts()}] ⚠  ALL LINKS DARK — DMU COUNTDOWN INITIATED")
        print(f"{'▓'*62}")
        self.timer.start()

    def _comms_restored(self, links):
        print(f"\n  [{ts()}] ✓  COMMS RESTORED: {', '.join(links)}")
        self.timer.reset(links)
        if self._triggered:
            self.safe_mode.abort()
            self.transmitter.stop()
            print(f"  [{ts()}] Safe mode aborted — nominal ops resuming.")
            self._triggered = False

    def _link_changed(self, link, prev, new):
        print(f"  [{ts()}] Link {link:<8}: {prev.value} → {new.value}")

    # ═══════════════════════════════════════════════════════════
    #  Timer stage callbacks
    # ═══════════════════════════════════════════════════════════

    def _alert_1(self, elapsed: float):
        print(f"\n  [{ts()}] ▲ ALERT-1 — All links dark for {elapsed:.0f}s")
        print(f"             Crew notification issued. Ground standing by.")
        # In real system: ring crew PA, page ground FLIGHT controller

    def _alert_2(self, elapsed: float):
        print(f"\n  [{ts()}] ▲ ALERT-2 — {elapsed:.0f}s dark — Running diagnostics...")
        results = run_diagnostics()
        print_diagnostics(results)
        self._diag_summary = {
            "pass": sum(1 for r in results if r.status.value == "PASS"),
            "warn": sum(1 for r in results if r.status.value == "WARN"),
            "fail": sum(1 for r in results if r.status.value == "FAIL"),
        }

    def _alert_3(self, elapsed: float):
        print(f"\n  [{ts()}] ▲ ALERT-3 — {elapsed:.0f}s dark — PRE-ARMING safe mode")
        print(f"             {'─'*52}")
        print(f"             Safe mode will execute in "
              f"{self.timer.time_to_trigger:.0f}s unless comms restored.")
        print(f"             Verify crew is strapped in.")
        print(f"             {'─'*52}")

    def _trigger(self, elapsed: float):
        self._triggered = True
        print(f"\n  [{ts()}] ◆ TRIGGER — {elapsed:.0f}s dark — EXECUTING AUTONOMOUS RESPONSE")

        # Collect health data
        health = self.safe_mode.health_summary()

        # Fire safe mode procedures
        self.safe_mode.execute()

        # Broadcast distress on all frequencies
        self.transmitter.fire(
            dark_duration_sec = elapsed,
            health_summary    = health,
            last_contact_utc  = self._last_contact,
        )

    def _abort(self, elapsed: float, links):
        print(f"\n  [{ts()}] ✓ ABORT — Timer reset after {elapsed:.0f}s")

    # ═══════════════════════════════════════════════════════════
    #  Other callbacks
    # ═══════════════════════════════════════════════════════════

    def _on_ack(self, burst):
        print(f"  [{ts()}] ✓ Ground ACK on {burst.band} — DMU standing down.")

    def _on_safe_complete(self, actions):
        print(f"\n  [{ts()}] ✓ Safe mode complete — ISS in autonomous survival config.")
        self._print_status()

    # ═══════════════════════════════════════════════════════════
    #  Public API
    # ═══════════════════════════════════════════════════════════

    def heartbeat(self, link: str, snr: float = 18.0, loss: float = 0.01):
        """Inject a heartbeat on a specific link."""
        self.monitor.receive_heartbeat(link, snr=snr, loss=loss)
        self._last_contact = time.time()

    def drop_link(self, link: str):
        self.monitor.simulate_drop(link)

    def restore_link(self, link: str):
        self.monitor.simulate_restore(link)

    def _print_status(self):
        print("\n  ── DMU STATUS ──────────────────────────────────────")
        for link, info in self.monitor.link_status().items():
            icon = "✓" if info["state"] == "NOMINAL" else ("⚠" if info["state"] == "DEGRADED" else "✗")
            print(f"  {icon} {link:<8} {info['state']:<10} age={info['age_sec']:>5.1f}s  "
                  f"SNR={info['snr_db']}dB  loss={info['pkt_loss']}%")
        st = self.timer.status()
        print(f"  Timer: {st['stage']}  elapsed={st['elapsed_s']}s  "
              f"to_trigger={st['time_to_trigger_s']}s")
        print()

    def shutdown(self):
        self._running = False
        self.monitor.shutdown()
        self.timer.shutdown()
        self.transmitter.stop()


# ═══════════════════════════════════════════════════════════════
#  Demo Scenario
# ═══════════════════════════════════════════════════════════════

def run_demo(dmu: DeadMansUplink):
    """
    Scenario A: All links drop, DMU triggers, partial restore.
    """
    import random as _random
    print("\n" + "─"*62)
    print("  DEMO SCENARIO: Simulated communications blackout")
    print("─"*62)

    # Phase 1: Nominal — heartbeats on primary links
    print(f"\n[DEMO] Phase 1: Nominal operations — pulsing heartbeats...")
    for _ in range(3):
        time.sleep(1)
        for link in ["TDRS-E", "TDRS-W", "KU"]:
            dmu.heartbeat(link, snr=_random.uniform(14, 22))

    # Phase 2: Lose links one by one
    import random
    print(f"\n[DEMO] Phase 2: Solar storm degrading links...")
    for link in ["KU", "TDRS-W", "TDRS-Z"]:
        time.sleep(1)
        dmu.drop_link(link)

    time.sleep(1)

    # Phase 3: Last links drop — total blackout
    print(f"\n[DEMO] Phase 3: Total blackout — dropping TDRS-E, UHF-A, VHF-G")
    for link in ["TDRS-E", "UHF-A", "VHF-G"]:
        dmu.drop_link(link)

    # Phase 4: Wait for timer stages (fast mode)
    if dmu.fast_mode:
        print(f"\n[DEMO] Waiting for DMU stages (fast mode — compressed timing)...")
        time.sleep(28)   # covers all stages through TRIGGER

        # Phase 5: Comms restored via UHF guard channel
        print(f"\n[DEMO] Phase 5: Ground restores contact via VHF guard channel")
        dmu.restore_link("VHF-G")
        time.sleep(4)
        dmu.restore_link("TDRS-E")
    else:
        print(f"\n[DEMO] Waiting 90 seconds (normal-mode demo)...")
        time.sleep(90)
        dmu.restore_link("TDRS-E")

    time.sleep(4)
    dmu._print_status()
    print("\n[DEMO] Scenario complete.\n")


# ─────────────────────────────────────────────────────────────
import random

def main():
    parser = argparse.ArgumentParser(description="Dead Man's Uplink — ISS DMU System")
    parser.add_argument("--demo",  action="store_true", help="Run automated demo scenario")
    parser.add_argument("--fast",  action="store_true", help="Compress timing for demo (seconds instead of minutes)")
    args = parser.parse_args()

    print(BANNER)

    dmu = DeadMansUplink(fast_mode=args.fast or args.demo)

    def handle_sig(s, f):
        print("\n[DMU] Signal received — shutting down.")
        dmu.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT,  handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    if args.demo:
        run_demo(dmu)
        dmu.shutdown()
        return

    # ── Interactive mode ──────────────────────────────────────
    print("Commands:")
    print("  hb <link>           — inject heartbeat (TDRS-E, TDRS-W, TDRS-Z, KU, UHF-A, VHF-G)")
    print("  drop <link>         — simulate link loss")
    print("  restore <link>      — restore link")
    print("  blackout            — drop ALL links (triggers DMU)")
    print("  restore-all         — restore all links")
    print("  status              — print link/timer status")
    print("  diag                — run diagnostic suite")
    print("  ack <band>          — simulate ground ACK on band")
    print("  quit / exit         — shutdown\n")

    while True:
        try:
            cmd = input("DMU> ").strip().split()
        except (EOFError, KeyboardInterrupt):
            break

        if not cmd:
            continue
        op = cmd[0].lower()

        if op in ("quit", "exit"):
            break
        elif op == "hb" and len(cmd) > 1:
            dmu.heartbeat(cmd[1].upper())
        elif op == "drop" and len(cmd) > 1:
            dmu.drop_link(cmd[1].upper())
        elif op == "restore" and len(cmd) > 1:
            dmu.restore_link(cmd[1].upper())
        elif op == "blackout":
            for lk in ["TDRS-E","TDRS-W","TDRS-Z","KU","UHF-A","VHF-G"]:
                dmu.drop_link(lk)
        elif op == "restore-all":
            for lk in ["TDRS-E","TDRS-W","TDRS-Z","KU","UHF-A","VHF-G"]:
                dmu.restore_link(lk)
        elif op == "status":
            dmu._print_status()
        elif op == "diag":
            print_diagnostics(run_diagnostics())
        elif op == "ack" and len(cmd) > 1:
            dmu.transmitter.acknowledge(cmd[1])
        else:
            print(f"Unknown command: {op}")

    dmu.shutdown()
    print("[DMU] Shutdown complete.")


if __name__ == "__main__":
    main()
