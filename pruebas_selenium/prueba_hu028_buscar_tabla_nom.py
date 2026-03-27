from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
import time


URL = "http://127.0.0.1:8000/"
USUARIO = "alfredo.arias@fortiapv.com"
PASSWORD = "Admin123*"

TABLA_BUSQUEDA = "Tabla 8.-Propiedades de los conductores"


def click_seguro(driver, elemento):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elemento)
    time.sleep(1)
    try:
        elemento.click()
    except Exception:
        driver.execute_script("arguments[0].click();", elemento)


def main():
    driver = webdriver.Chrome()
    wait = WebDriverWait(driver, 20)
    wait_largo = WebDriverWait(driver, 120)
    actions = ActionChains(driver)

    try:
        # 1. Abrir sistema
        driver.get(URL)
        print("Página abierta")

        # 2. Login (AUTOMÁTICO)
        usuario = wait.until(EC.presence_of_element_located((By.ID, "usuario")))
        password = wait.until(EC.presence_of_element_located((By.ID, "password")))

        usuario.clear()
        usuario.send_keys(USUARIO)
        password.clear()
        password.send_keys(PASSWORD)

        print("Resuelve captcha manualmente y presiona el botón ingresar...")

        # 3. Abrir menú Recursos (después de login manual)
        menu_recursos = wait_largo.until(
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

        # 4. Entrar a Tablas (ruta real)
        submenu_tablas = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(@href, '/recursos/tablas')]")
            )
        )
        click_seguro(driver, submenu_tablas)
        print("Entró al módulo Tablas NOM")
        time.sleep(2)

        # 5. Confirmar vista
        print(f"URL actual: {driver.current_url}")

        # 6. Capturar caja de búsqueda
        caja_busqueda = wait.until(
            EC.presence_of_element_located((By.NAME, "q"))
        )
        caja_busqueda.clear()
        caja_busqueda.send_keys(TABLA_BUSQUEDA)
        print(f"Texto capturado en búsqueda: {TABLA_BUSQUEDA}")

        # 7. Pulsar Buscar
        boton_buscar = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//form[@id='searchFormTablas']//button[@type='submit']")
            )
        )
        click_seguro(driver, boton_buscar)
        print("Botón Buscar presionado")
        time.sleep(2)

        # 8. Validar resultado (SOLO BUSCAR)
        body_text = driver.find_element(By.TAG_NAME, "body").text

        assert TABLA_BUSQUEDA in body_text, \
            f"No apareció la tabla '{TABLA_BUSQUEDA}' en los resultados"

        print("✅ Resultado encontrado correctamente")

        print("✅ PRUEBA EXITOSA (HU028 - búsqueda de tabla NOM)")
        time.sleep(5)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()