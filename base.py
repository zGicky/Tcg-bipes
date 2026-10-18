import os
import copy
from collections import Counter
import random
import types
import sys

from cartas import CARTAS

ENERGIA_POR_TURNO = 1
PUNTOS_PARA_GANAR = 3
TAM_BANCA = 3
MANO_INICIAL = 5
MAX_TURNOS = 150
DANO_QUEMADURA = 10
TURNOS_QUEMADURA = 3
BOOST_NIDO = 20
RELACIONADAS_MASIMO = ("Masimo",)
EXTRA_MASIMO = 20
TAM_MAZO = 20
MIN_COMBATE = 6
COPIAS_MAX = 2
COPIAS_POR_RAREZA = {"Legendario": 1, "Mítico": 1}
PAUSA_CPU = float(os.environ.get("TCG_PAUSA", "0.6"))

VENTAJA = {"Atacante": "Mago", "Mago": "Tanque", "Tanque": "Atacante"}
COMBATE = tuple(VENTAJA)
ECUADOR = {"Geeksito", "Dankul", "Chichotas", "Manuelito", "Forten"}


def mult(atacante, defensor):
    if VENTAJA[atacante.categoria] == defensor.categoria:
        return 2
    if VENTAJA[defensor.categoria] == atacante.categoria:
        return 0.5
    return 1


def copias_max(datos):
    """Cuántas copias de esta carta puede llevar un mazo."""
    return COPIAS_POR_RAREZA.get(datos.get("rareza", ""), COPIAS_MAX)


def validar_mazo(nombres):
    """Lista de problemas de un mazo (lista de nombres de cartas, con repetidos). Vacía si es válido."""
    por_nombre = {d["nombre"]: d for d in CARTAS}
    cuenta = Counter(nombres)
    errores = []
    if len(nombres) != TAM_MAZO:
        errores.append(f"El mazo tiene que tener {TAM_MAZO} cartas (ahora tiene {len(nombres)}).")
    for n, veces in cuenta.items():
        if n not in por_nombre:
            errores.append(f"La carta «{n}» no existe.")
        elif veces > copias_max(por_nombre[n]):
            errores.append(f"«{n}»: máximo {copias_max(por_nombre[n])} copia(s) (tiene {veces}).")
    combate = sum(v for n, v in cuenta.items() if n in por_nombre and por_nombre[n]["categoria"] in COMBATE)
    if combate < MIN_COMBATE:
        errores.append(f"Necesitás al menos {MIN_COMBATE} cartas de combate (tiene {combate}).")
    return errores


def mazo_aleatorio():
    """Un mazo válido armado al azar."""
    while True:
        cuenta = Counter()
        mazo = []
        pool = list(CARTAS)
        random.shuffle(pool)
        for d in [x for x in pool if x["categoria"] in COMBATE][:MIN_COMBATE]:
            mazo.append(d["nombre"]); cuenta[d["nombre"]] += 1
        while len(mazo) < TAM_MAZO:
            d = random.choice(CARTAS)
            if cuenta[d["nombre"]] < copias_max(d):
                mazo.append(d["nombre"]); cuenta[d["nombre"]] += 1
        if not validar_mazo(mazo):
            return mazo


class Carta:
    def __init__(self, datos):
        base = dict(rareza="", costo=0, retirada=0, ps=0, dano=0, full_art=False)
        base.update(datos)
        self.__dict__.update(base)
        self.ps_max = self.ps
        self.costo_base = self.costo
        self.energia = 0
        self.equipo = None
        self.confundido = False
        self.embobado = False
        self.escudo = 0
        self.turnos_activo = 0
        self.cargas = 0
        self.veneno = 0
        self.quemadura = 0
        self.boost = 0
        self.inmune = False
        self.dano_extra = 0

    @property
    def es_combate(self):
        return self.categoria in COMBATE

    def __str__(self):
        if not self.es_combate:
            return self.nombre
        s = f"{self.nombre} [{self.categoria}] PS {self.ps}/{self.ps_max} Energía {self.energia}/{self.costo}"
        if self.equipo:
            s += f" +{self.equipo.nombre}"
        if self.confundido:
            s += " (Confundido)"
        if self.embobado:
            s += " (Embobado)"
        if self.veneno:
            s += f" (Gangrena {self.veneno})"
        if self.quemadura:
            s += f" (Quemadura {self.quemadura})"
        if self.boost:
            s += f" (+{self.boost} daño)"
        if self.escudo:
            s += f" (Escudo -{self.escudo})"
        if self.nombre == "Tay":
            s += f" (Cargas {self.cargas})"
        return s

    def en_mano(self):
        if self.es_combate:
            return (f"{self.nombre} [{self.categoria}/{self.rareza}] PS {self.ps} | "
                    f"{self.habilidad}: {self.dano} (costo {self.costo}, retirada {self.retirada})")
        return f"{self.nombre} [{self.categoria}] | {self.habilidad}: {self.efecto}"


class Jugador:
    def __init__(self, nombre, cpu=False, mazo=None):
        self.nombre = nombre
        self.cpu = cpu
        if mazo is None:
            self.mazo = [Carta(d) for d in CARTAS]
        else:
            por_nombre = {d["nombre"]: d for d in CARTAS}
            self.mazo = [Carta(por_nombre[n]) for n in mazo]
        random.shuffle(self.mazo)
        self.mano = []
        self.activa = None
        self.banca = []
        self.puntos = 0
        self.energia_libre = 0
        self.apoyo_usado = False
        self.retirada_usada = False
        self.cubo_usado = False
        self.descarte = []
        self.ecuador_usado = False

    def en_juego(self):
        return ([self.activa] if self.activa else []) + self.banca


def a_basico(j, yo, rv, c, d):
    j.atk(c, d, c.dano)


def a_chichotas(j, yo, rv, c, d):
    j.golpear(c, d, 15 + j.bonus(c) + 15 * mult(c, d))


def a_spreenfino(j, yo, rv, c, d):
    j.atk(c, d, c.dano)
    d.confundido = True


def a_kono(j, yo, rv, c, d):
    j.atk(c, d, c.dano)
    d.embobado = True


def a_aleseto(j, yo, rv, c, d):
    j.atk(c, d, c.dano + min(30, 10 * c.turnos_activo))


def a_tay(j, yo, rv, c, d):
    j.atk(c, d, 20 * c.cargas)
    c.cargas = 0


def a_manuelito(j, yo, rv, c, d):
    extra = 30 if any(x.nombre == "Geeksito" for x in yo.en_juego()) else 0
    j.atk(c, d, c.dano + extra)


def a_mantekita(j, yo, rv, c, d):
    j.atk(c, d, c.dano)
    for x in rv.banca:
        x.ps = max(0, x.ps - 10)
        print(f"  {x.nombre} (banca) recibe 10 de daño directo (PS {x.ps}/{x.ps_max})")


def a_bondiola(j, yo, rv, c, d):
    j.atk(c, d, c.dano)
    j.curar(c, 20)


def a_dandyd(j, yo, rv, c, d):
    caras = sum(random.random() < 0.5 for _ in range(2))
    print(f"  Monedas: {caras} cara(s)")
    j.atk(c, d, c.dano + 10 * caras)


def a_larco(j, yo, rv, c, d):
    j.atk(c, d, c.dano)
    c.escudo = 20


def a_poto(j, yo, rv, c, d):
    j.atk(c, d, c.dano)
    j.robar(yo, 1)


def a_geeksito(j, yo, rv, c, d):
    j.atk(c, d, c.dano + 20 * (len(yo.en_juego()) - 1))


def a_flanseto(j, yo, rv, c, d):
    cara = random.random() < 0.5
    print(f"  Moneda: {'cara' if cara else 'cruz'}")
    j.atk(c, d, c.dano + (20 if cara else 0))


def a_tm_opera(j, yo, rv, c, d):
    j.atk(c, d, c.dano + (30 if d.ps > c.ps else 0))


def a_myome(j, yo, rv, c, d):
    j.atk(c, d, c.dano)
    cara = random.random() < 0.5
    print(f"  Neo Nato: sale {'cara' if cara else 'cruz'}")
    if cara:
        c.inmune = True


def a_gengar(j, yo, rv, c, d):
    j.atk(c, d, c.dano + (30 if any(x.nombre == "Mantekita" for x in yo.descarte) else 0))


def a_larc(j, yo, rv, c, d):
    j.atk(c, d, c.dano)
    if len(yo.mazo) < 2:
        return
    a, b = yo.mazo[-1], yo.mazo[-2]
    if not yo.cpu and j.elegir_idx([f"{a.nombre} arriba, luego {b.nombre}", f"{b.nombre} arriba, luego {a.nombre}"],
                                   "Pokemaniaco: ordená las 2 primeras cartas de tu mazo", yo) == 1:
        yo.mazo[-1], yo.mazo[-2] = b, a
    print(f"  {yo.nombre} mira y ordena las 2 primeras cartas de su mazo")


def a_kevin(j, yo, rv, c, d):
    ale = next((x for x in yo.en_juego() if x.nombre == "Aleseto"), None)
    j.atk(c, d, c.dano + (10 if ale else 0))
    if ale and ale is not yo.activa and c is yo.activa and c.ps > 0 and not c.embobado:
        if yo.cpu or j.elegir_idx(["Sí, retirarse gratis y poner a Aleseto", "No"], "ElKevinxdd: ¿te retirás gratis?", yo) == 0:
            c.turnos_activo = 0
            yo.banca.remove(ale)
            yo.banca.append(c)
            yo.activa = ale
            print(f"  {yo.nombre} retira gratis a {c.nombre} y pone a {ale.nombre} como activa")


ATAQUES = {
    "Gengar do mantequinha": a_gengar, "Larc": a_larc, "ElKevinxdd": a_kevin,
    "Flanseto": a_flanseto, "TM Opera": a_tm_opera, "MyomeSanz": a_myome,
    "Chichotas": a_chichotas, "Spreenfino": a_spreenfino, "Kono": a_kono,
    "Aleseto": a_aleseto, "Tay": a_tay, "Manuelito": a_manuelito,
    "Mantekita": a_mantekita, "Tanque Bondiola": a_bondiola, "Dandyd": a_dandyd,
    "Larco": a_larco, "Mr Poto": a_poto, "Geeksito": a_geeksito,
}


def p_bocchi(j, yo, rv):
    cartas = yo.en_juego()
    if not cartas:
        return
    if yo.cpu:
        c = max(cartas, key=lambda x: x.ps_max - x.ps)
    else:
        c = cartas[j.elegir_idx([f"{x.nombre} (PS {x.ps}/{x.ps_max})" for x in cartas], "Bocchi: ¿a qué carta curás?", yo)]
    j.curar(c, 50 if c.nombre == "Aleseto" else 30)


def p_denji(j, yo, rv):
    for c in yo.en_juego():
        if c.nombre in ("Geeksito", "Chichotas", "Manuelito"):
            j.curar(c, 20)


def p_fuecoco(j, yo, rv):
    if yo.mano:
        if yo.cpu:
            c = random.choice(yo.mano)
        else:
            c = yo.mano[j.elegir_idx([x.nombre for x in yo.mano], "Fuecoco: ¿qué carta descartás?", yo)]
        yo.mano.remove(c)
        yo.descarte.append(c)
        print(f"  {yo.nombre} descarta {c.nombre}")
    j.robar(yo, 2)


APOYOS = {"Bocchi": p_bocchi, "Denji": p_denji, "Fuecoco": p_fuecoco}


def datos_carta(c):
    estados = [n for n, v in (("Confundido", c.confundido), ("Embobado", c.embobado)) if v]
    if c.veneno:
        estados.append(f"Gangrena {c.veneno}")
    if c.quemadura:
        estados.append(f"Quemadura {c.quemadura}")
    if c.boost:
        estados.append(f"+{c.boost} daño")
    if c.escudo:
        estados.append(f"Escudo -{c.escudo}")
    if c.nombre == "Tay":
        estados.append(f"Cargas {c.cargas}")
    return dict(nombre=c.nombre, categoria=c.categoria, rareza=c.rareza, ps=c.ps, ps_max=c.ps_max,
                energia=c.energia, costo=c.costo, habilidad=c.habilidad, dano=c.dano,
                equipo=c.equipo.nombre if c.equipo else None, estados=estados, full_art=c.full_art,
                efecto=c.efecto, descripcion=c.descripcion, retirada=c.retirada,
                debil=next((k for k, v in VENTAJA.items() if v == c.categoria), ""),
                resiste=VENTAJA.get(c.categoria, ""))


class Juego:
    def __init__(self, auto=False, ui=None, pvp=False, mazos=None):
        """mazos: [mazo_asiento_0, mazo_asiento_1], cada uno una lista de nombres (o None).
        En las partidas del servidor (ui) quien no tenga un mazo válido juega con uno al azar."""
        if pvp:
            quienes = [("Jugador 1", False), ("Jugador 2", False)]
        else:
            quienes = [("CPU 1" if auto else "Vos", auto), ("CPU 2" if auto else "CPU", True)]
        mazos = list(mazos) if mazos else [None, None]
        self.jugadores = []
        for i, (nombre, cpu) in enumerate(quienes):
            m = mazos[i] if i < len(mazos) else None
            if m is not None and validar_mazo(m):
                m = None
            if m is None and ui is not None:
                m = mazo_aleatorio()
            self.jugadores.append(Jugador(nombre, cpu=cpu, mazo=m))
        self.preparando = False
        self.terreno = None
        self.ganador = None
        self.terminado = False
        self.ui = ui

    def rival_de(self, jug):
        return self.jugadores[1 - self.jugadores.index(jug)]

    def actualizar_costos(self):
        for j in self.jugadores:
            for c in j.mano + j.mazo:
                c.costo = c.costo_base
            rebaja = any(c.nombre == "Nekoark" for c in j.en_juego())
            for c in j.en_juego():
                c.costo = 1 if rebaja and c.nombre in RELACIONADAS_MASIMO and c.costo_base > 1 else c.costo_base

    def snapshot(self, desde=None):
        self.actualizar_costos()
        humano = desde or next((j for j in self.jugadores if not j.cpu), self.jugadores[0])

        def datos(j, ver_mano):
            return dict(nombre=j.nombre, puntos=j.puntos, mazo=len(j.mazo), mano_n=len(j.mano),
                        energia_libre=j.energia_libre,
                        activa=datos_carta(j.activa) if j.activa and (ver_mano or not self.preparando) else None,
                        banca=[datos_carta(c) for c in j.banca],
                        mano=[datos_carta(c) for c in j.mano] if ver_mano else [])
        return dict(yo=datos(humano, True), rival=datos(self.rival_de(humano), False),
                    terreno=self.terreno, puntos_meta=PUNTOS_PARA_GANAR, terminado=self.terminado,
                    ganador=self.ganador.nombre if self.ganador else None)

    def actualizar(self, pausa=0):
        if self.ui:
            self.ui.actualizar({j.nombre: self.snapshot(j) for j in self.jugadores if not j.cpu}, pausa)

    def robar(self, jug, n=1):
        rival = self.rival_de(jug)
        for _ in range(n):
            if not jug.mazo:
                print(f"  ({jug.nombre} no tiene más cartas en el mazo)")
                return
            jug.mano.append(jug.mazo.pop())
            for t in rival.en_juego():
                if t.nombre == "Tay":
                    t.cargas = min(4, t.cargas + 1)

    def curar(self, c, n):
        c.ps = min(c.ps_max, c.ps + n)
        print(f"  {c.nombre} se cura {n} PS (PS {c.ps}/{c.ps_max})")

    def bonus(self, c):
        b = 0
        if self.terreno == "Ecuador" and c.nombre in ECUADOR:
            b += 20
        if c.equipo and c.equipo.nombre == "Mezcladora de Forten":
            b += 10
        if c.equipo and c.equipo.nombre == "Yamato":
            b += 20
        if c.equipo and c.equipo.nombre == "Hacha oxidada":
            b += 30 if c.nombre in ("Geeksito", "Chichotas") else 20
        return b + c.boost

    def atk(self, c, d, base):
        self.golpear(c, d, (base + self.bonus(c)) * mult(c, d))

    def golpear(self, c, d, bruto):
        yamato = c.equipo and c.equipo.nombre == "Yamato"
        dano = bruto + d.dano_extra - (0 if yamato else d.escudo)
        if c.embobado and not yamato:
            dano -= 20
        dano = max(0, int(dano))
        if d.inmune:
            dano = 0
        d.ps = max(0, d.ps - dano)
        print(f"  {c.nombre} hace {dano} de daño a {d.nombre} (PS {d.ps}/{d.ps_max})")

    def elegir_idx(self, etiquetas, titulo, jug=None):
        if self.ui:
            return self.ui.elegir(etiquetas, titulo, jug)
        print(titulo)
        for i, e in enumerate(etiquetas, 1):
            print(f"  {i}) {e}")
        while True:
            r = input("> ").strip()
            if r.isdigit() and 1 <= int(r) <= len(etiquetas):
                return int(r) - 1
            print("Opción inválida")

    def elegir(self, jug, opciones, titulo):
        if jug.cpu:
            return opciones[0]
        return opciones[self.elegir_idx([str(o) for o in opciones], titulo, jug)]

    def atacar(self, yo, rv):
        c, d = yo.activa, rv.activa
        print(f"\n{c.nombre} usa {c.habilidad}")
        if c.confundido:
            if random.random() < 0.5:
                print("  Confundido: sale cruz, el ataque falla")
                return
            print("  Confundido: sale cara, el ataque sigue")
        if d.nombre == "Masimo":
            if random.random() < 0.5:
                print("  Pendejo bts: sale cara, el ataque falla")
                return
            print(f"  Pendejo bts: sale cruz, el ataque hace {EXTRA_MASIMO} de daño más")
            d.dano_extra = EXTRA_MASIMO
        try:
            ATAQUES.get(c.nombre, a_basico)(self, yo, rv, c, d)
        finally:
            d.dano_extra = 0
        if c.equipo and c.equipo.nombre == "Hacha oxidada" and d.ps > 0 and d is rv.activa:
            d.veneno = 3
            print(f"  Gangrena: {d.nombre} queda envenenado (3 turnos)")
        if d.equipo and d.equipo.nombre == "El Chori":
            c.ps = max(0, c.ps - 20)
            print(f"  Capa de Grasa: {c.nombre} recibe 20 de daño (PS {c.ps}/{c.ps_max})")
        if d.equipo and d.equipo.nombre == "Pelota Esquizo" and d.ps > 0 and d is rv.activa and rv.banca and not d.embobado:
            b = self.elegir(rv, rv.banca, f"Pelota Esquizo: {d.nombre} se retira gratis, elegí tu nueva carta activa")
            d.turnos_activo = 0
            rv.banca.remove(b)
            rv.banca.append(d)
            rv.activa = b
            print(f"  Dribbleo: {rv.nombre} retira gratis a {d.nombre} y pone a {b.nombre} como activa")
        self.resolver_kos()

    def usar_cubo(self, yo):
        rv = self.rival_de(yo)
        yo.cubo_usado = True
        caras = sum(random.random() < 0.5 for _ in range(4))
        print(f"  Cubo de tetris: {caras} de 4 monedas salen cara")
        if caras == 4 and rv.activa:
            print(f"  ¡Línea completa! {rv.activa.nombre} queda eliminado")
            rv.activa.ps = 0
            self.resolver_kos()

    def resolver_kos(self):
        for jug in self.jugadores:
            rv = self.rival_de(jug)
            for c in list(jug.en_juego()):
                if c.ps <= 0:
                    print(f"  {c.nombre} fue derrotado: {rv.nombre} gana 1 punto")
                    rv.puntos += 1
                    jug.descarte.append(c)
                    if c.equipo:
                        jug.descarte.append(c.equipo)
                    if c is jug.activa:
                        jug.activa = None
                    else:
                        jug.banca.remove(c)
            if rv.puntos >= PUNTOS_PARA_GANAR and not self.ganador:
                self.ganador = rv
            if self.ganador or jug.activa:
                continue
            if jug.banca:
                self.actualizar()
                nueva = self.elegir(jug, jug.banca, f"{jug.nombre}: elegí tu nueva carta activa")
                jug.banca.remove(nueva)
                jug.activa = nueva
                nueva.turnos_activo = 0
            else:
                print(f"  {jug.nombre} se quedó sin cartas de combate en juego")
                self.ganador = rv

    def dar_energia(self, yo, c):
        c.energia += 1
        yo.energia_libre -= 1

    def bajar(self, yo, c):
        yo.mano.remove(c)
        yo.banca.append(c)
        print(f"  {yo.nombre} baja a {c.nombre} a la banca")

    def equipar(self, yo, e, t):
        yo.mano.remove(e)
        t.equipo = e
        if e.nombre == "Mezcladora de Forten":
            t.ps += 20
            t.ps_max += 20
        print(f"  {yo.nombre} equipa {e.nombre} a {t.nombre}")

    def apoyo(self, yo, c):
        yo.mano.remove(c)
        yo.descarte.append(c)
        yo.apoyo_usado = True
        print(f"  {yo.nombre} juega al Partidario {c.nombre}: {c.habilidad}")
        APOYOS.get(c.nombre, lambda *a: None)(self, yo, self.rival_de(yo))

    def poner_terreno(self, yo, c):
        yo.mano.remove(c)
        self.terreno = c.nombre
        print(f"  {yo.nombre} juega el Terreno {c.nombre}")
        if c.nombre == "Nido de la dragona chichona":
            self.nido_chichona()

    def nido_chichona(self):
        for j in self.jugadores:
            a = j.activa
            if not a:
                continue
            if a.nombre == "Masimo":
                a.boost += BOOST_NIDO
                print(f"  {a.nombre} aguanta el fuego y se boostea: +{BOOST_NIDO} de daño")
            else:
                a.quemadura = TURNOS_QUEMADURA
                print(f"  Quemadura: {a.nombre} se quema ({TURNOS_QUEMADURA} turnos)")

    def retirar(self, yo, b):
        a = yo.activa
        a.energia -= a.retirada
        a.turnos_activo = 0
        yo.banca.remove(b)
        yo.banca.append(a)
        yo.activa = b
        yo.retirada_usada = True
        print(f"  {yo.nombre} retira a {a.nombre} y pone a {b.nombre} como activa")

    def acciones(self, yo):
        self.actualizar_costos()
        acc = []
        if yo.energia_libre:
            for c in yo.en_juego():
                acc.append(("energia", f"Asignar energía a {c.nombre} ({c.energia}/{c.costo})",
                            lambda c=c: self.dar_energia(yo, c)))
        for c in list(yo.mano):
            if c.es_combate:
                if len(yo.banca) < TAM_BANCA:
                    acc.append(("jugar", f"Bajar a la banca: {c.nombre}", lambda c=c: self.bajar(yo, c)))
            elif c.categoria == "Equipable":
                for t in yo.en_juego():
                    if not t.equipo:
                        acc.append(("equipar", f"Equipar {c.nombre} a {t.nombre}",
                                    lambda c=c, t=t: self.equipar(yo, c, t)))
            elif c.categoria == "Partidario" and not yo.apoyo_usado:
                acc.append(("apoyo", f"Jugar Partidario: {c.nombre}", lambda c=c: self.apoyo(yo, c)))
            elif c.categoria == "Terreno":
                acc.append(("terreno", f"Jugar Terreno: {c.nombre}", lambda c=c: self.poner_terreno(yo, c)))
        if self.terreno == "Ecuador" and not yo.ecuador_usado and self.opciones_ecuador(yo):
            acc.append(("ecuador", "Ecuador: buscar en el mazo", lambda: self.buscar_ecuador(yo)))
        if yo.activa and yo.activa.equipo and yo.activa.equipo.nombre == "Cubo de tetris" and not yo.cubo_usado and self.rival_de(yo).activa:
            acc.append(("cubo", "Cubo de tetris: tirar 4 monedas", lambda: self.usar_cubo(yo)))
        a = yo.activa
        if yo.banca and not yo.retirada_usada and not a.embobado and a.energia >= a.retirada:
            for b in yo.banca:
                acc.append(("retirar", f"Retirar a {a.nombre} y poner a {b.nombre} (cuesta {a.retirada} energía)",
                            lambda b=b: self.retirar(yo, b)))
        if a.energia >= a.costo:
            acc.append(("atacar", f"Atacar: {a.habilidad}", None))
        if not self.ui:
            acc.append(("ver", "Ver una carta en imagen", lambda: self.ver_carta(yo)))
        acc.append(("pasar", "Pasar el turno", None))
        return acc

    def opciones_ecuador(self, yo):
        ops = [(f"Un {cat}", lambda c, cat=cat: c.categoria == cat) for cat in ("Atacante", "Tanque", "Mago")]
        if any(c.nombre == "Geeksito" for c in yo.en_juego()):
            ops += [(n, lambda c, n=n: c.nombre == n) for n in ("Chichotas", "Manuelito")]
        return [(t, f) for t, f in ops if any(f(c) for c in yo.mazo)]

    def buscar_ecuador(self, yo):
        ops = self.opciones_ecuador(yo)
        if yo.cpu:
            i = random.randrange(len(ops))
        else:
            i = self.elegir_idx([t for t, _ in ops], "Ecuador: ¿qué buscás en tu mazo?", yo)
        carta = next(c for c in reversed(yo.mazo) if ops[i][1](c))
        yo.mazo.remove(carta)
        yo.mano.append(carta)
        yo.ecuador_usado = True
        print(f"  {yo.nombre} busca en Ecuador: {carta.nombre} a la mano")
        for t in self.rival_de(yo).en_juego():
            if t.nombre == "Tay":
                t.cargas = min(4, t.cargas + 1)

    def ver_carta(self, yo):
        try:
            from visual import dibujar_carta
        except ImportError:
            print("  Para ver cartas instalá Pillow: pip install pillow")
            return
        cartas = yo.mano + yo.en_juego() + self.rival_de(yo).en_juego()
        c = self.elegir(yo, cartas, "¿Qué carta querés ver?")
        dibujar_carta(next(d for d in CARTAS if d["nombre"] == c.nombre)).show()

    def elegir_accion(self, yo, acc):
        if yo.cpu:
            for tipo in ("energia", "jugar", "equipar", "apoyo", "terreno", "ecuador", "cubo", "atacar", "pasar"):
                for a in acc:
                    if a[0] == tipo:
                        return a
        return acc[self.elegir_idx([a[1] for a in acc], "¿Qué hacés?", yo)]

    def mostrar(self, yo, rv):
        if self.ui:
            print(f"\n--- Turno de {yo.nombre} ---")
            return
        print(f"\n=== Turno de {yo.nombre} | Puntos {yo.puntos}-{rv.puntos} | Terreno: {self.terreno or '-'} ===")
        for j in (rv, yo):
            print(f"{j.nombre}: {j.activa}")
            for b in j.banca:
                print(f"   banca: {b}")
        if not yo.cpu:
            print("Tu mano:")
            for c in yo.mano:
                print("  -", c.en_mano())

    def guardar(self):
        ui, self.ui = self.ui, None
        try:
            objs, vistos, cola = [], set(), [self]
            atomicos = (str, int, float, bool, bytes, type(None), type, types.FunctionType,
                        types.MethodType, types.BuiltinFunctionType, types.ModuleType)
            while cola:
                o = cola.pop()
                if isinstance(o, atomicos) or id(o) in vistos:
                    continue
                vistos.add(id(o))
                if isinstance(o, dict):
                    cola.extend(o.values())
                    objs.append(o)
                elif isinstance(o, (list, set)):
                    cola.extend(o)
                    objs.append(o)
                elif isinstance(o, tuple):
                    cola.extend(o)
                elif hasattr(o, "__dict__"):
                    cola.extend(vars(o).values())
                    objs.append(o)
            memo = {}
            copy.deepcopy(self, memo)
            return [(o, memo[id(o)]) for o in objs if id(o) in memo]
        finally:
            self.ui = ui

    def restaurar(self, estado):
        inv = {id(c): o for o, c in estado}

        def m(v):
            if id(v) in inv:
                return inv[id(v)]
            if isinstance(v, tuple):
                return tuple(m(x) for x in v)
            return v

        for o, c in estado:
            if isinstance(o, list):
                o[:] = [m(x) for x in c]
            elif isinstance(o, dict):
                o.clear()
                o.update({k: m(x) for k, x in c.items()})
            elif isinstance(o, set):
                o.clear()
                o.update(m(x) for x in c)
            else:
                ui = o.__dict__.get("ui")
                o.__dict__.clear()
                o.__dict__.update({k: m(x) for k, x in c.__dict__.items()})
                if "ui" in c.__dict__:
                    o.__dict__["ui"] = ui

    def turno(self, yo, rv):
        yo.apoyo_usado = yo.retirada_usada = yo.ecuador_usado = yo.cubo_usado = False
        yo.energia_libre = ENERGIA_POR_TURNO
        if self.terreno == "Techo de Maxi" and yo.activa:
            cara = random.random() < 0.5
            print(f"  Techo de Maxi: sale {'cara' if cara else 'cruz'}")
            if not cara and yo.activa.energia > 0:
                yo.activa.energia -= 1
                print(f"  {yo.activa.nombre} pierde 1 energía por la inundación")
        self.robar(yo)
        yo.activa.turnos_activo += 1
        self.mostrar(yo, rv)
        pila = []
        while not self.ganador:
            self.actualizar(PAUSA_CPU if yo.cpu else 0)
            acc = self.acciones(yo)
            if pila and not yo.cpu:
                acc.append(("deshacer", "↩️ Deshacer la última jugada", None))
            tipo, _, fn = self.elegir_accion(yo, acc)
            if tipo == "deshacer":
                self.restaurar(pila.pop())
                print(f"↩️ {yo.nombre} deshizo su última jugada.")
                continue
            if tipo == "pasar":
                break
            if tipo == "atacar":
                self.atacar(yo, rv)
                break
            antes = None if yo.cpu else self.guardar()
            mazos = [len(j.mazo) for j in self.jugadores]
            fn()
            if antes is not None:
                if [len(j.mazo) for j in self.jugadores] != mazos:
                    pila.clear()
                else:
                    pila.append(antes)
                    del pila[:-20]
        for c in yo.en_juego():
            c.confundido = c.embobado = False
        envenenados = [c for c in yo.en_juego() if c.veneno]
        for c in envenenados:
            c.ps = max(0, c.ps - 10)
            c.veneno -= 1
            print(f"  Gangrena: {c.nombre} pierde 10 PS (PS {c.ps}/{c.ps_max})")
        quemados = [c for c in yo.en_juego() if c.quemadura]
        for c in quemados:
            c.ps = max(0, c.ps - DANO_QUEMADURA)
            c.quemadura -= 1
            print(f"  Quemadura: {c.nombre} pierde {DANO_QUEMADURA} PS (PS {c.ps}/{c.ps_max})")
        if envenenados or quemados:
            self.resolver_kos()
        for c in rv.en_juego():
            c.escudo = 0
            c.inmune = False
        self.actualizar(PAUSA_CPU if yo.cpu else 0)

    def preparar(self):
        for jug in self.jugadores:
            while True:
                random.shuffle(jug.mazo)
                if any(c.es_combate for c in jug.mazo[:MANO_INICIAL]):
                    break
            jug.mano, jug.mazo = jug.mazo[:MANO_INICIAL], jug.mazo[MANO_INICIAL:]
            if not jug.cpu and not self.ui:
                print("Tu mano inicial:")
                for c in jug.mano:
                    print("  -", c.en_mano())
            opciones = [c for c in jug.mano if c.es_combate]
            self.actualizar()
            jug.activa = self.elegir(jug, opciones, f"{jug.nombre}: elegí tu carta activa")
            jug.mano.remove(jug.activa)

    def tirar_moneda(self):
        llama = random.choice(self.jugadores)
        if llama.cpu:
            canto = random.choice(["Cara", "Cruz"])
        else:
            self.actualizar()
            canto = self.elegir(llama, ["Cara", "Cruz"], f"{llama.nombre}: 🪙 cantá cara o cruz para ver quién empieza")
        salio = random.choice(["Cara", "Cruz"])
        gana = llama if canto == salio else next(j for j in self.jugadores if j is not llama)
        print(f"\n🪙 {llama.nombre} canta {canto} y la moneda cae en {salio}: empieza {gana.nombre}.")
        return self.jugadores.index(gana)

    def jugar(self):
        self.preparando = True
        self.preparar()
        self.preparando = False
        i = self.tirar_moneda()
        for _ in range(MAX_TURNOS):
            if self.ganador:
                break
            self.turno(self.jugadores[i], self.jugadores[1 - i])
            i = 1 - i
        if self.ganador:
            print(f"\n¡Ganó {self.ganador.nombre}!")
        else:
            print("\nEmpate por límite de turnos")
        self.terminado = True
        self.actualizar()


if __name__ == "__main__":
    if "--consola" in sys.argv or "--auto" in sys.argv:
        Juego(auto="--auto" in sys.argv).jugar()
    else:
        import servidor
        servidor.main()
