import os
import random
import sys

from cartas import CARTAS

ENERGIA_POR_TURNO = 1
PUNTOS_PARA_GANAR = 3
TAM_BANCA = 3
MANO_INICIAL = 5
MAX_TURNOS = 150
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


class Carta:
    def __init__(self, datos):
        base = dict(rareza="", costo=0, retirada=0, ps=0, dano=0, full_art=False)
        base.update(datos)
        self.__dict__.update(base)
        self.ps_max = self.ps
        self.energia = 0
        self.equipo = None
        self.confundido = False
        self.embobado = False
        self.escudo = 0
        self.turnos_activo = 0
        self.cargas = 0

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
    def __init__(self, nombre, cpu=False):
        self.nombre = nombre
        self.cpu = cpu
        self.mazo = [Carta(d) for d in CARTAS]
        random.shuffle(self.mazo)
        self.mano = []
        self.activa = None
        self.banca = []
        self.puntos = 0
        self.energia_libre = 0
        self.apoyo_usado = False
        self.retirada_usada = False

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
    j.atk(c, d, c.dano + min(40, 10 * c.turnos_activo))


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
    c.escudo = 30


def a_poto(j, yo, rv, c, d):
    j.atk(c, d, c.dano)
    j.robar(yo, 1)


def a_geeksito(j, yo, rv, c, d):
    j.atk(c, d, c.dano + 20 * (len(yo.en_juego()) - 1))


ATAQUES = {
    "Chichotas": a_chichotas, "Spreenfino": a_spreenfino, "Kono": a_kono,
    "Aleseto": a_aleseto, "Tay": a_tay, "Manuelito": a_manuelito,
    "Mantekita": a_mantekita, "Tanque Bondiola": a_bondiola, "Dandyd": a_dandyd,
    "Larco": a_larco, "Mr Poto": a_poto, "Geeksito": a_geeksito,
}


def p_bocchi(j, yo, rv):
    j.robar(yo, 2)
    if yo.activa and yo.activa.nombre == "Aleseto":
        j.curar(yo.activa, 30)


def p_denji(j, yo, rv):
    for c in yo.en_juego():
        if c.nombre in ("Geeksito", "Chichotas", "Manuelito"):
            j.curar(c, 20)


APOYOS = {"Bocchi": p_bocchi, "Denji": p_denji}


def datos_carta(c):
    estados = [n for n, v in (("Confundido", c.confundido), ("Embobado", c.embobado)) if v]
    if c.escudo:
        estados.append(f"Escudo -{c.escudo}")
    if c.nombre == "Tay":
        estados.append(f"Cargas {c.cargas}")
    return dict(nombre=c.nombre, categoria=c.categoria, ps=c.ps, ps_max=c.ps_max,
                energia=c.energia, costo=c.costo,
                equipo=c.equipo.nombre if c.equipo else None, estados=estados)


class Juego:
    def __init__(self, auto=False, ui=None, pvp=False):
        if pvp:
            self.jugadores = [Jugador("Jugador 1"), Jugador("Jugador 2")]
        else:
            self.jugadores = [Jugador("CPU 1" if auto else "Vos", cpu=auto),
                              Jugador("CPU 2" if auto else "CPU", cpu=True)]
        self.preparando = False
        self.terreno = None
        self.ganador = None
        self.terminado = False
        self.ui = ui  # None = consola; con ui, las decisiones se toman en la ventana

    def rival_de(self, jug):
        return self.jugadores[1 - self.jugadores.index(jug)]

    def snapshot(self, desde=None):
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
        return b

    def atk(self, c, d, base):
        self.golpear(c, d, (base + self.bonus(c)) * mult(c, d))

    def golpear(self, c, d, bruto):
        dano = bruto - d.escudo
        if c.embobado:
            dano -= 20
        dano = max(0, int(dano))
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
        ATAQUES.get(c.nombre, a_basico)(self, yo, rv, c, d)
        if d.equipo and d.equipo.nombre == "El Chori":
            c.ps = max(0, c.ps - 20)
            print(f"  Capa de Grasa: {c.nombre} recibe 20 de daño (PS {c.ps}/{c.ps_max})")
        self.resolver_kos()

    def resolver_kos(self):
        for jug in self.jugadores:
            rv = self.rival_de(jug)
            for c in list(jug.en_juego()):
                if c.ps <= 0:
                    print(f"  {c.nombre} fue derrotado: {rv.nombre} gana 1 punto")
                    rv.puntos += 1
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
        yo.apoyo_usado = True
        print(f"  {yo.nombre} juega al Partidario {c.nombre}: {c.habilidad}")
        APOYOS.get(c.nombre, lambda *a: None)(self, yo, self.rival_de(yo))

    def poner_terreno(self, yo, c):
        yo.mano.remove(c)
        self.terreno = c.nombre
        print(f"  {yo.nombre} juega el Terreno {c.nombre}")

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
            for tipo in ("energia", "jugar", "equipar", "apoyo", "terreno", "atacar", "pasar"):
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

    def turno(self, yo, rv):
        yo.apoyo_usado = yo.retirada_usada = False
        yo.energia_libre = ENERGIA_POR_TURNO
        self.robar(yo)
        yo.activa.turnos_activo += 1
        self.mostrar(yo, rv)
        while not self.ganador:
            self.actualizar(PAUSA_CPU if yo.cpu else 0)
            tipo, _, fn = self.elegir_accion(yo, self.acciones(yo))
            if tipo == "pasar":
                break
            if tipo == "atacar":
                self.atacar(yo, rv)
                break
            fn()
        for c in yo.en_juego():
            c.confundido = c.embobado = False
        for c in rv.en_juego():
            c.escudo = 0
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

    def jugar(self):
        self.preparando = True
        self.preparar()
        self.preparando = False
        i = random.randint(0, 1)
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
