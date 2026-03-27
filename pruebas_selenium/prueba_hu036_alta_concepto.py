from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
import time


URL = "http://127.0.0.1:8000/"
USUARIO = "alfredo.arias@fortiapv.com"
PASSWORD = "Admin123*"

NOMBRE_CONCEPTO = "Concepto prueba"
DESCRIPCION = "Este es una prueba alta de concepto"
CATEGORIA = "Electrico"


def click_seguro(driver, elemento):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elemento)
    time.sleep(1)
    try:
        elemento.click()
    except Exception:
        driver.execute_script("arguments[0].click();", elemento)


def abrir_menu_recursos(driver, wait, actions):
    menu_recursos = wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//a[normalize-space()='Recursos']")
        )
    )

    actions.move_to_element(menu_recursos).perform()
    time.sleep(1)

    try:
        menu_recursos.click()
    except Exception:
        driver.execute_script("arguments[0].click();", menu_recursos)

    print("Menú Recursos encontrado")
    time.sleep(1)


def main():
    driver = webdriver.Chrome()
    wait = WebDriverWait(driver, 20)
    wait_largo = WebDriverWait(driver, 120)
    actions = ActionChains(driver)

    try:
        # 1. Abrir sistema
        driver.get(URL)
        print("Página abierta")

        # 2. Login automático
        usuario = wait.until(EC.presence_of_element_located((By.ID, "usuario")))
        password = wait.until(EC.presence_of_element_located((By.ID, "password")))

        usuario.clear()
        usuario.send_keys(USUARIO)
        password.clear()
        password.send_keys(PASSWORD)

        print("Resuelve captcha manualmente y presiona el botón ingresar...")

        # 3. Esperar ingreso manual
        wait_largo.until(
            EC.presence_of_element_located(
                (By.XPATH, "//a[normalize-space()='Recursos']")
            )
        )

        # 4. Abrir Recursos
        abrir_menu_recursos(driver, wait, actions)

        # 5. Entrar a Alta de Concepto
        submenu_alta_concepto = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[normalize-space()='Alta de Concepto']")
            )
        )
        click_seguro(driver, submenu_alta_concepto)
        print("Entró al módulo Alta de Concepto")
        time.sleep(2)

        # 6. Confirmar vista actual
        print(f"URL actual: {driver.current_url}")

        titulo = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//h2[contains(., 'Alta de Concepto')]")
            )
        )
        print(f"Título detectado: {titulo.text}")

        # 7. Capturar campos del formulario
        campo_nombre = wait.until(
            EC.presence_of_element_located((By.NAME, "nombre_concepto"))
        )
        campo_descripcion = wait.until(
            EC.presence_of_element_located((By.NAME, "descripcion"))
        )
        campo_formula = wait.until(
            EC.presence_of_element_located((By.NAME, "formula"))
        )
        campo_categoria = wait.until(
            EC.presence_of_element_located((By.NAME, "categoria"))
        )

        campo_nombre.clear()
        campo_nombre.send_keys(NOMBRE_CONCEPTO)
        print(f"Nombre del concepto capturado: {NOMBRE_CONCEPTO}")

        campo_descripcion.clear()
        campo_descripcion.send_keys(DESCRIPCION)
        print(f"Descripción capturada: {DESCRIPCION}")

        campo_formula.clear()
        print("Fórmula vacía conforme a la prueba")

        campo_categoria.clear()
        campo_categoria.send_keys(CATEGORIA)
        print(f"Categoría capturada: {CATEGORIA}")

        # 8. Pulsar Alta
        boton_alta = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[@type='submit' and @name='action' and @value='save']")
            )
        )
        click_seguro(driver, boton_alta)
        print("Botón Alta presionado")
        time.sleep(2)

        # 9. Validar el alta buscando el concepto en Recursos > Conceptos
        abrir_menu_recursos(driver, wait, actions)

        submenu_conceptos = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(@href, '/recursos/conceptos/')]")
            )
        )
        click_seguro(driver, submenu_conceptos)
        print("Entró al módulo Glosario de Conceptos")
        time.sleep(2)

        print(f"URL actual: {driver.current_url}")

        caja_busqueda = wait.until(
            EC.presence_of_element_located((By.NAME, "q"))
        )
        caja_busqueda.clear()
        caja_busqueda.send_keys(NOMBRE_CONCEPTO)
        print(f"Texto capturado en búsqueda: {NOMBRE_CONCEPTO}")

        boton_buscar = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//form[@id='searchFormConceptos']//button[@type='submit']")
            )
        )
        click_seguro(driver, boton_buscar)
        print("Botón Buscar presionado")
        time.sleep(2)

        body_text = driver.find_element(By.TAG_NAME, "body").text

        assert NOMBRE_CONCEPTO in body_text, \
            f"No apareció el concepto '{NOMBRE_CONCEPTO}' en los resultados de búsqueda"

        print("✅ Resultado encontrado correctamente")
        print("✅ PRUEBA EXITOSA (HU036 - alta de concepto nuevo)")
        time.sleep(5)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()