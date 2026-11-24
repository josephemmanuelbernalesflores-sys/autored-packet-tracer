"""Interprete v2: espanol coloquial -> parametros del motor. Sin IA: tolera tildes, typos y jerga."""
import re
import unicodedata
from difflib import get_close_matches

from catalogo import sugerir

NUM = {"un": 1, "una": 1, "uno": 1, "dos": 2, "par": 2, "tres": 3, "cuatro": 4, "cinco": 5,
       "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}
COSAS = {
    "routers": "router routers enrutador enrutadores ruteador ruteadores",
    "switches": "switch switches swich conmutador conmutadores",
    "pcs": "pc pcs compu compus computadora computadoras computador computadores maquina maquinas equipo equipos host hosts",
    "laptops": "laptop laptops portatil portatiles notebook",
    "servers": "servidor servidores server servers",
    "vlans": "vlan vlans",
    "aps": "ap aps antena antenas",
}
VOC = {w: k for k, ws in COSAS.items() for w in ws.split()}
MAPA = {"routers": "routers", "switches": "switches_per_router", "pcs": "pcs_per_lan",
        "laptops": "laptops_per_lan", "servers": "servers", "vlans": "vlans", "aps": "access_points"}
LIM = {"routers": (1, 20), "pcs_per_lan": (0, 20), "switches_per_router": (1, 10), "servers": (0, 10),
       "vlans": (1, 8), "laptops_per_lan": (0, 10), "access_points": (0, 10)}


WANP = (r"\bwan\b", r"internet", r"\bisp\b", r"\bnat\b", r"\bpat\b")


def norm(t):
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"puntos? de acceso|access points?", "ap", t)
    return re.sub(r"[^a-z0-9 ]+", " ", t)


def cosa(w):
    if w in VOC:
        return VOC[w]
    if len(w) >= 4:
        m = get_close_matches(w, [v for v in VOC if len(v) >= 4], n=1, cutoff=0.8)
        if m:
            return VOC[m[0]]


def conteos(tk):
    out, i = {}, 0
    while i < len(tk):
        n = int(tk[i]) if tk[i].isdigit() else NUM.get(tk[i])
        if n is not None and n <= 100:
            j = i + 1
            if tk[i] == "un" and j < len(tk) and tk[j] == "par":
                n, j = 2, j + 1
            if j < len(tk) and tk[j] in ("de", "del"):
                j += 1
            k = cosa(tk[j]) if j < len(tk) else None
            if k:
                out[k], i = n, j
        i += 1
    return out


def interpretar(texto):
    t = norm(texto)
    c = conteos(t.split())
    a, notas, err = {MAPA[k]: v for k, v in c.items()}, [], []
    has = lambda *p: any(re.search(x, t) for x in p)
    routers = a.get("routers", 0)
    explicit = True
    if has(r"\banillo\b", r"\bring\b", r"\bmalla\b", r"\bmesh\b", r"\bbus\b", r"\barbol\b", r"\btree\b"):
        notas.append("Anillo, malla, bus y arbol aun no estan soportados (Fase 3): se usa una red multi-LAN.")
        a["template"] = "multi_lan"
    elif has(r"estrella", r"\bstar\b"):
        a["template"] = "hub_spoke" if routers > 1 else "star"
        if routers > 1:
            notas.append("Estrella con varios routers = 'hub_spoke' (1 router central y el resto como ramas).")
    elif has(r"triangulo"):
        a["template"] = "three_router_triangle"
    elif has(r"\bhub\b", r"spoke"):
        a["template"] = "hub_spoke"
    elif has(r"sucursal", r"branch"):
        a["template"] = "branch_office"
    elif has(r"on a stick", r"vlan"):
        a["template"] = "router_on_a_stick"
    elif has(*WANP):
        a["template"] = "multi_lan_wan"
    elif routers > 1:
        a["template"] = "multi_lan"
    else:
        a["template"], explicit = "single_lan", bool(c)
    tpl = a["template"]
    if has(*WANP):
        a["has_wan"] = True
    if tpl in ("single_lan", "router_on_a_stick"):
        a["routers"] = 1
    elif tpl == "star":
        a.pop("routers", None)
    elif tpl == "three_router_triangle":
        a["routers"] = 3
    elif tpl in ("multi_lan", "multi_lan_wan"):
        a.setdefault("routers", 2)
    if has(r"vlan") and "vlans" not in a:
        a["vlans"] = 2
    if a.get("vlans") and tpl != "router_on_a_stick":
        notas.append("Las VLANs solo se aplican con 'router_on_a_stick'; en esta topologia se ignoran.")
        a.pop("vlans")
    for p in ("ospf", "eigrp", "rip"):
        if has(rf"\b{p}\b"):
            a["routing"], explicit = p, True
    if has(r"estatic", r"\bstatic\b"):
        a["routing"], explicit = "static", True
    if has(r"sin (enrutamiento|routing|rutas)"):
        a["routing"] = "none"
    if has(r"sin dhcp"):
        a["dhcp"] = False
    if has(r"ipv6", r"dual stack"):
        a["dual_stack"] = True
    if has(r"flotante", r"respaldo", r"backup"):
        a["floating_routes"] = True
    if has(r"wifi", r"wi fi", r"inalambric", r"wireless"):
        a["wireless_laptops"] = True
        a.setdefault("access_points", 1)
        a.setdefault("laptops_per_lan", 1)
    m = re.search(r"\b(1941|2901|2911)\b|isr ?4321", t)
    if m:
        a["router_model"] = "ISR4321" if "isr" in m.group(0) else m.group(0)
    if has(r"\b3560\b"):
        a["switch_model"] = "3560-24PS"
    if tpl == "star" and a.get("pcs_per_lan"):
        notas.append("En 'star' el motor puso los PCs solo en el primer switch; revisa el diagrama.")
    if a.get("routing") in ("ospf", "eigrp", "rip") and a.get("routers", 1) <= 1 and tpl in ("single_lan", "star", "router_on_a_stick"):
        notas.append(f"{a['routing'].upper()} necesita al menos 2 routers para intercambiar rutas; con 1 router se omite.")
        a.pop("routing")
    if has(r"\bnat\b", r"\bpat\b"):
        a["nat_pat"] = True
    if has(r"aisla", r"bloquea", r"\bacl\b", r"incomunica"):
        if tpl in ("single_lan", "router_on_a_stick"):
            notas.append("Aislar una LAN necesita al menos 2 LANs con router; en esta topologia se ignora.")
        else:
            a["aislar_ultima_lan"] = True
    if has(r"hardening", r"seguridad basica", r"asegura", r"contrasena", r"\bbanner\b"):
        a["seguridad_basica"] = True
    if has(r"port security", r"seguridad de puerto", r"seguridad en puerto"):
        a["port_security"] = True
    for k, (lo, hi) in LIM.items():
        if k in a and not lo <= a[k] <= hi:
            err.append(f"{k} debe estar entre {lo} y {hi} (recibi {a[k]}).")
    if not explicit:
        err.append("No reconoci una topologia ni cantidades. Prueba: 'red estrella con 3 switches y 2 pcs'.")
    return {"params": a, "notas": notas, "errores": err, "ok": not err, "labs": sugerir(t)}