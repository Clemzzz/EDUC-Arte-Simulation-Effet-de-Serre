"""Modèle physique de l'expérience d'effet de serre sous cloche (EDUC'Arte).

Simule un capteur de température et d'humidité (type DHT22) placé sous une cloche
transparente exposée à une lampe (soleil) avec ou sans papier humide.
"""

from __future__ import annotations

import csv
import io
import math
import random
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

VALID_SPEEDS = (1, 10, 60)


def saturation_vapor_pressure_hpa(temp_c: float) -> float:
    """Formule de Magnus-Tetens pour la pression de vapeur saturante (hPa)."""
    return 6.112 * math.exp((17.67 * temp_c) / (temp_c + 243.5))


@dataclass(frozen=True)
class Sample:
    timestamp: str
    elapsed_s: float
    temperature_c: float
    humidity_pct: float
    lamp_on: bool
    wet_paper: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "elapsed_s": self.elapsed_s,
            "temperature_c": self.temperature_c,
            "humidity_pct": self.humidity_pct,
            "lamp_on": self.lamp_on,
            "wet_paper": self.wet_paper,
        }


class GreenhouseSimulator:
    """Simulateur thread-safe de la cloche à effet de serre."""

    AMBIENT_TEMP_C = 21.0
    AMBIENT_HUMIDITY_PCT = 48.0
    LAMP_EQUILIBRIUM_DRY_C = 40.5
    LAMP_EQUILIBRIUM_WET_C = 38.8
    TAU_HEAT_S = 240.0
    TAU_COOL_S = 300.0
    TAU_LEAK_S = 1200.0
    MAX_HISTORY = 10_000

    def __init__(
        self,
        *,
        lamp_on: bool = True,
        wet_paper: bool = True,
        speed: int = 1,
        seed: int | None = None,
    ) -> None:
        if speed not in VALID_SPEEDS:
            raise ValueError(f"Vitesse invalide: {speed}. Valeurs autorisées: {VALID_SPEEDS}")

        self._lock = threading.Lock()
        self._rng = random.Random(seed)
        self._lamp_on = bool(lamp_on)
        self._wet_paper = bool(wet_paper)
        self._speed = int(speed)

        self._ambient_e_a = (
            self.AMBIENT_HUMIDITY_PCT / 100.0
        ) * saturation_vapor_pressure_hpa(self.AMBIENT_TEMP_C)

        self._history: deque[Sample] = deque(maxlen=self.MAX_HISTORY)
        self._reset_state_unlocked()

    def _reset_state_unlocked(self) -> None:
        self._elapsed_s = 0.0
        self._true_temp_c = self.AMBIENT_TEMP_C
        self._vapor_pressure_hpa = self._ambient_e_a
        self._paper_moisture = 1.0 if self._wet_paper else 0.0
        self._history.clear()
        self._record_sample_unlocked()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _current_relative_humidity_unlocked(self) -> float:
        e_s = saturation_vapor_pressure_hpa(self._true_temp_c)
        rh = 100.0 * (self._vapor_pressure_hpa / e_s)
        return max(5.0, min(98.0, rh))

    def _record_sample_unlocked(self) -> Sample:
        noisy_temp = self._true_temp_c + self._rng.gauss(0.0, 0.06)
        noisy_hum = self._current_relative_humidity_unlocked() + self._rng.gauss(0.0, 0.2)

        sample = Sample(
            timestamp=self._now_iso(),
            elapsed_s=round(self._elapsed_s, 1),
            temperature_c=round(max(-10.0, min(80.0, noisy_temp)), 1),
            humidity_pct=round(max(5.0, min(98.0, noisy_hum)), 1),
            lamp_on=self._lamp_on,
            wet_paper=self._wet_paper,
        )
        self._history.append(sample)
        return sample

    def _advance_substep_unlocked(self, dt: float) -> None:
        # 1. Évolution thermique (effet de serre sous la cloche)
        if self._lamp_on:
            # La vapeur d'eau piégée et l'évaporation modulent légèrement le plateau thermique
            evap_cooling = (
                self.LAMP_EQUILIBRIUM_DRY_C - self.LAMP_EQUILIBRIUM_WET_C
            ) * (self._paper_moisture if self._wet_paper else 0.0)
            target_temp = self.LAMP_EQUILIBRIUM_DRY_C - evap_cooling
            tau = self.TAU_HEAT_S
        else:
            target_temp = self.AMBIENT_TEMP_C
            tau = self.TAU_COOL_S

        self._true_temp_c += (target_temp - self._true_temp_c) * (1.0 - math.exp(-dt / tau))

        # 2. Évolution hygrométrique (évaporation du papier humide + condensation / fuites)
        e_s = saturation_vapor_pressure_hpa(self._true_temp_c)

        # Fuite très lente avec l'air ambiant de la pièce
        self._vapor_pressure_hpa += (self._ambient_e_a - self._vapor_pressure_hpa) * (
            1.0 - math.exp(-dt / self.TAU_LEAK_S)
        )

        if self._wet_paper and self._paper_moisture > 0.0:
            # Pression de vapeur cible proche de la saturation (88 % à 94 % selon l'humidité du papier)
            target_rh = 0.78 + 0.16 * math.sqrt(self._paper_moisture)
            target_e_a = target_rh * e_s

            # L'évaporation s'accélère avec la température sous la lampe
            temp_factor = 0.7 + 0.06 * max(0.0, self._true_temp_c - 18.0)
            evap_rate = (1.0 / 150.0) * temp_factor * (0.25 + 0.75 * self._paper_moisture)

            if target_e_a > self._vapor_pressure_hpa:
                delta_e = (target_e_a - self._vapor_pressure_hpa) * (
                    1.0 - math.exp(-evap_rate * dt)
                )
                self._vapor_pressure_hpa += delta_e
            else:
                delta_e = 0.0

            # Assèchement progressif du papier humide (plus rapide lampe allumée / chaud)
            heat_dry_rate = (
                0.00028 * temp_factor if self._lamp_on else 0.00004 * temp_factor
            )
            moisture_loss = (delta_e * 0.0022) + (heat_dry_rate * dt)
            self._paper_moisture = max(0.0, self._paper_moisture - moisture_loss)

        # Condensation sur les parois de la cloche si l'air refroidit au-delà de 96 % HR
        max_e_a = 0.96 * e_s
        if self._vapor_pressure_hpa > max_e_a:
            self._vapor_pressure_hpa = max_e_a

        self._elapsed_s += dt

    def step(self, dt_real_s: float) -> dict[str, Any]:
        """Avance la simulation de `dt_real_s * speed` secondes simulées et enregistre un point."""
        if dt_real_s < 0:
            raise ValueError("dt_real_s doit être positif ou nul")

        with self._lock:
            dt_sim = dt_real_s * self._speed
            remaining = dt_sim
            max_substep = 2.0
            while remaining > 1e-9:
                chunk = min(remaining, max_substep)
                self._advance_substep_unlocked(chunk)
                remaining -= chunk

            self._record_sample_unlocked()
            return self._build_latest_payload_unlocked()

    def configure(
        self,
        *,
        lamp_on: bool | None = None,
        wet_paper: bool | None = None,
        speed: int | None = None,
    ) -> dict[str, Any]:
        """Met à jour les paramètres de l'expérience simulée."""
        if speed is not None and speed not in VALID_SPEEDS:
            raise ValueError(
                f"Vitesse invalide: {speed}. Valeurs autorisées: {VALID_SPEEDS}"
            )

        with self._lock:
            if lamp_on is not None:
                self._lamp_on = bool(lamp_on)
            if wet_paper is not None:
                new_wet = bool(wet_paper)
                if new_wet and not self._wet_paper and self._paper_moisture <= 0.01:
                    # On replace un papier fraîchement humidifié dans la cloche
                    self._paper_moisture = 1.0
                self._wet_paper = new_wet
            if speed is not None:
                self._speed = int(speed)
            return self._build_latest_payload_unlocked()

    def reset(self) -> dict[str, Any]:
        """Réinitialise l'expérience à t=0 (température et humidité ambiantes)."""
        with self._lock:
            self._reset_state_unlocked()
            return self._build_latest_payload_unlocked()

    def _build_latest_payload_unlocked(self) -> dict[str, Any]:
        latest = self._history[-1]
        return {
            "mode": "simulation",
            "sensor": "DHT22 (simulé)",
            "timestamp": latest.timestamp,
            "elapsed_s": latest.elapsed_s,
            "temperature_c": latest.temperature_c,
            "humidity_pct": latest.humidity_pct,
            "lamp_on": self._lamp_on,
            "wet_paper": self._wet_paper,
            "paper_moisture_pct": round(
                (self._paper_moisture * 100.0) if self._wet_paper else 0.0, 1
            ),
            "speed": self._speed,
            "sample_count": len(self._history),
        }

    def get_latest(self) -> dict[str, Any]:
        with self._lock:
            return self._build_latest_payload_unlocked()

    def get_history(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.to_dict() for s in self._history]

    def export_csv(self) -> str:
        with self._lock:
            samples = list(self._history)

        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(
            [
                "timestamp",
                "elapsed_s",
                "temperature_c",
                "humidity_pct",
                "lamp_on",
                "wet_paper",
            ]
        )
        for s in samples:
            writer.writerow(
                [
                    s.timestamp,
                    f"{s.elapsed_s:.1f}",
                    f"{s.temperature_c:.1f}",
                    f"{s.humidity_pct:.1f}",
                    1 if s.lamp_on else 0,
                    1 if s.wet_paper else 0,
                ]
            )
        return buf.getvalue()
