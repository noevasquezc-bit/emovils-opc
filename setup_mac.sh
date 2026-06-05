#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Emovils OPC — Setup en tu Mac (~/code/emovils-ops/)
# Ejecuta este script UNA VEZ para crear la estructura en tu Mac
# ═══════════════════════════════════════════════════════════════

set -e

DEST="$HOME/code/emovils-ops"
SRC="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  EMOVILS OPC — Instalando en ~/code/emovils-ops"
echo "═══════════════════════════════════════════════════"

# 1. Crear estructura de carpetas
echo "→ Creando carpetas..."
mkdir -p "$DEST/agents" "$DEST/lib" "$DEST/workflows" \
         "$DEST/config" "$DEST/tests" "$DEST/docs" "$DEST/logs"

# 2. Copiar todos los archivos del proyecto
echo "→ Copiando archivos del proyecto..."
cp -r "$SRC/agents/"*.py   "$DEST/agents/"    2>/dev/null || true
cp -r "$SRC/lib/"*.py      "$DEST/lib/"       2>/dev/null || true
cp -r "$SRC/workflows/"*.json "$DEST/workflows/" 2>/dev/null || true
cp -r "$SRC/config/"*.py   "$DEST/config/"    2>/dev/null || true
cp -r "$SRC/tests/"*.py    "$DEST/tests/"     2>/dev/null || true
cp    "$SRC/main.py"        "$DEST/"           2>/dev/null || true
cp    "$SRC/requirements.txt" "$DEST/"         2>/dev/null || true
cp    "$SRC/.env.example"   "$DEST/"           2>/dev/null || true
cp    "$SRC/deploy.sh"      "$DEST/"           2>/dev/null || true

# 3. Crear __init__.py vacíos si faltan
touch "$DEST/agents/__init__.py" "$DEST/lib/__init__.py" \
      "$DEST/config/__init__.py" "$DEST/tests/__init__.py"

# 4. Permisos de ejecución
chmod +x "$DEST/deploy.sh" "$DEST/setup_mac.sh" 2>/dev/null || true

# 5. Verificar instalación
echo ""
echo "Estructura creada en: $DEST"
echo ""
find "$DEST" -type f | sort | while read f; do
    echo "  ✅ ${f/$DEST\//}"
done

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✅ INSTALACIÓN COMPLETA"
echo ""
echo "  PRÓXIMOS PASOS:"
echo "  1. cd ~/code/emovils-ops"
echo "  2. cp .env.example .env"
echo "  3. nano .env  (llena tus credenciales)"
echo "  4. bash deploy.sh"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  RECORDATORIO: CPA MÁXIMO = \$6 — INVIOLABLE"
echo "  Presupuesto diario: \$4/día | Total piloto: \$100"
echo "═══════════════════════════════════════════════════"
echo ""
