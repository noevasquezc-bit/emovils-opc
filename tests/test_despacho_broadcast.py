"""
Tests OFFLINE del despacho por rondas (broadcast).

Caso de negocio real (07/07/2026): la reserva EMV-...-C7DE se ofreció a 4
choferes UNO POR VEZ con 60 segundos cada uno; nadie alcanzó a responder y el
cliente quedó sin chofer. Ahora la carrera inmediata se ofrece a los N más
cercanos A LA VEZ, el primero que responde ACEPTO gana, un RECHAZO no mata la
ronda, y una respuesta tardía todavía toma la carrera si sigue libre.

Todo mockeado — no llama a Airtable ni a WhatsApp.
"""
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import opc.mvp as mvp


def _booking(fields=None):
    base = {
        "Booking_ID": "EMV-TEST-0001",
        "Pickup_Location": "C. Viento Mistral 20, Santo Domingo",
        "Dropoff_Location": "Plaza de la Salud",
        "Passengers": 2,
        "final_price": 900,
        "payment_method": "efectivo",
        "customer_phone": "+18090000000",
        "offer_log": "",
        "offer_attempts": 0,
    }
    base.update(fields or {})
    return {"id": "recBK1", "fields": base}


def _candidatos(n=4):
    return [
        {"driver_id": f"DRV-{i}", "driver_name": f"Chofer {i}",
         "driver_phone": f"+1809555000{i}", "assigned_vehicle_id": "",
         "record_id": f"recD{i}", "dist_km": float(i), "loc_age_min": 1.0}
        for i in range(1, n + 1)
    ]


class TestRondas:
    def test_ronda_actual_parsea_la_ultima_linea(self):
        log = ("2026-07-07T11:52:04+00:00 oferta #1 (inmediata) -> DRV-1 (2.0 km) | -> DRV-2 (5.0 km)\n"
               "2026-07-07T11:53:15+00:00 VENCIDA sin respuesta — DRV-1\n"
               "2026-07-07T11:53:16+00:00 oferta #2 (inmediata) -> DRV-3 (9.0 km)")
        assert mvp._ronda_actual(log) == ["DRV-3"]

    def test_ronda_completa_rechazada(self):
        log = ("x oferta #1 (inmediata) -> DRV-1 (2.0 km) | -> DRV-2 (5.0 km)\n"
               "x RECHAZADA por DRV-1")
        assert not mvp._ronda_completa_rechazada(log)
        log += "\nx RECHAZADA por DRV-2"
        assert mvp._ronda_completa_rechazada(log)


class TestDespachoBroadcast:
    def test_inmediato_oferta_a_varios_a_la_vez(self):
        """Carrera YA: la oferta debe salir a los 3 más cercanos, no a 1."""
        enviados = []
        updates = {}
        with patch.object(mvp, "_geocode_pickup", return_value=(18.45, -69.95)), \
             patch.object(mvp, "choferes_cercanos", return_value=_candidatos(4)), \
             patch.object(mvp, "_at_update", side_effect=lambda t, r, f: updates.update(f)), \
             patch.object(mvp, "_enviar_oferta_whatsapp",
                          side_effect=lambda c, bf, dt=None: enviados.append(c["driver_id"])):
            r = mvp._despachar_siguiente(_booking())
        assert r["ok"]
        assert enviados == ["DRV-1", "DRV-2", "DRV-3"]  # ronda de 3, el 4to espera
        assert r["driver_id"] == "DRV-1"                # el más cercano es el principal
        # todos los ofertados quedan en el log para exclusión y avisos
        assert all(f"-> DRV-{i}" in updates["offer_log"] for i in (1, 2, 3))
        assert "-> DRV-4" not in updates["offer_log"]

    def test_ttl_ya_no_es_60_segundos(self):
        """60s era irreal: el TTL inmediato debe ser >= 5 minutos."""
        assert mvp.OFERTA_TTL_SEG >= 300

    def test_mensaje_dice_minutos_y_primero_gana(self):
        msg = mvp._msg_oferta(_candidatos(1)[0], _booking()["fields"])
        assert "minutos" in msg
        assert "primero" in msg.lower()

    def test_programado_sigue_siendo_uno_por_vez(self):
        from datetime import timedelta
        enviados = []
        futuro = (mvp._now_utc() + timedelta(hours=5)).isoformat()
        with patch.object(mvp, "_geocode_pickup", return_value=(18.45, -69.95)), \
             patch.object(mvp, "choferes_cercanos", return_value=_candidatos(4)), \
             patch.object(mvp, "_at_update"), \
             patch.object(mvp, "_enviar_oferta_whatsapp",
                          side_effect=lambda c, bf, dt=None: enviados.append(c["driver_id"])):
            r = mvp._despachar_siguiente(_booking({"Travel_Date": futuro}))
        assert r["ok"] and r["programado"]
        assert enviados == ["DRV-1"]


class TestResponderOferta:
    def _driver(self, i=2, status="available"):
        return {"id": f"recD{i}", "fields": {
            "driver_id": f"DRV-{i}", "driver_name": f"Chofer {i}",
            "driver_phone": f"+1809555000{i}", "driver_status": status,
        }}

    def test_cualquiera_de_la_ronda_puede_aceptar(self):
        """DRV-2 no es el offered_driver_id principal pero está en la ronda: gana."""
        log = "x oferta #1 (inmediata) -> DRV-1 (2.0 km) | -> DRV-2 (5.0 km)"
        bk = _booking({"offer_status": "offered", "offered_driver_id": "DRV-1",
                       "offer_log": log, "driver_id": ""})
        aceptadas = []
        with patch.object(mvp, "_buscar_driver_por_tel", return_value=[self._driver(2)]), \
             patch.object(mvp, "_at_get", return_value=[bk]), \
             patch.object(mvp, "_aceptar_oferta",
                          side_effect=lambda b, d: aceptadas.append(d["fields"]["driver_id"]) or {"ok": True}):
            r = mvp.responder_oferta("+18095550002", "ACEPTO")
        assert r["ok"] and aceptadas == ["DRV-2"]

    def test_si_ya_tiene_chofer_no_se_pisa(self):
        log = "x oferta #1 (inmediata) -> DRV-1 (2.0 km) | -> DRV-2 (5.0 km)"
        bk = _booking({"offer_status": "offered", "offered_driver_id": "DRV-1",
                       "offer_log": log, "driver_id": "DRV-1"})
        with patch.object(mvp, "_buscar_driver_por_tel", return_value=[self._driver(2)]), \
             patch.object(mvp, "_at_get", return_value=[bk]):
            r = mvp.responder_oferta("+18095550002", "ACEPTO")
        assert not r["ok"] and r["razon"] == "ya_tomada"

    def test_un_rechazo_no_mata_la_ronda(self):
        """Si DRV-2 rechaza pero DRV-1 sigue pensándolo, NO se reoferta todavía."""
        log = "x oferta #1 (inmediata) -> DRV-1 (2.0 km) | -> DRV-2 (5.0 km)"
        bk = _booking({"offer_status": "offered", "offered_driver_id": "DRV-1",
                       "offer_log": log, "driver_id": ""})
        updates = {}
        with patch.object(mvp, "_buscar_driver_por_tel", return_value=[self._driver(2)]), \
             patch.object(mvp, "_at_get", return_value=[bk]), \
             patch.object(mvp, "_at_update", side_effect=lambda t, r_, f: updates.update(f)), \
             patch.object(mvp, "_rechazar_oferta") as reo:
            r = mvp.responder_oferta("+18095550002", "RECHAZO")
        assert r["ok"] and r["accion"] == "rechazada"
        reo.assert_not_called()
        assert "RECHAZADA por DRV-2" in updates["offer_log"]

    def test_ultimo_rechazo_pasa_a_la_siguiente_ronda(self):
        log = ("x oferta #1 (inmediata) -> DRV-1 (2.0 km) | -> DRV-2 (5.0 km)\n"
               "x RECHAZADA por DRV-1")
        bk = _booking({"offer_status": "offered", "offered_driver_id": "DRV-1",
                       "offer_log": log, "driver_id": ""})
        with patch.object(mvp, "_buscar_driver_por_tel", return_value=[self._driver(2)]), \
             patch.object(mvp, "_at_get", return_value=[bk]), \
             patch.object(mvp, "_rechazar_oferta", return_value={"ok": True, "es_chofer": True,
                                                                 "accion": "rechazada"}) as reo:
            mvp.responder_oferta("+18095550002", "RECHAZO")
        reo.assert_called_once()

    def test_respuesta_tardia_toma_la_carrera_si_sigue_libre(self):
        """La oferta técnicamente venció pero sigue 'offered' y sin chofer: se acepta."""
        from datetime import timedelta
        log = "x oferta #1 (inmediata) -> DRV-2 (5.0 km)"
        vencida = (mvp._now_utc() - timedelta(seconds=90)).isoformat()
        bk = _booking({"offer_status": "offered", "offered_driver_id": "DRV-2",
                       "offer_expires_at": vencida, "offer_log": log, "driver_id": ""})
        aceptadas = []
        with patch.object(mvp, "_buscar_driver_por_tel", return_value=[self._driver(2)]), \
             patch.object(mvp, "_at_get", return_value=[bk]), \
             patch.object(mvp, "_aceptar_oferta",
                          side_effect=lambda b, d: aceptadas.append(d["fields"]["driver_id"]) or {"ok": True}):
            r = mvp.responder_oferta("+18095550002", "ACEPTO")
        assert r["ok"] and aceptadas == ["DRV-2"]
