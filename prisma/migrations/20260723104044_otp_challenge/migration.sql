-- CreateTable
CREATE TABLE "otp_challenges" (
    "id" TEXT NOT NULL,
    "destino" TEXT NOT NULL,
    "canal" TEXT NOT NULL,
    "pais" TEXT NOT NULL,
    "codigo_hash" TEXT NOT NULL,
    "expires_at" TIMESTAMP(3) NOT NULL,
    "consumido" BOOLEAN NOT NULL DEFAULT false,
    "intentos" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "otp_challenges_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "otp_challenges_destino_idx" ON "otp_challenges"("destino");
