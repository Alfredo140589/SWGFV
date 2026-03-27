from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from pathlib import Path
import time


URL = "http://127.0.0.1:8000/"
USUARIO = "alfredo.arias@fortiapv.com"
PASSWORD = "Admin123*"

PROYECTO = "Prueba corrección"

CARPETA_DESCARGAS = Path.cwd() / "descargas_prueba_modulos"


def limpiar_descargas():
    CARPETA_DESCARGAS.mkdir(exist_ok=True)
    for archivo in CARPETA_DESCARGAS.iterdir():
        if archivo.is_file():
            archivo.unlink()


def esperar_pdf(timeout=30):
    inicio = time.time()
    while time.time() - inicio < timeout:
        archivos = list(CARPETA_DESCARGAS.glob("*.pdf"))
        temporales = list(CARPETA_DESCARGAS.glob("*.crdownload"))
        if archivos and not temporales:
            return archivos[0]
        time.sleep(1)
    raise TimeoutError("No se descargó el PDF")


def click_seguro(driver, elemento):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elemento)
    time.sleep(1)
    try:
        elemento.click()
    except Exception:
        driver.execute_script("arguments[0].click();", elemento)


def seleccionar_proyecto(driver, wait, proyecto):
    select_proyecto = wait.until(
        EC.presence_of_element_located((By.ID, "proyectoSelect"))
    )
    combo = Select(select_proyecto)

    opciones = [op.text.strip() for op in combo.options]
    print("Opciones disponibles en proyecto:")
    for op in opciones:
        print(f"- '{op}'")

    combo.select_by_visible_text(proyecto)
    print(f"Proyecto seleccionado: {proyecto}")
    time.sleep(2)


def main():
    limpiar_descargas()

    options = webdriver.ChromeOptions()
    prefs = {
        "download.default_directory": str(CARPETA_DESCARGAS.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
    }
    options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 20)
    wait_largo = WebDriverWait(driver, 120)
    actions = ActionChains(driver)

    try:
        # 1. Abrir sistema
        driver.get(URL)
        print("Página abierta")

        # 2. Login
        usuario = wait.until(EC.presence_of_element_located((By.ID, "usuario")))
        password = wait.until(EC.presence_of_element_located((By.ID, "password")))

        usuario.clear()
        usuario.send_keys(USUARIO)
        password.clear()
        password.send_keys(PASSWORD)

        print("Resuelve captcha manualmente y entra al sistema...")

        # 3. Abrir menú Dimensionamiento
        menu_dimensionamiento = wait_largo.until(
            EC.presence_of_element_located(
                (By.XPATH, "//a[normalize-space()='Dimensionamiento']")
            )
        )

        actions.move_to_element(menu_dimensionamiento).perform()
        time.sleep(1)

        try:
            menu_dimensionamiento.click()
        except Exception:
            driver.execute_script("arguments[0].click();", menu_dimensionamiento)

        print("Menú Dimensionamiento encontrado")
        time.sleep(1)

        # 4. Entrar al submenú Cálculo de módulos
        submenu_modulos = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "(//a[contains(@href, 'modulos') or contains(., 'Cálculo de módulos') or contains(., 'módulos') or contains(., 'Módulos')])[1]"
                )
            )
        )

        click_seguro(driver, submenu_modulos)
        print("Entró al módulo de cálculo de módulos")
        time.sleep(2)

        # 5. Confirmar vista actual
        print(f"URL actual: {driver.current_url}")

        # 6. Seleccionar proyecto
        seleccionar_proyecto(driver, wait, PROYECTO)

        # 7. Esperar a que aparezca Descargar PDF
        boton_pdf = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//a[contains(., 'Descargar PDF')] | "
                    "//button[contains(., 'Descargar PDF')] | "
                    "//a[contains(@href, '/pdf/')]"
                )
            )
        )
        print("Botón Descargar PDF visible")

        # 8. Descargar PDF
        click_seguro(driver, boton_pdf)
        print("Descargando PDF...")

        archivo_pdf = esperar_pdf(timeout=30)
        print(f"PDF descargado: {archivo_pdf.name}")

        assert archivo_pdf.exists(), "El PDF no existe"

        print("✅ PRUEBA EXITOSA (HU017 - seleccionar proyecto y descargar PDF)")
        time.sleep(5)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()