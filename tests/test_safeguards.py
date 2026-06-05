"""
Tests para los Safeguards del Sistema Emovils OPC
EL $6 ES INVIOLABLE — estos tests lo garantizan.
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.safeguards import CPAEvaluator, BudgetGuardian, CPAStatus, CPA_LIMITS


class TestCPAEvaluator:
    """Tests del evaluador de CPA."""

    def setup_method(self):
        self.evaluator = CPAEvaluator()

    def test_cpa_saludable(self):
        """CPA < $6 debe ser SALUDABLE."""
        result = self.evaluator.evaluate(total_spent=45.0, total_clients=10)
        assert result["status"] == CPAStatus.SALUDABLE
        assert result["cpa"] == 4.5
        assert result["pause_ads"] is False
        assert result["emoji"] == "✅"

    def test_cpa_exactamente_6(self):
        """CPA = $6 debe ser SALUDABLE (en el límite)."""
        result = self.evaluator.evaluate(total_spent=60.0, total_clients=10)
        assert result["status"] == CPAStatus.SALUDABLE
        assert result["cpa"] == 6.0
        assert result["pause_ads"] is False

    def test_cpa_alerta_amarilla(self):
        """CPA $6-$8 debe ser ALERTA_AMARILLA, sin pausar."""
        result = self.evaluator.evaluate(total_spent=70.0, total_clients=10)
        assert result["status"] == CPAStatus.ALERTA_AMARILLA
        assert result["cpa"] == 7.0
        assert result["pause_ads"] is False  # No pausa todavía
        assert result["alert_owner"] is True
        assert "adjustments" in result

    def test_cpa_alerta_roja(self):
        """CPA > $8 debe activar PAUSA OBLIGATORIA."""
        result = self.evaluator.evaluate(total_spent=90.0, total_clients=10)
        assert result["status"] == CPAStatus.ALERTA_ROJA
        assert result["cpa"] == 9.0
        assert result["pause_ads"] is True  # PAUSA AUTOMÁTICA
        assert result["alert_owner"] is True

    def test_cpa_catastrofe(self):
        """CPA > $20 es catástrofe — como los últimos 5 meses."""
        result = self.evaluator.evaluate(total_spent=250.0, total_clients=10)
        assert result["status"] == CPAStatus.CATASTROFE
        assert result["cpa"] == 25.0
        assert result["pause_ads"] is True
        assert "options" in result  # 4 opciones de acción

    def test_sin_clientes(self):
        """Sin clientes no se puede calcular CPA."""
        result = self.evaluator.evaluate(total_spent=10.0, total_clients=0)
        assert result["cpa"] is None
        assert result["pause_ads"] is False

    def test_cpa_calculo_correcto(self):
        """Verifica fórmula: CPA = gastado / clientes."""
        result = self.evaluator.evaluate(total_spent=48.0, total_clients=8)
        assert result["cpa"] == 6.0  # 48/8 = 6

    def test_ejemplo_piloto_ok(self):
        """Escenario real: $40 gastados, 8 clientes → CPA $5 ✅"""
        result = self.evaluator.evaluate(total_spent=40.0, total_clients=8)
        assert result["cpa"] == 5.0
        assert result["status"] == CPAStatus.SALUDABLE

    def test_lo_que_paso_antes(self):
        """Escenario anterior: $7,200 / 1 cliente = CPA $7,200 — catástrofe."""
        result = self.evaluator.evaluate(total_spent=7200.0, total_clients=1)
        assert result["status"] == CPAStatus.CATASTROFE
        assert result["cpa"] == 7200.0
        assert result["pause_ads"] is True


class TestBudgetGuardian:
    """Tests del guardián de presupuesto diario."""

    def setup_method(self):
        self.guardian = BudgetGuardian()

    def test_presupuesto_dentro_limite(self):
        """$4/día está dentro del límite — OK."""
        result = self.guardian.check_daily_spend(4.0)
        assert result["allowed"] is True
        assert result["tier"] == 1

    def test_presupuesto_soft_limit(self):
        """$5/día requiere confirmación Tier 3."""
        result = self.guardian.check_daily_spend(5.0)
        assert result["allowed"] is None  # Requiere confirmación
        assert result["tier"] == 2
        assert result["requires_confirmation"] is True

    def test_presupuesto_bloqueado(self):
        """$10/día está BLOQUEADO — no permitido."""
        result = self.guardian.check_daily_spend(10.0)
        assert result["allowed"] is False
        assert result["tier"] == 3

    def test_presupuesto_muy_alto(self):
        """$50/día es catastrófico — debe estar bloqueado."""
        result = self.guardian.check_daily_spend(50.0)
        assert result["allowed"] is False

    def test_presupuesto_restante(self):
        """Verifica cálculo de presupuesto restante."""
        result = self.guardian.check_total_remaining(total_spent=40.0)
        assert result["remaining_usd"] == 60.0
        assert result["total_spent"] == 40.0
        assert result["percent_used"] == 40.0
        assert result["pilot_viable"] is True

    def test_presupuesto_agotado(self):
        """Cuando se agota el presupuesto, piloto no viable."""
        result = self.guardian.check_total_remaining(total_spent=100.0)
        assert result["remaining_usd"] == 0.0
        assert result["pilot_viable"] is False

    def test_presupuesto_limites_configuracion(self):
        """Verifica que los límites están correctamente configurados."""
        assert CPA_LIMITS.MAX_CPA_SAFE == 6.0
        assert CPA_LIMITS.DAILY_BUDGET_USD == 4.0
        assert CPA_LIMITS.TOTAL_PILOT_BUDGET == 100.0
        assert CPA_LIMITS.PILOT_DAYS == 21
        assert CPA_LIMITS.TARGET_CLIENTS == 17


class TestCPALimitsIntegrity:
    """Tests de integridad del contrato del $6."""

    def test_el_numero_6_es_inviolable(self):
        """EL $6 ES EL LÍMITE — verificar que está bien configurado."""
        assert CPA_LIMITS.MAX_CPA_SAFE == 6.0, "¡El límite de CPA es $6, NO debe cambiarse!"

    def test_presupuesto_piloto_correcto(self):
        """$100 en 21 días = $4.76/día → redondeado $4/día."""
        assert CPA_LIMITS.TOTAL_PILOT_BUDGET == 100.0
        assert CPA_LIMITS.PILOT_DAYS == 21
        daily = CPA_LIMITS.TOTAL_PILOT_BUDGET / CPA_LIMITS.PILOT_DAYS
        assert daily == pytest.approx(4.76, rel=0.01)
        assert CPA_LIMITS.DAILY_BUDGET_USD == 4.0  # Redondeado hacia abajo

    def test_target_clientes_correcto(self):
        """Con $100 y CPA $6 = 16.6 → 17 clientes."""
        expected = CPA_LIMITS.TOTAL_PILOT_BUDGET / CPA_LIMITS.MAX_CPA_SAFE
        assert expected == pytest.approx(16.67, rel=0.01)
        assert CPA_LIMITS.TARGET_CLIENTS == 17

    def test_roi_con_cpa_6(self):
        """Con CPA $6 y precio $25, el margen debe ser $19 (76%)."""
        service_price = 25.0
        cpa = CPA_LIMITS.MAX_CPA_SAFE
        margin = service_price - cpa
        margin_pct = (margin / service_price) * 100
        assert margin == 19.0
        assert margin_pct == pytest.approx(76.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
