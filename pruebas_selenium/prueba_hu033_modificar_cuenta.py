from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
import time


URL = "http://127.0.0.1:8000/"
USUARIO = "alfredo.arias@fortiapv.com"
PASSWORD = "Admin123*"

NUEVO_TELEFONO = "5522448899"


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

        # 3. Esperar ingreso manual al sistema
        menu_cuenta = wait_largo.until(
            EC.presence_of_element_located(
                (By.XPATH, "//a[normalize-space()='Cuenta']")
            )
        )

        actions.move_to_element(menu_cuenta).perform()
        time.sleep(1)

        try:
            menu_cuenta.click()
        except Exception:
            driver.execute_script("arguments[0].click();", menu_cuenta)

        print("Menú Cuenta encontrado")
        time.sleep(2)

        # 4. Confirmar vista actual
        print(f"URL actual: {driver.current_url}")

        # 5. Pulsar botón Editar
        boton_editar = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(., 'Editar')]")
            )
        )
        click_seguro(driver, boton_editar)
        print("Botón Editar presionado")
        time.sleep(2)

        # 6. Capturar campo Teléfono y modificarlo
        campo_telefono = wait.until(
            EC.presence_of_element_located((By.NAME, "Telefono"))
        )

        telefono_anterior = campo_telefono.get_attribute("value").strip()
        print(f"Teléfono anterior: {telefono_anterior}")

        campo_telefono.clear()
        campo_telefono.send_keys(NUEVO_TELEFONO)
        print(f"Nuevo teléfono capturado: {NUEVO_TELEFONO}")

        # 7. Pulsar Guardar
        boton_guardar = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[@type='submit' and @name='action' and @value='save']")
            )
        )
        click_seguro(driver, boton_guardar)
        print("Botón Guardar presionado")
        time.sleep(2)

        # 8. Validar resultado CORRECTAMENTE leyendo el value del input
        campo_telefono_actualizado = wait.until(
            EC.presence_of_element_located((By.NAME, "Telefono"))
        )
        telefono_actual = campo_telefono_actualizado.get_attribute("value").strip()

        print(f"Teléfono actual mostrado: {telefono_actual}")

        assert telefono_actual == NUEVO_TELEFONO, (
            f"El teléfono no se actualizó correctamente. "
            f"Esperado: {NUEVO_TELEFONO} | Obtenido: {telefono_actual}"
        )

        print("✅ Resultado encontrado correctamente")
        print("✅ PRUEBA EXITOSA (HU033 - modificación de información de la cuenta)")
        time.sleep(5)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()