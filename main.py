from playwright.sync_api import sync_playwright
import re

# =========================
# Función de normalización (marca/unidades/peso)
# =========================
def normalizar_producto(nombre):
    unidades = None
    match_un = re.search(r"(\d+)\s*un", nombre.lower())
    if match_un:
        unidades = int(match_un.group(1))

    peso = None
    match_g = re.search(r"(\d+)\s*g", nombre.lower())
    if match_g:
        peso = int(match_g.group(1))
    match_kg = re.search(r"(\d+)\s*kg", nombre.lower())
    if match_kg:
        peso = int(match_kg.group(1)) * 1000

    return {"nombre": nombre, "unidades": unidades, "peso": peso}


# =========================
# Scraping Jumbo
# =========================
def scrape_jumbo(page, max_pages=3):
    resultados = []
    base_url = "https://www.jumbo.cl/busqueda?ft=vienesas"
    for page_num in range(1, max_pages + 1):
        url = f"{base_url}&page={page_num}"
        print(f"🔎 Jumbo → {url}")
        page.goto(url, timeout=60000)
        page.wait_for_selector("div[data-cnstrc-item-name]", state="attached", timeout=30000)

        items = page.query_selector_all("div[data-cnstrc-item-name]")
        for pdt in items:
            nombre = pdt.get_attribute("data-cnstrc-item-name")
            precio = pdt.get_attribute("data-cnstrc-item-price")
            try:
                marca = pdt.query_selector("p.text-sm.text-gray-500").inner_text()
            except:
                marca = "N/A"

            try:
                precio = int(precio)
            except:
                precio = 0

            resultados.append({"nombre": nombre, "marca": marca, "precio": precio})
    return resultados


# =========================
# Scraping Santa Isabel
# =========================
def scrape_santaisabel(page, max_pages=2):
    resultados = []
    base_url = "https://www.santaisabel.cl/busqueda?ft=vienesas"
    for page_num in range(1, max_pages + 1):
        url = f"{base_url}&page={page_num}"
        print(f"🔎 Santa Isabel → {url}")
        page.goto(url, timeout=60000)
        page.wait_for_selector("div.product-card-wrap[data-cnstrc-item-name]", state="attached", timeout=30000)

        items = page.query_selector_all("div.product-card-wrap[data-cnstrc-item-name]")
        for pdt in items:
            nombre = pdt.get_attribute("data-cnstrc-item-name")
            precio = pdt.get_attribute("data-cnstrc-item-price")

            if precio is None:
                try:
                    precio = pdt.query_selector("span.prices-main-price").inner_text()
                    precio = precio.replace("$", "").replace(".", "").strip()
                except:
                    precio = "0"

            try:
                precio = int(precio)
            except:
                precio = 0

            try:
                marca = pdt.query_selector("p.product-card-brand").inner_text()
            except:
                marca = "N/A"

            resultados.append({"nombre": nombre, "marca": marca, "precio": precio})
    return resultados

# =========================
# Comparación (3 tiendas)
# =========================
def comparar_tiendas(jumbo, sisa, unimarc):
    print("\n📊 Comparación de precios entre Jumbo, Santa Isabel y Unimarc:\n")

    # recorremos todos los productos de Jumbo
    for j in jumbo:
        nj = normalizar_producto(j["nombre"])
        comparaciones = []

        # buscar en Santa Isabel
        for s in sisa:
            if j["marca"].lower() != s["marca"].lower():
                continue
            ns = normalizar_producto(s["nombre"])
            if nj["unidades"] == ns["unidades"] and nj["peso"] == ns["peso"]:
                comparaciones.append(("Santa Isabel", s["precio"]))

        # buscar en Unimarc
        for l in unimarc:
            if j["marca"].lower() != l["marca"].lower():
                continue
            nl = normalizar_producto(l["nombre"])
            if nj["unidades"] == nl["unidades"] and nj["peso"] == nl["peso"]:
                comparaciones.append(("Unimarc", l["precio"]))

        if comparaciones:
            print(f"{j['nombre']} ({j['marca']})")
            print(f"  Jumbo: ${j['precio']}")
            for tienda, precio in comparaciones:
                print(f"  {tienda}: ${precio}")
            precios = [j["precio"]] + [p for _, p in comparaciones]
            min_precio = min(precios)
            if min_precio == j["precio"]:
                mas_barato = "Jumbo"
            else:
                mas_barato = [t for t, p in comparaciones if p == min_precio][0]
            print(f"  → Más barato: {mas_barato}\n")
        else:
            print(f"{j['nombre']} ({j['marca']}) solo en Jumbo: ${j['precio']}")

def scrape_unimarc(page):
    url = "https://www.unimarc.cl/search?q=vienesas"
    print(f"🔎 Unimarc → {url}")
    page.goto(url, timeout=60000)

    # esperar a que aparezcan los productos
    page.wait_for_selector("p.Shelf_nameProduct__CXI5M", state="attached", timeout=30000)

    resultados = []
    productos = page.query_selector_all("div.baseContainer_container__TSgMX")
    for p in productos:
        try:
            marca = p.query_selector("p.Shelf_brandText__sGfsS").inner_text().strip()
        except:
            marca = "N/A"

        try:
            nombre = p.query_selector("p.Shelf_nameProduct__CXI5M").inner_text().strip()
        except:
            nombre = "N/A"

        try:
            precio = p.query_selector("p[id^='listPrice__offerPrice']").inner_text()
            precio = precio.replace("$", "").replace(".", "").strip()
            precio = int(precio)
        except:
            precio = 0

        if nombre != "N/A":
            resultados.append({
                "nombre": nombre,
                "marca": marca,
                "precio": precio
            })

    return resultados

def limpiar_productos(productos):
    """
    Quita duplicados y precios inválidos.
    Mantiene el menor precio encontrado por marca + unidades + peso.
    """
    normalizados = {}
    for p in productos:
        n = normalizar_producto(p["nombre"])
        if not n["unidades"] and not n["peso"]:
            clave = (p["marca"].lower(), p["nombre"].lower())
        else:
            clave = (p["marca"].lower(), n["unidades"], n["peso"])

        precio = p["precio"] if p["precio"] and p["precio"] > 0 else None
        if clave not in normalizados:
            normalizados[clave] = {**p, "precio": precio if precio else 9999999}
        else:
            # conservar el menor precio válido
            if precio and precio < normalizados[clave]["precio"]:
                normalizados[clave] = {**p, "precio": precio}

    # devolver lista limpia
    return list(normalizados.values())

# =========================
# MAIN
# =========================
def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # pon True si no quieres ver el navegador
        page = browser.new_page()

        jumbo = scrape_jumbo(page, max_pages=3)
        sisa = scrape_santaisabel(page, max_pages=2)
        unimarc = scrape_unimarc(page)
        
        jumbo = limpiar_productos(jumbo)
        sisa = limpiar_productos(sisa)
        unimarc = limpiar_productos(unimarc)

        print(f"\n🛒 Jumbo: {len(jumbo)} productos")
        print(f"🛒 Santa Isabel: {len(sisa)} productos")
        print(f"🛒 Unimarc: {len(unimarc)} productos")

        comparar_tiendas(jumbo, sisa, unimarc)

        browser.close()


if __name__ == "__main__":
    main()
