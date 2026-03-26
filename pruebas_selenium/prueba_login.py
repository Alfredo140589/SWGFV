from selenium import webdriver
from selenium.webdriver.common.by import By
import time

driver = webdriver.Chrome()

try:
    driver.get("http://127.0.0.1:8000/")
    time.sleep(2)

    usuario = driver.find_element(By.ID, "usuario")
    password = driver.find_element(By.ID, "password")

    usuario.clear()
    usuario.send_keys("alfredo.arias@fortiapv.com")

    password.clear()
    password.send_keys("Admin123*")

    print("Resuelve el captcha manualmente y entra al sistema...")
    time.sleep(30)

finally:
    driver.quit()