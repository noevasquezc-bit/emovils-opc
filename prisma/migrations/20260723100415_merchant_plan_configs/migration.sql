-- CreateTable
CREATE TABLE "merchant_plan_configs" (
    "id" TEXT NOT NULL,
    "country_id" TEXT NOT NULL,
    "plan" "MerchantPlan" NOT NULL,
    "cuota_fija" BIGINT NOT NULL,
    "tasa_comision_bps" INTEGER NOT NULL,
    "max_sucursales" INTEGER,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "merchant_plan_configs_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "merchant_plan_configs_country_id_plan_key" ON "merchant_plan_configs"("country_id", "plan");

-- AddForeignKey
ALTER TABLE "merchant_plan_configs" ADD CONSTRAINT "merchant_plan_configs_country_id_fkey" FOREIGN KEY ("country_id") REFERENCES "country_config"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
