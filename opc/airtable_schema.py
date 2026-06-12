"""
Emovils OPC — Definición de Schema Airtable

Las 26 tablas que componen la base de datos de la OPC.
Cada tabla está definida como diccionario con campos, tipos y descripciones.

Usar este módulo como referencia única para:
  - Crear las tablas en Airtable
  - Validar datos antes de insertar
  - Documentar la estructura
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────
# TIPOS DE CAMPO (mapeados a Airtable)
# ─────────────────────────────────────────────────────────────

FT_TEXT = "singleLineText"
FT_LONG_TEXT = "multilineText"
FT_NUMBER = "number"
FT_CURRENCY = "currency"
FT_PERCENT = "percent"
FT_DATE = "date"
FT_DATETIME = "dateTime"
FT_CHECKBOX = "checkbox"
FT_SELECT = "singleSelect"
FT_MULTI_SELECT = "multipleSelects"
FT_PHONE = "phoneNumber"
FT_EMAIL = "email"
FT_URL = "url"
FT_ATTACHMENT = "multipleAttachments"
FT_LINK = "multipleRecordLinks"
FT_LOOKUP = "multipleLookupValues"
FT_FORMULA = "formula"
FT_ROLLUP = "rollup"
FT_AUTONUMBER = "autoNumber"
FT_RATING = "rating"


@dataclass
class Campo:
    nombre: str
    tipo: str
    descripcion: str = ""
    opciones: list[str] | None = None
    obligatorio: bool = False
    link_a: str | None = None  # nombre de tabla relacionada


@dataclass
class Tabla:
    nombre: str
    descripcion: str
    bloque: str  # operacional / recursos / crm / finanzas / sistema
    campos: list[Campo] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# BLOQUE 1 — OPERACIONAL
# ═══════════════════════════════════════════════════════════════

SERVICIOS = Tabla(
    nombre="Servicios",
    descripcion="Cada viaje individual (corazón del sistema)",
    bloque="operacional",
    campos=[
        Campo("Servicio_ID", FT_AUTONUMBER, "Identificador único auto (SVC-0001)"),
        Campo("Fecha", FT_DATE, "Fecha del servicio", obligatorio=True),
        Campo("Hora_salida", FT_TEXT, "HH:MM (24h)", obligatorio=True),
        Campo("Cliente_B2C", FT_LINK, "Si es B2C", link_a="Clientes_B2C"),
        Campo("Empresa_B2B", FT_LINK, "Si es B2B", link_a="Empresas_B2B"),
        Campo("Canal", FT_SELECT, "Canal de origen", opciones=[
            "Call_Center_Intelcia", "Call_Center_Otro", "Naviera",
            "VIP", "Referido", "Redes_Sociales", "Web", "Llamada_Directa"
        ]),
        Campo("Modo_servicio", FT_SELECT, "Programado o inmediato",
              opciones=["Programado", "Inmediato"]),
        Campo("Ruta_Intelcia", FT_LINK, "Si es Intelcia", link_a="Rutas_Intelcia"),
        Campo("Origen_texto", FT_TEXT, "Dirección textual"),
        Campo("Destino_texto", FT_TEXT, "Dirección textual"),
        Campo("Km_calculados", FT_NUMBER, "De Google Maps"),
        Campo("Modo_calculo_precio", FT_SELECT, opciones=["CIUDAD", "LARGA_DISTANCIA", "INTELCIA"]),
        Campo("Cantidad_pax", FT_NUMBER, "Pasajeros", obligatorio=True),
        Campo("Tarifa_aplicada_RD", FT_CURRENCY, "RD$", obligatorio=True),
        Campo("Recargo_nocturno_RD", FT_CURRENCY),
        Campo("Recargo_H1_RD", FT_CURRENCY),
        Campo("Recargo_espera_RD", FT_CURRENCY),
        Campo("Total_a_cobrar_RD", FT_CURRENCY),
        Campo("Pasajeros_lista", FT_LONG_TEXT, "Nombres + Z-IDs si aplica"),
        Campo("Chofer_asignado", FT_LINK, link_a="Conductores"),
        Campo("Vehiculo_asignado", FT_LINK, link_a="Vehiculos"),
        Campo("Estado", FT_SELECT, opciones=[
            "PENDIENTE", "BUSCANDO_CHOFER", "ASIGNADO", "EN_CAMINO",
            "EN_SITIO", "EN_SERVICIO", "COMPLETADO", "CANCELADO"
        ]),
        Campo("QR_cliente_url", FT_URL, "QR generado para el cliente"),
        Campo("QR_vehiculo_url", FT_URL, "QR del vehículo de este servicio"),
        Campo("QR_verificado_cliente", FT_CHECKBOX, "Cliente escaneó QR vehículo"),
        Campo("QR_verificado_chofer", FT_CHECKBOX, "Chofer escaneó QR cliente"),
        Campo("Forma_pago", FT_SELECT, opciones=[
            "TARJETA_TAP", "APPLE_PAY", "GOOGLE_PAY", "PAYPAL", "STRIPE",
            "AZUL_ONLINE", "TRANSFERENCIA", "EFECTIVO", "CREDITO_B2B"
        ]),
        Campo("Estado_pago", FT_SELECT, opciones=[
            "PENDIENTE", "PAGADO", "A_CREDITO", "REEMBOLSADO"
        ]),
        Campo("Comision_Emovils_RD", FT_CURRENCY, "Lo que retiene Emovils"),
        Campo("Pago_al_chofer_RD", FT_CURRENCY, "Lo que recibe el chofer"),
        Campo("Calificacion_estrellas", FT_RATING, "1-5 estrellas"),
        Campo("Comentario_cliente", FT_LONG_TEXT),
        Campo("Notas", FT_LONG_TEXT),
        Campo("Creado_en", FT_DATETIME),
    ],
)

RESERVAS = Tabla(
    nombre="Reservas",
    descripcion="Bookings con estado (puede previo a Servicio)",
    bloque="operacional",
    campos=[
        Campo("Reserva_ID", FT_AUTONUMBER),
        Campo("Servicio", FT_LINK, link_a="Servicios"),
        Campo("Cliente_B2C", FT_LINK, link_a="Clientes_B2C"),
        Campo("Empresa_B2B", FT_LINK, link_a="Empresas_B2B"),
        Campo("Fecha_reserva", FT_DATETIME),
        Campo("Fecha_servicio_solicitada", FT_DATETIME),
        Campo("Origen", FT_TEXT),
        Campo("Destino", FT_TEXT),
        Campo("Estado_reserva", FT_SELECT, opciones=[
            "BORRADOR", "CONFIRMADA", "PAGADA", "EN_SERVICIO", "COMPLETADA", "CANCELADA"
        ]),
        Campo("Canal_origen", FT_SELECT, opciones=[
            "Web", "WhatsApp", "Instagram", "Facebook", "Llamada", "Email_B2B", "Excel_Intelcia"
        ]),
        Campo("Precio_cotizado_RD", FT_CURRENCY),
        Campo("Notas", FT_LONG_TEXT),
    ],
)

PAGOS = Tabla(
    nombre="Pagos",
    descripcion="Transacciones de cobro",
    bloque="operacional",
    campos=[
        Campo("Pago_ID", FT_AUTONUMBER),
        Campo("Servicio", FT_LINK, link_a="Servicios"),
        Campo("Cliente_B2C", FT_LINK, link_a="Clientes_B2C"),
        Campo("Empresa_B2B", FT_LINK, link_a="Empresas_B2B"),
        Campo("Monto_RD", FT_CURRENCY),
        Campo("Forma_pago", FT_SELECT, opciones=[
            "TARJETA_TAP", "APPLE_PAY", "GOOGLE_PAY", "PAYPAL", "STRIPE",
            "AZUL_ONLINE", "TRANSFERENCIA", "EFECTIVO", "CREDITO_B2B"
        ]),
        Campo("Procesador", FT_SELECT, opciones=["Azul", "PayPal", "Stripe", "CardNet", "Manual"]),
        Campo("ID_externo", FT_TEXT, "ID del procesador (transaction_id)"),
        Campo("Estado", FT_SELECT, opciones=["PENDIENTE", "AUTORIZADO", "COMPLETADO", "REEMBOLSADO", "FALLIDO"]),
        Campo("Comprobante_url", FT_URL),
        Campo("Fecha", FT_DATETIME),
    ],
)

QR_VERIFICACIONES = Tabla(
    nombre="QR_Verificaciones",
    descripcion="Log de verificaciones bidireccionales chofer↔pasajero",
    bloque="operacional",
    campos=[
        Campo("Verificacion_ID", FT_AUTONUMBER),
        Campo("Servicio", FT_LINK, link_a="Servicios"),
        Campo("Tipo", FT_SELECT, opciones=["CLIENTE_ESCANEA_VEHICULO", "CHOFER_ESCANEA_CLIENTE"]),
        Campo("Resultado", FT_SELECT, opciones=["MATCH", "NO_MATCH", "EXPIRADO"]),
        Campo("Timestamp", FT_DATETIME),
        Campo("Ubicacion_lat", FT_NUMBER),
        Campo("Ubicacion_lng", FT_NUMBER),
        Campo("Notas", FT_LONG_TEXT),
    ],
)

EVENTOS_SERVICIO = Tabla(
    nombre="Eventos_Servicio",
    descripcion="Timeline de cada servicio (asignado, en camino, recogió, etc.)",
    bloque="operacional",
    campos=[
        Campo("Evento_ID", FT_AUTONUMBER),
        Campo("Servicio", FT_LINK, link_a="Servicios"),
        Campo("Tipo_evento", FT_SELECT, opciones=[
            "CREADO", "ASIGNADO", "CONDUCTOR_ACEPTO", "EN_CAMINO",
            "EN_SITIO", "QR_VERIFICADO", "SERVICIO_INICIADO",
            "EN_VIAJE", "LLEGADA_DESTINO", "COMPLETADO", "CANCELADO",
            "INCIDENCIA", "REASIGNADO"
        ]),
        Campo("Timestamp", FT_DATETIME),
        Campo("Actor", FT_TEXT, "Quién originó el evento (sistema/chofer/cliente)"),
        Campo("Detalle", FT_LONG_TEXT),
    ],
)

INCIDENCIAS = Tabla(
    nombre="Incidencias",
    descripcion="Problemas durante o después de un servicio",
    bloque="operacional",
    campos=[
        Campo("Incidencia_ID", FT_AUTONUMBER),
        Campo("Servicio", FT_LINK, link_a="Servicios"),
        Campo("Tipo", FT_SELECT, opciones=[
            "CHOFER_NO_APARECIO", "CLIENTE_NO_APARECIO", "QUEJA_SERVICIO",
            "ACCIDENTE", "RETRASO_FUERTE", "OBJETO_OLVIDADO",
            "PROBLEMA_PAGO", "QUEJA_CHOFER", "OTRO"
        ]),
        Campo("Severidad", FT_SELECT, opciones=["BAJA", "MEDIA", "ALTA", "CRITICA"]),
        Campo("Estado", FT_SELECT, opciones=["ABIERTA", "EN_PROCESO", "RESUELTA", "ESCALADA_DUEÑO"]),
        Campo("Descripcion", FT_LONG_TEXT),
        Campo("Reportada_por", FT_TEXT),
        Campo("Resolucion", FT_LONG_TEXT),
        Campo("Fecha_reporte", FT_DATETIME),
        Campo("Fecha_resolucion", FT_DATETIME),
    ],
)

ENCUESTAS = Tabla(
    nombre="Encuestas",
    descripcion="Calidad post-servicio (1-5 estrellas + comentario)",
    bloque="operacional",
    campos=[
        Campo("Encuesta_ID", FT_AUTONUMBER),
        Campo("Servicio", FT_LINK, link_a="Servicios"),
        Campo("Cliente_B2C", FT_LINK, link_a="Clientes_B2C"),
        Campo("Estrellas", FT_RATING),
        Campo("Comentario", FT_LONG_TEXT),
        Campo("Recomendaria", FT_CHECKBOX),
        Campo("Acepto_resena_google", FT_CHECKBOX),
        Campo("Fecha", FT_DATETIME),
    ],
)


# ═══════════════════════════════════════════════════════════════
# BLOQUE 2 — RECURSOS
# ═══════════════════════════════════════════════════════════════

VEHICULOS = Tabla(
    nombre="Vehiculos",
    descripcion="Flota completa: propios + afiliados",
    bloque="recursos",
    campos=[
        Campo("Placa", FT_TEXT, obligatorio=True),
        Campo("Tipo", FT_SELECT, opciones=["Caravan", "H1", "Otro"]),
        Campo("Marca", FT_TEXT),
        Campo("Modelo", FT_TEXT),
        Campo("Año", FT_NUMBER),
        Campo("Color", FT_TEXT),
        Campo("Capacidad_pasajeros", FT_NUMBER),
        Campo("Propietario_tipo", FT_SELECT, opciones=["Emovils_Propio", "Afiliado"]),
        Campo("Conductor_propietario", FT_LINK, "Si es afiliado", link_a="Conductores"),
        Campo("Chasis_VIN", FT_TEXT),
        Campo("Motor_numero", FT_TEXT),
        Campo("Foto_exterior", FT_ATTACHMENT),
        Campo("Foto_interior", FT_ATTACHMENT),
        Campo("Marbete_doc", FT_ATTACHMENT),
        Campo("Marbete_vencimiento", FT_DATE),
        Campo("Seguro_doc", FT_ATTACHMENT),
        Campo("Seguro_vencimiento", FT_DATE),
        Campo("Revision_tecnica_doc", FT_ATTACHMENT),
        Campo("Revision_tecnica_vencimiento", FT_DATE),
        Campo("Permiso_INTRANT_doc", FT_ATTACHMENT),
        Campo("Estado", FT_SELECT, opciones=["ACTIVO", "MANTENIMIENTO", "INACTIVO"]),
        Campo("Km_actual", FT_NUMBER),
        Campo("Ultimo_mantenimiento_fecha", FT_DATE),
        Campo("Proximo_mantenimiento_fecha", FT_DATE),
        Campo("Consumo_km_litro", FT_NUMBER),
        Campo("QR_lateral_url", FT_URL),
        Campo("Notas", FT_LONG_TEXT),
    ],
)

DOCUMENTOS_VEHICULOS = Tabla(
    nombre="Documentos_Vehiculos",
    descripcion="Histórico de documentos por vehículo",
    bloque="recursos",
    campos=[
        Campo("Doc_ID", FT_AUTONUMBER),
        Campo("Vehiculo", FT_LINK, link_a="Vehiculos"),
        Campo("Tipo_documento", FT_SELECT, opciones=[
            "MARBETE", "SEGURO", "REVISION_TECNICA", "INTRANT", "OTRO"
        ]),
        Campo("Archivo", FT_ATTACHMENT),
        Campo("Fecha_emision", FT_DATE),
        Campo("Fecha_vencimiento", FT_DATE),
        Campo("Estado", FT_SELECT, opciones=["VIGENTE", "POR_VENCER", "VENCIDO"]),
    ],
)

CONDUCTORES = Tabla(
    nombre="Conductores",
    descripcion="16 propios + 12 afiliados",
    bloque="recursos",
    campos=[
        Campo("Codigo", FT_TEXT, "C001, A001, etc.", obligatorio=True),
        Campo("Nombre_completo", FT_TEXT, obligatorio=True),
        Campo("Tipo", FT_SELECT, opciones=["Propio", "Afiliado"], obligatorio=True),
        Campo("Cedula", FT_TEXT, obligatorio=True),
        Campo("WhatsApp", FT_PHONE, obligatorio=True),
        Campo("Email", FT_EMAIL),
        Campo("Direccion", FT_LONG_TEXT),
        Campo("Foto_perfil", FT_ATTACHMENT),
        Campo("Fecha_nacimiento", FT_DATE),
        Campo("Contacto_emergencia_nombre", FT_TEXT),
        Campo("Contacto_emergencia_telefono", FT_PHONE),
        Campo("Vehiculo_asignado", FT_LINK, link_a="Vehiculos"),
        Campo("Zona_base", FT_MULTI_SELECT, opciones=[
            "Santo Domingo Este", "Santo Domingo Norte", "Santo Domingo Oeste",
            "Distrito Nacional", "Villa Mella", "Boca Chica",
            "San Pedro de Macorís", "La Romana", "Punta Cana"
        ]),
        Campo("Idiomas", FT_MULTI_SELECT, opciones=["Español", "Inglés", "Francés", "Italiano"]),
        Campo("Especialidad", FT_MULTI_SELECT, opciones=[
            "Aeropuerto", "VIP", "Corporativo", "Naviera", "Eventos", "Multi-leg"
        ]),
        Campo("Estado_actual", FT_SELECT, opciones=[
            "DISPONIBLE", "OCUPADO", "OFFLINE", "MANTENIMIENTO", "INACTIVO"
        ]),
        Campo("Ultima_ubicacion_lat", FT_NUMBER),
        Campo("Ultima_ubicacion_lng", FT_NUMBER),
        Campo("Calificacion_promedio", FT_NUMBER),
        # Documentos
        Campo("Licencia_conducir_categoria", FT_TEXT),
        Campo("Licencia_doc", FT_ATTACHMENT),
        Campo("Licencia_vencimiento", FT_DATE),
        Campo("Cedula_scan", FT_ATTACHMENT),
        Campo("Antecedentes_penales_doc", FT_ATTACHMENT),
        Campo("Antecedentes_penales_fecha", FT_DATE),
        Campo("Certificado_medico_doc", FT_ATTACHMENT),
        Campo("Certificado_medico_fecha", FT_DATE),
        Campo("Contrato_afiliado_doc", FT_ATTACHMENT),
        # Financiero (solo afiliados)
        Campo("Banco", FT_TEXT),
        Campo("Cuenta_bancaria", FT_TEXT),
        Campo("Tipo_cuenta", FT_SELECT, opciones=["Ahorro", "Corriente"]),
        Campo("RNC", FT_TEXT),
        Campo("Comision_pct", FT_PERCENT, "30% para afiliados estándar"),
        # Calculados
        Campo("Saldo_quincena_RD", FT_CURRENCY),
        Campo("Comision_emovils_acumulada_RD", FT_CURRENCY),
        Campo("Servicios_totales", FT_NUMBER),
        Campo("Quejas", FT_NUMBER),
        Campo("Felicitaciones", FT_NUMBER),
        Campo("Fecha_ingreso", FT_DATE),
        Campo("Activo", FT_CHECKBOX),
    ],
)

DOCUMENTOS_CONDUCTORES = Tabla(
    nombre="Documentos_Conductores",
    descripcion="Histórico de documentos por conductor",
    bloque="recursos",
    campos=[
        Campo("Doc_ID", FT_AUTONUMBER),
        Campo("Conductor", FT_LINK, link_a="Conductores"),
        Campo("Tipo_documento", FT_SELECT, opciones=[
            "LICENCIA", "CEDULA", "ANTECEDENTES_PENALES",
            "CERTIFICADO_MEDICO", "CONTRATO", "OTRO"
        ]),
        Campo("Archivo", FT_ATTACHMENT),
        Campo("Fecha_emision", FT_DATE),
        Campo("Fecha_vencimiento", FT_DATE),
        Campo("Estado", FT_SELECT, opciones=["VIGENTE", "POR_VENCER", "VENCIDO"]),
    ],
)

TARIFAS_REFERENCIA = Tabla(
    nombre="Tarifas_Referencia",
    descripcion="~50 trayectos pre-calculados para consulta rápida",
    bloque="recursos",
    campos=[
        Campo("Tarifa_ID", FT_AUTONUMBER),
        Campo("Origen", FT_TEXT, obligatorio=True),
        Campo("Destino", FT_TEXT, obligatorio=True),
        Campo("Km", FT_NUMBER),
        Campo("Tarifa_dia_RD", FT_CURRENCY),
        Campo("Tarifa_noche_RD", FT_CURRENCY),
        Campo("Modo", FT_SELECT, opciones=["CIUDAD", "LARGA_DISTANCIA"]),
        Campo("Categoria", FT_SELECT, opciones=[
            "AILA_Hacia", "AILA_Desde", "Centro_SD_Hacia", "Intra_SD", "Interurbano"
        ]),
    ],
)

RUTAS_INTELCIA = Tabla(
    nombre="Rutas_Intelcia",
    descripcion="Las 13 rutas pre-acordadas con Intelcia",
    bloque="recursos",
    campos=[
        Campo("Ruta_ID", FT_NUMBER, "1-13", obligatorio=True),
        Campo("Nombre_ruta", FT_TEXT),
        Campo("Zonas_cubiertas", FT_MULTI_SELECT),
        Campo("Tarifa_1_4_pax_RD", FT_CURRENCY),
        Campo("Tarifa_5_10_pax_RD", FT_CURRENCY),
        Campo("Notas_especiales", FT_LONG_TEXT),
        Campo("Activa", FT_CHECKBOX),
    ],
)


# ═══════════════════════════════════════════════════════════════
# BLOQUE 3 — CRM Y CLIENTES
# ═══════════════════════════════════════════════════════════════

CLIENTES_B2C = Tabla(
    nombre="Clientes_B2C",
    descripcion="Individuos: turistas, ejecutivos, VIP, regulares",
    bloque="crm",
    campos=[
        Campo("Cliente_ID", FT_AUTONUMBER),
        Campo("Nombre_completo", FT_TEXT, obligatorio=True),
        Campo("WhatsApp", FT_PHONE, obligatorio=True),
        Campo("Email", FT_EMAIL),
        Campo("Idioma_preferido", FT_SELECT, opciones=["Español", "Inglés", "Francés"]),
        Campo("Pais", FT_TEXT),
        Campo("Notas", FT_LONG_TEXT),
        Campo("Servicios_totales", FT_NUMBER),
        Campo("Total_gastado_RD", FT_CURRENCY),
        Campo("Ultimo_servicio_fecha", FT_DATE),
        Campo("Es_VIP", FT_CHECKBOX),
        Campo("Activo", FT_CHECKBOX),
        Campo("Creado_en", FT_DATETIME),
    ],
)

EMPRESAS_B2B = Tabla(
    nombre="Empresas_B2B",
    descripcion="Intelcia, navieras, hoteles, otros corporativos",
    bloque="crm",
    campos=[
        Campo("Empresa_ID", FT_AUTONUMBER),
        Campo("Razon_social", FT_TEXT, obligatorio=True),
        Campo("Nombre_comercial", FT_TEXT),
        Campo("RNC", FT_TEXT),
        Campo("Tipo", FT_SELECT, opciones=[
            "Call_Center", "Naviera", "Hotel", "Agencia_Viajes",
            "Corporativo_Otro", "Embajada", "Gobierno"
        ]),
        Campo("Direccion", FT_LONG_TEXT),
        Campo("Telefono", FT_PHONE),
        Campo("Email_general", FT_EMAIL),
        Campo("Codigo_corporativo", FT_TEXT, "Para validar empleados"),
        Campo("Plazo_pago_dias", FT_NUMBER, "15, 30, etc."),
        Campo("Limite_credito_RD", FT_CURRENCY),
        Campo("Forma_recepcion_pedidos", FT_SELECT, opciones=[
            "Excel_diario", "Tabla_estructurada", "Email", "WhatsApp", "Llamada"
        ]),
        Campo("Tarifas_acordadas_doc", FT_ATTACHMENT),
        Campo("Contrato_doc", FT_ATTACHMENT),
        Campo("Fecha_inicio_relacion", FT_DATE),
        Campo("Saldo_pendiente_RD", FT_CURRENCY),
        Campo("Servicios_mes_actual", FT_NUMBER),
        Campo("Activo", FT_CHECKBOX),
        Campo("Notas", FT_LONG_TEXT),
    ],
)

CONTACTOS_EMPRESA = Tabla(
    nombre="Contactos_Empresa",
    descripcion="Personas dentro de cada empresa (HR, Ops, Decisor)",
    bloque="crm",
    campos=[
        Campo("Contacto_ID", FT_AUTONUMBER),
        Campo("Empresa", FT_LINK, link_a="Empresas_B2B"),
        Campo("Nombre", FT_TEXT, obligatorio=True),
        Campo("Cargo", FT_TEXT),
        Campo("Departamento", FT_SELECT, opciones=[
            "RRHH", "Operaciones", "Compras", "Direccion", "Otro"
        ]),
        Campo("WhatsApp", FT_PHONE),
        Campo("Email", FT_EMAIL),
        Campo("Es_decisor", FT_CHECKBOX),
        Campo("Notas", FT_LONG_TEXT),
    ],
)

EMPLEADOS_AUTORIZADOS_B2B = Tabla(
    nombre="Empleados_Autorizados_B2B",
    descripcion="Lista blanca para validar quién es 'a crédito'",
    bloque="crm",
    campos=[
        Campo("Empleado_ID", FT_AUTONUMBER),
        Campo("Empresa", FT_LINK, link_a="Empresas_B2B"),
        Campo("Nombre", FT_TEXT, obligatorio=True),
        Campo("Cedula", FT_TEXT),
        Campo("Codigo_interno", FT_TEXT, "Z-ID de Intelcia, código de empleado, etc."),
        Campo("WhatsApp", FT_PHONE),
        Campo("Email", FT_EMAIL),
        Campo("Departamento", FT_TEXT),
        Campo("Direccion_RRHH", FT_LONG_TEXT),
        Campo("Activo", FT_CHECKBOX),
        Campo("Fecha_alta", FT_DATE),
        Campo("Fecha_baja", FT_DATE),
    ],
)

HISTORIAL_CLIENTE = Tabla(
    nombre="Historial_Cliente",
    descripcion="Todos los servicios pasados por cliente para CRM y referidos",
    bloque="crm",
    campos=[
        Campo("Historial_ID", FT_AUTONUMBER),
        Campo("Cliente_B2C", FT_LINK, link_a="Clientes_B2C"),
        Campo("Servicio", FT_LINK, link_a="Servicios"),
        Campo("Fecha", FT_DATE),
        Campo("Monto_RD", FT_CURRENCY),
        Campo("Calificacion", FT_RATING),
    ],
)


# ═══════════════════════════════════════════════════════════════
# BLOQUE 4 — FINANZAS Y LIQUIDACIÓN
# ═══════════════════════════════════════════════════════════════

LIQUIDACIONES = Tabla(
    nombre="Liquidaciones",
    descripcion="Resumen quincenal por afiliado",
    bloque="finanzas",
    campos=[
        Campo("Liquidacion_ID", FT_TEXT, "Ej: LQ-2026-Q12 (quincena 12 de 2026)"),
        Campo("Conductor", FT_LINK, link_a="Conductores"),
        Campo("Quincena", FT_SELECT, opciones=[
            "Q1_ENE", "Q2_ENE", "Q1_FEB", "Q2_FEB", "Q1_MAR", "Q2_MAR",
            "Q1_ABR", "Q2_ABR", "Q1_MAY", "Q2_MAY", "Q1_JUN", "Q2_JUN",
            "Q1_JUL", "Q2_JUL", "Q1_AGO", "Q2_AGO", "Q1_SEP", "Q2_SEP",
            "Q1_OCT", "Q2_OCT", "Q1_NOV", "Q2_NOV", "Q1_DIC", "Q2_DIC",
        ]),
        Campo("Año", FT_NUMBER),
        Campo("Servicios_total", FT_NUMBER),
        Campo("Facturacion_quincena_RD", FT_CURRENCY),
        Campo("Pago_al_chofer_RD", FT_CURRENCY),
        Campo("Comision_Emovils_RD", FT_CURRENCY),
        Campo("Estado", FT_SELECT, opciones=["PENDIENTE", "APROBADA", "PAGADA"]),
        Campo("Fecha_corte", FT_DATE),
        Campo("Fecha_pago", FT_DATE),
        Campo("Comprobante_pago_url", FT_URL),
        Campo("Notas", FT_LONG_TEXT),
    ],
)

DETALLE_LIQUIDACION = Tabla(
    nombre="Detalle_Liquidacion",
    descripcion="Servicios incluidos en cada liquidación",
    bloque="finanzas",
    campos=[
        Campo("Detalle_ID", FT_AUTONUMBER),
        Campo("Liquidacion", FT_LINK, link_a="Liquidaciones"),
        Campo("Servicio", FT_LINK, link_a="Servicios"),
        Campo("Fecha_servicio", FT_DATE),
        Campo("Monto_servicio_RD", FT_CURRENCY),
        Campo("Comision_RD", FT_CURRENCY),
        Campo("Pago_chofer_RD", FT_CURRENCY),
    ],
)

PAGOS_AFILIADOS = Tabla(
    nombre="Pagos_Afiliados",
    descripcion="Transferencias hechas + comprobantes",
    bloque="finanzas",
    campos=[
        Campo("Pago_ID", FT_AUTONUMBER),
        Campo("Liquidacion", FT_LINK, link_a="Liquidaciones"),
        Campo("Conductor", FT_LINK, link_a="Conductores"),
        Campo("Monto_RD", FT_CURRENCY),
        Campo("Banco_origen", FT_TEXT),
        Campo("Cuenta_origen", FT_TEXT),
        Campo("Referencia_transferencia", FT_TEXT),
        Campo("Comprobante", FT_ATTACHMENT),
        Campo("Fecha", FT_DATETIME),
        Campo("Aprobado_por", FT_TEXT),
    ],
)

FACTURAS_NCF = Tabla(
    nombre="Facturas_NCF",
    descripcion="Facturación electrónica DGII",
    bloque="finanzas",
    campos=[
        Campo("NCF", FT_TEXT, "Número Comprobante Fiscal", obligatorio=True),
        Campo("Empresa_B2B", FT_LINK, link_a="Empresas_B2B"),
        Campo("Fecha_emision", FT_DATE),
        Campo("Periodo_servicios", FT_TEXT, "Ej: 'Junio 2026' o '1-15 Jun 2026'"),
        Campo("Subtotal_RD", FT_CURRENCY),
        Campo("ITBIS_RD", FT_CURRENCY),
        Campo("Total_RD", FT_CURRENCY),
        Campo("Servicios_incluidos", FT_LINK, link_a="Servicios"),
        Campo("Estado", FT_SELECT, opciones=["BORRADOR", "EMITIDA", "ENVIADA", "PAGADA", "VENCIDA"]),
        Campo("Fecha_vencimiento", FT_DATE),
        Campo("Fecha_pago", FT_DATE),
        Campo("PDF_factura", FT_ATTACHMENT),
        Campo("XML_DGII", FT_ATTACHMENT),
    ],
)

INGRESOS_EGRESOS = Tabla(
    nombre="Ingresos_Egresos",
    descripcion="Tabla contable para reportes",
    bloque="finanzas",
    campos=[
        Campo("Movimiento_ID", FT_AUTONUMBER),
        Campo("Tipo", FT_SELECT, opciones=["INGRESO", "EGRESO"]),
        Campo("Categoria", FT_SELECT, opciones=[
            # Ingresos
            "COBROS_B2C", "FACTURACION_B2B", "COMISIONES_RETENIDAS", "OTROS_INGRESOS",
            # Egresos operativos
            "PAGO_AFILIADOS", "SALARIO_PROPIOS", "COMBUSTIBLE",
            "MANTENIMIENTO", "SEGUROS", "COMISIONES_TARJETA",
            # Egresos admin
            "TECNOLOGIA_IA", "OFICINA", "MARKETING_APIFY", "HONORARIOS",
            # Impuestos
            "ITBIS", "ISR", "OTROS_IMPUESTOS"
        ]),
        Campo("Concepto", FT_TEXT),
        Campo("Monto_RD", FT_CURRENCY),
        Campo("Fecha", FT_DATE),
        Campo("Mes", FT_TEXT, "YYYY-MM"),
        Campo("Servicio_relacionado", FT_LINK, link_a="Servicios"),
        Campo("Liquidacion_relacionada", FT_LINK, link_a="Liquidaciones"),
        Campo("Factura_relacionada", FT_LINK, link_a="Facturas_NCF"),
        Campo("Comprobante", FT_ATTACHMENT),
        Campo("Conciliado_con_contabilidad", FT_CHECKBOX),
    ],
)


# ═══════════════════════════════════════════════════════════════
# BLOQUE 5 — CAPTACIÓN Y SISTEMA
# ═══════════════════════════════════════════════════════════════

PIPELINE_COMERCIAL = Tabla(
    nombre="Pipeline_Comercial",
    descripcion="Prospects B2B (call centers nuevos, hoteles)",
    bloque="captacion",
    campos=[
        Campo("Prospect_ID", FT_AUTONUMBER),
        Campo("Empresa_nombre", FT_TEXT),
        Campo("Tipo_empresa", FT_SELECT, opciones=[
            "Call_Center", "Hotel", "Naviera", "Agencia_Viajes",
            "Corporativo_Otro"
        ]),
        Campo("Fuente_scraping", FT_SELECT, opciones=[
            "Apify_Google_Maps", "Apify_LinkedIn", "Apollo",
            "Manual", "Referido_Cliente"
        ]),
        Campo("Contacto_principal", FT_TEXT),
        Campo("Cargo", FT_TEXT),
        Campo("Email", FT_EMAIL),
        Campo("LinkedIn_url", FT_URL),
        Campo("WhatsApp", FT_PHONE),
        Campo("Estado_pipeline", FT_SELECT, opciones=[
            "NUEVO", "CONTACTADO", "EMAIL_ABIERTO", "RESPONDIO",
            "LLAMADA_AGENDADA", "DEMO_REALIZADA", "PROPUESTA_ENVIADA",
            "GANADO", "PERDIDO", "EN_PAUSA"
        ]),
        Campo("Score_calificacion", FT_NUMBER, "1-100"),
        Campo("Razon_perdida", FT_LONG_TEXT),
        Campo("Fecha_primer_contacto", FT_DATETIME),
        Campo("Fecha_ultimo_contacto", FT_DATETIME),
        Campo("Convertido_a_Empresa_B2B", FT_LINK, link_a="Empresas_B2B"),
        Campo("Notas", FT_LONG_TEXT),
    ],
)

EMAIL_CAMPAÑAS = Tabla(
    nombre="Email_Campañas",
    descripcion="Cada email outreach enviado + tracking",
    bloque="captacion",
    campos=[
        Campo("Email_ID", FT_AUTONUMBER),
        Campo("Prospect", FT_LINK, link_a="Pipeline_Comercial"),
        Campo("Campaña", FT_TEXT),
        Campo("Asunto", FT_TEXT),
        Campo("Cuerpo", FT_LONG_TEXT),
        Campo("Personalizacion_aplicada", FT_LONG_TEXT, "Datos custom usados"),
        Campo("Fecha_envio", FT_DATETIME),
        Campo("Estado", FT_SELECT, opciones=[
            "PROGRAMADO", "ENVIADO", "ABIERTO", "CLICKEADO", "RESPONDIO", "BOUNCED"
        ]),
        Campo("Plataforma", FT_SELECT, opciones=["Smartlead", "Instantly", "Manual"]),
    ],
)

METRICAS_DIARIAS = Tabla(
    nombre="Metricas_Diarias",
    descripcion="KPIs para dashboard del dueño",
    bloque="sistema",
    campos=[
        Campo("Fecha", FT_DATE, obligatorio=True),
        Campo("Servicios_completados", FT_NUMBER),
        Campo("Servicios_cancelados", FT_NUMBER),
        Campo("Facturacion_RD", FT_CURRENCY),
        Campo("Comisiones_pagadas_RD", FT_CURRENCY),
        Campo("Ganancia_operativa_RD", FT_CURRENCY),
        Campo("Conductores_activos", FT_NUMBER),
        Campo("Incidencias_abiertas", FT_NUMBER),
        Campo("Calificacion_promedio_dia", FT_NUMBER),
        Campo("Leads_nuevos", FT_NUMBER),
        Campo("Demos_agendadas", FT_NUMBER),
        Campo("Alertas_sistema", FT_LONG_TEXT),
    ],
)


# ─────────────────────────────────────────────────────────────
# LISTA MAESTRA DE TODAS LAS TABLAS
# ─────────────────────────────────────────────────────────────

TODAS_LAS_TABLAS: list[Tabla] = [
    # Operacional
    SERVICIOS, RESERVAS, PAGOS, QR_VERIFICACIONES, EVENTOS_SERVICIO,
    INCIDENCIAS, ENCUESTAS,
    # Recursos
    VEHICULOS, DOCUMENTOS_VEHICULOS, CONDUCTORES, DOCUMENTOS_CONDUCTORES,
    TARIFAS_REFERENCIA, RUTAS_INTELCIA,
    # CRM
    CLIENTES_B2C, EMPRESAS_B2B, CONTACTOS_EMPRESA,
    EMPLEADOS_AUTORIZADOS_B2B, HISTORIAL_CLIENTE,
    # Finanzas
    LIQUIDACIONES, DETALLE_LIQUIDACION, PAGOS_AFILIADOS,
    FACTURAS_NCF, INGRESOS_EGRESOS,
    # Captación
    PIPELINE_COMERCIAL, EMAIL_CAMPAÑAS,
    # Sistema
    METRICAS_DIARIAS,
]


def resumen() -> str:
    """Devuelve un resumen del schema completo."""
    from collections import Counter
    bloques = Counter(t.bloque for t in TODAS_LAS_TABLAS)
    total_campos = sum(len(t.campos) for t in TODAS_LAS_TABLAS)
    lineas = [
        f"📊 EMOVILS OPC — Schema Airtable",
        f"════════════════════════════════════",
        f"Total tablas: {len(TODAS_LAS_TABLAS)}",
        f"Total campos: {total_campos}",
        f"",
        f"Tablas por bloque:",
    ]
    for bloque, count in bloques.most_common():
        lineas.append(f"  • {bloque}: {count}")
    lineas.append("")
    lineas.append("Detalle por tabla:")
    for t in TODAS_LAS_TABLAS:
        lineas.append(f"  [{t.bloque[:5]}] {t.nombre} ({len(t.campos)} campos)")
    return "\n".join(lineas)


if __name__ == "__main__":
    print(resumen())
