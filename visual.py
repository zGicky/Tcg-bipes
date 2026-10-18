"""Dibuja las cartas como PNG (600x840) con el diseño nuevo:
marco de rareza, fondo del color de la categoría, paneles oscuros, círculo de PS,
rombos de energía, barra de debilidad/resistencia/retirada y descripción.

Tipografías: si ponés archivos Barlow en la carpeta  fuentes/  se usan automáticamente
(BarlowCondensed-Bold.ttf, Barlow-Bold.ttf, Barlow-Medium.ttf, Barlow-Italic.ttf).
Si no están, prueba otras fuentes del sistema y al final usa DejaVu.
"""
import os
import sys
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from base import VENTAJA
from cartas import CARTAS

ANCHO, ALTO = 600, 840
S = 2
CARPETA_ARTE = "arte"
CARPETA_SALIDA = "cartas_png"
CARPETA_FUENTES = "fuentes"

COLOR_CAT = {
    "Atacante": "#d6453d", "Tanque": "#2f6fd1", "Mago": "#8b46c9",
    "Partidario": "#23a455", "Terreno": "#a8741f", "Equipable": "#d9781c",
}
RAREZA_GRAD = {
    "Común":      ((196, 198, 207), (128, 131, 144), (214, 216, 224)),
    "Raro":       ((132, 196, 255), (38, 108, 232), (118, 180, 255)),
    "Épico":      ((222, 176, 255), (138, 58, 230), (192, 132, 250)),
    "Legendario": ((214, 142, 8), (255, 232, 118), (240, 184, 28)),
    "Mítico":     ((255, 120, 150), (255, 77, 109), (255, 176, 96)),
    "":           ((214, 218, 228), (118, 123, 139), (204, 208, 218)),
}
PANEL = (10, 13, 26)

FUENTES = {
    "titulo":  ["BarlowCondensed-Bold.ttf", "BarlowCondensed-SemiBold.ttf", "Oswald-Bold.ttf", "bahnschrift.ttf",
                "ARIALNB.TTF", "DejaVuSansCondensed-Bold.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
    "negrita": ["Barlow-Bold.ttf", "Barlow-SemiBold.ttf", "arialbd.ttf", "Arial Bold.ttf",
                "DejaVuSansCondensed-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "normal":  ["Barlow-Medium.ttf", "Barlow-Regular.ttf", "arial.ttf", "Arial.ttf",
                "DejaVuSansCondensed.ttf", "DejaVuSans.ttf"],
    "cursiva": ["Barlow-Italic.ttf", "Barlow-MediumItalic.ttf", "ariali.ttf", "Arial Italic.ttf",
                "DejaVuSansCondensed-Oblique.ttf", "DejaVuSans-Oblique.ttf"],
}


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mezclar(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def k(v):
    """unidades de diseño (600x840) -> píxeles del lienzo grande"""
    return int(round(v * S))


@lru_cache(maxsize=None)
def fuente(tam, estilo):
    """tam en unidades de diseño"""
    for nombre in FUENTES[estilo]:
        for ruta in (os.path.join(CARPETA_FUENTES, nombre), nombre):
            try:
                return ImageFont.truetype(ruta, k(tam))
            except OSError:
                pass
    try:
        return ImageFont.load_default(k(tam))
    except TypeError:
        return ImageFont.load_default()


def envolver(draw, texto, fnt, ancho_px):
    lineas, actual = [], ""
    for palabra in texto.split():
        prueba = f"{actual} {palabra}".strip()
        if draw.textlength(prueba, font=fnt) <= ancho_px:
            actual = prueba
        else:
            if actual:
                lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


def texto_ajustado(draw, texto, caja, tam_max, tam_min, estilo="normal", color=(255, 255, 255), centrar_v=True):
    """Escribe el texto dentro de la caja (en unidades de diseño), achicando la letra hasta que entre."""
    x1, y1, x2, y2 = caja
    for tam in [t / 2 for t in range(int(tam_max * 2), int(tam_min * 2) - 1, -1)]:
        fnt = fuente(tam, estilo)
        lineas = envolver(draw, texto, fnt, k(x2 - x1))
        alto_linea = tam * 1.22
        if len(lineas) * alto_linea <= (y2 - y1) or tam == tam_min:
            total = len(lineas) * alto_linea
            y = y1 + ((y2 - y1 - total) / 2 if centrar_v else 0)
            for i, linea in enumerate(lineas):
                draw.text((k(x1), k(y + i * alto_linea)), linea, font=fnt, fill=color)
            return


def texto_espaciado(draw, x, y, txt, fnt, color, espacio, anclaje="l"):
    """Texto con separación entre letras. anclaje: 'l' izquierda, 'm' centro. y = centro vertical."""
    anchos = [draw.textlength(c, font=fnt) for c in txt]
    total = sum(anchos) + k(espacio) * (len(txt) - 1)
    cx = k(x) - (total / 2 if anclaje == "m" else 0)
    for c, a in zip(txt, anchos):
        draw.text((cx, k(y)), c, font=fnt, fill=color, anchor="lm")
        cx += a + k(espacio)


def degradado(tam, c1, c2, c3=None, diagonal=False):
    w, h = tam
    vert = Image.linear_gradient("L").resize((w, h))
    if diagonal:
        horiz = vert.rotate(90, expand=True).resize((w, h))
        vert = ImageChops.add(vert, horiz, scale=2)
    if c3 is None:
        return ImageOps.colorize(vert, black=c1, white=c2)
    return ImageOps.colorize(vert, black=c1, white=c3, mid=c2)


def mascara_redondeada(tam, radio):
    m = Image.new("L", tam, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, tam[0] - 1, tam[1] - 1], k(radio), fill=255)
    return m


def arte(d, tam):
    """Imagen de la carta: arte/<Nombre>.png|jpg|... si existe; si no, un marcador con la inicial."""
    for ext in ("png", "jpg", "jpeg", "webp"):
        ruta = os.path.join(CARPETA_ARTE, f"{d['nombre']}.{ext}")
        if os.path.exists(ruta):
            return ImageOps.fit(Image.open(ruta).convert("RGBA"), tam)
    w, h = tam
    base = rgb(COLOR_CAT[d["categoria"]])
    img = degradado(tam, mezclar(base, (255, 255, 255), .18), mezclar(base, (0, 0, 0), .55)).convert("RGBA")
    capa = Image.new("RGBA", tam, (0, 0, 0, 0))
    ImageDraw.Draw(capa).text((w // 2, h // 2), d["nombre"][0].upper(), font=fuente(min(w, h) / S * .42, "titulo"),
                              fill=mezclar(base, (255, 255, 255), .55) + (110,), anchor="mm")
    return Image.alpha_composite(img, capa)


def rombo(draw, cx, cy, r):
    draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)],
                 fill=(255, 214, 10), outline=(176, 128, 0), width=k(1.6))


def dibujar_carta(d):
    cat = d["categoria"]
    combate = cat in VENTAJA
    full = d.get("full_art", False)
    base = rgb(COLOR_CAT[cat])
    claro = mezclar(base, (255, 255, 255), .28)
    W, H = k(ANCHO), k(ALTO)

    carta = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    marco = degradado((W, H), *RAREZA_GRAD.get(d.get("rareza", ""), RAREZA_GRAD[""]), diagonal=True).convert("RGBA")
    carta.paste(marco, (0, 0), mascara_redondeada((W, H), 36))

    ix1, iy1, ix2, iy2 = 14, 14, ANCHO - 14, ALTO - 14
    tam_int = (k(ix2 - ix1), k(iy2 - iy1))
    if full:
        interior = arte(d, tam_int)
    else:
        interior = degradado(tam_int, mezclar(base, (255, 255, 255), .12), mezclar(base, (0, 0, 0), .5)).convert("RGBA")
    carta.paste(interior, (k(ix1), k(iy1)), mascara_redondeada(tam_int, 26))
    ImageDraw.Draw(carta).rounded_rectangle([k(ix1), k(iy1), k(ix2), k(iy2)], k(26), outline=PANEL + (200,), width=k(2.5))

    if not full:
        caja = (30, 192, ANCHO - 30, 400)
        tam = (k(caja[2] - caja[0]), k(caja[3] - caja[1]))
        carta.paste(arte(d, tam), (k(caja[0]), k(caja[1])), mascara_redondeada(tam, 16))
        ImageDraw.Draw(carta).rounded_rectangle([k(caja[0]), k(caja[1]), k(caja[2]), k(caja[3])], k(16),
                                                outline=PANEL + (150,), width=k(2))

    alfa = 205 if full else 235
    capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(capa)

    def panel(caja, radio=16):
        cd.rounded_rectangle([k(caja[0]), k(caja[1]), k(caja[2]), k(caja[3])], k(radio), fill=PANEL + (alfa,))

    panel((30, 30, ANCHO - 30, 180), 20)
    panel((30, 412, ANCHO - 30, 476), 16)
    panel((30, 486, ANCHO - 30, 636 if combate else 676), 16)
    if combate:
        panel((30, 646, ANCHO - 30, 678), 12)
    panel((30, 688, ANCHO - 30, 812), 14)
    carta = Image.alpha_composite(carta, capa)
    draw = ImageDraw.Draw(carta)

    ancho_nombre = (ANCHO - 60 - 36) - (170 if combate else 0)
    nombre = d["nombre"].upper()
    centro_y = 105
    colocado = False
    for tam in range(66, 33, -2):
        fnt = fuente(tam, "titulo")
        if draw.textlength(nombre, font=fnt) <= k(ancho_nombre):
            draw.text((k(48), k(centro_y + 4)), nombre, font=fnt, fill=(255, 255, 255), anchor="ls")
            y_cat = centro_y + 34
            colocado = True
            break
    if not colocado:
        for tam in range(44, 25, -2):
            fnt = fuente(tam, "titulo")
            lineas = envolver(draw, nombre, fnt, k(ancho_nombre))
            if len(lineas) <= 2 and len(lineas) * tam * 1.02 <= 84:
                y0 = centro_y - 22 - (len(lineas) - 1) * tam * .5
                for i, ln in enumerate(lineas):
                    draw.text((k(48), k(y0 + i * tam * 1.02 + 20)), ln, font=fnt, fill=(255, 255, 255), anchor="ls")
                y_cat = y0 + (len(lineas) - 1) * tam * 1.02 + 20 + 28
                break
    texto_espaciado(draw, 49, y_cat, cat.upper(), fuente(20, "negrita"), claro, 3)

    if combate:
        cx, cy, R = ANCHO - 30 - 18 - 70, 105, 68
        draw.ellipse([k(cx - R), k(cy - R), k(cx + R), k(cy + R)], fill=claro)
        draw.ellipse([k(cx - R + 9), k(cy - R + 9), k(cx + R - 9), k(cy + R - 9)], fill=(8, 10, 20))
        texto_espaciado(draw, cx, cy - 26, "PS", fuente(25, "normal"), (150, 156, 174), 3, "m")
        draw.text((k(cx), k(cy + 18)), str(d["ps"]), font=fuente(48, "titulo"), fill=(255, 255, 255), anchor="mm")

    x = 52
    if combate:
        for _ in range(d["costo"]):
            rombo(draw, k(x + 12), k(444), k(12))
            x += 31
        x += 8
        efecto_l = d["efecto"].lower()
        variable = any(t in efecto_l for t in ("daño más", "más de daño", "suma"))
        dano = f"{d['dano']}{'+' if variable else ''}"
        fd = fuente(52, "titulo")
        draw.text((k(ANCHO - 50), k(446)), dano, font=fd, fill=(255, 255, 255), anchor="rm")
        ancho_hab = (ANCHO - 50 - draw.textlength(dano, font=fd) / S - 16) - x
    else:
        ancho_hab = ANCHO - 50 - x
    texto_ajustado(draw, d["habilidad"], (x, 418, x + ancho_hab, 470), 32, 16, "negrita")

    texto_ajustado(draw, d["efecto"], (50, 496, ANCHO - 50, 626 if combate else 666), 26, 13, "normal")

    if combate:
        debil = next(c for c, v in VENTAJA.items() if v == cat)
        resiste = VENTAJA[cat]
        gris = (206, 211, 224)
        t_izq, t_cen, t_der = f"DÉBIL. {debil.upper()} ×2", f"RESISTE {resiste.upper()} ×0,5", f"RET. {d['retirada']}"
        for tam in (17, 16, 15, 14, 13):
            fb = fuente(tam, "negrita")
            w_i, w_c, w_d = (draw.textlength(t, font=fb) / S for t in (t_izq, t_cen, t_der))
            if w_i + w_c + w_d + 2 * 18 <= ANCHO - 92:
                break
        x_izq_fin, x_der_ini = 46 + w_i, ANCHO - 46 - w_d
        draw.text((k(46), k(662)), t_izq, font=fb, fill=gris, anchor="lm")
        draw.text((k((x_izq_fin + x_der_ini) / 2), k(662)), t_cen, font=fb, fill=gris, anchor="mm")
        draw.text((k(ANCHO - 46), k(662)), t_der, font=fb, fill=gris, anchor="rm")

    texto_ajustado(draw, d["descripcion"], (48, 696, ANCHO - 48, 804), 20, 11, "cursiva", (196, 202, 216))

    return carta.resize((ANCHO, ALTO), Image.LANCZOS)


def hoja_contacto(imagenes, columnas=6, ancho=300):
    alto = int(ancho * ALTO / ANCHO)
    filas = -(-len(imagenes) // columnas)
    hoja = Image.new("RGB", (columnas * (ancho + 16) + 16, filas * (alto + 16) + 16), (40, 40, 48))
    for i, img in enumerate(imagenes):
        mini = img.resize((ancho, alto), Image.LANCZOS)
        hoja.paste(mini, (16 + (i % columnas) * (ancho + 16), 16 + (i // columnas) * (alto + 16)), mini)
    return hoja


if __name__ == "__main__":
    if len(sys.argv) > 1:
        datos = next((c for c in CARTAS if c["nombre"].lower() == " ".join(sys.argv[1:]).lower()), None)
        if datos is None:
            sys.exit("No encontré esa carta. Nombres: " + ", ".join(c["nombre"] for c in CARTAS))
        dibujar_carta(datos).show()
    else:
        os.makedirs(CARPETA_SALIDA, exist_ok=True)
        imagenes = []
        for c in CARTAS:
            img = dibujar_carta(c)
            img.save(os.path.join(CARPETA_SALIDA, f"{c['nombre']}.png"))
            imagenes.append(img)
        hoja_contacto(imagenes).save("hoja_cartas.png")
        print(f"Listo: {len(imagenes)} cartas en '{CARPETA_SALIDA}/' y hoja_cartas.png")
