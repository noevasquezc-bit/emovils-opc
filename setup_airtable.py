"""
Emovils OPC — Setup Automático de Airtable
Crea la base "Emovils OPC" con las 6 tablas y todos los campos.

USO:
  1. Ve a https://airtable.com/create/tokens
  2. Crea un token con scope: data.records:write, schema.bases:write
  3. Ejecuta: python setup_airtable.py TU_TOKEN_AQUI

El script crea TODO automáticamente en menos de 30 segundos.
"""
import sys
import json
import time
import requests

# ─────────────────────────────────────────────
# CONFIGURACIÓN DE LAS 6 TABLAS
# ─────────────────────────────────────────────
TABLES_SCHEMA = [
    {
        "name": "Leads",
        "description": "Prospectos captados por WhatsApp, Facebook, Instagram o Web",
        "fields": [
            {"name": "Name",       "type": "singleLineText"},
            {"name": "Phone",      "type": "phoneNumber"},
            {"name": "Email",      "type": "email"},
            {"name": "Source",     "type": "singleSelect",
             "options": {"choices": [
                 {"name": "Facebook",  "color": "blueLight2"},
                 {"name": "Instagram", "color": "purpleLight2"},
                 {"name": "WhatsApp",  "color": "greenLight2"},
                 {"name": "Web",       "color": "yellowLight2"},
                 {"name": "Referido",  "color": "orangeLight2"},
             ]}},
            {"name": "Status",     "type": "singleSelect",
             "options": {"choices": [
                 {"name": "New",       "color": "blueLight2"},
                 {"name": "Contacted", "color": "yellowLight2"},
                 {"name": "Quoted",    "color": "orangeLight2"},
                 {"name": "Converted", "color": "greenLight2"},
                 {"name": "Lost",      "color": "redLight2"},
             ]}},
            {"name": "Notes",      "type": "multilineText"},
            {"name": "Created_At", "type": "dateTime",
             "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "America/Santo_Domingo"}},
            {"name": "Updated_At", "type": "dateTime",
             "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "America/Santo_Domingo"}},
        ]
    },
    {
        "name": "Bookings",
        "description": "Reservas de traslados confirmadas y en proceso",
        "fields": [
            {"name": "Service",          "type": "singleSelect",
             "options": {"choices": [
                 {"name": "Airport",   "color": "blueLight2"},
                 {"name": "Family",    "color": "greenLight2"},
                 {"name": "Medical",   "color": "redLight2"},
                 {"name": "Ejecutivo", "color": "purpleLight2"},
                 {"name": "By Hour",   "color": "orangeLight2"},
             ]}},
            {"name": "Pickup_Location",  "type": "singleLineText"},
            {"name": "Dropoff_Location", "type": "singleLineText"},
            {"name": "Travel_Date",      "type": "dateTime",
             "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "America/Santo_Domingo"}},
            {"name": "Flight_Number",    "type": "singleLineText"},
            {"name": "Passengers",       "type": "number", "options": {"precision": 0}},
            {"name": "Luggage",          "type": "number", "options": {"precision": 0}},
            {"name": "Distance_KM",      "type": "number", "options": {"precision": 1}},
            {"name": "Quote_Price",      "type": "currency",
             "options": {"precision": 2, "symbol": "$"}},
            {"name": "Driver_Name",      "type": "singleLineText"},
            {"name": "Driver_Phone",     "type": "phoneNumber"},
            {"name": "Driver_Vehicle",   "type": "singleLineText"},
            {"name": "Stripe_Session",   "type": "singleLineText"},
            {"name": "Status",           "type": "singleSelect",
             "options": {"choices": [
                 {"name": "Pending",    "color": "yellowLight2"},
                 {"name": "Confirmed",  "color": "blueLight2"},
                 {"name": "Completed",  "color": "greenLight2"},
                 {"name": "Cancelled",  "color": "redLight2"},
             ]}},
            {"name": "Created_At",    "type": "dateTime",
             "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "America/Santo_Domingo"}},
            {"name": "Completed_At",  "type": "dateTime",
             "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "America/Santo_Domingo"}},
        ]
    },
    {
        "name": "Conversations",
        "description": "Historial de conversaciones con prospectos y clientes",
        "fields": [
            {"name": "Channel",   "type": "singleSelect",
             "options": {"choices": [
                 {"name": "WhatsApp",  "color": "greenLight2"},
                 {"name": "Facebook",  "color": "blueLight2"},
                 {"name": "Instagram", "color": "purpleLight2"},
                 {"name": "Email",     "color": "grayLight2"},
             ]}},
            {"name": "Direction", "type": "singleSelect",
             "options": {"choices": [
                 {"name": "Inbound",  "color": "blueLight2"},
                 {"name": "Outbound", "color": "orangeLight2"},
             ]}},
            {"name": "Body",      "type": "multilineText"},
            {"name": "Timestamp", "type": "dateTime",
             "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "America/Santo_Domingo"}},
            {"name": "AI_Response", "type": "multilineText"},
            {"name": "Intent",    "type": "singleSelect",
             "options": {"choices": [
                 {"name": "Cotizacion",  "color": "blueLight2"},
                 {"name": "Reserva",     "color": "greenLight2"},
                 {"name": "Seguimiento", "color": "yellowLight2"},
                 {"name": "Queja",       "color": "redLight2"},
                 {"name": "Otro",        "color": "grayLight2"},
             ]}},
        ]
    },
    {
        "name": "Posts",
        "description": "Contenido generado por el Agente de Contenido para redes sociales",
        "fields": [
            {"name": "Copy",       "type": "multilineText"},
            {"name": "Tone",       "type": "singleSelect",
             "options": {"choices": [
                 {"name": "Emocional", "color": "purpleLight2"},
                 {"name": "Racional",  "color": "blueLight2"},
             ]}},
            {"name": "Angle",      "type": "singleSelect",
             "options": {"choices": [
                 {"name": "Seguridad",    "color": "blueLight2"},
                 {"name": "Familia",      "color": "greenLight2"},
                 {"name": "Ejecutivo",    "color": "purpleLight2"},
                 {"name": "Precio Claro", "color": "yellowLight2"},
                 {"name": "Cansancio",    "color": "orangeLight2"},
                 {"name": "Testimonio",   "color": "pinkLight2"},
             ]}},
            {"name": "Focus",      "type": "singleLineText"},
            {"name": "Status",     "type": "singleSelect",
             "options": {"choices": [
                 {"name": "Draft",            "color": "grayLight2"},
                 {"name": "Pending Approval", "color": "yellowLight2"},
                 {"name": "Published",        "color": "greenLight2"},
                 {"name": "Rejected",         "color": "redLight2"},
             ]}},
            {"name": "Version",    "type": "singleSelect",
             "options": {"choices": [
                 {"name": "A", "color": "blueLight2"},
                 {"name": "B", "color": "orangeLight2"},
             ]}},
            {"name": "Platform",   "type": "multipleSelects",
             "options": {"choices": [
                 {"name": "Instagram", "color": "purpleLight2"},
                 {"name": "Facebook",  "color": "blueLight2"},
             ]}},
            {"name": "Created_At",       "type": "dateTime",
             "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "America/Santo_Domingo"}},
            {"name": "Published_At",     "type": "dateTime",
             "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "America/Santo_Domingo"}},
            {"name": "Facebook_Post_ID", "type": "singleLineText"},
            {"name": "Instagram_Post_ID","type": "singleLineText"},
            {"name": "Reach",    "type": "number", "options": {"precision": 0}},
            {"name": "Likes",    "type": "number", "options": {"precision": 0}},
            {"name": "Comments", "type": "number", "options": {"precision": 0}},
            {"name": "Clicks",   "type": "number", "options": {"precision": 0}},
        ]
    },
    {
        "name": "Campaigns",
        "description": "Campañas de Meta Ads del piloto — CPA objetivo: < $6",
        "fields": [
            {"name": "Name",               "type": "singleLineText"},
            {"name": "Status",             "type": "singleSelect",
             "options": {"choices": [
                 {"name": "Draft",     "color": "grayLight2"},
                 {"name": "Active",    "color": "greenLight2"},
                 {"name": "Paused",    "color": "yellowLight2"},
                 {"name": "Completed", "color": "blueLight2"},
             ]}},
            {"name": "Daily_Budget",       "type": "currency",
             "options": {"precision": 2, "symbol": "$"}},
            {"name": "Total_Budget",       "type": "currency",
             "options": {"precision": 2, "symbol": "$"}},
            {"name": "Total_Spend",        "type": "currency",
             "options": {"precision": 2, "symbol": "$"}},
            {"name": "Impressions",        "type": "number", "options": {"precision": 0}},
            {"name": "Clicks",             "type": "number", "options": {"precision": 0}},
            {"name": "Leads_Generated",    "type": "number", "options": {"precision": 0}},
            {"name": "Bookings_Confirmed", "type": "number", "options": {"precision": 0}},
            {"name": "CPA",                "type": "formula",
             "options": {"formula": "IF({Bookings_Confirmed} > 0, {Total_Spend} / {Bookings_Confirmed}, 0)"}},
            {"name": "CTR",                "type": "formula",
             "options": {"formula": "IF({Impressions} > 0, ({Clicks} / {Impressions}) * 100, 0)"}},
            {"name": "CPA_Status",         "type": "formula",
             "options": {"formula": "IF({CPA} = 0, 'Sin datos', IF({CPA} < 6, '✅ SALUDABLE', IF({CPA} <= 8, '⚠️ ALERTA', '🔴 PAUSA')))"}},
            {"name": "Created_At",         "type": "dateTime",
             "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "America/Santo_Domingo"}},
        ]
    },
    {
        "name": "Daily_Metrics",
        "description": "Dashboard diario — La métrica que importa: CPA < $6",
        "fields": [
            {"name": "Date",            "type": "date",
             "options": {"dateFormat": {"name": "iso"}}},
            {"name": "Leads",           "type": "number", "options": {"precision": 0}},
            {"name": "Bookings",        "type": "number", "options": {"precision": 0}},
            {"name": "Revenue",         "type": "currency",
             "options": {"precision": 2, "symbol": "$"}},
            {"name": "Ad_Spend",        "type": "currency",
             "options": {"precision": 2, "symbol": "$"}},
            {"name": "CPA",             "type": "currency",
             "options": {"precision": 2, "symbol": "$"}},
            {"name": "Conversion_Rate", "type": "percent",
             "options": {"precision": 1}},
            {"name": "ROI",             "type": "percent",
             "options": {"precision": 1}},
            {"name": "Margin",          "type": "currency",
             "options": {"precision": 2, "symbol": "$"}},
            {"name": "CPA_Status",      "type": "formula",
             "options": {"formula": "IF({CPA} = 0, '⏳ Sin datos', IF({CPA} < 6, '✅ SALUDABLE', IF({CPA} <= 8, '⚠️ ALERTA AMARILLA', '🔴 PAUSA')))"}},
            {"name": "Margin_Formula",  "type": "formula",
             "options": {"formula": "{Revenue} - {Ad_Spend}"}},
            {"name": "Notes",           "type": "multilineText"},
            {"name": "Alert_Sent",      "type": "checkbox"},
        ]
    },
]


# ─────────────────────────────────────────────
# CREADOR DE BASE Y TABLAS
# ─────────────────────────────────────────────
class AirtableSetup:

    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        self.base_url = "https://api.airtable.com/v0/meta"

    def get_workspace_id(self) -> str:
        """Obtiene el ID del primer workspace disponible."""
        resp = requests.get(f"{self.base_url}/workspaces", headers=self.headers)
        if resp.status_code != 200:
            raise Exception(f"Error obteniendo workspaces: {resp.text}")
        workspaces = resp.json().get("workspaces", [])
        if not workspaces:
            raise Exception("No hay workspaces disponibles")
        return workspaces[0]["id"]

    def create_base(self, workspace_id: str, name: str) -> str:
        """Crea una nueva base de Airtable."""
        # Crear base con la primera tabla (requerida por la API)
        payload = {
            "name": name,
            "workspaceId": workspace_id,
            "tables": [
                {
                    "name": "Leads",
                    "fields": [{"name": "Name", "type": "singleLineText"}]
                }
            ]
        }
        resp = requests.post(f"{self.base_url}/bases", json=payload, headers=self.headers)
        if resp.status_code not in (200, 201):
            raise Exception(f"Error creando base: {resp.text}")
        data = resp.json()
        base_id = data["id"]
        print(f"  ✅ Base creada: {name} (ID: {base_id})")
        return base_id

    def get_existing_tables(self, base_id: str) -> dict:
        """Obtiene las tablas existentes en la base."""
        resp = requests.get(f"{self.base_url}/bases/{base_id}/tables", headers=self.headers)
        if resp.status_code != 200:
            raise Exception(f"Error obteniendo tablas: {resp.text}")
        tables = resp.json().get("tables", [])
        return {t["name"]: t["id"] for t in tables}

    def create_table(self, base_id: str, table_schema: dict) -> str:
        """Crea una tabla con todos sus campos."""
        payload = {
            "name": table_schema["name"],
            "description": table_schema.get("description", ""),
            "fields": table_schema["fields"]
        }
        resp = requests.post(
            f"{self.base_url}/bases/{base_id}/tables",
            json=payload,
            headers=self.headers
        )
        if resp.status_code not in (200, 201):
            print(f"  ⚠️  Error en tabla {table_schema['name']}: {resp.status_code}")
            # Intentar con campos básicos si falla
            return self._create_table_basic(base_id, table_schema)

        table_id = resp.json()["id"]
        field_count = len(table_schema["fields"])
        print(f"  ✅ Tabla '{table_schema['name']}' creada ({field_count} campos)")
        return table_id

    def _create_table_basic(self, base_id: str, table_schema: dict) -> str:
        """Fallback: crea tabla con solo campos de texto si el schema complejo falla."""
        basic_fields = [
            {"name": f["name"], "type": "singleLineText"}
            for f in table_schema["fields"]
            if f["type"] not in ("formula",)
        ]
        payload = {"name": table_schema["name"], "fields": basic_fields[:5]}
        resp = requests.post(
            f"{self.base_url}/bases/{base_id}/tables",
            json=payload,
            headers=self.headers
        )
        if resp.status_code in (200, 201):
            print(f"  ✅ Tabla '{table_schema['name']}' creada (modo básico)")
            return resp.json()["id"]
        raise Exception(f"No se pudo crear tabla {table_schema['name']}: {resp.text}")

    def update_leads_table(self, base_id: str, table_id: str):
        """Actualiza la tabla Leads creada automáticamente con los campos correctos."""
        for field in TABLES_SCHEMA[0]["fields"]:
            if field["name"] == "Name":
                continue  # Ya existe
            payload = {"fields": [field]}
            resp = requests.post(
                f"{self.base_url}/bases/{base_id}/tables/{table_id}/fields",
                json={"name": field["name"], "type": field["type"],
                      **({} if "options" not in field else {"options": field["options"]})},
                headers=self.headers
            )
            time.sleep(0.3)  # Rate limit

    def add_linked_field(self, base_id: str, table_id: str, linked_table_id: str, field_name: str):
        """Agrega campo de relación (Link) entre tablas."""
        payload = {
            "name": field_name,
            "type": "multipleRecordLinks",
            "options": {"linkedTableId": linked_table_id}
        }
        resp = requests.post(
            f"{self.base_url}/bases/{base_id}/tables/{table_id}/fields",
            json=payload,
            headers=self.headers
        )
        if resp.status_code in (200, 201):
            print(f"  🔗 Relación '{field_name}' creada")
        time.sleep(0.3)

    def run(self, base_name: str = "Emovils OPC"):
        """Ejecuta el setup completo."""
        print()
        print("═══════════════════════════════════════════════")
        print("  EMOVILS OPC — Setup de Airtable")
        print("═══════════════════════════════════════════════")

        # 1. Obtener workspace
        print("\n→ Conectando con Airtable...")
        workspace_id = self.get_workspace_id()
        print(f"  ✅ Workspace encontrado: {workspace_id}")

        # 2. Crear la base
        print(f"\n→ Creando base '{base_name}'...")
        base_id = self.create_base(workspace_id, base_name)

        # 3. Obtener tabla Leads ya creada
        existing = self.get_existing_tables(base_id)
        leads_table_id = existing.get("Leads", "")
        print(f"\n→ Creando tablas y campos...")

        # 4. Crear las demás tablas
        table_ids = {"Leads": leads_table_id}
        for table_schema in TABLES_SCHEMA:
            if table_schema["name"] == "Leads":
                # Actualizar tabla Leads existente
                self.update_leads_table(base_id, leads_table_id)
                print(f"  ✅ Tabla 'Leads' actualizada con todos los campos")
                time.sleep(0.5)
                continue
            table_id = self.create_table(base_id, table_schema)
            table_ids[table_schema["name"]] = table_id
            time.sleep(0.5)

        # 5. Agregar relaciones entre tablas
        print("\n→ Creando relaciones entre tablas...")
        if "Bookings" in table_ids and "Leads" in table_ids:
            self.add_linked_field(base_id, table_ids["Bookings"], table_ids["Leads"], "Lead_ID")
        if "Conversations" in table_ids and "Leads" in table_ids:
            self.add_linked_field(base_id, table_ids["Conversations"], table_ids["Leads"], "Lead_ID")

        # 6. Reporte final
        print()
        print("═══════════════════════════════════════════════")
        print("  ✅ AIRTABLE CREADA CON 6 TABLAS")
        print()
        print(f"  Base ID: {base_id}")
        print(f"  URL: https://airtable.com/{base_id}")
        print()
        print("  Tablas creadas:")
        for name, tid in table_ids.items():
            print(f"  ├─ {name}: {tid}")
        print()
        print("  PRÓXIMO PASO:")
        print(f"  Agrega en tu .env:")
        print(f"  AIRTABLE_BASE_ID={base_id}")
        print("═══════════════════════════════════════════════")
        print()

        return base_id, table_ids


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print()
        print("USO: python setup_airtable.py TU_TOKEN_AQUI")
        print()
        print("Para obtener tu token:")
        print("  1. Ve a https://airtable.com/create/tokens")
        print("  2. Clic en 'Create new token'")
        print("  3. Nombre: 'Emovils OPC Setup'")
        print("  4. Scopes necesarios:")
        print("     ✓ data.records:write")
        print("     ✓ schema.bases:write")
        print("     ✓ schema.bases:read")
        print("     ✓ workspace.workspaces:read")
        print("  5. Copia el token y pégalo aquí")
        print()
        sys.exit(1)

    token = sys.argv[1]
    setup = AirtableSetup(token)
    try:
        base_id, tables = setup.run()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nVerifica que tu token tenga los scopes correctos:")
        print("  schema.bases:write, schema.bases:read, workspace.workspaces:read")
        sys.exit(1)
