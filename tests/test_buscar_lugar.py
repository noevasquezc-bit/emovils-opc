"""
Tests OFFLINE de la búsqueda de lugares por nombre (tipo buscador de Google Maps).

Caso de negocio: el cliente escribe "Plaza de la Salud" o "Megacentro" en vez de
una dirección exacta. El geocoding clásico falla o resuelve a nivel país; el
fallback a Places Text Search debe encontrar el lugar y permitir cotizar.

Todo mockeado — no llama a Google.
"""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

os.environ.setdefault("GOOGLE_MAPS_API_KEY", "test-key")

import lib.google_maps as gm


def _resp(payload):
    m = MagicMock()
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    return m


PLACES_OK = {
    "status": "OK",
    "results": [{
        "name": "Hospital General de la Plaza de la Salud",
        "formatted_address": "Av. Ortega y Gasset, Santo Domingo, República Dominicana",
        "geometry": {"location": {"lat": 18.4794, "lng": -69.9276}},
        "types": ["hospital", "point_of_interest", "establishment"],
    }],
}

GEOCODE_VACIO = {"status": "ZERO_RESULTS", "results": []}

GEOCODE_SOLO_PAIS = {
    "status": "OK",
    "results": [{
        "formatted_address": "República Dominicana",
        "geometry": {"location": {"lat": 18.7357, "lng": -70.1627},
                     "location_type": "APPROXIMATE"},
        "types": ["country", "political"],
    }],
}


class TestBuscarLugar:
    def test_encuentra_lugar_en_rd(self):
        with patch.object(gm, "GOOGLE_MAPS_API_KEY", "test-key"), \
             patch.object(gm.requests, "get", return_value=_resp(PLACES_OK)):
            r = gm.buscar_lugar("plaza de la salud")
        assert r["lat"] == 18.4794 and r["lng"] == -69.9276
        assert "Plaza de la Salud" in r["formatted"]
        assert "República Dominicana" in r["formatted"]

    def test_acepta_direccion_sin_pais(self):
        """La API real con language=es omite el país: debe aceptarse por coordenadas."""
        sin_pais = {
            "status": "OK",
            "results": [{
                "name": "Hospital General De La Plaza de la Salud",
                "formatted_address": "Av. José Ortega y Gasset, Santo Domingo",
                "geometry": {"location": {"lat": 18.4887904, "lng": -69.9219151}},
                "types": ["hospital", "establishment"],
            }],
        }
        with patch.object(gm, "GOOGLE_MAPS_API_KEY", "test-key"), \
             patch.object(gm.requests, "get", return_value=_resp(sin_pais)):
            r = gm.buscar_lugar("plaza de la salud")
        assert r["lat"] == 18.4887904
        assert "Plaza de la Salud" in r["formatted"]

    def test_rechaza_resultado_fuera_de_rd(self):
        fuera = {
            "status": "OK",
            "results": [{
                "name": "Megacenter",
                "formatted_address": "La Paz, Bolivia",
                "geometry": {"location": {"lat": -16.5, "lng": -68.1}},
                "types": ["shopping_mall"],
            }],
        }
        with patch.object(gm, "GOOGLE_MAPS_API_KEY", "test-key"), \
             patch.object(gm.requests, "get", return_value=_resp(fuera)):
            assert gm.buscar_lugar("megacenter") == {}

    def test_zero_results_devuelve_vacio(self):
        with patch.object(gm, "GOOGLE_MAPS_API_KEY", "test-key"), \
             patch.object(gm.requests, "get", return_value=_resp({"status": "ZERO_RESULTS", "results": []})):
            assert gm.buscar_lugar("xyzzy no existe") == {}

    def test_request_denied_devuelve_vacio_sin_reventar(self):
        denied = {"status": "REQUEST_DENIED", "error_message": "Places API not enabled"}
        with patch.object(gm, "GOOGLE_MAPS_API_KEY", "test-key"), \
             patch.object(gm.requests, "get", return_value=_resp(denied)):
            assert gm.buscar_lugar("megacentro") == {}

    def test_sin_api_key_devuelve_vacio(self):
        with patch.object(gm, "GOOGLE_MAPS_API_KEY", ""):
            assert gm.buscar_lugar("megacentro") == {}


class TestGeocodeDetalladoConFallbackPlaces:
    def test_sin_resultados_cae_a_places(self):
        """Geocoding vacío + Places encuentra → preciso=True via_places."""
        def fake_get(url, params=None, timeout=None):
            if "/place/textsearch/" in url:
                return _resp(PLACES_OK)
            return _resp(GEOCODE_VACIO)
        with patch.object(gm, "GOOGLE_MAPS_API_KEY", "test-key"), \
             patch.object(gm.requests, "get", side_effect=fake_get):
            g = gm.geocode_detallado("plaza de la salud, República Dominicana")
        assert g["ok"] and g["preciso"] and g.get("via_places")
        assert g["lat"] == 18.4794

    def test_solo_pais_cae_a_places(self):
        """Geocoding resuelve solo a nivel país ('Caribe Tours') → Places rescata."""
        def fake_get(url, params=None, timeout=None):
            if "/place/textsearch/" in url:
                return _resp(PLACES_OK)
            return _resp(GEOCODE_SOLO_PAIS)
        with patch.object(gm, "GOOGLE_MAPS_API_KEY", "test-key"), \
             patch.object(gm.requests, "get", side_effect=fake_get):
            g = gm.geocode_detallado("caribe tours")
        assert g["ok"] and g["preciso"] and g.get("via_places")

    def test_solo_pais_y_places_falla_sigue_impreciso(self):
        """Si Places tampoco encuentra, se mantiene preciso=False (no inventar precio)."""
        def fake_get(url, params=None, timeout=None):
            if "/place/textsearch/" in url:
                return _resp({"status": "ZERO_RESULTS", "results": []})
            return _resp(GEOCODE_SOLO_PAIS)
        with patch.object(gm, "GOOGLE_MAPS_API_KEY", "test-key"), \
             patch.object(gm.requests, "get", side_effect=fake_get):
            g = gm.geocode_detallado("texto sin sentido")
        assert g["ok"] and not g["preciso"]

    def test_direccion_exacta_no_llama_places(self):
        """Una dirección que geocodifica bien NO debe gastar una llamada a Places."""
        exacto = {
            "status": "OK",
            "results": [{
                "formatted_address": "Av. Winston Churchill 93, Santo Domingo, República Dominicana",
                "geometry": {"location": {"lat": 18.47, "lng": -69.94},
                             "location_type": "ROOFTOP"},
                "types": ["street_address"],
            }],
        }
        llamadas = []
        def fake_get(url, params=None, timeout=None):
            llamadas.append(url)
            return _resp(exacto)
        with patch.object(gm, "GOOGLE_MAPS_API_KEY", "test-key"), \
             patch.object(gm.requests, "get", side_effect=fake_get):
            g = gm.geocode_detallado("Av. Winston Churchill 93")
        assert g["preciso"] and not g.get("via_places")
        assert all("/place/" not in u for u in llamadas)
