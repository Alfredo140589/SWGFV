from django.core.management.base import BaseCommand
from django.db import connection, transaction

from core.models import GlosarioConcepto


class Command(BaseCommand):
    help = "Reordena los IDs del glosario alfabéticamente y reinicia el autoincrement."

    def handle(self, *args, **kwargs):
        with transaction.atomic():
            conceptos = list(
                GlosarioConcepto.objects.all().order_by("nombre_concepto")
            )

            if not conceptos:
                self.stdout.write(self.style.WARNING("No hay conceptos para reordenar."))
                return

            datos = []
            for c in conceptos:
                datos.append({
                    "nombre_concepto": c.nombre_concepto,
                    "descripcion": c.descripcion,
                    "formula": c.formula,
                    "categoria": c.categoria,
                    "created_at": c.created_at,
                })

            # Eliminar todos los registros actuales
            GlosarioConcepto.objects.all().delete()

            # Recrear con IDs consecutivos desde 1
            nuevos = []
            for i, d in enumerate(datos, start=1):
                nuevos.append(
                    GlosarioConcepto(
                        id=i,
                        nombre_concepto=d["nombre_concepto"],
                        descripcion=d["descripcion"],
                        formula=d["formula"],
                        categoria=d["categoria"],
                        created_at=d["created_at"],
                    )
                )

            GlosarioConcepto.objects.bulk_create(nuevos)

            motor = connection.vendor

            with connection.cursor() as cursor:
                if motor == "postgresql":
                    cursor.execute(
                        "SELECT setval(pg_get_serial_sequence('glosario_conceptos','id'), %s, true)",
                        [len(nuevos)]
                    )
                elif motor == "sqlite":
                    cursor.execute(
                        "UPDATE sqlite_sequence SET seq = ? WHERE name = ?",
                        [len(nuevos), "glosario_conceptos"]
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"IDs reordenados correctamente. Total registros: {len(nuevos)}"
            )
        )