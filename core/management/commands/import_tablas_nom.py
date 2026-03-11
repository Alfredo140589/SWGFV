import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import TablaNOM


class Command(BaseCommand):
    help = "Importa y sincroniza tablas NOM desde data/tablas_nom.csv"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default="data/tablas_nom.csv",
            help="Ruta relativa al proyecto del archivo CSV a importar."
        )

    def handle(self, *args, **options):
        relative_path = options["file"]
        csv_path = Path(settings.BASE_DIR) / relative_path

        if not csv_path.exists():
            raise CommandError(f"No se encontró el archivo CSV: {csv_path}")

        creados = 0
        actualizados = 0
        eliminados = 0

        nombres_csv = set()
        filas_validas = []

        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)

            columnas_esperadas = {"nombre_tabla", "notas", "nombre_imagen"}
            columnas_encontradas = set(reader.fieldnames or [])

            if not columnas_esperadas.issubset(columnas_encontradas):
                raise CommandError(
                    f"El CSV no tiene las columnas esperadas. "
                    f"Esperadas: {sorted(columnas_esperadas)} | "
                    f"Encontradas: {sorted(columnas_encontradas)}"
                )

            for row in reader:
                nombre_tabla = (row.get("nombre_tabla") or "").strip()
                notas = (row.get("notas") or "").strip()
                nombre_imagen = (row.get("nombre_imagen") or "").strip()

                if not nombre_tabla:
                    continue

                nombres_csv.add(nombre_tabla)
                filas_validas.append({
                    "nombre_tabla": nombre_tabla,
                    "notas": notas,
                    "nombre_imagen": nombre_imagen,
                })

        # 1) Eliminar registros que ya no estén en el CSV
        qs_eliminar = TablaNOM.objects.exclude(nombre_tabla__in=nombres_csv)
        eliminados = qs_eliminar.count()
        qs_eliminar.delete()

        # 2) Crear o actualizar SOLO texto
        # La imagen NO se asigna aquí porque ahora el modelo usa ImageField.
        for fila in filas_validas:
            obj, creado = TablaNOM.objects.update_or_create(
                nombre_tabla=fila["nombre_tabla"],
                defaults={
                    "notas": fila["notas"],
                }
            )

            if creado:
                creados += 1
            else:
                actualizados += 1

        self.stdout.write(self.style.SUCCESS(
            f"Sincronización completada. Creados: {creados} | "
            f"Actualizados: {actualizados} | Eliminados: {eliminados}"
        ))

        self.stdout.write(
            self.style.WARNING(
                "Nota: este comando ya no carga imágenes al ImageField. "
                "Para relacionar imágenes físicas usa el comando "
                "'sincronizar_imagenes_tablas_nom'."
            )
        )