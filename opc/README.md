# Emovils OPC — Módulo `/opc/`

Motor real de la One Person Company de Emovils. Construido sobre la infraestructura existente (Flask, Airtable, Green API, Google Maps).

---

## 📦 Estado de construcción

### ✅ COMPLETADO (sprint inicial)

#### Núcleo de cálculo
| Módulo | Función | Estado |
|--------|---------|--------|
| `precios.py` | Calculadora de tarifas completa (Ciudad/Larga Distancia + recargos + comisiones) | ✅ Probado |
| `intelcia_parser.py` | Lee Excel real de Intelcia y extrae servicios | ✅ Probado con data real (16 servicios extraídos) |
| `qr_generator_opc.py` | Sistema QR bidireccional firmado HMAC-SHA256 | ✅ 5 casos validados |

#### Base de datos
| Módulo | Función | Estado |
|--------|---------|--------|
| `airtable_schema.py` | Definición de 26 tablas con 336 campos | ✅ Documentado |
| `airtable_api_opc.py` | Cliente CRUD con retry, batching, helpers | ✅ Probado en producción |
| `bootstrap_airtable.py` | Crea las 26 tablas automáticamente en Airtable | ✅ **Ejecutado** |
| `cargar_datos_iniciales.py` | Carga tarifarios + servicios reales | ✅ **Ejecutado** |

#### Agentes IA
| Módulo | Función | Estado |
|--------|---------|--------|
| `agente_ingesta.py` | Orquestador de los 5 canales (Excel, WhatsApp, web, IG, llamada) | ✅ Probado |
| `agente_despachador.py` | Algoritmo Uber-style: zona + capacidad + rotación + cadena de fallback | ✅ 5 escenarios validados |
| `agente_coordinador.py` | Cerebro WhatsApp (Monserrat). Cotiza, reserva, escalado de quejas | ✅ 8 conversaciones probadas |
| `agente_financiero.py` | Liquidación quincenal automática 30%/70% afiliados | ✅ Conectado |
| `agente_reportes.py` | Reporte diario al dueño 7AM con KPIs y alertas | ✅ Datos reales (RD$12,200) |

#### Servidor
| Archivo | Función | Estado |
|--------|---------|--------|
| `main_opc.py` (en root) | Flask con 9 endpoints REST v2 | ✅ Importa OK |

### 📊 En Airtable producción (https://airtable.com/appEfjmRTQSywhCmt)

```
27 tablas creadas · 336 campos · 36 links entre tablas

Datos cargados (REAL):
  ✓ 13 rutas Intelcia con zonas y tarifas
  ✓ 40 tarifas de referencia pre-calculadas
  ✓ Empresa B2B Intelcia (código INTELCIA-2026, plazo 30 días)
  ✓ 16 servicios reales del 6-jun-2026 con sus pasajeros
    → RD$12,200 facturados
    → RD$3,660 comisión Emovils
    → RD$8,540 pago a choferes
```

### 📁 Datos auxiliares

| Archivo | Contenido |
|---------|-----------|
| `data/tarifario_intelcia.json` | Las 13 rutas con zonas y tarifas |
| `data/tarifario_referencia.json` | 40+ trayectos pre-calculados |
| `data/choferes_template.json` | Estructura para los 28 conductores |

---

## 🟡 Pendiente (próximo sprint)

| Componente | Por qué |
|------------|---------|
| `agente_voz_dominicana.py` | Integración ElevenLabs (requiere voz clonada) |
| `agente_social.py` | Publicación IG/FB (requiere Meta Business token) |
| `agente_prospector.py` | Scraping Apify (Fase 3) |
| `agente_outreach.py` | Email cold (Fase 3) |
| `agente_sdr_voz.py` | Llamadas Vapi (Fase 3) |
| `integracion_web.py` | Conectar emovils.com a backend |
| `dashboard_web.html` | Vista gráfica del dashboard |

---

## 🚀 Cómo probar cada módulo

```bash
cd /Users/noevasquez/Desktop/PROYECTO\ OPC/emovils-opc/

# Calculadora de precios
python3 opc/precios.py

# Parser Excel Intelcia (con archivo real)
python3 opc/intelcia_parser.py

# Schema (lista las 26 tablas)
python3 opc/airtable_schema.py

# QR bidireccional (genera PNGs + valida casos)
python3 opc/qr_generator_opc.py

# Despachador (5 escenarios de asignación)
python3 opc/agente_despachador.py

# Ingesta multi-canal
python3 opc/agente_ingesta.py

# Coordinador (Monserrat conversa)
python3 opc/agente_coordinador.py

# Financiero (cierre quincena, datos reales)
python3 opc/agente_financiero.py

# Reportes (lo que llega al dueño cada mañana)
python3 opc/agente_reportes.py

# Servidor Flask completo (puerto 5001)
python3 main_opc.py
```

---

## 🌐 Endpoints API (main_opc.py)

| Endpoint | Método | Función |
|----------|--------|---------|
| `/` | GET | Info del sistema |
| `/health` | GET | Healthcheck |
| `/api/v2/cotizar` | POST | Cotización instantánea (texto libre o estructurado) |
| `/api/v2/reservar` | POST | Crea reserva + asigna conductor + genera QR |
| `/api/v2/whatsapp/webhook` | POST | Recibe mensajes de Green API |
| `/api/v2/qr/validar` | POST | Valida QR escaneado (CHOFER o CLIENTE) |
| `/api/v2/intelcia/ingestar` | POST | Procesa Excel de Intelcia (path o multipart) |
| `/api/v2/reporte/diario` | GET | Reporte diario para el dueño |
| `/api/v2/liquidaciones/quincena` | POST | Cierre de quincena |

---

## 🔐 Variables de entorno

Configuradas en `.env`:

```bash
# Airtable (TOKEN NUEVO, base nueva)
AIRTABLE_API_KEY=patQUDAQ...
AIRTABLE_BASE_ID=appEfjmRTQSywhCmt

# WhatsApp Green API (ya configurado)
GREEN_API_URL=https://7107.api.greenapi.com
GREEN_API_TOKEN=...

# Google Maps (ya configurado)
GOOGLE_MAPS_API_KEY=...

# LLMs
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...

# QR (cambiar en producción!)
EMOVILS_QR_SECRET=secret-dev
```

---

## 📚 Documentación relacionada (PDFs en `/PROYECTO OPC/`)

- `EMOVILS_BLUEPRINT_MAESTRO.pdf` — Arquitectura completa (35 pp) — **el bueno**
- `EMOVILS_KIT_INSTAGRAM.pdf` — Playbook redes sociales
- `EMOVILS_OPC_PLAN_MAESTRO.pdf` — Plan original

---

## 🎯 Próximos pasos prioritarios

1. **Onboarding de 28 conductores reales** → llenar tabla `Conductores` en Airtable
2. **Onboarding de vehículos reales** → llenar tabla `Vehiculos`
3. **Conectar Green API real para enviar/recibir WhatsApp**
4. **Sesión Cowork para resolver Page-Instagram + Meta token**
5. **Activar voz dominicana clonada en ElevenLabs**

---

## ⚠️ Recordatorios de seguridad

- Las llaves API antiguas estaban expuestas en `RESUMEN_ESTADO_ACTUAL.md`
- ✅ Token Airtable rotado (nuevo: `patQUDAQ...`)
- 🟡 Pendiente rotar: GitHub token, OpenAI key, Green API, PayPal

Cuando estés con cabeza fresca, dedicar 30 min a rotar las 4 restantes.
