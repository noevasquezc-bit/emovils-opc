"""
Tests del agente de leads diario (opc/agente_leads_diario.py).

Sin credenciales (Airtable/Apify/Resend) todo corre en modo mock:
nada sale a internet, nada se escribe en el CRM, y el ciclo TERMINA
(los "preparados" cuentan hacia la meta para evitar bucle infinito).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Config de test ANTES de importar main_opc (el import lee el entorno).
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ["ENABLE_SCHEDULER"] = "0"

from opc.agente_leads_diario import (  # noqa: E402
    BUSQUEDAS_ROTACION,
    META_DIARIA,
    _email_para_envio,
    ejecutar_ciclo_leads,
    enviar_followups,
)


@pytest.fixture(autouse=True)
def _forzar_modo_mock(monkeypatch):
    """Quita TODAS las credenciales del entorno (config/settings.py carga el
    .env real): los tests deben correr 100% offline, sin tocar Airtable,
    Apify, OpenAI, Green API ni correo."""
    for var in (
        "AIRTABLE_API_KEY", "AIRTABLE_BASE_ID",
        "APIFY_API_TOKEN", "APOLLO_API_KEY",
        "RESEND_API_KEY", "EMAIL_SMTP_PASS",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "GREEN_API_TOKEN", "GREEN_API_INSTANCE",
        "GREEN_API_ID_INSTANCE", "GREEN_API_TOKEN_INSTANCE",
        "OWNER_WHATSAPP",
    ):
        monkeypatch.delenv(var, raising=False)


class TestEmailParaEnvio:
    """Nunca escribir a direcciones adivinadas (rebotes = dominio quemado)."""

    def test_rechaza_email_mock_adivinado(self):
        # Patrón del enriquecimiento mock: info@<slug>.com.do sin sitio real.
        p = {"nombre_empresa": "Hotel Casa Colonial", "email": "info@hotelcasacolonial.com.do",
             "sitio_web": "", "enriquecido_con": "mock"}
        assert _email_para_envio(p) == ""

    def test_acepta_email_real_del_scraping(self):
        p = {"nombre_empresa": "Turinter", "email": "reservas@turinter.com",
             "sitio_web": "https://www.turinter.com", "enriquecido_con": "mock"}
        assert _email_para_envio(p) == "reservas@turinter.com"

    def test_acepta_email_apollo(self):
        p = {"nombre_empresa": "Empresa X", "email": "contacto@empresax.com.do",
             "sitio_web": "", "enriquecido_con": "apollo"}
        assert _email_para_envio(p) == "contacto@empresax.com.do"

    def test_fallback_info_del_sitio_real(self):
        p = {"nombre_empresa": "Hotel Y", "email": "",
             "sitio_web": "https://www.hotely.com.do/contacto"}
        assert _email_para_envio(p) == "info@hotely.com.do"

    def test_sin_email_ni_sitio_devuelve_vacio(self):
        assert _email_para_envio({"nombre_empresa": "Z", "email": "", "sitio_web": ""}) == ""

    def test_sitio_facebook_no_sirve_de_dominio(self):
        p = {"nombre_empresa": "Bar W", "email": "",
             "sitio_web": "https://www.facebook.com/barw"}
        assert _email_para_envio(p) == ""


class TestCicloMock:
    """Sin tokens el ciclo corre en mock, termina y no manda nada."""

    def test_ciclo_termina_y_devuelve_estructura(self):
        r = ejecutar_ciclo_leads(enviar_correos=False, avisar_owner=False)
        assert isinstance(r, dict)
        assert r["modo"] == "mock"
        assert r["meta_diaria"] == META_DIARIA
        assert isinstance(r["nuevos"], list)
        assert isinstance(r["preparados"], list)
        # En mock nada se "envía" de verdad:
        assert r["nuevos"] == []
        # Y los preparados no exceden la meta (el ciclo se detiene).
        assert len(r["preparados"]) <= META_DIARIA
        assert "followups" in r

    def test_meta_personalizada(self):
        r = ejecutar_ciclo_leads(meta=2, enviar_correos=False, avisar_owner=False)
        assert r["meta_diaria"] == 2
        assert len(r["preparados"]) <= 2

    def test_no_avisa_owner_si_avisar_false(self):
        r = ejecutar_ciclo_leads(enviar_correos=False, avisar_owner=False)
        assert "aviso_whatsapp" not in r

    def test_followups_mock_no_envia(self):
        r = enviar_followups(None)
        assert r == {"modo": "mock", "enviados": 0}

    def test_rotacion_tiene_busquedas_rd(self):
        assert len(BUSQUEDAS_ROTACION) >= 10
        assert all("busqueda" in q and "fuente" in q for q in BUSQUEDAS_ROTACION)


class TestEndpointLeadsCiclo:
    """POST /api/v2/leads/ciclo: 401 sin llave admin, 200 con ella."""

    @pytest.fixture()
    def client(self):
        import main_opc
        main_opc.app.config["TESTING"] = True
        with main_opc.app.test_client() as c:
            yield c

    def test_sin_token_401(self, client):
        resp = client.post("/api/v2/leads/ciclo", json={})
        assert resp.status_code == 401

    def test_con_token_200(self, client):
        resp = client.post(
            "/api/v2/leads/ciclo",
            json={"meta": 1, "enviar_correos": False, "avisar_owner": False},
            headers={"X-Admin-Token": os.environ["ADMIN_TOKEN"]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["modo"] == "mock"
        assert data["meta_diaria"] == 1


class TestSchedulerRegistro:
    """El job está registrado en el dict JOBS del scheduler."""

    def test_job_leads_diario_en_jobs(self):
        from opc.scheduler import JOBS
        assert "leads_diario" in JOBS
        assert callable(JOBS["leads_diario"]["func"])

    def test_job_corre_sin_credenciales(self):
        from opc.scheduler import job_leads_diario
        r = job_leads_diario()
        assert isinstance(r, dict)
        assert r.get("modo") == "mock" or r.get("ok") is False
