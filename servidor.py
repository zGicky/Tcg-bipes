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

class Rendicion(BaseException):
    def __init__(self, asiento):
        self.asiento = asiento


NOMBRES = ["Jugador 1", "Jugador 2"]
MAX_SALAS = 40
GRACIA = int(os.environ.get("TCG_GRACIA", 30))
GRACIA_ADIOS = int(os.environ.get("TCG_ADIOS", 6))
DESCONECTADO = GRACIA // 2


def nombrar(texto, asiento, nombres=NOMBRES, rival="Rival"):
    return texto.replace(nombres[asiento], "Vos").replace(nombres[1 - asiento], rival)


def snap_vacio():
    j = dict(nombre="Vos", puntos=0, mazo=0, mano_n=0, energia_libre=0, activa=None, banca=[], mano=[])
    return dict(yo=dict(j), rival=dict(j, nombre="Rival"), terreno=None,
                puntos_meta=PUNTOS_PARA_GANAR, terminado=False, ganador=None)


def vista(snap, asiento, nombres=NOMBRES, rival="Rival"):
    if snap is None:
        return snap_vacio()
    s = dict(snap)
    s["yo"] = dict(snap["yo"], nombre="Vos")
    s["rival"] = dict(snap["rival"], nombre=rival)
    if s.get("ganador"):
        s["ganador"] = "Vos" if s["ganador"] == nombres[asiento] else rival
    return s


class Sala:
    def __init__(self, nombre, cpu=False):
        self.nombre = nombre
        self.cpu = cpu
        self.nombres = ["Vos", "CPU"] if cpu else NOMBRES
        self.rival = "CPU" if cpu else "Rival"
        self.sids = [None, "cpu"] if cpu else [None, None]
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
        self.juego = None
        self.abortar = None

    @property
    def completa(self):
        return all(self.sids)

    def cerrar(self):
        self.cerrada = True
        self.evento.set()

    def actualizar(self, snaps, pausa=0):
        if self.cerrada:
            raise SystemExit
        if self.abortar is not None:
            raise Rendicion(self.abortar)
        for i, nombre in enumerate(self.nombres):
            if nombre in snaps:
                self.snaps[i] = snaps[nombre]
        if pausa:
            time.sleep(pausa)

    def elegir(self, etiquetas, titulo, jug=None, asientos=None):
        if asientos is None:
            asientos = [self.nombres.index(jug.nombre)]
        with self.lock:
            self.n += 1
            self.preguntas = {a: dict(id=self.n, titulo=titulo, opciones=list(etiquetas)) for a in asientos}
            self.faltan = set(asientos)
            self.respuestas = {}
            self.evento.clear()
            if self.abortar is not None:
                raise Rendicion(self.abortar)
        self.evento.wait()
        if self.cerrada:
            raise SystemExit
        if self.abortar is not None:
            raise Rendicion(self.abortar)
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

    def rendirse(self, asiento):
        if self.cerrada or self.juego is None or self.juego.terminado or self.abortar is not None:
            return False
        self.abortar = asiento
        self.evento.set()
        return True

    def estado(self, asiento):
        lineas = [nombrar(l, asiento, self.nombres, self.rival) for l in self.log[-80:]]
        if self.cerrada:
            return dict(snap=vista(self.snaps[asiento], asiento, self.nombres, self.rival), pregunta=None,
                        log=lineas + [f"⚠️ {self.motivo}"], salida=self.motivo)
        if not self.completa:
            lineas.append(f"⏳ Esperando al otro jugador… pasale este link (sala «{self.nombre}»).")
            return dict(snap=snap_vacio(), pregunta=None, log=lineas)
        with self.lock:
            p = self.preguntas.get(asiento) if asiento in self.faltan else None
            pregunta = dict(p, titulo=nombrar(p["titulo"], asiento, self.nombres, self.rival)) if p else None
        snap = vista(self.snaps[asiento], asiento, self.nombres, self.rival)
        if pregunta is None and not snap["terminado"]:
            if self.cpu:
                lineas.append("⏳ Juega la CPU…")
            elif time.time() - self.vistos[1 - asiento] > DESCONECTADO:
                lineas.append("⚠️ El otro jugador parece desconectado: la partida se cerrará si no vuelve.")
            else:
                lineas.append("⏳ Esperando al rival…")
        extra = {}
        if pregunta:
            for i, o in enumerate(pregunta["opciones"]):
                if o.startswith("↩️"):
                    extra["deshacer"] = dict(id=pregunta["id"], idx=i)
        return dict(snap=snap, pregunta=pregunta, log=lineas, **extra)


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
            return sid, e[0], e[1]
        soltar_locked(sid)
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


def nueva_ia(sid):
    with REGISTRO:
        soltar_locked(sid)
        if len(SALAS) >= MAX_SALAS:
            cerrar_sala(min(SALAS.values(), key=lambda x: x.visto), "Sala cerrada por falta de espacio.")
        nombre = "ia-" + secrets.token_hex(3)
        while nombre in SALAS:
            nombre = "ia-" + secrets.token_hex(3)
        sala = SALAS[nombre] = Sala(nombre, cpu=True)
        nuevo = secrets.token_urlsafe(16)
        sala.sids[0] = nuevo
        JUGADORES[nuevo] = (sala, 0)
        sala.visto = sala.vistos[0] = time.time()
        sala.hilo = threading.Thread(target=correr, args=(sala,), daemon=True)
        sala.hilo.start()
        return nuevo, nombre


def buscar(sid, tocar=True):
    with REGISTRO:
        e = JUGADORES.get(sid)
    if e and tocar and not e[0].cerrada:
        e[0].visto = e[0].vistos[e[1]] = time.time()
    return e


def listar(sid):
    with REGISTRO:
        salas = [dict(nombre=x.nombre, jugadores=sum(1 for c in x.sids if c), llena=x.completa)
                 for x in SALAS.values() if not x.cpu]
        e = JUGADORES.get(sid)
        mia = e[0].nombre if e and not e[0].cerrada else None
    return dict(salas=sorted(salas, key=lambda x: (x["llena"], x["nombre"])), mia=mia)


def limpiador():
    while True:
        time.sleep(1)
        ahora = time.time()
        with REGISTRO:
            for sala in list(SALAS.values()):
                idos = [i for i, c in enumerate(sala.sids) if c and not (sala.cpu and i == 1) and (
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


_cache_fondo = {}


def fondo_terreno(nombre):
    if not any(d["nombre"] == nombre and d["categoria"] == "Terreno" for d in CARTAS):
        return None
    for carpeta in ("terrenos", "arte"):
        for ext in TIPOS_IMG:
            ruta = os.path.join(AQUI, carpeta, f"{nombre}.{ext}")
            if os.path.isfile(ruta):
                with open(ruta, "rb") as f:
                    return f.read(), TIPOS_IMG[ext]
    if nombre not in _cache_fondo:
        try:
            from PIL import Image, ImageFilter, ImageOps
            carta = Image.open(io.BytesIO(png_de(nombre))).convert("RGB")
            buf = io.BytesIO()
            ImageOps.fit(carta, (1280, 720)).filter(ImageFilter.GaussianBlur(26)).save(buf, "JPEG", quality=80)
            _cache_fondo[nombre] = buf.getvalue()
        except Exception:
            return None
    return _cache_fondo[nombre], "image/jpeg"


FONDOS = os.path.join(AQUI, "fondos")
TIPOS_IMG = dict(jpg="image/jpeg", jpeg="image/jpeg", png="image/png", webp="image/webp")
DEGRADADOS = dict(
    menu="radial-gradient(ellipse at 50% 0%,#37336a 0%,#1b1b2e 45%,#14141c 100%)",
    juego="radial-gradient(ellipse at 50% 25%,#26304a 0%,#171a28 55%,#101018 100%)")


def css_fondo(nombre):
    for ext in TIPOS_IMG:
        if os.path.isfile(os.path.join(FONDOS, f"{nombre}.{ext}")):
            capa = "linear-gradient(rgba(10,10,16,.55),rgba(10,10,16,.55))"
            return (f"html{{background:{capa},url(/fondos/{nombre}.{ext}) center/cover fixed no-repeat #14141c !important;"
                    "min-height:100vh}body{background:transparent!important}")
    return f"html{{background:{DEGRADADOS[nombre]} fixed #14141c !important;min-height:100vh}}body{{background:transparent!important}}"


PAGINA_LLENA = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<body style="font-family:sans-serif;background:#16161e;color:#eee;padding:2rem;line-height:1.5">
<h2>La sala «{sala}» ya tiene dos jugadores</h2>
<p><a style="color:#6aa9ff" href="/">← Volver al menú</a> y elegí otra sala.</p></body>"""

LOBBY = r"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TCG · Salas</title><style>/*FONDO*/
body{margin:0;font-family:system-ui,sans-serif;background:#16161e;color:#eee;display:flex;justify-content:center;padding:24px}
main{width:100%;max-width:520px}h1{margin:0 0 4px}p.s{color:#9aa;margin:0 0 20px}
.c{background:#1f1f2b;border:1px solid #33334a;border-radius:12px;padding:12px 14px;margin-bottom:10px;display:flex;gap:10px;align-items:center;justify-content:space-between}
.e{color:#9aa;font-size:.85rem}button,input{font:inherit;border-radius:8px;border:1px solid #44445e;padding:8px 14px;background:#2a2a3c;color:#eee}
button{cursor:pointer;background:#3b82f6;border-color:#3b82f6}button:disabled{background:#2a2a3c;color:#777;border-color:#33334a;cursor:default}
a.g{display:block;background:#1f1f2b;border:1px solid #44445e;border-radius:14px;padding:18px 20px;margin-bottom:12px;color:#eee;text-decoration:none}a.g:hover{border-color:#3b82f6;background:#252538}a.g b{display:block;font-size:1.2rem;margin-bottom:4px}a.g span{color:#9aa;font-size:.9rem}a.s{color:#9aa}input{flex:1;min-width:0}.b{background:#7a5a10;border-color:#a77d1c}</style>
<main><div id="mia"></div>
<div id="ini"><h1>⚔️ TCG</h1><p class="s">¿Cómo querés jugar?</p>
<a class="g" href="/ia"><b>🤖 Jugar contra la IA</b><span>Una partida vos solo contra la CPU.</span></a>
<a class="g" href="#salas" id="pvp"><b>👥 Jugar contra otro jugador</b><span>Entrá a una sala o creá la tuya y pasale el nombre a tu amigo.</span></a></div>
<div id="sal" style="display:none"><h1>👥 Elegí una sala</h1><p class="s">Entrá a una que esté esperando o creá la tuya.</p><div id="l"></div>
<form class="c" id="f"><input id="n" maxlength="24" placeholder="Nombre de tu sala (opcional)"><button>Crear / entrar</button></form>
<p><a class="s" href="#" id="vol">← Volver</a></p></div></main>
<script>
var L=function(s){return s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim().replace(/\s+/g,"-").replace(/[^a-z0-9_-]/g,"").slice(0,24)};
function ir(n){location.href="/jugar?sala="+encodeURIComponent(n)}
function fila(t,e,txt,fn,off){var d=document.createElement("div");d.className="c";var a=document.createElement("div"),
 b=document.createElement("div");b.textContent=t;a.appendChild(b);var x=document.createElement("div");x.className="e";x.textContent=e;a.appendChild(x);
 var k=document.createElement("button");k.textContent=txt;k.disabled=!!off;k.onclick=fn;d.appendChild(a);d.appendChild(k);return d}
function cargar(){fetch("/salas",{cache:"no-store"}).then(function(r){return r.json()}).then(function(d){
 var m=document.getElementById("mia");m.innerHTML="";if(d.mia){var f=fila(d.mia.indexOf("ia-")==0?"Tenés una partida contra la IA en curso":"Tenés una partida en la sala «"+d.mia+"»","Se cierra sola si no volvés enseguida","Volver",function(){ir(d.mia)});f.className="c b";m.appendChild(f)}
 var l=document.getElementById("l");l.innerHTML="";if(!d.salas.length){var p=document.createElement("p");p.className="s";p.textContent="No hay salas abiertas todavía.";l.appendChild(p)}
 d.salas.forEach(function(s){l.appendChild(fila(s.nombre,s.llena?"En juego (2/2)":"Esperando rival (1/2)",s.llena?"Llena":"Unirme",function(){ir(s.nombre)},s.llena))})}).catch(function(){})}
document.getElementById("f").onsubmit=function(e){e.preventDefault();ir(L(document.getElementById("n").value)||"sala-"+Math.random().toString(36).slice(2,6))};
function vista(v){document.getElementById("ini").style.display=v?"none":"block";document.getElementById("sal").style.display=v?"block":"none"}
document.getElementById("pvp").onclick=function(e){e.preventDefault();vista(true)};
document.getElementById("vol").onclick=function(e){e.preventDefault();vista(false)};
if(location.hash=="#salas")vista(true);
cargar();setInterval(cargar,2000);
</script></html>"""

EXTRA_JUEGO = """<style>/*FONDO*/#tcg-bg{position:fixed;inset:0;z-index:-1;background:center/cover no-repeat;opacity:0;transition:opacity .8s;pointer-events:none}
#tcg-t{display:none;position:fixed;right:430px;top:50%;margin-top:-135px;width:190px;z-index:1;text-align:center;font:600 13px sans-serif;color:#eee;pointer-events:none;opacity:1;transition:opacity .15s}#tcg-t.oculta{opacity:0}
#tcg-t img{width:100%;border-radius:11px;box-shadow:0 12px 34px rgba(0,0,0,.65),0 0 24px rgba(245,179,1,.35);display:block;animation:tcgF 3.4s ease-in-out infinite}
@keyframes tcgF{0%,100%{transform:translateY(0)}50%{transform:translateY(-12px)}}
#tcg-t b{display:block;margin-top:6px;text-shadow:0 1px 3px #000}#tcg-t.nuevo{animation:tcgT .7s ease-out}
@keyframes tcgT{0%{opacity:0;transform:scale(.6) rotate(-6deg)}60%{transform:scale(1.08)}100%{opacity:1;transform:none}}
@media(max-width:1000px){#tcg-t{display:none!important}}#tcg-b{position:fixed;right:10px;bottom:10px;z-index:9998;display:flex;gap:6px}
#tcg-b button{font:12px sans-serif;background:rgba(30,30,44,.88);color:#ddd;border:1px solid #555;border-radius:14px;padding:6px 12px;cursor:pointer}
#tcg-b button:disabled{opacity:.4;cursor:default}#tcg-r:not(:disabled):hover{background:#c0392b;color:#fff}#tcg-u:not(:disabled){border-color:#3b82f6;color:#fff}#tcg-u:not(:disabled):hover{background:#2e6da4}
#tcg-f{position:fixed;inset:0;z-index:9999;background:rgba(10,10,16,.9);display:none;align-items:center;justify-content:center;flex-direction:column;gap:18px;font-family:sans-serif;color:#eee;text-align:center;padding:24px}
#tcg-f a{background:#3b82f6;color:#fff;text-decoration:none;padding:12px 22px;border-radius:10px;font-size:1.1rem}</style>
<div id="tcg-bg"></div><div id="tcg-t"><img alt=""><b></b></div><div id="tcg-b"><button id="tcg-u" disabled>↩️ Deshacer</button><button id="tcg-s">Salir al menú</button></div><div id="tcg-f"><h2 id="tcg-m"></h2><a href="/salir">Volver al menú</a></div>
<script>(function(){var fin=false,tn=null;
function ter(n){n=n||null;if(n===tn)return;tn=n;var p=document.getElementById("tcg-t"),g=document.getElementById("tcg-bg");g.style.opacity=0;if(!n){p.style.display="none";return}
var e=encodeURIComponent(n);setTimeout(function(){if(tn!==n)return;g.style.backgroundImage="linear-gradient(rgba(10,10,16,.4),rgba(10,10,16,.4)),url(/terreno/"+e+".fondo)";g.style.opacity=1},400);
p.getElementsByTagName("img")[0].src="/carta/"+e+".png";p.getElementsByTagName("b")[0].textContent="Terreno: "+n;
p.style.display="block";p.classList.remove("nuevo");void p.offsetWidth;p.classList.add("nuevo")}
document.getElementById("tcg-s").onclick=function(){if(confirm("¿Salir de la partida? Tu rival va a ser avisado."))location.href="/salir"};
function bot(d){var u=document.getElementById("tcg-u");u._d=d&&d.deshacer||null;u.disabled=!u._d}
document.getElementById("tcg-u").onclick=function(){var x=this._d;if(x)fetch("/elegir",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:x.id,idx:x.idx})})};
var tp=document.getElementById("tcg-t");
function esCarta(t){if(!t||!t.tagName||tp.contains(t))return false;if(t.tagName==="IMG"&&(t.src||"").indexOf("/carta/")>-1)return true;
try{return getComputedStyle(t).backgroundImage.indexOf("/carta/")>-1}catch(x){return false}}
document.addEventListener("mouseover",function(e){if(esCarta(e.target))tp.classList.add("oculta")});
document.addEventListener("mouseout",function(e){if(esCarta(e.target))tp.classList.remove("oculta")});
addEventListener("pagehide",function(){if(!fin&&navigator.sendBeacon)navigator.sendBeacon("/adios")});
setInterval(function(){if(fin)return;fetch("/estado",{cache:"no-store"}).then(function(r){return r.json()}).then(function(d){
bot(d);if(d&&d.snap)ter(d.snap.terreno);if(d&&d.salida){fin=true;document.getElementById("tcg-m").textContent=d.salida;document.getElementById("tcg-f").style.display="flex"}}).catch(function(){})},1000)})();</script>"""


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
        if self.cookie_nueva:
            self.send_header("Set-Cookie", f"sid={self.cookie_nueva}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()

    def do_GET(self):
        partes = urlsplit(self.path)
        ruta = unquote(partes.path)
        pedida = parse_qs(partes.query).get("sala", [""])[0]
        if ruta == "/":
            if pedida:
                return self.ir_a("/jugar?sala=" + limpiar_nombre(pedida))
            return self.enviar(LOBBY.replace("/*FONDO*/", css_fondo("menu")).encode("utf-8"), "text/html; charset=utf-8")
        if ruta == "/ia":
            self.cookie_nueva, nombre = nueva_ia(self.sid())
            return self.ir_a("/jugar?sala=" + nombre)
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
            extra = EXTRA_JUEGO.replace("/*FONDO*/", css_fondo("juego"))
            i = html.rfind("</body>")
            html = html + extra if i < 0 else html[:i] + extra + html[i:]
            return self.enviar(html.encode("utf-8"), "text/html; charset=utf-8")
        if ruta == "/estado":
            e = buscar(self.sid())
            if e is None:
                return self.enviar_json(dict(snap=snap_vacio(), pregunta=None, salida="La sesión venció.",
                                             log=["La sesión venció: volvé al menú."]))
            return self.enviar_json(e[0].estado(e[1]))
        if ruta.startswith("/terreno/") and ruta.endswith(".fondo"):
            r = fondo_terreno(ruta[len("/terreno/"):-len(".fondo")])
            if r:
                return self.enviar(r[0], r[1], cache="max-age=3600")
        if ruta.startswith("/fondos/"):
            nombre = os.path.basename(ruta)
            ruta_img = os.path.join(FONDOS, nombre)
            tipo = TIPOS_IMG.get(nombre.rsplit(".", 1)[-1].lower())
            if tipo and os.path.isfile(ruta_img):
                with open(ruta_img, "rb") as f:
                    return self.enviar(f.read(), tipo, cache="max-age=3600")
        if ruta.startswith("/arte/"):
            stem = os.path.splitext(os.path.basename(ruta))[0]
            for ext in ("png", "jpg", "jpeg", "webp"):
                p = os.path.join(AQUI, "arte", f"{stem}.{ext}")
                if os.path.isfile(p):
                    with open(p, "rb") as f:
                        return self.enviar(f.read(), TIPOS_IMG[ext], cache="no-cache")
        if ruta.startswith("/carta/") and ruta.endswith(".png"):
            try:
                png = png_de(ruta[len("/carta/"):-len(".png")])
            except ImportError:
                png = None
            if png:
                return self.enviar(png, "image/png", cache="no-cache")
        self.enviar(b"No encontrado", "text/plain", 404)

    def do_POST(self):
        if self.path == "/rendirse":
            e = buscar(self.sid())
            ok = bool(e and e[0].rendirse(e[1]))
            return self.enviar(json.dumps({"ok": ok}).encode(), "application/json")
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
        sala.abortar = None
        juego = sala.juego = Juego(ui=sala, pvp=not sala.cpu)
        try:
            juego.jugar()
        except SystemExit:
            return
        except Rendicion as r:
            perdedor = juego.jugadores[r.asiento]
            juego.ganador = next(j for j in juego.jugadores if j is not perdedor)
            juego.terminado = True
            sala.abortar = None
            print(f"\n🏳️ {perdedor.nombre} se rindió. ¡Ganó {juego.ganador.nombre}!")
            juego.actualizar()
        except Exception:
            sala.log.append("Error en el juego: mirá la terminal (o los logs de Render) para ver el detalle.")
            traceback.print_exc(file=sys.__stderr__)
            return
        sala.elegir(["Jugar de nuevo"], "Fin de la partida", asientos=[0] if sala.cpu else [0, 1])


def main():
    global ROUTER
    try:
        __import__("visual")
    except ImportError:
        print("Aviso: para ver las imágenes de las cartas instalá Pillow con:  pip install pillow")
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
