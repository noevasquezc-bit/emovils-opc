# Vínculo — Especificación del Producto

> Plataforma de afiliación **comercio ↔ cliente**. El cliente se registra gratis
> y recibe un QR. Va a un comercio afiliado, muestra el QR, la cajera aplica el
> descuento (10 % plan Free / 20 % plan Plus) y **cobra al cliente directamente**.
> Vínculo **factura una comisión mensual recurrente al comercio** sobre el volumen
> transaccionado. Modelo **multi-país** (arranca en **México**), **sin custodia del
> dinero del cliente**.

- **Documento:** SPEC funcional y técnico (v1)
- **Estado:** Borrador de referencia para el MVP
- **País de arranque:** México (MXN, IVA 16 %, CFDI 4.0)
- **Moneda del código:** amounts en **centavos** (enteros), nunca floats

---

## 1. Resumen ejecutivo

Vínculo conecta dos lados:

1. **Comercios afiliados** — restaurantes, cafés, tiendas, servicios. Pagan una
   suscripción/comisión mensual a Vínculo a cambio de tráfico de clientes y de
   una herramienta simple para aplicar descuentos en caja.
2. **Clientes finales** — se registran **gratis**, obtienen un **QR personal** y
   reciben descuentos (10 % o 20 %) en los comercios afiliados.

**Lo que Vínculo NO hace (invariante de diseño):**

- ❌ **No custodia el dinero del cliente.** El cliente paga **directo al comercio**
  (efectivo, tarjeta, TPV del propio comercio). Vínculo nunca es intermediario de
  ese pago.
- ❌ **No procesa pagos de consumidor.** El único flujo de dinero que toca Vínculo
  es el **cobro de su comisión al comercio** (B2B).

**Cómo gana dinero Vínculo:** comisión mensual recurrente al comercio, calculada
sobre el **volumen transaccionado registrado** por ese comercio en el periodo.

Esta separación hace que Vínculo **no sea una entidad de fondos de pago / IFPE**
en México respecto al consumidor: no toma, guarda ni transmite el dinero del
cliente. Ver §11 (Cumplimiento).

---

## 2. Glosario

| Término | Definición |
|---|---|
| **Cliente** | Usuario final que se registra gratis y usa su QR para obtener descuentos. |
| **Comercio** | Negocio afiliado que aplica descuentos y paga comisión a Vínculo. |
| **Sucursal** | Punto físico de un comercio (un comercio puede tener N sucursales). |
| **Cajera / operador** | Persona en la sucursal que escanea el QR y registra la transacción. |
| **QR del cliente** | Código único, firmado, que identifica al cliente. Rotable. |
| **Transacción** | Registro de un consumo con descuento aplicado (no un cobro de Vínculo). |
| **Plan del cliente** | `free` (10 %) o `plus` (20 %). |
| **Comisión** | Cargo mensual recurrente de Vínculo al comercio (el ingreso de Vínculo). |
| **Volumen transaccionado** | Suma de `monto_bruto` de las transacciones de un comercio en el periodo. |
| **País** | Configuración regional (moneda, impuestos, facturación). Arranca `MX`. |

---

## 3. Actores y roles

```
┌─────────────┐        muestra QR         ┌──────────────┐
│   Cliente   │ ────────────────────────▶ │   Sucursal   │
│  (gratis)   │                            │  (cajera)    │
└─────────────┘ ◀──────────────────────── └──────────────┘
        ▲          aplica 10%/20% y                │
        │          cobra directo                   │ registra transacción
        │                                          ▼
        │ QR + descuentos                   ┌──────────────┐
        └────────────────────────────────  │   Vínculo    │
                                            │  (plataforma)│
                                            └──────────────┘
                                                   │ factura comisión
                                                   ▼  mensual al comercio
                                            ┌──────────────┐
                                            │   Comercio   │  ← paga a Vínculo
                                            └──────────────┘
```

| Rol | Descripción | Autenticación |
|---|---|---|
| `cliente` | Se registra, ve su QR, historial y ahorro. | OTP por WhatsApp/SMS o email. |
| `cajera` | Escanea QR y registra transacción. Acceso solo a su sucursal. | PIN de sucursal + sesión corta. |
| `comercio_admin` | Dueño/gerente. Ve reportes, sucursales, facturación. | Email + contraseña / OTP. |
| `vinculo_ops` | Staff de Vínculo. Onboarding, soporte, cobranza. | SSO interno. |
| `vinculo_admin` | Superadmin. Configura países, planes, comisiones. | SSO interno + MFA. |

---

## 4. Modelo de negocio y economía

### 4.1 Descuentos al cliente

| Plan cliente | Descuento en caja | Precio para el cliente |
|---|---|---|
| **Free** | **10 %** | Gratis |
| **Plus** | **20 %** | Suscripción del cliente (opcional, fase 2) |

> El descuento sale del **margen del comercio**, no de Vínculo. El comercio acepta
> este descuento como costo de adquisición/retención de clientes.

### 4.2 Comisión al comercio (ingreso de Vínculo)

La comisión es **mensual recurrente** y se calcula sobre el **volumen
transaccionado** del comercio en el periodo. Modelo configurable por país/plan:

```
comision_mes = max(
    cuota_fija_mensual,
    tasa_comision * volumen_transaccionado_mes
)
```

- `cuota_fija_mensual`: piso mínimo (ej. MXN $499 / mes) — asegura ingreso aunque
  haya poco volumen.
- `tasa_comision`: porcentaje sobre volumen (ej. 3 %–5 %).
- Se elige el **mayor** de ambos (o `fija + tasa`, según el plan del comercio; ver
  §4.3). El operador del país configura la fórmula exacta.

**Ejemplo (MX, tasa 4 %, fija $499):**

| Volumen mes | 4 % del volumen | Comisión cobrada |
|---|---|---|
| $5,000 | $200 | **$499** (aplica piso) |
| $20,000 | $800 | **$800** |
| $50,000 | $2,000 | **$2,000** |

### 4.3 Planes del comercio

| Plan comercio | Cuota fija | Tasa comisión | Incluye |
|---|---|---|---|
| `starter` | $499 / mes | 5 % | 1 sucursal, dashboard básico. |
| `growth` | $999 / mes | 4 % | Hasta 5 sucursales, reportes, soporte. |
| `scale` | Negociado | ≤3 % | Multi-sucursal, API, gerente de cuenta. |

> Valores de ejemplo; el `vinculo_admin` los define por país en la tabla `Planes`.

### 4.4 Ciclo de facturación al comercio

1. Corte mensual por comercio (fecha de alta o día fijo del mes).
2. Se calcula `comision_mes` a partir del volumen del periodo.
3. Se genera **factura B2B** (CFDI 4.0 en MX) al comercio.
4. Cobro por el método del comercio: domiciliación (SPEI/CoDi/tarjeta) o
   transferencia. **Este es el único dinero que toca Vínculo.**
5. Estados: `pendiente → emitida → pagada → vencida → en_cobranza`.

---

## 5. Recorridos principales (user journeys)

### 5.1 Registro del cliente (gratis)

1. Cliente entra al link/QR de Vínculo o app web.
2. Ingresa teléfono (+ país) o email → recibe **OTP**.
3. Verifica OTP → cuenta creada con plan `free`.
4. Se genera su **QR personal firmado** (§7) y su tarjeta digital.
5. Cliente ve: su QR, comercios afiliados cercanos, y su ahorro acumulado.

### 5.2 Uso en comercio (el momento clave)

1. Cliente consume en la sucursal.
2. Al pagar, muestra su QR (pantalla o impreso).
3. **Cajera escanea** el QR con la app/web de sucursal.
4. Sistema valida el QR → devuelve `plan` del cliente (`free`=10 %, `plus`=20 %) y
   nombre para confirmar identidad.
5. Cajera ingresa el **monto bruto de la cuenta**.
6. Sistema muestra: `descuento`, `monto a cobrar = bruto − descuento`.
7. Cajera **cobra ese monto al cliente por su TPV/efectivo habitual** (Vínculo no
   interviene en el cobro).
8. Cajera confirma → se **registra la transacción** en Vínculo.
9. Cliente recibe confirmación (push/WhatsApp) con su ahorro.

### 5.3 Cierre mensual del comercio

1. Corte del periodo → suma de `monto_bruto` = volumen.
2. Cálculo de comisión (§4.2).
3. Emisión de factura + cobro (§4.4).

---

## 6. Modelo de datos

> Entidades lógicas. La implementación puede ser Postgres o Airtable (el repo ya
> usa Airtable; ver §12). Montos siempre en **centavos** (enteros). Timestamps en
> UTC (ISO 8601). Cada tabla lleva `pais` para el modelo multi-país.

### 6.1 `paises`
| Campo | Tipo | Notas |
|---|---|---|
| `id` | string | `MX`, `CO`, `AR`… (ISO 3166-1 alpha-2) |
| `nombre` | string | "México" |
| `moneda` | string | `MXN` (ISO 4217) |
| `tasa_iva` | int (bps) | 1600 = 16 % |
| `facturacion` | string | `cfdi_4.0` |
| `activo` | bool | |

### 6.2 `clientes`
| Campo | Tipo | Notas |
|---|---|---|
| `id` | uuid | |
| `pais` | fk paises | |
| `telefono` | string (E.164) | único por país |
| `email` | string | opcional |
| `nombre` | string | |
| `plan` | enum | `free` \| `plus` |
| `qr_token` | string | token firmado actual (§7); rotable |
| `qr_version` | int | incrementa al rotar |
| `estado` | enum | `activo` \| `suspendido` |
| `creado_en` | ts | |

### 6.3 `comercios`
| Campo | Tipo | Notas |
|---|---|---|
| `id` | uuid | |
| `pais` | fk paises | |
| `razon_social` | string | |
| `rfc` | string | ID fiscal (MX: RFC) |
| `plan_comercio` | enum | `starter`\|`growth`\|`scale` |
| `dia_corte` | int | 1–28 |
| `metodo_cobro` | enum | `spei`\|`tarjeta`\|`domiciliacion` |
| `estado` | enum | `activo`\|`moroso`\|`suspendido` |
| `creado_en` | ts | |

### 6.4 `sucursales`
| Campo | Tipo | Notas |
|---|---|---|
| `id` | uuid | |
| `comercio_id` | fk comercios | |
| `nombre` | string | |
| `direccion` | string | |
| `lat`, `lng` | float | para "cercanos" |
| `pin_hash` | string | PIN de acceso de cajera (hash) |
| `activa` | bool | |

### 6.5 `transacciones`  *(no es un cobro de Vínculo; es un registro de consumo)*
| Campo | Tipo | Notas |
|---|---|---|
| `id` | uuid | |
| `pais` | fk paises | |
| `sucursal_id` | fk sucursales | |
| `cliente_id` | fk clientes | |
| `cajera_ref` | string | quién registró |
| `monto_bruto` | int (centavos) | cuenta antes de descuento |
| `plan_aplicado` | enum | `free`\|`plus` (snapshot) |
| `tasa_descuento` | int (bps) | 1000 / 2000 (snapshot) |
| `monto_descuento` | int (centavos) | `bruto * tasa / 10000` |
| `monto_cobrado` | int (centavos) | `bruto − descuento` |
| `estado` | enum | `registrada`\|`anulada` |
| `creado_en` | ts | |
| `idempotency_key` | string | único; evita doble registro |

### 6.6 `comisiones` *(el ingreso de Vínculo)*
| Campo | Tipo | Notas |
|---|---|---|
| `id` | uuid | |
| `comercio_id` | fk comercios | |
| `periodo` | string | `2026-07` |
| `volumen` | int (centavos) | suma de `monto_bruto` del periodo |
| `cuota_fija` | int (centavos) | snapshot del plan |
| `tasa_comision` | int (bps) | snapshot del plan |
| `monto_comision` | int (centavos) | resultado de la fórmula |
| `iva` | int (centavos) | sobre la comisión |
| `total` | int (centavos) | comisión + IVA |
| `estado` | enum | `pendiente`\|`emitida`\|`pagada`\|`vencida`\|`en_cobranza` |
| `factura_ref` | string | folio/UUID CFDI |
| `creado_en` | ts | |

### 6.7 `usuarios` (staff comercio / Vínculo)
| Campo | Tipo | Notas |
|---|---|---|
| `id` | uuid | |
| `rol` | enum | ver §3 |
| `comercio_id` | fk | null si es staff Vínculo |
| `email` | string | |
| `password_hash` | string | argon2/bcrypt |
| `mfa` | bool | |

---

## 7. Sistema de QR del cliente

**Objetivo:** identificar al cliente de forma segura, offline-verificable y no
falsificable, reutilizando el patrón HMAC-SHA256 ya presente en el repo
(`opc/qr_generator_opc.py`).

### 7.1 Formato del payload (firmado)
```
VINCULO|v1|{pais}|{cliente_id}|{qr_version}|{exp_unix}|{sig}
```
- `sig = HMAC_SHA256(secret_pais, "{pais}|{cliente_id}|{qr_version}|{exp_unix}")`
  truncado (ej. 16 bytes base64url).
- `exp_unix`: expiración (ej. token rotable cada 24 h para QR dinámico, o sin
  expiración para tarjeta física con `qr_version`).
- El QR **no** contiene datos personales — solo el ID y la firma.

### 7.2 Validación en caja
1. Sucursal escanea → backend `POST /v1/qr/validar`.
2. Backend verifica firma, `pais`, `qr_version` vigente y `exp`.
3. Devuelve `{ cliente_nombre_parcial, plan, tasa_descuento }` — **nada sensible**.
4. Rate-limit por sucursal e IP; log de cada validación.

### 7.3 Rotación / revocación
- Rotar = incrementar `qr_version` → los QR viejos dejan de validar.
- Cliente puede regenerar su QR desde su app (antifraude / pérdida de teléfono).
- `secret_pais` distinto por país; rotable sin invalidar identidades (re-firma).

---

## 8. Cálculo de importes (reglas exactas)

Toda aritmética en **enteros (centavos)**. `bps` = basis points (1 % = 100 bps).

```python
# Descuento en caja
tasa_descuento_bps = 1000 if plan == "free" else 2000   # 10% / 20%
monto_descuento = (monto_bruto * tasa_descuento_bps) // 10000
monto_cobrado   = monto_bruto - monto_descuento          # lo que paga el cliente

# Comisión mensual al comercio (ingreso de Vínculo)
por_tasa      = (volumen * tasa_comision_bps) // 10000
monto_comision = max(cuota_fija, por_tasa)               # o cuota_fija + por_tasa
iva            = (monto_comision * tasa_iva_bps) // 10000
total_factura  = monto_comision + iva
```

Reglas:
- **Redondeo:** truncamiento (`//`) por defecto; el país puede configurar
  `round-half-up`. Documentar y ser consistente.
- **Snapshots:** `transacciones` y `comisiones` guardan las tasas usadas, no las
  leen del plan actual (para auditoría histórica).
- **Idempotencia:** registrar transacción y emitir comisión requieren
  `idempotency_key`.

---

## 9. API (REST v1)

Base: `/v1`. JSON. Auth por rol (§3). Todos los endpoints validan `pais`.

### 9.1 Cliente
| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/clientes/registro` | Inicia registro (teléfono/email) → envía OTP. |
| `POST` | `/clientes/verificar` | Verifica OTP → crea cuenta + QR. |
| `GET`  | `/clientes/me` | Perfil, plan, QR vigente. |
| `POST` | `/clientes/me/qr/rotar` | Regenera QR (nueva `qr_version`). |
| `GET`  | `/clientes/me/historial` | Transacciones y ahorro acumulado. |
| `GET`  | `/comercios/cercanos?lat&lng` | Sucursales afiliadas cercanas. |

### 9.2 Caja / sucursal
| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/sucursal/login` | PIN de sucursal → sesión corta. |
| `POST` | `/qr/validar` | Valida QR escaneado → plan + descuento. |
| `POST` | `/transacciones` | Registra consumo (idempotente). Body: `qr_token`, `monto_bruto`. |
| `POST` | `/transacciones/{id}/anular` | Anula (ventana de N min, con motivo). |

### 9.3 Comercio (admin)
| Método | Ruta | Descripción |
|---|---|---|
| `GET`  | `/comercio/dashboard` | Volumen, transacciones, comisión estimada. |
| `GET`  | `/comercio/sucursales` | Lista/gestiona sucursales. |
| `GET`  | `/comercio/comisiones` | Historial de facturación. |
| `GET`  | `/comercio/comisiones/{periodo}/factura` | Descarga CFDI/PDF. |

### 9.4 Vínculo (ops/admin)
| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/admin/comercios` | Alta de comercio + plan. |
| `POST` | `/admin/comisiones/correr` | Corre el cálculo de comisiones del periodo. |
| `POST` | `/admin/paises` | Configura país (moneda, IVA, facturación). |
| `GET`  | `/health` | Healthcheck. |

**Respuesta de `POST /transacciones` (ejemplo):**
```json
{
  "transaccion_id": "…",
  "cliente": "María G.",
  "plan": "free",
  "monto_bruto": 25000,
  "tasa_descuento_bps": 1000,
  "monto_descuento": 2500,
  "monto_cobrado": 22500,
  "moneda": "MXN"
}
```
> `monto_cobrado` es **informativo**: lo cobra el comercio por su medio. Vínculo no
> mueve ese dinero.

---

## 10. Seguridad

- **Sin datos sensibles en el QR** (solo ID + firma; §7).
- **HMAC por país** con secreto en KMS/variable de entorno, rotable.
- **PIN de sucursal** hasheado (argon2/bcrypt); sesiones de caja de vida corta.
- **Rate-limiting** en `/qr/validar` y `/transacciones` (antifraude).
- **Idempotencia** obligatoria en escritura de transacciones/comisiones.
- **Anulación** con ventana temporal, motivo y auditoría (evita descuentos falsos).
- **Least-privilege:** cajera solo ve su sucursal; comercio solo sus datos.
- **PII mínima:** teléfono/nombre; cifrado en reposo; borrado a solicitud (LFPDPPP
  en MX / GDPR-like).
- **Auditoría:** log inmutable de validaciones, transacciones y cambios de plan.

---

## 11. Cumplimiento y encuadre regulatorio

**Principio rector: Vínculo NO custodia ni transmite el dinero del cliente.**

- El pago consumidor→comercio ocurre **fuera** de Vínculo (TPV/efectivo del
  comercio). Vínculo solo **registra** el consumo para calcular su comisión.
- Por tanto, respecto al consumidor, Vínculo **no es** Institución de Fondos de
  Pago Electrónico (IFPE) ni agregador — no aplica la parte de la Ley Fintech
  relativa a custodia/transmisión de fondos del cliente.
- El **único flujo de dinero** de Vínculo es **B2B**: cobra su comisión al
  comercio (factura CFDI 4.0, IVA 16 % en MX). Ese cobro se hace por medios
  regulados estándar (SPEI/domiciliación/tarjeta) vía un PSP.
- **Datos personales (MX):** cumplir LFPDPPP — aviso de privacidad, consentimiento,
  ARCO, minimización de datos.
- **Facturación:** integrar PAC para timbrado CFDI 4.0.
- **Multi-país:** cada `pais` encapsula moneda, impuesto y régimen de facturación;
  revisar encuadre local antes de lanzar (Colombia, Argentina, etc.).

> ⚠️ Este encuadre es de diseño de producto, **no** asesoría legal. Validar con
> abogado fintech local antes de operar en cada país.

---

## 12. Arquitectura e implementación

### 12.1 Stack propuesto (reusa lo del repo)
- **Backend:** Python + **Flask** (ya en uso; `main_opc.py`, `requirements.txt`).
- **Datos:** Postgres recomendado para producción; **Airtable** viable para MVP
  (el repo ya tiene `opc/airtable_api_opc.py` con CRUD, retry y batching).
- **QR:** reusar `opc/qr_generator_opc.py` (HMAC-SHA256 firmado).
- **Mensajería:** WhatsApp (patrón Green API ya presente) / SMS / email para OTP y
  confirmaciones.
- **PSP / facturación MX:** proveedor SPEI + PAC para CFDI (no incluido aún).
- **Despliegue:** Railway (`railway.json`, `Procfile` ya presentes).

### 12.2 Separación de dominios
El módulo de Vínculo debe vivir aislado del dominio Emovils (taxi) existente:
```
vinculo/
  __init__.py
  modelos.py          # entidades §6
  qr_cliente.py       # firma/validación (envuelve opc/qr_generator_opc.py)
  importes.py         # aritmética §8 (centavos, bps)
  comisiones.py       # cálculo y corrida mensual §4/§8
  api.py              # blueprint Flask con endpoints §9
  paises.py           # config multi-país §6.1
  tests/
```
> Nada del código de Vínculo debe acoplarse a la lógica de dispatch de Emovils.

### 12.3 Configuración
- `.env` (ver `.env.example`): `VINCULO_QR_SECRET_MX`, credenciales PSP/PAC,
  Airtable/Postgres, proveedor OTP.
- Nunca commitear secretos. Un secreto HMAC **por país**.

---

## 13. Alcance del MVP (México)

**Incluye:**
1. Registro de cliente (OTP) + emisión de QR. (§5.1, §7)
2. App/web de caja: login por PIN, validar QR, registrar transacción. (§5.2, §9.2)
3. Cálculo de descuento 10 %/20 % con snapshots. (§8)
4. Dashboard de comercio: volumen y comisión estimada. (§9.3)
5. Corrida mensual de comisiones + generación de factura. (§4.4, §9.4)
6. Config de país MX (MXN, IVA 16 %). (§6.1)

**Fuera del MVP (fases siguientes):**
- Plan `plus` de cliente con cobro de suscripción al cliente.
- Segundo país.
- App móvil nativa (MVP es web/PWA).
- Programa de referidos, gamificación de ahorro.
- Integraciones POS directas (que el POS del comercio registre solo).

### 13.1 Criterios de aceptación (MVP)
- [ ] Un cliente nuevo puede registrarse y ver su QR en < 60 s.
- [ ] Una cajera valida un QR y registra una transacción con el descuento correcto.
- [ ] `monto_cobrado = monto_bruto − monto_descuento` es exacto en centavos.
- [ ] El registro de transacción es idempotente (doble submit no duplica).
- [ ] Al cierre, el volumen del comercio = suma de `monto_bruto` del periodo.
- [ ] La comisión aplica `max(cuota_fija, tasa*volumen)` con IVA correcto.
- [ ] Ningún endpoint mueve dinero del cliente (verificado por diseño y pruebas).
- [ ] QR no contiene PII y la firma inválida se rechaza.

---

## 14. Métricas (North Star y operativas)

- **North Star:** volumen transaccionado mensual afiliado (proxy de valor a ambos
  lados).
- Clientes activos / mes; transacciones por cliente; ahorro promedio.
- Comercios activos; churn de comercios; comisión promedio; morosidad.
- Tiempo de caja por transacción (fricción de la cajera).

---

## 15. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Comercio no registra transacciones (evita comisión por volumen) | Piso `cuota_fija`; conciliación por muestreo; incentivos. |
| Fraude de QR / descuento sin consumo | Firma HMAC, rate-limit, anulación auditada, ventana corta. |
| Encuadre regulatorio de custodia | Diseño sin custodia (§11); validación legal por país. |
| Fricción en caja | Flujo de 3 toques; validación < 1 s; UI para no-técnicos. |
| Dependencia de Airtable a escala | Ruta de migración a Postgres definida (§12.1). |

---

## 16. Roadmap

1. **Fase 0 — MVP MX** (§13): caja + descuentos + comisiones.
2. **Fase 1 — Retención:** plan `plus`, referidos, confirmaciones WhatsApp.
3. **Fase 2 — Escala:** segundo país, integraciones POS, API para `scale`.
4. **Fase 3 — Inteligencia:** analítica para comercios, recomendaciones a clientes.

---

*Fin del SPEC v1. Cambios sustanciales deben versionar este documento.*
