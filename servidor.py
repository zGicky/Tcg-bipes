import io
import json
import os
import re
import unicodedata
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
GRACIA = int(os.environ.get("TCG_GRACIA", 30))
GRACIA_ADIOS = int(os.environ.get("TCG_ADIOS", 6))
DESCONECTADO = GRACIA // 2


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
        self.sids = [None, None]
        self.vistos = [0.0, 0.0]
        self.snaps = [None, None]
        self.log = []
        self.preguntas = {}
        self.faltan = set()
        self.respuestas = {}
        self.n = 0
        self.evento = threading.Event()
        self.lock = threading.Lock()
        self.cerrada = False
        self.hilo = None
        self.visto = time.time()
        self.adios = [0.0, 0.0]
        self.motivo = None
        self.cerrada_en = 0.0

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
        if self.cerrada:
            return dict(snap=vista(self.snaps[asiento], asiento), pregunta=None,
                        log=lineas + [f"⚠️ {self.motivo}"], salida=self.motivo)
        if not self.completa:
            lineas.append(f"⏳ Esperando al otro jugador… pasale este link (sala «{self.nombre}»).")
            return dict(snap=snap_vacio(), pregunta=None, log=lineas)
        with self.lock:
            p = self.preguntas.get(asiento) if asiento in self.faltan else None
            pregunta = dict(p, titulo=nombrar(p["titulo"], asiento)) if p else None
        snap = vista(self.snaps[asiento], asiento)
        if pregunta is None and not snap["terminado"]:
            if time.time() - self.vistos[1 - asiento] > DESCONECTADO:
                lineas.append("⚠️ El otro jugador parece desconectado: la partida se cerrará si no vuelve.")
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
JUGADORES = {}
REGISTRO = threading.Lock()


def limpiar_nombre(s):
    s = unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9_-]", "", re.sub(r"\s+", "-", s.strip()))[:24] or "principal"


def cerrar_sala(sala, motivo):
    if sala.cerrada:
        return
    sala.motivo = motivo or "La sala se cerró."
    sala.cerrada_en = time.time()
    sala.cerrar()
    if SALAS.get(sala.nombre) is sala:
        del SALAS[sala.nombre]


def soltar_locked(sid, motivo="El otro jugador salió de la partida."):
    e = JUGADORES.pop(sid, None)
    if e and not e[0].cerrada:
        cerrar_sala(e[0], motivo if e[0].completa else None)


def soltar(sid):
    with REGISTRO:
        soltar_locked(sid)


def unirse(sid, nombre_sala):
    with REGISTRO:
        e = JUGADORES.get(sid)
        if e and e[0].nombre == nombre_sala and not e[0].cerrada:
            return sid, e[0], e[1]                       # vuelve a su lugar
        soltar_locked(sid)                               # si estaba en otra sala, la deja
        sala = SALAS.get(nombre_sala)
        if sala is None:
            if len(SALAS) >= MAX_SALAS:
                cerrar_sala(min(SALAS.values(), key=lambda x: x.visto), "Sala cerrada por falta de espacio.")
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


def buscar(sid, tocar=True):
    with REGISTRO:
        e = JUGADORES.get(sid)
    if e and tocar and not e[0].cerrada:
        e[0].visto = e[0].vistos[e[1]] = time.time()
    return e


def listar(sid):
    with REGISTRO:
        salas = [dict(nombre=x.nombre, jugadores=sum(1 for c in x.sids if c), llena=x.completa)
                 for x in SALAS.values()]
        e = JUGADORES.get(sid)
        mia = e[0].nombre if e and not e[0].cerrada else None
    return dict(salas=sorted(salas, key=lambda x: (x["llena"], x["nombre"])), mia=mia)


def limpiador():
    while True:
        time.sleep(1)
        ahora = time.time()
        with REGISTRO:
            for sala in list(SALAS.values()):
                idos = [i for i, c in enumerate(sala.sids) if c and (
                    ahora - sala.vistos[i] > GRACIA or
                    (sala.adios[i] > sala.vistos[i] and ahora - sala.adios[i] > GRACIA_ADIOS))]
                if idos:
                    cerrar_sala(sala, "El otro jugador se desconectó, así que la partida se cerró."
                                if sala.completa else None)
            for sid, (sala, _) in list(JUGADORES.items()):
                if sala.cerrada and ahora - sala.cerrada_en > 600:
                    del JUGADORES[sid]


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
<p><a style="color:#6aa9ff" href="/">← Volver al menú</a> y elegí otra sala.</p></body>"""

LOBBY = r"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TCG · Salas</title><style>
body{margin:0;font-family:system-ui,sans-serif;background:#16161e;color:#eee;display:flex;justify-content:center;padding:24px}
main{width:100%;max-width:520px}h1{margin:0 0 4px}p.s{color:#9aa;margin:0 0 20px}
.c{background:#1f1f2b;border:1px solid #33334a;border-radius:12px;padding:12px 14px;margin-bottom:10px;display:flex;gap:10px;align-items:center;justify-content:space-between}
.e{color:#9aa;font-size:.85rem}button,input{font:inherit;border-radius:8px;border:1px solid #44445e;padding:8px 14px;background:#2a2a3c;color:#eee}
button{cursor:pointer;background:#3b82f6;border-color:#3b82f6}button:disabled{background:#2a2a3c;color:#777;border-color:#33334a;cursor:default}
input{flex:1;min-width:0}.b{background:#7a5a10;border-color:#a77d1c}</style>
<main><h1>⚔️ Elegí una sala</h1><p class="s">Entrá a una sala que esté esperando o creá la tuya y pasale el nombre a tu rival.</p>
<div id="mia"></div><div id="l"></div>
<form class="c" id="f"><input id="n" maxlength="24" placeholder="Nombre de tu sala (opcional)"><button>Crear / entrar</button></form></main>
<script>
var L=function(s){return s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim().replace(/\s+/g,"-").replace(/[^a-z0-9_-]/g,"").slice(0,24)};
function ir(n){location.href="/jugar?sala="+encodeURIComponent(n)}
function fila(t,e,txt,fn,off){var d=document.createElement("div");d.className="c";var a=document.createElement("div"),
 b=document.createElement("div");b.textContent=t;a.appendChild(b);var x=document.createElement("div");x.className="e";x.textContent=e;a.appendChild(x);
 var k=document.createElement("button");k.textContent=txt;k.disabled=!!off;k.onclick=fn;d.appendChild(a);d.appendChild(k);return d}
function cargar(){fetch("/salas",{cache:"no-store"}).then(function(r){return r.json()}).then(function(d){
 var m=document.getElementById("mia");m.innerHTML="";if(d.mia){var f=fila("Tenés una partida en la sala «"+d.mia+"»","Se cierra sola si no volvés enseguida","Volver",function(){ir(d.mia)});f.className="c b";m.appendChild(f)}
 var l=document.getElementById("l");l.innerHTML="";if(!d.salas.length){var p=document.createElement("p");p.className="s";p.textContent="No hay salas abiertas todavía.";l.appendChild(p)}
 d.salas.forEach(function(s){l.appendChild(fila(s.nombre,s.llena?"En juego (2/2)":"Esperando rival (1/2)",s.llena?"Llena":"Unirme",function(){ir(s.nombre)},s.llena))})}).catch(function(){})}
document.getElementById("f").onsubmit=function(e){e.preventDefault();ir(L(document.getElementById("n").value)||"sala-"+Math.random().toString(36).slice(2,6))};
cargar();setInterval(cargar,2000);
</script></html>"""

EXTRA_JUEGO = """<style>#tcg-s{position:fixed;right:10px;bottom:10px;z-index:9998;font:12px sans-serif;background:rgba(30,30,44,.85);color:#ddd;border:1px solid #555;border-radius:14px;padding:5px 10px;cursor:pointer}
#tcg-f{position:fixed;inset:0;z-index:9999;background:rgba(10,10,16,.9);display:none;align-items:center;justify-content:center;flex-direction:column;gap:18px;font-family:sans-serif;color:#eee;text-align:center;padding:24px}
#tcg-f a{background:#3b82f6;color:#fff;text-decoration:none;padding:12px 22px;border-radius:10px;font-size:1.1rem}</style>
<button id="tcg-s">Salir al menú</button><div id="tcg-f"><h2 id="tcg-m"></h2><a href="/salir">Volver al menú</a></div>
<script>(function(){var fin=false;document.getElementById("tcg-s").onclick=function(){if(confirm("¿Salir de la partida? Tu rival va a ser avisado."))location.href="/salir"};
addEventListener("pagehide",function(){if(!fin&&navigator.sendBeacon)navigator.sendBeacon("/adios")});
setInterval(function(){if(fin)return;fetch("/estado",{cache:"no-store"}).then(function(r){return r.json()}).then(function(d){
if(d&&d.salida){fin=true;document.getElementById("tcg-m").textContent=d.salida;document.getElementById("tcg-f").style.display="flex"}}).catch(function(){})},2000)})();</script>"""


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

    def ir_a(self, destino):
        self.send_response(302)
        self.send_header("Location", destino)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        partes = urlsplit(self.path)
        ruta = unquote(partes.path)
        pedida = parse_qs(partes.query).get("sala", [""])[0]
        if ruta == "/":
            if pedida:  # links viejos tipo /?sala=amigos
                return self.ir_a("/jugar?sala=" + limpiar_nombre(pedida))
            return self.enviar(LOBBY.encode("utf-8"), "text/html; charset=utf-8")
        if ruta == "/salas":
            return self.enviar_json(listar(self.sid()))
        if ruta == "/salir":
            soltar(self.sid())
            return self.ir_a("/")
        if ruta == "/jugar":
            if not pedida:
                return self.ir_a("/")
            nombre = limpiar_nombre(pedida)
            res = unirse(self.sid(), nombre)
            if res is None:
                return self.enviar(PAGINA_LLENA.format(sala=nombre).encode("utf-8"), "text/html; charset=utf-8")
            if res[0] != self.sid():
                self.cookie_nueva = res[0]
            with open(os.path.join(AQUI, "interfaz.html"), encoding="utf-8") as f:
                html = f.read()
            i = html.rfind("</body>")
            html = html + EXTRA_JUEGO if i < 0 else html[:i] + EXTRA_JUEGO + html[i:]
            return self.enviar(html.encode("utf-8"), "text/html; charset=utf-8")
        if ruta == "/estado":
            e = buscar(self.sid())
            if e is None:
                return self.enviar_json(dict(snap=snap_vacio(), pregunta=None, salida="La sesión venció.",
                                             log=["La sesión venció: volvé al menú."]))
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
        if self.path == "/adios":
            e = buscar(self.sid(), tocar=False)
            if e:
                e[0].adios[e[1]] = time.time()
            return self.enviar(b"", "text/plain", 204)
        if self.path != "/elegir":
            return self.enviar(b"No encontrado", "text/plain", 404)
        e = buscar(self.sid())
        ok = False
        if e is not None and not e[0].cerrada:
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
    url = f"http://localhost:{PUERTO}/"
    print(f"Juego abierto en {url}  (Ctrl+C en esta terminal para cerrarlo)")
    if not EN_NUBE:
        print("Para jugar contra vos mismo: abrí el menú en una ventana normal y en otra de incógnito.")
        webbrowser.open(url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nJuego cerrado.")
