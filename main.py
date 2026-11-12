"""
Comparador de precios de supermercados (Jumbo, Santa Isabel, Unimarc) con Playwright.

Uso:
  python main.py "vienesas"                       # navegador visible (lo normal en la demo)
  python main.py "coca cola" --headless           # sin ver el navegador
  python main.py "pan" --offline                  # sin internet: usa la ultima busqueda guardada
  python main.py "vienesas" --paginas 2 --tiendas jumbo,unimarc
  python main.py "vienesas" --lento               # el navegador va mas lento para que el publico lo vea
  python main.py "vienesas" --post                # ademas guarda la "oferta del dia" para el bot

Scraping responsable (para conversar en la charla):
  - robots.txt es un archivo que cada sitio publica para decir que partes pueden recorrer los bots.
  - No hay que saturar un sitio: por eso hay pausas entre paginas.
  - Los selectores dependen del HTML de la pagina: si la tienda cambia su diseno, el scraper se rompe.
    Por eso cada dato tiene varios selectores de respaldo, y hay un modo offline con cache.
"""

import argparse
import csv
import html
import json
import os
import random
import re
import time
import unicodedata
import webbrowser
from datetime import datetime

try:
    from rich.console import Console
    from rich.table import Table
    consola = Console()
except ImportError:  # si no esta rich, se imprime texto plano
    consola = None

CARPETA_CACHE = "cache"
CARPETA_REPORTES = "reportes"
CARPETA_SALIDAS = "salidas"

# =========================================================
# Configuracion de cada tienda
# Para agregar una tienda nueva basta con agregar una entrada aqui.
# Cada campo tiene una lista de selectores: se prueban en orden hasta que uno funcione.
# =========================================================
TIENDAS = {
    "jumbo": {
        "nombre": "Jumbo",
        "url": "https://www.jumbo.cl/busqueda?ft={q}&page={p}",
        "tarjeta": ["div[data-cnstrc-item-name]"],
        "nombre_attr": "data-cnstrc-item-name",
        "precio_attr": "data-cnstrc-item-price",
        "nombre_sel": [],
        "marca_sel": ["p.text-sm.text-gray-500", "p.product-card-brand", "[class*='brand']"],
        "precio_sel": ["span.prices-main-price", "[class*='price']"],
    },
    "santaisabel": {
        "nombre": "Santa Isabel",
        "url": "https://www.santaisabel.cl/busqueda?ft={q}&page={p}",
        "tarjeta": ["div.product-card-wrap[data-cnstrc-item-name]", "div[data-cnstrc-item-name]"],
        "nombre_attr": "data-cnstrc-item-name",
        "precio_attr": "data-cnstrc-item-price",
        "nombre_sel": [],
        "marca_sel": ["p.product-card-brand", "p.text-sm.text-gray-500", "[class*='brand']"],
        "precio_sel": ["span.prices-main-price", "[class*='price']"],
    },
    "unimarc": {
        "nombre": "Unimarc",
        "url": "https://www.unimarc.cl/search?q={q}&page={p}",
        # Unimarc usa clases con un codigo al final (Shelf_nameProduct__CXI5M) que cambia
        # cada vez que actualizan el sitio. [class*='...'] busca solo el comienzo del nombre.
        "tarjeta": ["div[class*='baseContainer_container']"],
        "nombre_attr": None,
        "precio_attr": None,
        "nombre_sel": ["p[class*='Shelf_nameProduct']", "[class*='nameProduct']"],
        "marca_sel": ["p[class*='Shelf_brandText']", "[class*='brandText']"],
        "precio_sel": ["p[id^='listPrice__offerPrice']", "[class*='offerPrice']", "[class*='price']"],
    },
}

# =========================================================
# Utilidades de texto y numeros
# =========================================================


def sin_tildes(texto):
    texto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in texto if unicodedata.category(c) != "Mn").lower().strip()


def a_entero(valor):
    """'$1.990' -> 1990. Devuelve 0 si no se puede."""
    if valor is None:
        return 0
    if isinstance(valor, (int, float)):
        return int(valor)
    digitos = re.sub(r"[^\d]", "", str(valor).split(",")[0])
    return int(digitos) if digitos else 0


def numero(texto):
    return float(texto.replace(",", "."))


def normalizar_producto(nombre):
    """Saca del nombre las unidades, el peso (en g) y el volumen (en ml).
    Ej: 'Vienesas 20 un 1 kg' -> {'unidades': 20, 'peso': 1000, 'volumen': None}"""
    n = sin_tildes(nombre)
    unidades = peso = volumen = None

    m = re.search(r"(\d+)\s*(?:unidades|unidad|unid\.?|un\.?|u)\b", n)
    if m:
        unidades = int(m.group(1))
    else:  # packs: "6 x 1 lt" o "350 cc x 6"
        m = (re.search(r"\b(\d+)\s*x\s*\d", n)
             or re.search(r"\bx\s*(\d+)\b(?!\s*(?:kg|g|gr|grs|ml|cc|l|lt|lts)\b)", n))
        if m:
            unidades = int(m.group(1))

    m_kg = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:kg|kilo|kilos)\b", n)
    m_g = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:g|gr|grs|gramos)\b", n)
    if m_kg:
        peso = round(numero(m_kg.group(1)) * 1000)
    elif m_g:
        peso = round(numero(m_g.group(1)))

    m_l = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:l|lt|lts|litro|litros)\b", n)
    m_ml = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:ml|cc)\b", n)
    if m_l:
        volumen = round(numero(m_l.group(1)) * 1000)
    elif m_ml:
        volumen = round(numero(m_ml.group(1)))

    return {"unidades": unidades, "peso": peso, "volumen": volumen}


def formato_texto(f):
    partes = []
    if f["unidades"]:
        partes.append(f"{f['unidades']} un")
    if f["peso"]:
        partes.append(f"{f['peso'] / 1000:g} kg" if f["peso"]
                      >= 1000 else f"{f['peso']} g")
    if f["volumen"]:
        partes.append(f"{f['volumen'] / 1000:g} L" if f["volumen"]
                      >= 1000 else f"{f['volumen']} ml")
    return " ".join(partes) or "-"


def precio_unitario(precio, f):
    """Precio por kilo, por litro o por unidad. El paquete mas barato no siempre conviene."""
    if f["peso"]:
        return round(precio * 1000 / f["peso"]), "kg"
    if f["volumen"]:
        return round(precio * 1000 / f["volumen"]), "L"
    if f["unidades"]:
        return round(precio / f["unidades"]), "un"
    return None, None


def pesos(valor):
    return f"${valor:,.0f}".replace(",", ".")


def slug(texto):
    return re.sub(r"[^a-z0-9]+", "_", sin_tildes(texto)).strip("_") or "busqueda"


def mostrar(msg):
    if consola:
        consola.print(msg)
    else:
        print(re.sub(r"\[/?[a-z ]+\]", "", msg))

# =========================================================
# Scraping
# =========================================================


def primero(elemento, selectores):
    """Prueba los selectores en orden y devuelve el texto del primero que exista."""
    for sel in selectores:
        hallado = elemento.query_selector(sel)
        if hallado:
            texto = hallado.inner_text().strip()
            if texto:
                return texto
    return None


def scrape_tienda(page, clave, query, paginas, lento=False):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from urllib.parse import quote

    cfg = TIENDAS[clave]
    resultados = []
    for p in range(1, paginas + 1):
        url = cfg["url"].format(q=quote(query), p=p)
        mostrar(f"🔎 [bold]{cfg['nombre']}[/bold] → pagina {p}")
        page.goto(url, timeout=60000)

        tarjetas = []
        for sel in cfg["tarjeta"]:
            try:
                page.wait_for_selector(sel, state="attached", timeout=15000)
                tarjetas = page.query_selector_all(sel)
                if tarjetas:
                    break
            except PlaywrightTimeout:
                continue
        if not tarjetas:
            mostrar(f"   [yellow]⚠️  Sin productos en la pagina {p}[/yellow]")
            break

        if lento:  # scroll para que el publico vea los productos
            page.mouse.wheel(0, 1200)
            time.sleep(1)

        for t in tarjetas:
            nombre = t.get_attribute(
                cfg["nombre_attr"]) if cfg["nombre_attr"] else None
            nombre = nombre or primero(t, cfg["nombre_sel"])
            precio = a_entero(t.get_attribute(
                cfg["precio_attr"])) if cfg["precio_attr"] else 0
            precio = precio or a_entero(primero(t, cfg["precio_sel"]))
            marca = primero(t, cfg["marca_sel"]) or "N/A"
            if nombre and precio > 0:
                resultados.append(
                    {"nombre": nombre.strip(), "marca": marca, "precio": precio})

        mostrar(f"   ✅ {len(tarjetas)} productos leidos")
        time.sleep(random.uniform(1, 2.5))  # pausa para no saturar el sitio
    return resultados


def buscar_online(query, tiendas, paginas, headless, lento):
    from playwright.sync_api import sync_playwright

    cache = leer_cache(query)
    datos, origen = {}, {}
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(
            headless=headless, slow_mo=250 if lento else 0)
        page = navegador.new_page()
        for clave in tiendas:
            try:
                productos = scrape_tienda(page, clave, query, paginas, lento)
            except Exception as e:  # una tienda caida no debe botar la demo
                mostrar(
                    f"   [red]❌ {TIENDAS[clave]['nombre']} fallo: {type(e).__name__}[/red]")
                productos = []
            if productos:
                datos[clave], origen[clave] = productos, "en vivo"
            elif cache and cache["tiendas"].get(clave):
                datos[clave], origen[clave] = cache["tiendas"][
                    clave], f"cache {cache['fecha']}"
                mostrar(
                    f"   [yellow]↩️  Usando datos guardados de {TIENDAS[clave]['nombre']} ({cache['fecha']})[/yellow]")
            else:
                datos[clave], origen[clave] = [], "sin datos"
        navegador.close()

    en_vivo = {c: datos[c] for c in datos if origen[c] == "en vivo"}
    if en_vivo:
        guardar_cache(query, en_vivo, cache)
    return datos, origen

# =========================================================
# Cache (modo offline)
# =========================================================


def ruta_cache(query):
    return os.path.join(CARPETA_CACHE, f"{slug(query)}.json")


def leer_cache(query):
    try:
        with open(ruta_cache(query), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def guardar_cache(query, datos, anterior=None):
    os.makedirs(CARPETA_CACHE, exist_ok=True)
    tiendas = dict(anterior["tiendas"]) if anterior else {}
    tiendas.update(datos)
    contenido = {"query": query, "fecha": datetime.now().strftime(
        "%d-%m-%Y %H:%M"), "tiendas": tiendas}
    with open(ruta_cache(query), "w", encoding="utf-8") as f:
        json.dump(contenido, f, ensure_ascii=False, indent=2)

# =========================================================
# Comparacion
# =========================================================


def comparar(datos):
    """Agrupa los productos de TODAS las tiendas por marca + formato.
    Devuelve una lista de filas ordenada por el ahorro (mayor primero)."""
    grupos = {}
    for clave, productos in datos.items():
        for p in productos:
            f = normalizar_producto(p["nombre"])
            if not (f["unidades"] or f["peso"] or f["volumen"]):
                continue  # sin formato no se puede comparar justo
            k = (sin_tildes(p["marca"]), f["unidades"],
                 f["peso"], f["volumen"])
            grupo = grupos.setdefault(
                k, {"nombre": p["nombre"], "marca": p["marca"], "formato": f, "precios": {}})
            # si una tienda tiene el mismo producto repetido, nos quedamos con el mas barato
            if clave not in grupo["precios"] or p["precio"] < grupo["precios"][clave]:
                grupo["precios"][clave] = p["precio"]

    filas = []
    for g in grupos.values():
        if len(g["precios"]) < 2:
            continue
        ganador = min(g["precios"], key=g["precios"].get)
        minimo, maximo = g["precios"][ganador], max(g["precios"].values())
        unitario, unidad = precio_unitario(minimo, g["formato"])
        filas.append({**g, "ganador": ganador, "minimo": minimo, "ahorro": maximo - minimo,
                      "unitario": unitario, "unidad": unidad})
    filas.sort(key=lambda f: f["ahorro"], reverse=True)
    return filas


def victorias(filas, tiendas):
    conteo = {t: 0 for t in tiendas}
    for f in filas:
        conteo[f["ganador"]] = conteo.get(f["ganador"], 0) + 1
    return conteo

# =========================================================
# Salidas: terminal, HTML, CSV y oferta del dia
# =========================================================


def imprimir_tabla(filas, tiendas, query):
    if not filas:
        mostrar(
            "\n[yellow]No se encontraron productos iguales en mas de una tienda.[/yellow]")
        return
    if not consola:
        for f in filas:
            print(
                f"\n{f['nombre']} ({f['marca']}) {formato_texto(f['formato'])}")
            for t in tiendas:
                print(
                    f"  {TIENDAS[t]['nombre']}: {pesos(f['precios'][t]) if t in f['precios'] else '-'}")
            print(
                f"  → Mas barato: {TIENDAS[f['ganador']]['nombre']} (ahorras {pesos(f['ahorro'])})")
        return
    tabla = Table(
        title=f"🛒 Comparacion: {query}", show_lines=True, header_style="bold cyan")
    tabla.add_column("Producto", max_width=40)
    tabla.add_column("Marca")
    tabla.add_column("Formato")
    for t in tiendas:
        tabla.add_column(TIENDAS[t]["nombre"], justify="right")
    tabla.add_column("Ahorro", justify="right", style="bold magenta")
    for f in filas:
        celdas = []
        for t in tiendas:
            if t not in f["precios"]:
                celdas.append("[dim]—[/dim]")
            elif t == f["ganador"]:
                celdas.append(
                    f"[bold green]{pesos(f['precios'][t])}[/bold green]")
            else:
                celdas.append(pesos(f["precios"][t]))
        tabla.add_row(f["nombre"], f["marca"], formato_texto(
            f["formato"]), *celdas, pesos(f["ahorro"]))
    consola.print(tabla)


def imprimir_resumen(filas, tiendas, datos, origen):
    mostrar("\n[bold]📦 Productos por tienda[/bold]")
    for t in tiendas:
        mostrar(
            f"   {TIENDAS[t]['nombre']}: {len(datos.get(t, []))}  [dim]({origen.get(t, '-')})[/dim]")
    if filas:
        conteo = victorias(filas, tiendas)
        mostrar("\n[bold]🏆 Veces que fue la mas barata[/bold]")
        for t, v in sorted(conteo.items(), key=lambda x: -x[1]):
            mostrar(f"   {TIENDAS[t]['nombre']}: {'█' * v} {v}")
        top = filas[0]
        mostrar(f"\n💰 [bold]Mayor ahorro:[/bold] {top['nombre']} — comprando en "
                f"[green]{TIENDAS[top['ganador']]['nombre']}[/green] ahorras [bold magenta]{pesos(top['ahorro'])}[/bold magenta]")


def generar_html(filas, tiendas, query, origen):
    os.makedirs(CARPETA_REPORTES, exist_ok=True)
    fecha = datetime.now()
    ruta = os.path.join(
        CARPETA_REPORTES, f"{slug(query)}_{fecha:%Y%m%d_%H%M}.html")
    conteo = victorias(filas, tiendas)
    maximo = max(conteo.values()) if conteo and max(conteo.values()) > 0 else 1
    e = html.escape

    barras = "".join(
        f'<div class="barra"><span>{e(TIENDAS[t]["nombre"])}</span>'
        f'<div class="fondo"><div class="lleno" style="width:{100 * v / maximo:.0f}%">{v}</div></div></div>'
        for t, v in sorted(conteo.items(), key=lambda x: -x[1]))

    tarjetas = []
    for f in filas:
        precios = "".join(
            f'<li class="{"gana" if t == f["ganador"] else ""}"><span>{e(TIENDAS[t]["nombre"])}</span>'
            f'<b>{pesos(f["precios"][t]) if t in f["precios"] else "—"}</b></li>'
            for t in tiendas)
        unit = f'<p class="unit">{pesos(f["unitario"])} por {f["unidad"]}</p>' if f["unitario"] else ""
        tarjetas.append(
            f'<article><h3>{e(f["nombre"])}</h3><p class="meta">{e(f["marca"])} · {formato_texto(f["formato"])}</p>'
            f'<ul>{precios}</ul>{unit}<p class="ahorro">Ahorras {pesos(f["ahorro"])} en {e(TIENDAS[f["ganador"]]["nombre"])}</p></article>')

    fuentes = " · ".join(
        f"{TIENDAS[t]['nombre']}: {e(origen.get(t, '-'))}" for t in tiendas)
    contenido = f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<title>Comparacion: {e(query)}</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:system-ui,Segoe UI,sans-serif;margin:0;background:#0f172a;color:#e2e8f0;font-size:20px}}
header{{padding:32px 48px;background:linear-gradient(90deg,#2563eb,#7c3aed)}}
h1{{margin:0;font-size:44px}} header p{{margin:6px 0 0;opacity:.85}}
main{{padding:32px 48px}} h2{{font-size:30px}}
.barra{{display:flex;align-items:center;gap:16px;margin:10px 0}} .barra span{{width:160px}}
.fondo{{flex:1;background:#1e293b;border-radius:8px}} .lleno{{background:#22c55e;color:#052e16;font-weight:700;padding:8px 12px;border-radius:8px;min-width:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:20px}}
article{{background:#1e293b;border-radius:16px;padding:20px}} h3{{margin:0 0 4px;font-size:22px}}
.meta{{color:#94a3b8;margin:0 0 12px}} ul{{list-style:none;padding:0;margin:0}}
li{{display:flex;justify-content:space-between;padding:8px 12px;border-radius:8px}}
li.gana{{background:#14532d;color:#bbf7d0;font-weight:700}} .unit{{color:#94a3b8;font-size:17px}}
.ahorro{{margin:12px 0 0;font-weight:700;color:#f0abfc}} footer{{padding:24px 48px;color:#64748b;font-size:15px}}
</style></head><body>
<header><h1>🛒 {e(query)}</h1><p>{len(filas)} productos comparados · {fecha:%d-%m-%Y %H:%M}</p></header>
<main><h2>🏆 Veces que fue la mas barata</h2>{barras or "<p>Sin datos</p>"}
<h2>Productos (ordenados por ahorro)</h2><div class="grid">{"".join(tarjetas) or "<p>No hubo productos iguales en mas de una tienda.</p>"}</div></main>
<footer>Fuente de los datos — {fuentes}</footer></body></html>"""
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    return ruta


def generar_csv(filas, tiendas, query):
    os.makedirs(CARPETA_REPORTES, exist_ok=True)
    ruta = os.path.join(
        CARPETA_REPORTES, f"{slug(query)}_{datetime.now():%Y%m%d_%H%M}.csv")
    # utf-8-sig: Excel lee bien las tildes
    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Producto", "Marca", "Formato"] + [TIENDAS[t]["nombre"] for t in tiendas]
                   + ["Mas barato", "Ahorro", "Precio unitario"])
        for fi in filas:
            w.writerow([fi["nombre"], fi["marca"], formato_texto(fi["formato"])]
                       + [fi["precios"].get(t, "") for t in tiendas]
                       + [TIENDAS[fi["ganador"]]["nombre"], fi["ahorro"],
                          f"{fi['unitario']} por {fi['unidad']}" if fi["unitario"] else ""])
    return ruta


def guardar_oferta(filas, query):
    """Para la demo 3: el bot lee este archivo y publica la 'oferta del dia'."""
    if not filas:
        return None
    top = filas[0]
    os.makedirs(CARPETA_SALIDAS, exist_ok=True)
    ruta = os.path.join(CARPETA_SALIDAS, "oferta_del_dia.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({
            "busqueda": query, "producto": top["nombre"], "marca": top["marca"],
            "formato": formato_texto(top["formato"]), "tienda": TIENDAS[top["ganador"]]["nombre"],
            "precio": top["minimo"], "precio_mas_caro": top["minimo"] + top["ahorro"], "ahorro": top["ahorro"],
            "precios": {TIENDAS[t]["nombre"]: p for t, p in top["precios"].items()},
        }, f, ensure_ascii=False, indent=2)
    return ruta

# =========================================================
# MAIN
# =========================================================


def main():
    ap = argparse.ArgumentParser(
        description="Compara precios entre supermercados")
    ap.add_argument("busqueda", nargs="*",
                    help="producto a buscar (ej: vienesas)")
    ap.add_argument("--paginas", type=int, default=2,
                    help="paginas por tienda (por defecto 2)")
    ap.add_argument("--tiendas", default=",".join(TIENDAS),
                    help="ej: jumbo,unimarc")
    ap.add_argument("--headless", action="store_true",
                    help="no mostrar el navegador")
    ap.add_argument("--offline", action="store_true",
                    help="usar la ultima busqueda guardada")
    ap.add_argument("--lento", action="store_true",
                    help="navegacion lenta para la demo")
    ap.add_argument("--post", action="store_true",
                    help="guardar la oferta del dia para el bot")
    ap.add_argument("--no-abrir", action="store_true",
                    help="no abrir el reporte HTML al final")
    args = ap.parse_args()

    query = " ".join(args.busqueda).strip()
    if not query:
        query = input(
            "🔎 Producto a buscar (ej: vienesas, cerveza, pan): ").strip() or "vienesas"
    tiendas = [t.strip()
               for t in args.tiendas.split(",") if t.strip() in TIENDAS]
    if not tiendas:
        raise SystemExit(f"Tiendas validas: {', '.join(TIENDAS)}")

    mostrar(
        f"\n🧭 Buscando [bold]{query}[/bold] en {', '.join(TIENDAS[t]['nombre'] for t in tiendas)}\n")

    if args.offline:
        cache = leer_cache(query)
        if not cache:
            raise SystemExit(
                f"No hay datos guardados para '{query}'. Corre una vez con internet: python main.py \"{query}\"")
        mostrar(
            f"[yellow]📂 Modo offline: datos guardados el {cache['fecha']}[/yellow]")
        datos = {t: cache["tiendas"].get(t, []) for t in tiendas}
        origen = {t: f"cache {cache['fecha']}" if datos[t]
                  else "sin datos" for t in tiendas}
    else:
        datos, origen = buscar_online(
            query, tiendas, args.paginas, args.headless, args.lento)

    filas = comparar(datos)
    imprimir_tabla(filas, tiendas, query)
    imprimir_resumen(filas, tiendas, datos, origen)

    ruta_html = generar_html(filas, tiendas, query, origen)
    ruta_csv = generar_csv(filas, tiendas, query)
    mostrar(f"\n📄 Reporte: {ruta_html}\n📊 CSV: {ruta_csv}")
    if args.post:
        ruta = guardar_oferta(filas, query)
        mostrar(
            f"🤖 Oferta del dia para el bot: {ruta}" if ruta else "🤖 No hubo oferta para guardar")
    if not args.no_abrir:
        webbrowser.open("file://" + os.path.abspath(ruta_html))


if __name__ == "__main__":
    main()
