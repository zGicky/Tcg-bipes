import os
import sys

from PIL import Image, ImageDraw, ImageFont, ImageOps

from base import VENTAJA
from cartas import CARTAS

ANCHO, ALTO = 600, 840
CARPETA_ARTE = "arte"
CARPETA_SALIDA = "cartas_png"

COLOR_CAT = {
    "Atacante": "#c0392b", "Tanque": "#2e6da4", "Mago": "#7d3c98",
    "Partidario": "#1e8449", "Terreno": "#9c6b1e", "Equipable": "#ca6f1e",
}
COLOR_RAREZA = {
    "Común": "#b8bcc4", "Raro": "#22c55e", "Épico": "#a855f7",
    "Legendario": "#f5b301", "Mítico": "#e11d9a",
}


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mezclar(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def fuente(tam, negrita=False):
    nombres = (["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"] if negrita
               else ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf"])
    for n in nombres:
        try:
            return ImageFont.truetype(n, tam)
        except OSError:
            pass
    try:
        return ImageFont.load_default(tam)
    except TypeError:
        return ImageFont.load_default()


def envolver(draw, texto, fnt, ancho):
    lineas, actual = [], ""
    for palabra in texto.split():
        prueba = f"{actual} {palabra}".strip()
        if draw.textlength(prueba, font=fnt) <= ancho:
            actual = prueba
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


def texto_ajustado(draw, texto, caja, tam_max, tam_min, negrita=False, color=(255, 255, 255)):
    x1, y1, x2, y2 = caja
    for tam in range(tam_max, tam_min - 1, -1):
        fnt = fuente(tam, negrita)
        lineas = envolver(draw, texto, fnt, x2 - x1)
        alto_linea = int(tam * 1.25)
        if len(lineas) * alto_linea <= y2 - y1 or tam == tam_min:
            for i, linea in enumerate(lineas):
                draw.text((x1, y1 + i * alto_linea), linea, font=fnt, fill=color)
            return


def arte(d, tam):
    for ext in ("png", "jpg", "jpeg", "webp"):
        ruta = os.path.join(CARPETA_ARTE, f"{d['nombre']}.{ext}")
        if os.path.exists(ruta):
            return ImageOps.fit(Image.open(ruta).convert("RGBA"), tam)
    w, h = tam
    base = rgb(COLOR_CAT[d["categoria"]])
    img = Image.new("RGBA", tam)
    px = ImageDraw.Draw(img)
    for y in range(h):
        px.line([(0, y), (w, y)], fill=mezclar(mezclar(base, (0, 0, 0), 0.65), base, y / h))
    px.text((w // 2, h // 2), d["nombre"][0], font=fuente(min(w, h) // 2, True),
            fill=mezclar(base, (255, 255, 255), 0.3), anchor="mm")
    px.text((w // 2, h - 22), "ARTE AQUÍ", font=fuente(18, True),
            fill=mezclar(base, (255, 255, 255), 0.55), anchor="mm")
    return img


def pegar_redondeado(carta, img, caja, radio):
    x1, y1, x2, y2 = caja
    mascara = Image.new("L", img.size, 0)
    ImageDraw.Draw(mascara).rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], radio, fill=255)
    carta.paste(img, (x1, y1), mascara)


def dibujar_carta(d):
    cat = d["categoria"]
    combate = cat in VENTAJA
    color = rgb(COLOR_CAT[cat])
    borde = (14, 14, 20)
    full = d.get("full_art", False)
    alfa = 165 if full else 255
    oscuro = (24, 24, 34)

    carta = Image.new("RGBA", (ANCHO, ALTO), (0, 0, 0, 0))
    draw = ImageDraw.Draw(carta)
    draw.rounded_rectangle([0, 0, ANCHO - 1, ALTO - 1], 34, fill=borde)
    draw.rounded_rectangle([14, 14, ANCHO - 15, ALTO - 15], 24, fill=oscuro)

    if full:
        caja_arte = (14, 14, ANCHO - 14, ALTO - 14)
        radio_arte = 24
    else:
        caja_arte = (34, 122, ANCHO - 34, 440)
        radio_arte = 14
    tam = (caja_arte[2] - caja_arte[0], caja_arte[3] - caja_arte[1])
    pegar_redondeado(carta, arte(d, tam), caja_arte, radio_arte)

    capa = Image.new("RGBA", carta.size, (0, 0, 0, 0))
    cd = ImageDraw.Draw(capa)
    cd.rounded_rectangle([28, 28, ANCHO - 28, 110], 14, fill=color + (alfa,))
    cd.rounded_rectangle([28, 452, ANCHO - 28, 520], 14, fill=oscuro + (alfa,))
    cd.rounded_rectangle([28, 528, ANCHO - 28, 668], 14, fill=oscuro + (alfa,))
    caja_desc = (28, 724, ANCHO - 28, 812) if combate else (28, 676, ANCHO - 28, 812)
    if combate:
        cd.rounded_rectangle([28, 676, ANCHO - 28, 716], 14, fill=color + (alfa,))
    cd.rounded_rectangle(caja_desc, 14, fill=oscuro + (alfa,))
    carta = Image.alpha_composite(carta, capa)
    draw = ImageDraw.Draw(carta)

    ancho_nombre = ANCHO - 56 - 24 - (150 if combate else 0)
    texto_ajustado(draw, d["nombre"], (44, 34, 44 + ancho_nombre, 76), 36, 20, True)
    sub = f"{cat} · {d['rareza']}" if d.get("rareza") else cat
    if full:
        sub += " · Full art"
    draw.text((44, 80), sub, font=fuente(17), fill=(235, 235, 235))
    if combate:
        draw.text((ANCHO - 44, 70), f"PS {d['ps']}", font=fuente(36, True),
                  fill=(255, 255, 255), anchor="rm")

    x = 44
    if combate:
        for _ in range(d["costo"]):
            draw.ellipse([x, 472, x + 28, 500], fill=(255, 214, 10), outline=(20, 20, 20), width=2)
            x += 34
        x += 8
    ancho_hab = (ANCHO - 44 - 130 if combate else ANCHO - 44) - x
    texto_ajustado(draw, d["habilidad"], (x, 474, x + ancho_hab, 506), 28, 16, True)
    if combate:
        efecto = d["efecto"].lower()
        variable = any(k in efecto for k in ("daño más", "más de daño", "suma"))
        draw.text((ANCHO - 44, 486), f"{d['dano']}{'+' if variable else ''}",
                  font=fuente(40, True), fill=(255, 255, 255), anchor="rm")

    texto_ajustado(draw, d["efecto"], (44, 540, ANCHO - 44, 662), 23, 14)

    if combate:
        debil = next(k for k, v in VENTAJA.items() if v == cat)
        resiste = VENTAJA[cat]
        draw.text((40, 696), f"Debilidad: {debil} x2", font=fuente(16, True), fill=(255, 255, 255), anchor="lm")
        draw.text((262, 696), f"Resiste: {resiste} x0,5", font=fuente(16, True),
                  fill=(255, 255, 255), anchor="lm")
        draw.text((ANCHO - 40, 696), f"Ret. {d['retirada']}", font=fuente(16, True),
                  fill=(255, 255, 255), anchor="rm")

    x1, y1, x2, y2 = caja_desc
    texto_ajustado(draw, d["descripcion"], (x1 + 16, y1 + 8, x2 - 16, y2 - 6), 18, 11, color=(200, 200, 210))
    return carta.convert("RGB")


def hoja_contacto(imagenes, columnas=6, ancho=300):
    alto = int(ancho * ALTO / ANCHO)
    filas = -(-len(imagenes) // columnas)
    hoja = Image.new("RGB", (columnas * (ancho + 16) + 16, filas * (alto + 16) + 16), (40, 40, 48))
    for i, img in enumerate(imagenes):
        mini = img.resize((ancho, alto), Image.LANCZOS)
        hoja.paste(mini, (16 + (i % columnas) * (ancho + 16), 16 + (i // columnas) * (alto + 16)))
    return hoja


if __name__ == "__main__":
    if len(sys.argv) > 1:
        datos = next((d for d in CARTAS if d["nombre"].lower() == " ".join(sys.argv[1:]).lower()), None)
        if datos is None:
            sys.exit("No encontré esa carta. Nombres: " + ", ".join(d["nombre"] for d in CARTAS))
        dibujar_carta(datos).show()
    else:
        os.makedirs(CARPETA_SALIDA, exist_ok=True)
        imagenes = []
        for d in CARTAS:
            img = dibujar_carta(d)
            img.save(os.path.join(CARPETA_SALIDA, f"{d['nombre']}.png"))
            imagenes.append(img)
        hoja_contacto(imagenes).save("hoja_cartas.png")
        print(f"Listo: {len(imagenes)} cartas en '{CARPETA_SALIDA}/' y hoja_cartas.png")
