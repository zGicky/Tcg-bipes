import io
import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from base import Juego, PUNTOS_PARA_GANAR
from cartas import CARTAS

EN_NUBE = "PORT" in os.environ
PUERTO = int(os.environ.get("PORT", 8765))
HOST = os.environ.get("HOST", "0.0.0.0" if EN_NUBE else "127.0.0.1")
AQUI = os.path.dirname(os.path.abspath(__file__))

NOMBRES = ["Jugador 1", "Jugador 2"]
MAX_SALAS = 15
INACTIVIDAD = 30 * 60
DESCONECTADO = 20


def nombrar(texto, asiento):
    return texto.replace(NOMBRES[asiento], "Vos").replace(NOMBRES[1 - asiento], "Rival")


def snap_vacio():
    j = dict(nombre="Vos", puntos=0, mazo=0, mano_n=0, energia_libre=0, activa=None, banca=[], mano=[])
    return dict(yo=dict(j), rival=dict(j, nombre="Rival"), terreno=None,
                puntos_meta=PUNTOS_PARA_GANAR, terminado=False, ganador=None)


def vista(snap, asiento):
    if snap is None:
        return snap_vacio()
    s = dict(snap)
    s["yo"] = dict(snap["yo"], nombre="Vos")
    s["rival"] = dict(snap["rival"], nombre="Rival")
    if s.get("ganador"):
        s["ganador"] = "Vos" if s["ganador"] == NOMBRES[asiento] else "Rival"
    return s


class Sala:
    def __init__(self, nombre):
        self.nombre = nombre
        self.sids = [None, None]           # cookie de cada asiento
        self.vistos = [0.0, 0.0]           # última vez que cada persona consultó
        self.snaps = [None, None]          # lo que ve cada persona
        self.log = []
        self.preguntas = {}                # asiento -> pregunta pendiente
        self.faltan = set()                # asientos que todavía no respondieron
        self.respuestas = {}
        self.n = 0
        self.evento = threading.Event()
        self.lock = threading.Lock()
        self.cerrada = False
        self.hilo = None
        self.visto = time.time()

    @property
    def completa(self):
        return all(self.sids)

    def cerrar(self):
        self.cerrada = True
        self.evento.set()

    def actualizar(self, snaps, pausa=0):
        if self.cerrada:
            raise SystemExit
        for i, nombre in enumerate(NOMBRES):
            if nombre in snaps:
                self.snaps[i] = snaps[nombre]
        if pausa:
            time.sleep(pausa)

    def elegir(self, etiquetas, titulo, jug=None, asientos=None):
        if asientos is None:
            asientos = [NOMBRES.index(jug.nombre)]
        with self.lock:
            self.n += 1
            self.preguntas = {a: dict(id=self.n, titulo=titulo, opciones=list(etiquetas)) for a in asientos}
            self.faltan = set(asientos)
            self.respuestas = {}
            self.evento.clear()
        self.evento.wait()
        if self.cerrada:
            raise SystemExit
        return self.respuestas[asientos[0]]

    def responder(self, asiento, id_, idx):
        with self.lock:
            p = self.preguntas.get(asiento)
            if (asiento not in self.faltan or not p or p["id"] != id_ or isinstance(idx, bool)
                    or not isinstance(idx, int) or not 0 <= idx < len(p["opciones"])):
                return False
            self.respuestas[asiento] = idx
            self.faltan.discard(asiento)
            if not self.faltan:
                self.evento.set()
            return True

    def estado(self, asiento):
        lineas = [nombrar(l, asiento) for l in self.log[-80:]]
        if not self.completa:
            lineas.append(f"⏳ Esperando al otro jugador… pasale este link (sala «{self.nombre}»).")
            return dict(snap=snap_vacio(), pregunta=None, log=lineas)
        with self.lock:
            p = self.preguntas.get(asiento) if asiento in self.faltan else None
            pregunta = dict(p, titulo=nombrar(p["titulo"], asiento)) if p else None
        snap = vista(self.snaps[asiento], asiento)
        if pregunta is None and not snap["terminado"]:
            if time.time() - self.vistos[1 - asiento] > DESCONECTADO:
                lineas.append("⚠️ El otro jugador parece desconectado.")
            else:
                lineas.append("⏳ Esperando al rival…")
        return dict(snap=snap, pregunta=pregunta, log=lineas)


class Capturador:
    def __init__(self, sala):
        self.sala, self.buf = sala, ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            linea, self.buf = self.buf.split("\n", 1)
            if linea.strip():
                self.sala.log.append(linea)
        del self.sala.log[:-300]
        return len(s)

    def flush(self):
        pass


class RouterStdout:
    def __init__(self, original):
        self.original = original
        self.local = threading.local()

    def write(self, s):
        cap = getattr(self.local, "cap", None)
        return cap.write(s) if cap else self.original.write(s)

    def flush(self):
        self.original.flush()


ROUTER = None
SALAS = {}
JUGADORES = {}            # cookie -> (sala, asiento)
REGISTRO = threading.Lock()


def limpiar_nombre(s):
    return re.sub(r"[^a-z0-9_-]", "", s.lower())[:24] or "principal"


def unirse(sid, nombre_sala):
    with REGISTRO:
        e = JUGADORES.get(sid)
        if e and e[0].nombre == nombre_sala and not e[0].cerrada:
            return sid, e[0], e[1]                       # vuelve a su lugar
        sala = SALAS.get(nombre_sala)
        if sala is None:
            if len(SALAS) >= MAX_SALAS:
                vieja = min(SALAS.values(), key=lambda x: x.visto)
                vieja.cerrar()
                SALAS.pop(vieja.nombre, None)
            sala = SALAS[nombre_sala] = Sala(nombre_sala)
        libres = [i for i, c in enumerate(sala.sids) if c is None]
        if not libres:
            return None
        nuevo = secrets.token_urlsafe(16)
        asiento = libres[0]
        sala.sids[asiento] = nuevo
        JUGADORES[nuevo] = (sala, asiento)
        sala.visto = sala.vistos[asiento] = time.time()
        if sala.completa and sala.hilo is None:
            sala.hilo = threading.Thread(target=correr, args=(sala,), daemon=True)
            sala.hilo.start()
        return nuevo, sala, asiento


def buscar(sid):
    with REGISTRO:
        e = JUGADORES.get(sid)
    if not e or e[0].cerrada:
        return None
    sala, asiento = e
    sala.visto = sala.vistos[asiento] = time.time()
    return sala, asiento


def limpiador():
    while True:
        time.sleep(60)
        ahora = time.time()
        with REGISTRO:
            for nombre in [n for n, s in SALAS.items() if ahora - s.visto > INACTIVIDAD]:
                sala = SALAS.pop(nombre)
                sala.cerrar()
                for c in sala.sids:
                    JUGADORES.pop(c, None)


_cache = {}


def png_de(nombre):
    if nombre not in _cache:
        datos = next((d for d in CARTAS if d["nombre"] == nombre), None)
        if datos is None:
            return None
        from visual import dibujar_carta
        buf = io.BytesIO()
        dibujar_carta(datos).save(buf, "PNG")
        _cache[nombre] = buf.getvalue()
    return _cache[nombre]


PAGINA_LLENA = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<body style="font-family:sans-serif;background:#16161e;color:#eee;padding:2rem;line-height:1.5">
<h2>La sala «{sala}» ya tiene dos jugadores</h2>
<p>Para jugar otra partida, elegí un nombre de sala distinto y compartilo con tu rival:</p>
<p><code>/?sala=<b>el-nombre-que-quieras</b></code></p></body>"""


class Handler(BaseHTTPRequestHandler):
    cookie_nueva = None

    def log_message(self, *args):
        pass

    def sid(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c["sid"].value if "sid" in c else None

    def enviar(self, cuerpo, tipo, codigo=200, cache="no-store"):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", cache)
        if self.cookie_nueva:
            self.send_header("Set-Cookie", f"sid={self.cookie_nueva}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()
        self.wfile.write(cuerpo)

    def enviar_json(self, obj):
        self.enviar(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        partes = urlsplit(self.path)
        ruta = unquote(partes.path)
        if ruta == "/":
            pedida = limpiar_nombre(parse_qs(partes.query).get("sala", ["principal"])[0])
            res = unirse(self.sid(), pedida)
            if res is None:
                return self.enviar(PAGINA_LLENA.format(sala=pedida).encode("utf-8"), "text/html; charset=utf-8")
            if res[0] != self.sid():
                self.cookie_nueva = res[0]
            with open(os.path.join(AQUI, "interfaz.html"), "rb") as f:
                return self.enviar(f.read(), "text/html; charset=utf-8")
        if ruta == "/estado":
            e = buscar(self.sid())
            if e is None:
                return self.enviar_json(dict(snap=snap_vacio(), pregunta=None,
                                             log=["Sesión vencida o sala cerrada: recargá la página."]))
            return self.enviar_json(e[0].estado(e[1]))
        if ruta.startswith("/carta/") and ruta.endswith(".png"):
            try:
                png = png_de(ruta[len("/carta/"):-len(".png")])
            except ImportError:
                png = None
            if png:
                return self.enviar(png, "image/png", cache="max-age=3600")
        self.enviar(b"No encontrado", "text/plain", 404)

    def do_POST(self):
        if self.path != "/elegir":
            return self.enviar(b"No encontrado", "text/plain", 404)
        e = buscar(self.sid())
        ok = False
        if e is not None:
            try:
                datos = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                ok = e[0].responder(e[1], datos.get("id"), datos.get("idx"))
            except (ValueError, AttributeError):
                ok = False
        self.enviar(json.dumps({"ok": ok}).encode(), "application/json")


def correr(sala):
    ROUTER.local.cap = Capturador(sala)
    while True:
        sala.log.clear()
        try:
            Juego(ui=sala, pvp=True).jugar()
        except SystemExit:
            return  # sala cerrada por inactividad
        except Exception:
            sala.log.append("Error en el juego: mirá la terminal (o los logs de Render) para ver el detalle.")
            traceback.print_exc(file=sys.__stderr__)
            return
        sala.elegir(["Jugar de nuevo"], "Fin de la partida", asientos=[0, 1])


def main():
    global ROUTER
    try:
        import visual
    except ImportError:
        print("Instalá Pillow para ver las imágenes de las cartas")
    ROUTER = RouterStdout(sys.stdout)
    sys.stdout = ROUTER
    try:
        servidor = ThreadingHTTPServer((HOST, PUERTO), Handler)
    except OSError:
        sys.exit(f"El puerto {PUERTO} está ocupado (¿ya hay un juego abierto?). Cerralo e intentá de nuevo.")
    threading.Thread(target=limpiador, daemon=True).start()
    url = f"http://localhost:{PUERTO}/?sala=local"
    print(f"Juego abierto en {url}  (Ctrl+C en esta terminal para cerrarlo)")
    if not EN_NUBE:
        print("Para jugar contra vos mismo: abrí ese link en una ventana normal y en otra de incógnito.")
        webbrowser.open(url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nJuego cerrado.")
