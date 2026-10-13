-- AlterTable
ALTER TABLE "transactions" ADD COLUMN     "sucursal_id" TEXT;

-- CreateTable
CREATE TABLE "sucursales" (
    "id" TEXT NOT NULL,
    "merchant_id" TEXT NOT NULL,
    "nombre" TEXT NOT NULL,
    "direccion" TEXT,
    "lat" DOUBLE PRECISION,
    "lng" DOUBLE PRECISION,
    "pin_hash" TEXT NOT NULL,
    "activa" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "sucursales_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "sucursales_merchant_id_idx" ON "sucursales"("merchant_id");

-- AddForeignKey
ALTER TABLE "sucursales" ADD CONSTRAINT "sucursales_merchant_id_fkey" FOREIGN KEY ("merchant_id") REFERENCES "merchants"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "transactions" ADD CONSTRAINT "transactions_sucursal_id_fkey" FOREIGN KEY ("sucursal_id") REFERENCES "sucursales"("id") ON DELETE SET NULL ON UPDATE CASCADE;
