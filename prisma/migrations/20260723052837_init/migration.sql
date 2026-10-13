-- CreateEnum
CREATE TYPE "Role" AS ENUM ('cliente', 'cajera', 'comercio_admin', 'super_admin');

-- CreateEnum
CREATE TYPE "ClientTier" AS ENUM ('free', 'plus');

-- CreateEnum
CREATE TYPE "PaymentStatus" AS ENUM ('pendiente', 'pagado', 'vencido');

-- CreateEnum
CREATE TYPE "MerchantPlan" AS ENUM ('starter', 'growth', 'scale');

-- CreateEnum
CREATE TYPE "MerchantStatus" AS ENUM ('activo', 'moroso', 'suspendido');

-- CreateEnum
CREATE TYPE "CollectionMethod" AS ENUM ('spei', 'tarjeta', 'domiciliacion');

-- CreateEnum
CREATE TYPE "TransactionStatus" AS ENUM ('registrada', 'anulada');

-- CreateEnum
CREATE TYPE "InvoiceStatus" AS ENUM ('pendiente', 'emitida', 'pagada', 'vencida', 'en_cobranza');

-- CreateTable
CREATE TABLE "country_config" (
    "id" TEXT NOT NULL,
    "nombre" TEXT NOT NULL,
    "moneda" TEXT NOT NULL,
    "procesador_pago" TEXT NOT NULL,
    "idioma_default" TEXT NOT NULL,
    "iva_pct" INTEGER NOT NULL,
    "activo" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "country_config_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "users" (
    "id" TEXT NOT NULL,
    "role" "Role" NOT NULL,
    "email" TEXT,
    "telefono" TEXT,
    "nombre" TEXT,
    "password_hash" TEXT,
    "mfa" BOOLEAN NOT NULL DEFAULT false,
    "estado" TEXT NOT NULL DEFAULT 'activo',
    "country_id" TEXT,
    "qr_token" TEXT,
    "qr_version" INTEGER NOT NULL DEFAULT 1,
    "merchant_id" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "membership_tiers" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "tier" "ClientTier" NOT NULL DEFAULT 'free',
    "vencimiento" TIMESTAMP(3),
    "estado_pago" "PaymentStatus" NOT NULL DEFAULT 'pendiente',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "membership_tiers_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "merchants" (
    "id" TEXT NOT NULL,
    "razon_social" TEXT NOT NULL,
    "rfc" TEXT,
    "plan" "MerchantPlan" NOT NULL DEFAULT 'starter',
    "dia_corte" INTEGER NOT NULL DEFAULT 1,
    "metodo_cobro" "CollectionMethod" NOT NULL DEFAULT 'spei',
    "estado" "MerchantStatus" NOT NULL DEFAULT 'activo',
    "country_id" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "merchants_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "transactions" (
    "id" TEXT NOT NULL,
    "country_id" TEXT NOT NULL,
    "cliente_id" TEXT NOT NULL,
    "merchant_id" TEXT NOT NULL,
    "monto_bruto" BIGINT NOT NULL,
    "tier_aplicado" "ClientTier" NOT NULL,
    "tasa_descuento_bps" INTEGER NOT NULL,
    "monto_descuento" BIGINT NOT NULL,
    "monto_cobrado" BIGINT NOT NULL,
    "estado" "TransactionStatus" NOT NULL DEFAULT 'registrada',
    "idempotency_key" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "transactions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "commission_invoices" (
    "id" TEXT NOT NULL,
    "merchant_id" TEXT NOT NULL,
    "periodo" TEXT NOT NULL,
    "monto_transaccionado" BIGINT NOT NULL,
    "cuota_fija" BIGINT NOT NULL,
    "tasa_comision_bps" INTEGER NOT NULL,
    "comision" BIGINT NOT NULL,
    "iva" BIGINT NOT NULL,
    "total" BIGINT NOT NULL,
    "estado" "InvoiceStatus" NOT NULL DEFAULT 'pendiente',
    "factura_ref" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "commission_invoices_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "users_email_key" ON "users"("email");

-- CreateIndex
CREATE UNIQUE INDEX "users_telefono_key" ON "users"("telefono");

-- CreateIndex
CREATE UNIQUE INDEX "users_qr_token_key" ON "users"("qr_token");

-- CreateIndex
CREATE INDEX "users_merchant_id_idx" ON "users"("merchant_id");

-- CreateIndex
CREATE UNIQUE INDEX "membership_tiers_user_id_key" ON "membership_tiers"("user_id");

-- CreateIndex
CREATE INDEX "merchants_country_id_idx" ON "merchants"("country_id");

-- CreateIndex
CREATE UNIQUE INDEX "transactions_idempotency_key_key" ON "transactions"("idempotency_key");

-- CreateIndex
CREATE INDEX "transactions_merchant_id_created_at_idx" ON "transactions"("merchant_id", "created_at");

-- CreateIndex
CREATE INDEX "transactions_cliente_id_idx" ON "transactions"("cliente_id");

-- CreateIndex
CREATE UNIQUE INDEX "commission_invoices_merchant_id_periodo_key" ON "commission_invoices"("merchant_id", "periodo");

-- AddForeignKey
ALTER TABLE "users" ADD CONSTRAINT "users_country_id_fkey" FOREIGN KEY ("country_id") REFERENCES "country_config"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "users" ADD CONSTRAINT "users_merchant_id_fkey" FOREIGN KEY ("merchant_id") REFERENCES "merchants"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "membership_tiers" ADD CONSTRAINT "membership_tiers_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "merchants" ADD CONSTRAINT "merchants_country_id_fkey" FOREIGN KEY ("country_id") REFERENCES "country_config"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "transactions" ADD CONSTRAINT "transactions_country_id_fkey" FOREIGN KEY ("country_id") REFERENCES "country_config"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "transactions" ADD CONSTRAINT "transactions_cliente_id_fkey" FOREIGN KEY ("cliente_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "transactions" ADD CONSTRAINT "transactions_merchant_id_fkey" FOREIGN KEY ("merchant_id") REFERENCES "merchants"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "commission_invoices" ADD CONSTRAINT "commission_invoices_merchant_id_fkey" FOREIGN KEY ("merchant_id") REFERENCES "merchants"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
