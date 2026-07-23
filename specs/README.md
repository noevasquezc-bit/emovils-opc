# specs/

Documentación de especificación de **Vínculo**.

- La especificación funcional y técnica canónica vive en [`../SPEC.md`](../SPEC.md).
- Este directorio guarda ADRs (registros de decisiones de arquitectura) y notas de
  diseño que complementan el SPEC.

---

## ADR-0001 — Arquitectura base (scaffolding)

**Fecha:** 2026-07-23 · **Estado:** aceptada

### Contexto
Primer sprint de arquitectura. Objetivo: dejar el proyecto Next.js 15 + Prisma +
NextAuth listo para construir features, con `prisma migrate dev` y `npm run build`
pasando. **Sin features de negocio todavía.**

### Decisiones
1. **Stack:** Next.js 15 (App Router) + TypeScript · Prisma + PostgreSQL ·
   NextAuth (Auth.js v5).
2. **Provider de base de datos:** `postgresql`, apuntado a **Neon** en producción.
   En desarrollo dentro del contenedor se usa un PostgreSQL local; la migración
   generada es idéntica para Neon (solo cambia `DATABASE_URL`).
3. **Roles (4, por instrucción del sprint):** `cliente`, `cajera`,
   `comercio_admin`, `super_admin`. (El SPEC §3 describe 5 roles operativos;
   `vinculo_ops`/`vinculo_admin` se consolidan por ahora en `super_admin`.)
4. **Dinero:** todos los montos en **centavos como `BigInt`** para evitar overflow
   de volumen mensual agregado (un `Int` de 32 bits topa en ~$21.4M). Ver SPEC §8
   y `lib/money.ts`.
5. **Auth:** estrategia JWT + provider Credentials contra el modelo `User` propio
   (no se usa el adaptador Prisma de Auth.js, para no acoplar `User` al esquema de
   Auth.js). El rol viaja en el token y se expone en la sesión.
6. **Modelos (6, por instrucción del sprint):** `User`, `Merchant`,
   `MembershipTier`, `Transaction`, `CommissionInvoice`, `CountryConfig`. La
   entidad `Sucursal` del SPEC §6.4 se pospone (Transaction enlaza directo a
   Merchant en esta base).

### Estructura
```
/app     — App Router (páginas + route handlers: /api/health, /api/auth)
/lib     — prisma.ts (singleton), auth.ts (NextAuth), money.ts (aritmética §8)
/prisma  — schema.prisma + migraciones
/specs   — SPEC + ADRs (este directorio)
/types   — augmentación de tipos (next-auth.d.ts: rol en sesión)
```

### Pendiente (próximos sprints)
- Entidad `Sucursal` + PIN de caja (SPEC §6.4).
- Endpoints REST v1 (SPEC §9) y flujos de OTP/QR.
- Seed de `CountryConfig` MX y planes de comercio.
- ESLint/Prettier y suite de tests de `lib/money.ts`.

---

## ADR-0002 — Motor de descuentos/comisiones + comisión negociable

**Fecha:** 2026-07-23 · **Estado:** aceptada

### Contexto
Segundo sprint funcional: registrar consumos con descuento y calcular la comisión
mensual al comercio. Requisito del negocio: la comisión **no debe ser siempre
fija** — debe haber espacio para negociar un % distinto con cada comercio.

### Decisiones
1. **Comisión negociable por comercio.** `Merchant` gana dos campos opcionales:
   `tasaComisionBps` y `cuotaFija`. Si están presentes, **mandan** sobre el default
   del plan (`MerchantPlanConfig`); si son null, se usa el del plan. La resolución
   vive en `resolverTerminos()` (`lib/comisiones.ts`). El % acordado se captura al
   dar de alta el comercio (`POST /api/v1/admin/comercios`).
2. **Motor puro + endpoints.** La aritmética (descuento, comisión, IVA, rango de
   periodo) es pura y testeada (`lib/money.ts`, `lib/comisiones.ts`); los route
   handlers solo orquestan DB.
3. **Endpoints v1:** `POST /transacciones` (registro idempotente con descuento
   10 %/20 %), `POST /admin/comisiones/correr` (corrida mensual → factura),
   `POST /admin/comercios` (alta con % negociado).
4. **Snapshots + idempotencia** en transacciones (tasa aplicada) y comisiones
   (términos usados), conforme a SPEC §8.
5. **Tests con Vitest** (`npm test`): 15 casos sobre descuentos, comisión (piso vs
   %), IVA, términos negociados y rango de periodo. Los archivos `*.test.ts` se
   excluyen del `next build`.

### Pendiente
- Front de caja escaneando QR (depende del sprint de QR).
- Emisión de CFDI real (PAC) para la factura de comisión.

---

## ADR-0003 — Sistema de QR + registro de cliente

**Fecha:** 2026-07-23 · **Estado:** aceptada

### Contexto
Tercer sprint funcional: onboarding del cliente (registro por OTP) y su QR
firmado, que la caja escanea para aplicar el descuento. Conecta el registro con
el motor de descuentos del ADR-0002.

### Decisiones
1. **QR firmado HMAC-SHA256 por país** (`lib/qr.ts`), formato
   `VINCULO|v1|{pais}|{clienteId}|{qrVersion}|{expUnix}|{sig}`. El QR **no lleva
   PII**: solo id + firma. Secreto por país en `VINCULO_QR_SECRET_<PAIS>`.
2. **Rotación por versión.** `verificarQr` valida firma/expiración; la vigencia
   de `qrVersion` se compara contra `user.qrVersion` en la DB. Rotar =
   incrementar la versión → los QR anteriores dejan de validar (antifraude).
3. **Registro por OTP** (`OtpChallenge`): `POST /clientes/registro` (genera y
   "envía" un OTP de 6 dígitos, hasheado con bcrypt, TTL 10 min, máx. 5 intentos)
   y `POST /clientes/verificar` (crea el cliente plan `free` + emite QR). El envío
   real (SMS/WhatsApp/email) queda como integración pendiente; en no-producción
   el código se expone en `devCodigo` para pruebas.
4. **Caja:** `POST /qr/validar` devuelve solo `{ nombreParcial, plan,
   tasaDescuentoBps }`. `POST /transacciones` ahora acepta `qrToken` (además de
   `clienteId`) y valida versión.
5. **Rotar:** `POST /clientes/qr/rotar`. En producción se protegerá con sesión de
   cliente; por ahora recibe `clienteId`.
6. **Tests** (Vitest): 10 casos de firmar/verificar (roundtrip, manipulación,
   expiración, formato) y `nombreParcial`. Total del proyecto: 25 tests.

### Pendiente
- Envío real de OTP y sesión de cliente (para proteger `/me/*`).
- Front de caja (escáner) y tarjeta digital del cliente (render del QR).

---

## ADR-0004 — Sucursal + login de caja

**Fecha:** 2026-07-23 · **Estado:** aceptada

### Contexto
Cuarto sprint: puntos físicos (sucursales) y acceso de la cajera. Completa el
modelo del MVP — la transacción queda registrada por sucursal y la cajera solo
opera en la suya (least-privilege, SPEC §10).

### Decisiones
1. **Modelo `Sucursal`** (SPEC §6.4): nombre, dirección, lat/lng, `pinHash`
   (bcrypt), activa. `Transaction` gana `sucursalId` (opcional, por compatibilidad).
2. **Sesión de caja** (`lib/sesion_caja.ts`): token corto firmado con HMAC
   (AUTH_SECRET), payload `{sucursalId, merchantId, exp}`, TTL 8 h. No es una
   sesión de usuario NextAuth — es específica de caja.
3. **Endpoints:** `POST /admin/sucursales` (alta con PIN; respeta el máx. de
   sucursales del plan) y `POST /sucursal/login` (PIN → token de caja).
4. **`/transacciones` con sesión de caja.** Si viene `Authorization: Bearer`, el
   comercio y la sucursal salen del token (la cajera no puede registrar en otra
   sucursal); sin sesión, se usan los del body (admin). La sucursal se valida
   contra el comercio.
5. **Tests** (Vitest): 4 casos de sesión de caja (roundtrip, firma, expiración,
   formato). Total del proyecto: 29 tests.

### Pendiente
- Sesión de cliente y protección de `/clientes/me/*`.
- Front de caja (escáner) y dashboard de comercio.
