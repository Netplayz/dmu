# Dead Man's Uplink (DMU)

> *"If the silence lasts too long — we speak for ourselves."*

**ISS Autonomous Communications Loss Response System**  
NetByte — v1.0

---

## What It Is

Dead Man's Uplink is a safety-critical software system designed for spacecraft that have lost contact with ground control. If the ISS goes comms-dark for longer than a configurable threshold with no heartbeat on any channel, DMU autonomously executes a pre-planned safe mode sequence, broadcasts a signed distress packet on every available frequency, and runs a full diagnostic cascade — all without any human trigger.

It is modeled on real ISS communications architecture (TDRS constellation, CCSDS packet standards, NASA Safe Haven protocol) and is designed to be radiation-aware, fault-tolerant, and deterministic.

---

## Architecture

```
HeartbeatMonitor
  └── Tracks 6 comm links (TDRS-E/W/Z, Ku, UHF, VHF)
  └── Fires on_comms_dark() when ALL links go dead
        │
        ▼
DeadMansTimer
  └── Multi-stage countdown (ALERT-1 → ALERT-2 → ALERT-3 → TRIGGER)
  └── Resets instantly on any comms restoration
        │
        ├──▶ DiagnosticsEngine   (at ALERT-2)
        │      Power / Thermal / ECLSS / ADCS / Software
        │
        ├──▶ SafeModeEngine      (at TRIGGER)
        │      13 sequential ISS safe-mode procedures
        │
        └──▶ DistressTransmitter (at TRIGGER)
               CCSDS signed burst on S/UHF/VHF/L/Ka-Band
               Retry loop every 30s until ACK
```

---

## Files

| File | Role |
|---|---|
| `main.py` | Master orchestrator, CLI entrypoint, demo scenario |
| `heartbeat.py` | Comm link monitor — 6 channels, per-link timeouts |
| `timer.py` | Multi-stage countdown engine |
| `safemode.py` | Safe mode procedures + automated diagnostics |
| `distress.py` | Multi-frequency distress transmitter, CCSDS packets |

---

## Timer Stages

| Stage | Default Threshold | Action |
|---|---|---|
| ALERT-1 | T+2:00 dark | Crew PA notification, ground page |
| ALERT-2 | T+5:00 dark | Full automated diagnostic cascade |
| ALERT-3 | T+8:00 dark | Safe mode pre-armed, crew straps in |
| TRIGGER | T+12:00 dark | Safe mode executes + distress burst fires |

If comms restore at any point before TRIGGER, the timer aborts and resets completely. If comms restore after TRIGGER, safe mode halts mid-execution and the distress transmitter stands down.

---

## Comm Links Monitored

| Link | Frequency | Band | Timeout |
|---|---|---|---|
| TDRS-E | 2.087 GHz | S-Band | 45s |
| TDRS-W | 2.106 GHz | S-Band | 45s |
| TDRS-Z | 26.55 GHz | Ka-Band | 30s |
| KU | 15.003 GHz | Ku-Band | 60s |
| UHF-A | 437.525 MHz | UHF | 90s |
| VHF-G | 243.000 MHz | VHF (guard) | 300s |

---

## Distress Transmitter

On trigger, a signed [CCSDS](https://public.ccsds.org/default.aspx) distress packet is broadcast simultaneously on all five channels:

| Band | Frequency | Power |
|---|---|---|
| S-Band | 2.025 GHz | 40 W |
| UHF | 437.525 MHz | 5 W |
| VHF | 243.000 MHz | 10 W |
| L-Band | 1.544 GHz | 25 W |
| Ka-Band | 26.550 GHz | 100 W |

Each packet contains: spacecraft ID, sequence number, orbital TLE snapshot, crew count, ECLSS readings, system health summary, last known ground contact timestamp, and a SHA-256 checksum. Retransmits every 30 seconds until ACK or 20 retries (~10 minutes).

---

## Safe Mode Procedures

Executed sequentially across 4 stages after TRIGGER:

**SAFE-1 — Load shedding**
- EXPRESS racks → standby
- SSRMS → park (canbus config)
- Crew PA + C&W tones
- Non-essential lighting dimmed 50%

**SAFE-2 — Attitude**
- Attitude mode: LVLH → XPOP (sun-pointing)
- SARJ released to free-drift solar acquisition

**SAFE-3 — Life support**
- ECLSS → standalone autonomous loop
- Fire suppression armed
- O₂ reserve manifold valve opened

**SAFE-4 — Comms hardware reset**
- SGANT antenna slewed to TDRS acquisition search
- S-Band + Ku-Band transponders cold-rebooted
- UHF carrier beacon enabled (continuous)
- CCSDS distress packet transmitted

---

## Automated Diagnostics

Run automatically at ALERT-2. Covers:

- **Power** — solar array current, battery SoC, main bus voltage
- **Thermal** — node temperatures, radiator output, ITCS loop A/B
- **ECLSS** — O₂ partial pressure, CO₂ level, cabin pressure, WRS recovery rate
- **ADCS** — CMG RPM, star tracker lock, attitude error
- **Software** — ECC single/multi-bit error counters, watchdog heartbeats, uptime

Each check returns `PASS`, `WARN`, or `FAIL`. Results are embedded in the distress packet.

---

## Usage

**Demo mode** (compressed timing — seconds instead of minutes):
```bash
cd dmu
python3 main.py --demo
```

**Interactive mode:**
```bash
python3 main.py
```

Interactive commands:
```
hb <link>        Inject a heartbeat  (e.g. hb TDRS-E)
drop <link>      Simulate link loss
restore <link>   Restore a link
blackout         Drop ALL links — triggers DMU countdown
restore-all      Restore all links
status           Print current link/timer status
diag             Run diagnostic suite
ack <band>       Simulate ground ACK on band (e.g. ack S-Band)
quit             Shutdown
```

---

## Requirements

- Python 3.10+
- No external dependencies — stdlib only (`threading`, `time`, `hashlib`, `json`, `signal`)

---

## Design Principles

**Deterministic.** Every stage fires exactly once and in order. No stage can re-fire in the same blackout event. The system is entirely event-driven with no polling ambiguity.

**Fail-safe default.** The system acts on silence, not on a command. Inaction is the trigger. This means hardware failures, software crashes, or severed uplinks all have the same effect — DMU fires.

**Graceful abort.** Comms restoration at any point before TRIGGER cleanly aborts the countdown. Restoration after TRIGGER halts in-progress safe mode and stands down the transmitter — the ISS doesn't stay locked in safe mode if the link comes back mid-procedure.

**No single point of failure.** Six independent comm channels must all go dead simultaneously before DMU triggers. A single degraded or lost link is normal and expected.

**Modular.** Each subsystem (`heartbeat`, `timer`, `safemode`, `distress`) is independently testable and replaceable. Swap in a real RF driver behind `DistressTransmitter._transmit()` or real telemetry behind `run_diagnostics()` without touching the orchestration logic.

---

## Extending for Real Hardware

| Hook | Where | What to replace |
|---|---|---|
| RF transmit | `distress.py → DistressTransmitter._transmit()` | Write packet to hardware RF FIFO |
| Link health | `heartbeat.py → HeartbeatMonitor.receive_heartbeat()` | Call from real comms driver interrupt |
| Telemetry | `safemode.py → run_diagnostics()` | Read from MDM (Multiplexer/Demultiplexer) |
| Actuators | `safemode.py → SafeModeAction` steps | Replace print statements with C&DH commands |

---

## Related Projects

- [Radiation-Hardened Memory Core](../c/tmr_core.c) — TMR + Hamming(7,4) ECC + memory scrubber
- BytePen — Python-based penetration testing toolkit
- ezmirror — Open-source Linux mirror server

---

*NetByte — open source, built for space.*
