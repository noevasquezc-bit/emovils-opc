"""
Emovils OPC — Safeguards (Protecciones del Sistema)
EL NÚMERO $6 ES INVIOLABLE.
Máximo CPA = $6 USD. Si se cruza, pausa automática.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# ESTADOS DE CPA
# ─────────────────────────────────────────────
class CPAStatus(Enum):
    SALUDABLE = "SALUDABLE"       # CPA < $6 — continúa igual
    ALERTA_AMARILLA = "ALERTA_AMARILLA"  # CPA $6-$8 — ajustar sin pausar
    ALERTA_ROJA = "ALERTA_ROJA"   # CPA > $8 — PAUSA OBLIGATORIA
    CATASTROFE = "CATASTROFE"     # CPA > $20 — revisión total


@dataclass
class CPALimits:
    """Límites financieros del piloto Emovils OPC."""
    MAX_CPA_SAFE: float = 6.0          # Límite ideal
    MAX_CPA_YELLOW_ALERT: float = 8.0  # Alerta amarilla
    MAX_CPA_RED_ALERT: float = 10.0    # Pausa obligatoria
    MAX_CPA_CATASTROPHE: float = 20.0  # Catástrofe (lo que pasó antes)

    # Presupuesto diario blindado
    DAILY_BUDGET_USD: float = 4.0      # $100 / 21 días
    DAILY_BUDGET_SOFT_LIMIT: float = 5.0  # Requiere confirmación Tier 3
    DAILY_BUDGET_HARD_LIMIT: float = 10.0  # BLOQUEADO automáticamente

    # Piloto
    TOTAL_PILOT_BUDGET: float = 100.0
    PILOT_DAYS: int = 21
    TARGET_CLIENTS: int = 17


CPA_LIMITS = CPALimits()


# ─────────────────────────────────────────────
# EVALUADOR DE CPA
# ─────────────────────────────────────────────
class CPAEvaluator:
    """
    Evalúa el CPA acumulado y determina el estado del sistema.
    Esta es LA métrica que decide todo.
    """

    def __init__(self, limits: CPALimits = CPA_LIMITS):
        self.limits = limits

    def calculate_cpa(self, total_spent: float, total_clients: int) -> Optional[float]:
        """
        Calcula el CPA actual.
        CPA = Total gastado / Total clientes confirmados
        """
        if total_clients == 0:
            return None  # No hay clientes todavía, no se puede calcular
        return round(total_spent / total_clients, 2)

    def evaluate(self, total_spent: float, total_clients: int) -> dict:
        """
        Evalúa el estado del negocio basado en el CPA.
        Retorna un dict con status, cpa, acción recomendada y alerta para el dueño.
        """
        cpa = self.calculate_cpa(total_spent, total_clients)

        if cpa is None:
            return {
                "cpa": None,
                "status": CPAStatus.SALUDABLE,
                "emoji": "⏳",
                "message": "Aún sin clientes confirmados. Continúa captando.",
                "action": "CONTINUAR",
                "alert_owner": False,
                "pause_ads": False,
                "margin_usd": None,
                "roi_percent": None
            }

        service_price = 25.0  # Emovils Airport
        margin = service_price - cpa
        roi = round(((service_price - cpa) / cpa) * 100, 1) if cpa > 0 else 0

        # CPA = $6 exacto sigue siendo saludable (el MÁXIMO permitido es $6)
        if cpa <= self.limits.MAX_CPA_SAFE:
            return {
                "cpa": cpa,
                "status": CPAStatus.SALUDABLE,
                "emoji": "✅",
                "message": f"CPA ${cpa} — EXCELENTE. Margen: ${margin:.2f} ({round(margin/service_price*100)}%)",
                "action": "CONTINUAR igual, no cambies nada",
                "alert_owner": False,
                "pause_ads": False,
                "margin_usd": round(margin, 2),
                "roi_percent": roi
            }

        elif cpa <= self.limits.MAX_CPA_YELLOW_ALERT:
            return {
                "cpa": cpa,
                "status": CPAStatus.ALERTA_AMARILLA,
                "emoji": "⚠️",
                "message": f"CPA ${cpa} — ALERTA AMARILLA. Está en el límite. Ajustar targeting o copy.",
                "action": "Ajusta targeting o primer mensaje WhatsApp. Espera 3-4 días.",
                "alert_owner": True,
                "pause_ads": False,
                "margin_usd": round(margin, 2),
                "roi_percent": roi,
                "adjustments": [
                    "Cambiar targeting a público más específico (ej: diáspora dominicana NYC)",
                    "Reescribir primer mensaje WhatsApp con más urgencia",
                    "Revisar si la competencia está haciendo algo diferente"
                ]
            }

        elif cpa <= self.limits.MAX_CPA_RED_ALERT:
            return {
                "cpa": cpa,
                "status": CPAStatus.ALERTA_ROJA,
                "emoji": "🔴",
                "message": f"CPA ${cpa} — ALERTA ROJA. PAUSA AUTOMÁTICA ACTIVADA.",
                "action": "PAUSAR TODOS LOS ANUNCIOS AHORA",
                "alert_owner": True,
                "pause_ads": True,
                "margin_usd": round(margin, 2),
                "roi_percent": roi,
                "diagnosis_checklist": [
                    "¿El público está equivocado? (gente sin dinero o que no viaja)",
                    "¿El creativo está feo? (imagen/video no llama atención)",
                    "¿La oferta no es clara?",
                    "¿El precio es el problema?",
                    "¿El primer mensaje WhatsApp es genérico y no convence?"
                ],
                "restart_budget": 1.0  # Reiniciar con $1/día para test
            }

        else:  # cpa > MAX_CPA_RED_ALERT
            return {
                "cpa": cpa,
                "status": CPAStatus.CATASTROFE,
                "emoji": "💀",
                "message": f"CPA ${cpa} — CATÁSTROFE. Esto es lo que pasó los últimos 5 meses.",
                "action": "PAUSA TOTAL + Análisis profundo + Decisión crítica",
                "alert_owner": True,
                "pause_ads": True,
                "margin_usd": round(margin, 2),
                "roi_percent": roi,
                "options": [
                    "A: Cambiar TODO y reiniciar (riesgo alto)",
                    "B: Bajar a $0.50/día solo para aprender",
                    "C: Parar piloto, intentar estrategia diferente",
                    "D: Pasar a orgánico/WhatsApp directo (costo $0)"
                ]
            }


# ─────────────────────────────────────────────
# GUARDIÁN DE PRESUPUESTO DIARIO
# ─────────────────────────────────────────────
class BudgetGuardian:
    """
    Protege el presupuesto diario de $4.
    Tier 1: OK ($0-$4)
    Tier 2: SOFT LIMIT — requiere confirmación ($4-$5)
    Tier 3: HARD LIMIT — bloqueado ($10+)
    """

    def __init__(self, limits: CPALimits = CPA_LIMITS):
        self.limits = limits

    def check_daily_spend(self, proposed_daily_budget: float) -> dict:
        """
        Verifica si el presupuesto diario propuesto es válido.
        """
        if proposed_daily_budget <= self.limits.DAILY_BUDGET_USD:
            return {
                "allowed": True,
                "tier": 1,
                "message": f"✅ Presupuesto ${proposed_daily_budget}/día — dentro del límite",
                "requires_confirmation": False
            }

        elif proposed_daily_budget <= self.limits.DAILY_BUDGET_SOFT_LIMIT:
            return {
                "allowed": None,  # Requiere confirmación
                "tier": 2,
                "message": f"⚠️ ${proposed_daily_budget}/día supera el límite de $4. ¿Por qué? Debes justificarlo.",
                "requires_confirmation": True,
                "warning": "Si apruebas, es tu responsabilidad si falla."
            }

        else:
            return {
                "allowed": False,
                "tier": 3,
                "message": f"🔴 BLOQUEADO — ${proposed_daily_budget}/día supera el límite máximo de ${self.limits.DAILY_BUDGET_HARD_LIMIT}",
                "requires_confirmation": False,
                "error": "Debes cambiar el presupuesto manualmente. Esta es una decisión consciente."
            }

    def check_total_remaining(self, total_spent: float) -> dict:
        """Verifica cuánto presupuesto queda del piloto."""
        remaining = self.limits.TOTAL_PILOT_BUDGET - total_spent
        days_remaining = max(0, round(remaining / self.limits.DAILY_BUDGET_USD))
        percent_used = round((total_spent / self.limits.TOTAL_PILOT_BUDGET) * 100, 1)

        return {
            "total_budget": self.limits.TOTAL_PILOT_BUDGET,
            "total_spent": total_spent,
            "remaining_usd": round(remaining, 2),
            "percent_used": percent_used,
            "estimated_days_remaining": days_remaining,
            "pilot_viable": remaining > 0
        }


# ─────────────────────────────────────────────
# EL CONTRATO (resumen de reglas)
# ─────────────────────────────────────────────
SYSTEM_CONTRACT = """
CONTRATO DEL SISTEMA EMOVILS OPC
════════════════════════════════
1. EL NÚMERO $6 ES INVIOLABLE
   └─ Máximo costo por cliente = $6 USD

2. SI EL CPA CRUZA $6:
   └─ Sistema pausa automático, sin excepciones

3. EL DUEÑO TOMA TODAS LAS DECISIONES
   └─ Agentes solo proponen, el dueño aprueba

4. VISIBILIDAD DIARIA
   └─ Email/WhatsApp cada mañana 7:15 AM con 1 número: tu CPA

5. PRESUPUESTO BLINDADO
   └─ Máximo $100 en 21 días ($4/día)

6. MÁXIMO PÉRDIDA POSIBLE
   └─ $100, no $7,200 como pasó antes

VIOLACIONES CRÍTICAS:
├─ Si el sistema gasta > $4/día sin aprobación Tier 3: ERROR CRÍTICO
├─ Si el sistema no pausa con CPA > $8: ERROR CRÍTICO
└─ Si el dueño no recibe email cada mañana: ERROR CRÍTICO
"""
