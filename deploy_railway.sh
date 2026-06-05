#!/bin/bash
# ═══════════════════════════════════════════
# Emovils OPC — Deploy a Railway
# Ejecuta este script UNA SOLA VEZ
# ═══════════════════════════════════════════

set -e  # Detener si hay error
cd "$(dirname "$0")"

echo "🚀 Iniciando despliegue de Emovils OPC en Railway..."
echo ""

# 1. Verificar que git está instalado
if ! command -v git &> /dev/null; then
    echo "📦 Instalando git via Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    brew install git
fi

# 2. Inicializar repositorio git si no existe
if [ ! -d ".git" ]; then
    echo "📁 Inicializando repositorio git..."
    git init
    git add .
    git commit -m "Initial commit: Emovils OPC sistema completo"
    echo "✅ Repositorio git creado"
fi

# 3. Instalar Railway CLI si no está
if ! command -v railway &> /dev/null; then
    echo "📦 Instalando Railway CLI..."
    bash <(curl -fsSL cli.railway.app)
    echo "✅ Railway CLI instalado"
fi

echo ""
echo "🔑 Abriendo Railway para login (se abrirá el navegador)..."
railway login

echo ""
echo "🏗️  Creando proyecto en Railway..."
railway init --name "emovils-opc"

echo ""
echo "⚙️  Configurando variables de entorno..."
# Leer el .env y subir las variables a Railway
while IFS='=' read -r key value; do
    # Ignorar líneas vacías y comentarios
    [[ -z "$key" || "$key" == \#* ]] && continue
    # Limpiar espacios
    key=$(echo "$key" | xargs)
    value=$(echo "$value" | xargs)
    [[ -z "$key" || -z "$value" ]] && continue
    railway variables --set "$key=$value" 2>/dev/null || true
done < .env
echo "✅ Variables de entorno configuradas"

echo ""
echo "🚀 Desplegando..."
railway up --detach

echo ""
echo "🌍 Obteniendo URL del servidor..."
railway domain

echo ""
echo "═══════════════════════════════════════"
echo "✅ ¡EMOVILS OPC ESTÁ ONLINE!"
echo "═══════════════════════════════════════"
echo ""
echo "Próximo paso: actualizar el webhook de Green API"
echo "con la URL que aparece arriba."
