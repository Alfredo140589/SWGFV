import csv
from decimal import Decimal
from core.models import Inversor, MicroInversor

INV_PATH = r"C:\Users\alfre\Desktop\Escuela\2026-1\SWGFV\data\inversor.csv"
MICRO_PATH = r"C:\Users\alfre\Desktop\Escuela\2026-1\SWGFV\data\microinversor.csv"
ENC = "utf-8-sig"  # tus CSV ya están en UTF-8

def to_decimal(val):
    val = (val or "").strip().replace(",", ".")
    if not val:
        return None
    try:
        return Decimal(val)
    except Exception:
        return None

def clean_text(val):
    return (val or "").strip()

def to_int(val):
    val = (val or "").strip()
    return int(val) if val.isdigit() else None

# =========================
# IMPORTAR INVERSORES
# =========================
new_i = 0
upd_i = 0

with open(INV_PATH, encoding=ENC, newline="") as f:
    reader = csv.DictReader(f)
    print("HEADERS INVERSOR:", reader.fieldnames)

    for row in reader:
        marca = clean_text(row.get("Marca"))
        modelo = clean_text(row.get("Modelo"))
        potencia_w = to_decimal(row.get("Potencia"))  # ✅ W
        volt_nom = clean_text(row.get("Voltaje nominal"))

        if not marca or not modelo:
            continue

        obj, created = Inversor.objects.update_or_create(
            marca=marca,
            modelo=modelo,
            defaults={
                "potencia_w": potencia_w,      # ✅ W (como pediste)
                "voltaje_salida": volt_nom,    # texto
            },
        )
        if created:
            new_i += 1
        else:
            upd_i += 1

print("INVERSORES -> nuevos:", new_i, "| actualizados:", upd_i, "| total:", Inversor.objects.count())

# =========================
# IMPORTAR MICROINVERSORES
# =========================
new_m = 0
upd_m = 0

with open(MICRO_PATH, encoding=ENC, newline="") as f:
    reader = csv.DictReader(f)
    print("HEADERS MICRO:", reader.fieldnames)

    for row in reader:
        marca = clean_text(row.get("Marca"))
        modelo = clean_text(row.get("Modelo"))
        potencia_w = to_decimal(row.get("Potencia"))  # ✅ W
        canales = to_int(row.get("No mppt"))          # mapeo a canales

        if not marca or not modelo:
            continue

        obj, created = MicroInversor.objects.update_or_create(
            marca=marca,
            modelo=modelo,
            defaults={
                "potencia_w": potencia_w,  # ✅ W
                "canales": canales,
            },
        )
        if created:
            new_m += 1
        else:
            upd_m += 1

print("MICRO -> nuevos:", new_m, "| actualizados:", upd_m, "| total:", MicroInversor.objects.count())

print("Ej Inversor:", Inversor.objects.first())
print("Ej Micro:", MicroInversor.objects.first())