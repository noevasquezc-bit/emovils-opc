#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Emovils OPC — Deploy Script
# ═══════════════════════════════════════════════════════════════

set -e

echo ""
echo "═══════════════════════════════════════════"
echo "  EMOVILS OPC — Sistema de Despliegue"
echo "  CPA Máximo: \$6 USD — INVIOLABLE"
echo "═══════════════════════════════════════════"
echo ""

# 1. Verificar Python
echo "→ Verificando Python..."
python3 --version || { echo "ERROR: Python 3 requerido"; exit 1; }

# 2. Crear entorno virtual
echo "→ Creando entorno virtual..."
python3 -m venv venv
source venv/bin/activate

# 3. Instalar dependencias
echo "→ Instalando dependencias..."
pip install -r requirements.txt

# 4. Verificar .env
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "⚠️  IMPORTANTE: Edita .env con tus credenciales reales antes de continuar"
    echo "   nano .env"
fi

# 5. Crear carpeta de logs
mkdir -p logs

# 6. Ejecutar tests
echo "→ Ejecutando tests de safeguards..."
python -m pytest tests/test_safeguards.py -v --tb=short
echo "✅ Tests pasados — El \$6 está protegido"

# 7. Iniciar servidor
echo ""
echo "═══════════════════════════════════════════"
echo "  ✅ Sistema listo para iniciar"
echo "  Ejecuta: python main.py"
echo "═══════════════════════════════════════════"
echo ""

# Descomenta para auto-iniciar:
# python main.py
