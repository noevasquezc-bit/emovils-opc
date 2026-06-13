# workers=1 OBLIGATORIO: el scheduler (opc/scheduler.py) corre en el proceso web
# y con >1 worker se duplicarían reportes/follow-ups. Concurrencia via threads.
web: gunicorn main_opc:app --bind 0.0.0.0:$PORT --workers 1 --threads 16 --timeout 120
