import csv
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import TablaNOM


class Command(BaseCommand):
    help = "Sincroniza el campo imagen de TablaNOM usando data/tablas_nom.csv y las imágenes existentes en core/static/core/img/tablas_nom"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default="data/tablas_nom.csv",
            help="Ruta relativa al CSV de tablas NOM."
        )
        parser.add_argument(
            "--origen",
            type=str,
            default="core/static/core/img/tablas_nom",
            help="Ruta relativa a la carpeta donde ya están las imágenes fuente."
        )
        parser.add_argument(
            "--destino",
            type=str,
            default="media/tablas_nom",
            help="Ruta relativa a la carpeta media donde quedarán las imágenes usadas por ImageField."
        )

    def handle(self, *args, **options):
        csv_path = Path(settings.BASE_DIR) / options["file"]
        origen_dir = Path(settings.BASE_DIR) / options["origen"]
        destino_dir = Path(settings.BASE_DIR) / options["destino"]

        if not csv_path.exists():
            raise CommandError(f"No se encontró el CSV: {csv_path}")

        if not origen_dir.exists():
            raise CommandError(f"No se encontró la carpeta de imágenes origen: {origen_dir}")

        destino_dir.mkdir(parents=True, exist_ok=True)

        actualizadas = 0
        omitidas = 0
        no_encontradas_bd = 0
        no_encontradas_archivo = 0

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
                nombre_imagen = (row.get("nombre_imagen") or "").strip()

                if not nombre_tabla or not nombre_imagen:
                    omitidas += 1
                    continue

                tabla = TablaNOM.objects.filter(nombre_tabla=nombre_tabla).first()
                if not tabla:
                    no_encontradas_bd += 1
                    self.stdout.write(
                        self.style.WARNING(f"[BD] No encontrada: {nombre_tabla}")
                    )
                    continue

                origen = origen_dir / nombre_imagen
                if not origen.exists():
                    no_encontradas_archivo += 1
                    self.stdout.write(
                        self.style.WARNING(f"[IMG] No encontrada: {origen}")
                    )
                    continue

                destino = destino_dir / nombre_imagen

                # Copiar imagen a media/tablas_nom
                shutil.copy2(origen, destino)

                # Guardar ruta relativa en ImageField
                tabla.imagen.name = f"tablas_nom/{nombre_imagen}"
                tabla.save(update_fields=["imagen"])

                actualizadas += 1
                self.stdout.write(self.style.SUCCESS(
                    f"[OK] {tabla.id} -> tablas_nom/{nombre_imagen}"
                ))

        self.stdout.write(self.style.SUCCESS(
            f"Sincronización terminada. "
            f"Actualizadas: {actualizadas} | "
            f"Omitidas: {omitidas} | "
            f"No encontradas en BD: {no_encontradas_bd} | "
            f"No encontradas en carpeta: {no_encontradas_archivo}"
        ))