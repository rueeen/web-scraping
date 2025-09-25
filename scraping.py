# archivo: comparar_supermercados.py
from playwright.sync_api import sync_playwright
import re
import sys
from urllib.parse import quote

# =============== Utilidades ===============

def safe_int(x, default=0):
    if x is None:
        return default
    if isinstance(x, (int, float)):
        return int(x)
    try:
        x = str(x).replace("$", "").replace(".", "").replace(",", "").strip()
        return int(x) if x.isdigit() else default
    except:
        return default

def normalizar_producto(nombre: str):
    if not nombre:
        return {"unidades": None, "peso": None}
    n = nombre.lower()
    m_un = re.search(r"(\d+)\s*(un|un\.|unidades)", n)
    unidades = int(m_un.group(1)) if m_un else None
    m_kg = re.search(r"(\d+(?:[.,]\d+)?)\s*kg", n)
    m_g = re.search(r"(\d+)\s*g", n)
    peso = None
    if m_kg:
        kg_val = float(m_kg.group(1).replace(",", "."))
        peso = int(round(kg_val * 1000))
    elif m_g:
        peso = int(m_g.group(1))
    return {"unidades": unidades, "peso": peso}

def clave_producto(item):
    marca = (item.get("marca") or "").strip().lower()
    nombre = (item.get("nombre") or "").strip().lower()
    n = normalizar_producto(nombre)
    if n["unidades"] or n["peso"]:
        return (marca, n["unidades"], n["peso"])
    return (marca, nombre)

def limpiar_productos(productos):
    acc = {}
    for p in productos:
        clave = clave_producto(p)
        precio = safe_int(p.get("precio"), default=0)
        if precio <= 0:
            continue
        if clave not in acc or precio < acc[clave]["precio"]:
            acc[clave] = {
                "nombre": p.get("nombre"),
                "marca": p.get("marca"),
                "precio": precio,
            }
    return list(acc.values())

def equivalentes(a, b):
    marca_a = (a.get("marca") or "").lower().strip()
    marca_b = (b.get("marca") or "").lower().strip()
    if not marca_a or marca_a != marca_b:
        return False
    na = normalizar_producto(a.get("nombre") or "")
    nb = normalizar_producto(b.get("nombre") or "")
    if (na["unidades"] is None and na["peso"] is None) or (nb["unidades"] is None and nb["peso"] is None):
        return False
    if (na["unidades"] is not None) and (nb["unidades"] is not None) and na["unidades"] != nb["unidades"]:
        return False
    if (na["peso"] is not None) and (nb["peso"] is not None) and na["peso"] != nb["peso"]:
        return False
    return True

# =============== Scrapers ===============

def scrape_jumbo(page, query="vienesas", max_pages=3):
    resultados = []
    base = f"https://www.jumbo.cl/busqueda?ft={quote(query)}"
    for page_num in range(1, max_pages + 1):
        url = f"{base}&page={page_num}"
        print(f"🔎 Jumbo → {url}")
        page.goto(url, timeout=60000)
        try:
            page.wait_for_selector("div[data-cnstrc-item-name]", state="attached", timeout=15000)
        except:
            print(f"  ⚠️ No se encontraron productos en página {page_num} de Jumbo.")
            break
        items = page.query_selector_all("div[data-cnstrc-item-name]")
        if not items:
            break
        for pdt in items:
            nombre = pdt.get_attribute("data-cnstrc-item-name")
            precio = safe_int(pdt.get_attribute("data-cnstrc-item-price"))
            marca_el = pdt.query_selector("p.text-sm.text-gray-500") or pdt.query_selector("p.product-card-brand")
            marca = marca_el.inner_text().strip() if marca_el else "N/A"
            resultados.append({"nombre": nombre, "marca": marca, "precio": precio})
    return resultados

def scrape_santaisabel(page, query="vienesas", max_pages=3):
    resultados = []
    base = f"https://www.santaisabel.cl/busqueda?ft={quote(query)}"
    for page_num in range(1, max_pages + 1):
        url = f"{base}&page={page_num}"
        print(f"🔎 Santa Isabel → {url}")
        page.goto(url, timeout=60000)
        try:
            page.wait_for_selector("div.product-card-wrap[data-cnstrc-item-name]", state="attached", timeout=15000)
        except:
            print(f"  ⚠️ No se encontraron productos en página {page_num} de Santa Isabel.")
            break
        items = page.query_selector_all("div.product-card-wrap[data-cnstrc-item-name]")
        if not items:
            break
        for pdt in items:
            nombre = pdt.get_attribute("data-cnstrc-item-name")
            precio_attr = pdt.get_attribute("data-cnstrc-item-price")
            if precio_attr is None:
                price_el = pdt.query_selector("span.prices-main-price")
                precio = safe_int(price_el.inner_text() if price_el else None)
            else:
                precio = safe_int(precio_attr)
            brand_el = pdt.query_selector("p.product-card-brand")
            marca = brand_el.inner_text().strip() if brand_el else "N/A"
            resultados.append({"nombre": nombre, "marca": marca, "precio": precio})
    return resultados

def scrape_unimarc(page, query="vienesas", max_pages=1):
    resultados = []
    base = f"https://www.unimarc.cl/search?q={quote(query)}"
    for page_num in range(1, 2):
        url = f"{base}&page={page_num}"
        print(f"🔎 Unimarc → {url}")
        page.goto(url, timeout=60000)
        try:
            page.wait_for_selector("p.Shelf_nameProduct__CXI5M", state="attached", timeout=15000)
        except:
            print(f"  ⚠️ No se encontraron productos en página {page_num} de Unimarc.")
            break
        cards = page.query_selector_all("div.baseContainer_container__TSgMX")
        if not cards:
            break
        for c in cards:
            name_el = c.query_selector("p.Shelf_nameProduct__CXI5M")
            brand_el = c.query_selector("p.Shelf_brandText__sGfsS")
            price_el = c.query_selector("p[id^='listPrice__offerPrice']")
            nombre = name_el.inner_text().strip() if name_el else None
            marca  = brand_el.inner_text().strip() if brand_el else "N/A"
            precio = safe_int(price_el.inner_text() if price_el else None)
            if nombre:
                resultados.append({"nombre": nombre, "marca": marca, "precio": precio})
    return resultados

# =============== Comparación ===============

def comparar_tiendas(jumbo, sisa, unimarc):
    print("\n📊 Comparación de precios entre Jumbo, Santa Isabel y Unimarc:\n")
    for j in jumbo:
        comparables = []
        for s in sisa:
            if equivalentes(j, s):
                comparables.append(("Santa Isabel", s["precio"]))
        for u in unimarc:
            if equivalentes(j, u):
                comparables.append(("Unimarc", u["precio"]))
        if comparables:
            print(f"{j['nombre']} ({j['marca']})")
            print(f"  Jumbo: ${j['precio']}")
            for tienda, precio in comparables:
                print(f"  {tienda}: ${precio}")
            candidatos = [("Jumbo", j["precio"])] + comparables
            tienda_min, precio_min = min(candidatos, key=lambda t: t[1])
            print(f"  → Más barato: {tienda_min}\n")
        else:
            print(f"{j['nombre']} ({j['marca']}) solo en Jumbo: ${j['precio']}")

# =============== MAIN ===============

def main():
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = input("🔎 Ingrese producto a buscar (ej: vienesas, cerveza, pan): ").strip()
        if not query:
            query = "vienesas"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        print(f"\n🧭 Buscando: {query}\n")

        jumbo_raw   = scrape_jumbo(page, query=query, max_pages=3)
        sisa_raw    = scrape_santaisabel(page, query=query, max_pages=3)
        unimarc_raw = scrape_unimarc(page, query=query, max_pages=3)

        jumbo   = limpiar_productos(jumbo_raw)
        sisa    = limpiar_productos(sisa_raw)
        unimarc = limpiar_productos(unimarc_raw)

        print(f"\n🛒 Jumbo: {len(jumbo)} productos")
        print(f"🛒 Santa Isabel: {len(sisa)} productos")
        print(f"🛒 Unimarc: {len(unimarc)} productos")

        comparar_tiendas(jumbo, sisa, unimarc)

        browser.close()

if __name__ == "__main__":
    main()
