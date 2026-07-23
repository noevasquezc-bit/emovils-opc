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
