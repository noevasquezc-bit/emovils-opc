"""
Emovils OPC — Catálogo de Productos B2C
Prioridad: Airport primero, luego Family, Medical, By Hour, Night Safe
"""

PRODUCTS = {
    "airport": {
        "id": "airport",
        "name": "Emovils Airport",
        "tagline": "Llegas a Santo Domingo y tu transporte ya está resuelto.",
        "priority": 1,
        "active_pilot": True,
        "description": "Traslado privado desde o hacia AILA/SDQ. Vehículo confirmado, chofer identificado, seguimiento por WhatsApp.",
        "price_usd": 25.0,
        "price_dop": None,   # Se calcula en tiempo real (tasa de cambio)
        "target_audience": [
            "Diáspora dominicana (NYC, NJ, Boston, Miami, Orlando, Puerto Rico, España)",
            "Turistas que viajan a Santo Domingo, Punta Cana, Bávaro, La Romana, Samaná",
            "Ejecutivos de negocios",
            "Familias con niños, maletas o adultos mayores"
        ],
        "pain_points": [
            "No quieren negociar transporte al salir del aeropuerto cansados",
            "Miedo de llegar y no tener transporte confirmado",
            "Viajan con familia/niños y necesitan certeza",
            "Ejecutivos que no pueden dejar su llegada al azar"
        ],
        "whatsapp_questions": {
            "general": [
                "Nombre completo del pasajero",
                "Fecha de llegada/salida",
                "Hora estimada (llegada o salida)",
                "Punto de recogida",
                "Destino final",
                "Cantidad de pasajeros",
                "Tipo de servicio (ida / regreso / espera)",
                "WhatsApp de contacto",
                "Forma de pago preferida"
            ],
            "airport_specific": [
                "Número de vuelo",
                "Aerolínea",
                "Hora estimada de llegada/salida",
                "Cantidad de maletas",
                "Punto de encuentro preferido (sala de llegadas, etc.)"
            ]
        },
        "promise": "Nuestro servicio es privado, formal, con precio confirmado antes de su llegada.",
        "campaign_hooks": {
            "seguridad": "Evita negociar transporte al salir del aeropuerto. Tu chofer te espera identificado y tu precio está confirmado.",
            "cansancio": "Después de un vuelo largo, no improvises tu traslado. Resérvalo antes de llegar.",
            "familia": "Si viajas con niños, maletas o adultos mayores, tu traslado debe estar resuelto antes de aterrizar.",
            "precio_claro": "Sin sorpresas al llegar. Cotiza antes de salir del aeropuerto.",
            "ejecutivo": "Traslado privado, puntual y formal para viajeros que no pueden dejar su llegada al azar."
        }
    },

    "family": {
        "id": "family",
        "name": "Emovils Family",
        "tagline": "Movemos a tu familia con el cuidado de un servicio privado.",
        "priority": 2,
        "active_pilot": False,
        "description": "Traslados familiares para padres con hijos en el exterior, familias sin chofer fijo.",
        "price_usd": 30.0,
        "target_audience": [
            "Hijos que viven fuera y coordinan traslados para sus padres",
            "Familias sin chofer fijo",
            "Adultos mayores con citas o diligencias"
        ]
    },

    "medical": {
        "id": "medical",
        "name": "Emovils Medical",
        "tagline": "Transporte confiable para citas médicas y procesos de salud.",
        "priority": 3,
        "active_pilot": False,
        "description": "Movilidad segura para citas médicas, laboratorios, clínicas.",
        "price_usd": 20.0,
        "target_audience": [
            "Adultos mayores y pacientes ambulatorios",
            "Familiares que coordinan citas desde el exterior",
            "Pacientes de clínicas y laboratorios"
        ]
    },

    "by_hour": {
        "id": "by_hour",
        "name": "Emovils By Hour",
        "tagline": "Un chofer y vehículo a tu disposición por el tiempo que necesites.",
        "priority": 4,
        "active_pilot": False,
        "description": "Servicio por horas para empresarios, consultores y familias.",
        "price_usd": 35.0,  # Por hora
        "target_audience": [
            "Empresarios y consultores",
            "Extranjeros en visita de negocios",
            "Ejecutivos y familias de paso"
        ]
    },

    "night_safe": {
        "id": "night_safe",
        "name": "Emovils Night Safe",
        "tagline": "Sales tranquilo. Regresas seguro.",
        "priority": 5,
        "active_pilot": False,
        "description": "Transporte nocturno seguro para salidas y regresos.",
        "price_usd": 30.0,
        "target_audience": [
            "Mujeres que salen de noche",
            "Parejas y turistas",
            "Ejecutivos con salidas nocturnas"
        ]
    }
}


def get_active_product():
    """Retorna el producto activo del piloto actual."""
    for product in PRODUCTS.values():
        if product.get("active_pilot"):
            return product
    return PRODUCTS["airport"]


def get_product_by_id(product_id: str) -> dict:
    """Retorna las especificaciones de un producto por ID."""
    return PRODUCTS.get(product_id, PRODUCTS["airport"])
