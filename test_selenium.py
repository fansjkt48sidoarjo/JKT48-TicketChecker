from selenium import webdriver
from selenium.webdriver.common.by import By
import time

# Membuka browser Chrome secara otomatis
driver = webdriver.Chrome()

try:
    # Mengakses website target
    driver.get("https://jkt48.com/")
    
    # Menunggu beberapa detik agar halaman termuat sempurna
    time.sleep(3)
    
    # Mengambil judul halaman (Title)
    print("Judul Website:", driver.title)

finally:
    # Menutup browser setelah selesai
    driver.quit()