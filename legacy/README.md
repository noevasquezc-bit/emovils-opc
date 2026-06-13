# Legacy — Primera generación de Emovils (ABANDONADA)

Esta carpeta contiene los módulos de la **primera arquitectura** del sistema,
previa al motor OPC actual (`main_opc.py` + paquete `opc/`). Se conservan
**solo como referencia histórica** — no se importan desde producción y varios
tienen imports rotos.

## Contenido

| Archivo / carpeta          | Qué era                                                        |
|----------------------------|----------------------------------------------------------------|
| `main_legacy_b2c.py`       | Servidor Flask v1 del piloto B2C                               |
| `main.py.new`              | Borrador de reescritura del servidor v1 (nunca terminado)      |
| `agents/`                  | Agentes v1 (marketing, contenido, analytics, vendedor, director) |
| `wf_reserva_urgente.py`    | Workflow Python de reserva urgente (v1)                        |
| `wf_followup_sequence.py`  | Workflow Python de seguimiento a leads (v1)                    |

## Notas

- Producción corre `gunicorn main_opc:app` (ver `Procfile`) e importa **solo** del paquete `opc/`.
- Los workflows JSON de n8n siguen vivos en `workflows/*.json` (no son legacy).
- Los clientes reutilizables (`lib/paypal_api.py`, `lib/google_maps.py`, etc.)
  y la configuración (`config/`) se mantienen en la raíz porque el flujo OPC los usa.
- No agregar código nuevo aquí.
