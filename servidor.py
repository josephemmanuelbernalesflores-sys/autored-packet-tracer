"""AutoRed v2: servidor web + puente persistente al motor MCP (packet-tracer-mcp).
Uso: py -3.12 servidor.py  ->  http://127.0.0.1:5000   (Packet Tracer abierto con Extensions > MCP BUILDER)"""
import asyncio
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import catalogo
from interprete import interpretar

PERMITIDOS = {"routers", "pcs_per_lan", "laptops_per_lan", "switches_per_router", "servers", "access_points",
              "has_wan", "dhcp", "routing", "router_model", "switch_model", "template", "floating_routes",
              "ospf_process_id", "eigrp_as", "vlans", "dual_stack", "ipv6_base", "wireless_laptops"}

EXTRAS = {"aislar_ultima_lan", "nat_pat", "seguridad_basica", "port_security"}  # pasos que AutoRed aplica despues de construir


CFG = Path(__file__).with_name("config.json")


def cargar_cfg():
    try:
        return json.loads(CFG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def carpeta_proyectos():
    return cargar_cfg().get("carpeta", "")


def comando():
    carpeta = carpeta_proyectos()  # el motor exporta sus archivos a PT_MCP_WORKDIR
    env = {**os.environ, "PT_MCP_WORKDIR": carpeta} if carpeta else None
    if os.environ.get("AUTORED_SERVER"):
        return StdioServerParameters(command=os.environ["AUTORED_SERVER"], args=[], env=env)
    uvx = shutil.which("uvx") or str(Path.home() / ".local" / "bin" / "uvx.exe")
    if os.path.exists(uvx):
        return StdioServerParameters(command=uvx, args=["packet-tracer-mcp", "--stdio"], env=env)
    return StdioServerParameters(command=sys.executable, args=["-m", "packet_tracer_mcp", "--stdio"], env=env)


class Motor:
    """Mantiene UNA sesion MCP abierta en un hilo propio (arranque lento solo la primera vez)."""

    def __init__(self):
        self.loop, self.sesion, self.error = asyncio.new_event_loop(), None, None
        self.listo, self.parar = threading.Event(), None
        threading.Thread(target=lambda: (asyncio.set_event_loop(self.loop), self.loop.run_until_complete(self._vivir())),
                         daemon=True).start()

    async def _vivir(self):
        self.parar = asyncio.Event()
        try:
            async with stdio_client(comando()) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    self.sesion = s
                    self.listo.set()
                    await self.parar.wait()
        except Exception as e:  # el error se muestra en la interfaz
            sub = getattr(e, "exceptions", None) or [e]  # desenvuelve ExceptionGroup
            self.error = "; ".join(f"{type(x).__name__}: {x}" for x in sub)
            self.listo.set()

    def cerrar(self):
        if self.parar:
            self.loop.call_soon_threadsafe(self.parar.set)

    async def _call(self, herramienta, args):
        r = await self.sesion.call_tool(herramienta, args)
        return "\n".join(c.text for c in r.content if getattr(c, "type", "") == "text")

    def llamar(self, herramienta, args, timeout=240):
        if not self.listo.wait(120) or not self.sesion:
            raise RuntimeError(self.error or "El motor MCP no arranco (revisa uvx / packet-tracer-mcp).")
        return asyncio.run_coroutine_threadsafe(self._call(herramienta, args), self.loop).result(timeout)


_motor = None
_cerrojo = threading.Lock()


def motor():
    global _motor
    with _cerrojo:
        if _motor is None:
            _motor = Motor()
    return _motor


def reiniciar_motor():
    global _motor
    with _cerrojo:
        if _motor:
            _motor.cerrar()
            time.sleep(1.5)  # deja liberar el puerto del puente antes de arrancar otro
        _motor = Motor()


app = Flask(__name__, static_folder="static")


@app.get("/")
def inicio():
    return send_from_directory("static", "autored.html")


@app.post("/api/interpretar")
def api_interpretar():
    return jsonify(interpretar((request.get_json(silent=True) or {}).get("texto", "")))


@app.get("/api/estado")
def api_estado():
    try:
        return jsonify({"motor": True, "texto": motor().llamar("pt_bridge_status", {}, 150)[:400]})
    except Exception as e:
        return jsonify({"motor": False, "texto": str(e)})


@app.post("/api/construir")
def api_construir():
    d = request.get_json(silent=True) or {}
    args = {k: v for k, v in (d.get("params") or {}).items() if k in PERMITIDOS}
    args["deploy"] = bool(d.get("deploy", True))
    try:
        texto = motor().llamar("pt_full_build", args)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    plan = next((json.loads(l) for l in texto.splitlines() if l.startswith('{"name"')), None)
    if plan:
        _aplicar_ips_vivas(plan, texto)  # usa las IP reales que entrego DHCP, no las previstas
    g = lambda p: (re.search(p, texto) or [None, None])[1]
    resumen = {"dispositivos": g(r"Devices: (\d+)"), "enlaces": g(r"Links: (\d+)"),
               "validacion": "PASS" if "Validation: PASS" in texto else ("FAIL" if "Validation" in texto else "?"),
               "desplegado": "deployed to Packet Tracer" in texto}
    quiero = {k for k in EXTRAS if (d.get("params") or {}).get(k)}
    extra = pasos_extra(plan, quiero, not args["deploy"]) if plan and quiero else []
    return jsonify({"ok": True, "texto": texto, "plan": plan, "resumen": resumen, "params": args, "extras": extra})


def evaluar(t):
    m = re.search(r"Received\s*=\s*(\d+)", t) or re.search(r"Success rate is (\d+)", t)
    return int(m.group(1)) > 0 if m else None  # None = no pude interpretar la respuesta


def _perdida(t):
    """(enviados, recibidos) de un ping de Packet Tracer, o None si no se puede leer."""
    m = re.search(r"Sent\s*=\s*(\d+),\s*Received\s*=\s*(\d+)", t)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _recorta(t, cumple, calidad=""):
    """Guarda la respuesta completa de lo que falla o pierde paquetes (para diagnosticar) y recorta lo que sale bien."""
    return t[:3000] if (cumple is not True or calidad == "parcial") else t[:300]


def _medir(x, esperado, esperar):
    """Mide una prueba y la clasifica. Devuelve (ok, texto, calidad).
    calidad (solo pings que deben responder): 'completo' = sin perdidas; 'tras_perdida' = perdio paquetes al
    principio (ARP, STP) y la repeticion salio completa; 'parcial' = sigue perdiendo paquetes; '' = otra prueba."""
    ok, t = _probar(x)
    hubo_fallo = False
    reintentos = 2 if x.get("metodo") in ("cli", "web") else 1  # un show puede necesitar mas tiempo que un ping
    while ok is False and esperado and reintentos > 0:
        hubo_fallo = True
        esperar()
        ok, t = _probar(x)
        reintentos -= 1
    calidad = ""
    if x.get("metodo", "ping") == "ping" and ok and esperado:
        pr = _perdida(t)
        if pr and pr[1] >= pr[0]:
            calidad = "tras_perdida" if hubo_fallo else "completo"
        elif pr:
            textos = [t]
            for _ in range(2):  # hasta 2 repeticiones: STP, OSPF o ARP pueden tardar en estabilizarse
                esperar()
                ok2, t2 = _probar(x)
                textos.append(t2)
                pr2 = _perdida(t2)
                if ok2 and pr2 and pr2[1] >= pr2[0]:
                    calidad, t = "tras_perdida", t2
                    break
            else:
                calidad, t = "parcial", "\n--- reintento ---\n".join(textos)
    return ok, t, calidad


def _aplicar_ips_vivas(plan, texto):
    m = re.search(r"DHCP leases \(live[^)]*\):\s*(.+)", texto)
    if not m:
        return
    vivas = dict(re.findall(r"(\w+)\s+(\d+\.\d+\.\d+\.\d+)", m.group(1)))
    for x in plan.get("devices", []):
        ifs, ip = x.get("interfaces") or {}, vivas.get(x["name"])
        if ip and ifs:
            k = next(iter(ifs))
            ifs[k] = f"{ip}/{ifs[k].split('/')[1] if '/' in ifs[k] else 24}"


def _net(s):
    return ipaddress.ip_interface(s).network


def _wild(ip):
    n = _net(ip)
    return f"{n.network_address} {n.hostmask}"


def lans(plan):
    """LANs del plan: (router, puerto, 'ip/prefijo') de cada interfaz router->switch."""
    d = {x["name"]: x for x in plan.get("devices", [])}
    out = []
    for l in plan.get("links", []):
        for r, p, sw in ((l["device_a"], l["port_a"], l["device_b"]), (l["device_b"], l["port_b"], l["device_a"])):
            if d.get(r, {}).get("category") == "router" and d.get(sw, {}).get("category") == "switch" and d[r]["interfaces"].get(p):
                out.append((r, p, d[r]["interfaces"][p]))
    return out


def _resumen(t):
    try:
        return str(json.loads(t).get("summary", t)).split("\n")[0]
    except ValueError:
        return t


def _cli(t):
    try:
        return "\n".join(json.loads(t).get("cli_lines", []))
    except (ValueError, AttributeError):
        return ""


def pasos_extra(plan, quiero, dry):
    """Cada paso = (nombre, [(herramienta, argumentos), ...], aviso). En modo prueba solo se valida (dry_run)."""
    L, d, pasos = lans(plan), {x["name"]: x for x in plan.get("devices", [])}, []
    if "aislar_ultima_lan" in quiero:
        if len(L) < 2:
            pasos.append(("ACL: aislar la ultima LAN", [], "Necesita al menos 2 LANs con router y switch."))
        else:
            r, p, ip = L[-1]
            ent = [{"action": "deny", "protocol": "ip", "source": _wild(ip), "destination": _wild(o[2])} for o in L[:-1]]
            ent.append({"action": "permit", "protocol": "ip", "source": "any", "destination": "any"})
            pasos.append(("ACL: aislar la ultima LAN", [("pt_apply_acl", {"router": r, "name_or_number": "AISLAR", "acl_type": "extended",
                          "entries": ent, "binding_interface": p, "binding_direction": "in"})], ""))
    if "nat_pat" in quiero:
        wan = next(((l["device_b"], l["port_b"]) if d[l["device_a"]]["category"] == "cloud" else (l["device_a"], l["port_a"])
                    for l in plan.get("links", []) if "cloud" in (d[l["device_a"]]["category"], d[l["device_b"]]["category"])), None)
        propias = [o for o in L if wan and o[0] == wan[0]]
        if not wan or not propias:
            pasos.append(("NAT/PAT", [], "Necesita una nube (WAN) y un router con LAN conectado a ella."))
        else:
            pasos.append(("NAT/PAT", [("pt_apply_nat", {"router": wan[0], "mode": "pat", "inside_interface": propias[0][1],
                          "outside_interface": wan[1], "inside_networks": [_wild(o[2]) for o in L], "use_interface_overload": True})], ""))
    if "seguridad_basica" in quiero:
        eq = [x["name"] for x in plan.get("devices", []) if x.get("category") in ("router", "switch")]
        pasos.append(("Seguridad basica: banner, enable secret (Cisco123) y cifrado de claves", [("pt_apply_hardening",
                      {"device": n, "banner_motd": "Acceso solo a personal autorizado", "enable_secret": "Cisco123",
                       "service_password_encryption": True}) for n in eq], "No hay routers ni switches."))
    if "port_security" in quiero:
        pu = [(sw, p) for l in plan.get("links", []) for sw, p, h in ((l["device_a"], l["port_a"], l["device_b"]), (l["device_b"], l["port_b"], l["device_a"]))
              if d.get(sw, {}).get("category") == "switch" and d.get(h, {}).get("category") in ("pc", "laptop", "server")]
        pasos.append((f"Port security (max 1 MAC, sticky) en {len(pu)} puertos de acceso", [("pt_apply_port_security",
                      {"switch": s, "port": p, "max_mac": 1, "violation": "restrict", "sticky": True}) for s, p in pu], "No hay puertos de acceso con hosts."))
    res = []
    for nombre, llamadas, aviso in pasos:
        if not llamadas:
            res.append({"nombre": nombre, "ok": False, "texto": aviso})
            continue
        sub, pend, cli = [], False, []
        for h, a in llamadas:
            try:
                t = motor().llamar(h, {**a, "dry_run": True} if dry else a, 120)
            except Exception as e:
                sub.append((False, str(e)))
                continue
            if dry and "NOT_FOUND" in t:  # en modo prueba los equipos aun no existen en Packet Tracer
                pend = True
                continue
            sub.append((not re.search(r'"valid"\s*:\s*false|^\W*(error|fail)', t, re.I), _resumen(t)))
            cli.append(_cli(t))
        malos = [t for ok, t in sub if not ok]
        if pend and not sub:
            res.append({"nombre": nombre, "ok": None, "texto": "Pendiente: en modo prueba los equipos aun no existen en Packet Tracer; se aplicara al construir de verdad."})
        else:
            txt = malos[0] if malos else (sub[0][1] if len(sub) == 1 else f"{len(sub)} elementos {'validados' if dry else 'configurados'}.")
            res.append({"nombre": nombre, "ok": not malos, "texto": txt[:300], "cli": "\n\n".join(c for c in cli if c)})
    return res


def pruebas(plan, aislar=False):
    """Gateway de cada LAN, LAN a LAN (un host por LAN) y dentro de cada LAN. 'esperado' = si debe responder."""
    ip = lambda x: next((v for v in x.get("interfaces", {}).values() if v), "")
    hosts = [x for x in plan.get("devices", []) if x.get("category") in ("pc", "laptop", "server") and ip(x)]
    por = {}
    for h in hosts:
        por.setdefault(_net(ip(h)), []).append(h)
    gr = list(por.values())
    iso = _net(lans(plan)[-1][2]) if aislar and lans(plan) else None
    esp = lambda a, dst: not (iso and ((_net(ip(a)) == iso) != (ipaddress.ip_address(dst) in iso)))
    out = [(g[0], g[0]["gateway"], "gateway") for g in gr if g[0].get("gateway")]
    out += [(a[0], ip(b[0]).split("/")[0], "LAN a LAN") for i, a in enumerate(gr) for b in gr[i + 1:]]
    out += [(g[0], ip(g[1]).split("/")[0], "misma LAN") for g in gr if len(g) > 1]
    if iso in por:  # tambien desde la LAN aislada hacia las demas
        out += [(por[iso][0], ip(g[0]).split("/")[0], "desde la LAN aislada") for g in gr if _net(ip(g[0])) != iso]
    return [(h["name"], dst, t, esp(h, dst)) for h, dst, t in out][:24]


def _probar(x):
    """Una prueba: ping (por defecto), acceso web (pt_web_browser) o estado de un protocolo (salida de un show)."""
    if x.get("metodo") == "cli":
        t = motor().llamar("pt_cli", {"device": x["de"], "commands": ["enable", x["a"]], "timeout": 30}, 60)
        return len(re.findall(x["patron"], t, re.M)) >= x.get("minimo", 1), t
    if x.get("metodo") == "web":
        t = motor().llamar("pt_web_browser", {"device": x["de"], "url": x["a"], "timeout": 15}, 60)
        return x.get("contiene", "") in t, t
    t = motor().llamar("pt_verify_connectivity", {"from_device": x["de"], "to_ip": x["a"], "count": 4}, 90)
    return evaluar(t), t


@app.post("/api/verificar")
def api_verificar():
    d = request.get_json(silent=True) or {}
    res = []
    lista = d["pruebas"] if d.get("pruebas") else [{"de": a, "a": b, "tipo": t, "esperado": e}
                                                   for a, b, t, e in pruebas(d.get("plan") or {}, bool(d.get("aislar")))]
    for x in lista:
        esperado = x.get("esperado", True)
        calidad = ""
        try:
            ok, t, calidad = _medir(x, esperado, lambda: time.sleep(3))  # el primer ping puede perderse por ARP o por STP
        except Exception as e:
            t, ok = str(e), None
        cumple = None if ok is None else ok == esperado
        res.append({"de": x["de"], "a": x["a"], "tipo": x.get("tipo", "ping"), "ok": ok, "esperado": esperado,
                    "cumple": cumple, "calidad": calidad, "texto": _recorta(t, cumple, calidad)})
    return jsonify({"resultados": res, "total": len(res), "ok": sum(1 for r in res if r["cumple"]),
                    "con_perdida": sum(1 for r in res if r["calidad"] in ("tras_perdida", "parcial"))})


@app.post("/api/guardar")
def api_guardar():
    nombre = re.sub(r"[^\w\-]", "_", str((request.get_json(silent=True) or {}).get("nombre") or "autored"))[:60]
    try:
        return jsonify({"ok": True, "texto": motor().llamar("pt_save_project", {"filename": nombre, "directory": carpeta_proyectos()}, 60)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


_VENTANA = {"ultimo": time.time(), "cierre": None, "visto": False}


@app.post("/api/latido")
def api_latido():
    _VENTANA.update(ultimo=time.time(), cierre=None, visto=True)
    return jsonify({"ok": True})


@app.post("/api/cerrar")
def api_cerrar():
    _VENTANA["cierre"] = time.time()
    return jsonify({"ok": True})


@app.get("/api/ventana")
def api_ventana():
    t = time.time()
    return jsonify({"visto": _VENTANA["visto"], "silencio": t - _VENTANA["ultimo"],
                    "cierre": None if _VENTANA["cierre"] is None else t - _VENTANA["cierre"]})


@app.post("/api/limpiar")
def api_limpiar():
    plan = (request.get_json(silent=True) or {}).get("plan") or {}
    borrados, errores, motivo = 0, [], ""
    for x in plan.get("devices", []):
        try:
            t = motor().llamar("pt_delete_device", {"device_name": x["name"]}, 30)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        if "deleted from the topology" in t:
            borrados += 1
        else:
            errores.append(x["name"])
            motivo = t.strip().splitlines()[0] if t.strip() else "sin respuesta"
    if errores and not borrados:
        return jsonify({"ok": False, "error": f"No se borro ningun equipo: {motivo}"}), 500
    return jsonify({"ok": True, "borrados": borrados, "errores": errores})


def _red_de(plan, ip):
    """Red (del plan) que contiene la IP indicada."""
    dest = ipaddress.ip_address(ip)
    for dv in plan.get("devices", []):
        for cidr in (dv.get("interfaces") or {}).values():
            try:
                if dest in _net(cidr):
                    return _net(cidr)
            except ValueError:
                continue
    return None


@app.post("/api/diagnostico")
def api_diagnostico():
    """Orientativo: prueba el gateway del origen y busca la red de destino en las tablas de rutas."""
    d = request.get_json(silent=True) or {}
    plan, hallazgos = d.get("plan") or {}, []
    try:
        rutas = motor().llamar("pt_read_routes", {}, 60)
    except Exception as e:
        return jsonify({"hallazgos": [f"No pude leer las tablas de rutas de Packet Tracer: {e}"]})
    if re.search(r"convergence_gaps\W+\[\s*\{", rutas):
        hallazgos.append("Algunos routers aun no conocen todos los prefijos (convergence_gaps): espera unos segundos y vuelve a verificar.")
    for f in (d.get("fallos") or [])[:6]:
        origen, destino = f.get("de"), f.get("a")
        host = next((x for x in plan.get("devices", []) if x["name"] == origen), {})
        if host.get("gateway"):
            try:
                gw_ok = evaluar(motor().llamar("pt_verify_connectivity", {"from_device": origen, "to_ip": host["gateway"], "count": 2}, 60))
            except Exception:
                gw_ok = None
            if gw_ok is False:
                hallazgos.append(f"{origen} no alcanza ni su propio gateway ({host['gateway']}): revisa cable, VLAN, IP o espera a que STP converja.")
                continue
        try:
            red = _red_de(plan, destino)
        except ValueError:
            red = None
        if red is None:
            hallazgos.append(f"No encuentro a que red pertenece {destino} en el plan.")
        elif str(red.network_address) not in rutas:
            hallazgos.append(f"Ningun router lista la red {red} en su tabla de rutas: falta una ruta o el protocolo de enrutamiento no convergio (para {origen} -> {destino}).")
        else:
            hallazgos.append(f"La red {red} si aparece en las rutas; para {origen} -> {destino} revisa una ACL, el NAT o el gateway del destino.")
    return jsonify({"hallazgos": hallazgos or ["No hay fallos que diagnosticar."]})


@app.post("/api/lecturas")
def api_lecturas():
    """Lee de Packet Tracer el estado real de los protocolos que usa la red (rutas, OSPF, NAT, ACL, STP, VLAN)."""
    d = request.get_json(silent=True) or {}
    plan = d.get("plan") or {}
    cli = plan.get("extra_cli") or {}
    pedidos = [("Tablas de rutas (show ip route)", "pt_read_routes", {})]
    if plan.get("ospf_configs") or any("router ospf" in " ".join(v) for v in cli.values()):
        pedidos.append(("Vecinos y estado de OSPF", "pt_read_ospf", {}))
    for n, lineas in cli.items():
        t = " ".join(lineas)
        if "ip nat" in t:
            pedidos.append((f"NAT en {n} (show ip nat translations)", "pt_read_nat", {"device": n}))
        if "access-list" in t or "access-group" in t:
            pedidos.append((f"ACL en {n}", "pt_read_acl", {"device": n}))
        if "spanning-tree" in t:
            pedidos.append((f"STP en {n}", "pt_read_stp", {"switch": n}))
    if plan.get("vlans"):
        pedidos += [(f"VLAN en {x['name']}", "pt_read_vlans", {"switch": x["name"]}) for x in plan.get("devices", []) if x.get("category") == "switch"]
    secciones = []
    for titulo, herramienta, args in pedidos[:10]:
        try:
            texto = motor().llamar(herramienta, args, 60)
        except Exception as e:
            texto = f"No se pudo leer: {e}"
        secciones.append({"titulo": titulo, "texto": texto[:2500]})
    for x in (d.get("lecturas") or [])[:6]:  # comandos show propios de cada laboratorio
        try:
            texto = motor().llamar("pt_cli", {"device": x["dispositivo"], "commands": ["enable", x["comando"]], "timeout": 30}, 60)
        except Exception as e:
            texto = f"No se pudo leer: {e}"
        secciones.append({"titulo": x["titulo"] + f" ({x['comando']})", "texto": texto[:2500]})
    return jsonify({"secciones": secciones})


@app.post("/api/reiniciar")
def api_reiniciar():
    reiniciar_motor()
    return jsonify({"ok": True})


# ---------- Autoverificacion del catalogo (se ejecuta en un hilo y se consulta por sondeo) ----------
_AUTO = {"estado": "inactivo", "actual": "", "hecho": 0, "total": 0, "resultados": [], "log": [], "cancelar": False}
_AUTO_LOCK = threading.Lock()


def _auto_log(msg):
    _AUTO["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
    del _AUTO["log"][:-80]


def _dormir(seg):
    if _AUTO["cancelar"]:
        raise InterruptedError("cancelado")
    time.sleep(seg)


def _avanzar(veces=4):
    """Adelanta el reloj de Packet Tracer (convergencia de STP, OSPF, HSRP...) sin esperar en tiempo real."""
    for _ in range(veces):
        try:
            motor().llamar("pt_fast_forward", {}, 30)
        except Exception:
            pass
        _dormir(1)


def _correr_pruebas(lista):
    res = []
    for x in lista:
        esperado = x.get("esperado", True)
        calidad = ""
        try:
            ok, t, calidad = _medir(x, esperado, lambda: _avanzar(1))
        except InterruptedError:
            raise
        except Exception as e:
            ok, t = None, str(e)
        cumple = None if ok is None else ok == esperado
        res.append({"de": x["de"], "a": x["a"], "tipo": x.get("tipo", "ping"), "cumple": cumple, "calidad": calidad, "texto": _recorta(t, cumple, calidad)})
    return res


def _hasta(lista, objetivo, rondas=3):
    """Repite las pruebas hasta que (todas cumplen) == objetivo, adelantando el reloj entre rondas."""
    res = []
    for i in range(rondas + 1):
        res = _correr_pruebas(lista)
        if all(x["cumple"] for x in res) == objetivo or i == rondas:
            break
        _avanzar()
    return res


def _auto_lab(e, con_fallas):
    r = {"id": e["id"], "titulo": e["titulo"], "nivel": e["nivel"], "construccion": False, "pruebas": [], "fallas": [], "error": ""}
    p = e["gen"]()
    dep = motor().llamar("pt_live_deploy", {"plan_json": json.dumps(p.d)}, 300)
    if not re.search(r"deployed", dep, re.I) or re.search(r"not (connected|deployed)", dep, re.I):
        r["error"] = (dep.strip().splitlines() or ["sin respuesta"])[0]
        return r
    r["construccion"] = True
    for _, herramienta, args in p.post:
        try:
            motor().llamar(herramienta, args, 60)
        except Exception as ex:
            r["error"] = f"paso posterior: {ex}"
    _avanzar(5)
    r["pruebas"] = _hasta(p.pruebas, True)
    if con_fallas:
        for f in e["fallas"]:
            _dormir(0)
            fr = {"id": f["id"], "titulo": f["titulo"], "efecto": False, "reparada": False, "errores": [], "detalle": []}
            try:
                fr["errores"] += _aplicar_falla(f, True)
                _avanzar()
                objetivo = bool(f["pruebas"] or f.get("sigue"))  # True = la red debe seguir bien (redundancia)
                res = _hasta(f["pruebas"] or p.pruebas, objetivo)
                fr["efecto"] = all(x["cumple"] for x in res) == objetivo
                fr["detalle"] = [f"{x['de']} -> {x['a']}: {x['texto'][:400]}" for x in res if not x["cumple"]][:3]
                fr["errores"] += _aplicar_falla(f, False)
                _avanzar()
                fr["reparada"] = all(x["cumple"] for x in _hasta(p.pruebas, True))
            except InterruptedError:
                raise
            except Exception as ex:
                fr["errores"].append(str(ex))
            r["fallas"].append(fr)
    return r


def _limpiar_lab(e):
    for x in e["gen"]().d["devices"]:
        try:
            motor().llamar("pt_delete_device", {"device_name": x["name"]}, 30)
        except Exception:
            pass


def _auto_hilo(labs, con_fallas):
    estado = "terminado"
    try:
        for e in labs:
            if _AUTO["cancelar"]:
                estado = "cancelado"
                break
            _AUTO["actual"] = e["titulo"]
            _auto_log(f"Construyendo y probando: {e['titulo']}")
            try:
                r = _auto_lab(e, con_fallas)
            except InterruptedError:
                estado = "cancelado"
                break
            except Exception as ex:
                r = {"id": e["id"], "titulo": e["titulo"], "nivel": e["nivel"], "construccion": False, "pruebas": [], "fallas": [], "error": str(ex)}
            if r["construccion"]:
                _limpiar_lab(e)  # solo si lo construyo AutoRed: nunca se borra algo del usuario
            _AUTO["resultados"].append(r)
            _AUTO["hecho"] += 1
            if "not connected" in r["error"]:
                estado = "error"
                _auto_log("Packet Tracer no esta conectado: se detiene la autoverificacion.")
                break
    finally:
        _AUTO.update(estado=estado, actual="")


@app.post("/api/auto/iniciar")
def api_auto_iniciar():
    d = request.get_json(silent=True) or {}
    alcance = d.get("alcance", "todos")
    labs = [e for e in catalogo.CATALOGO if e["tipo"] == "laboratorio" and alcance in ("todos", e["nivel"], e["id"])]
    with _AUTO_LOCK:
        if _AUTO["estado"] == "corriendo":
            return jsonify({"ok": False, "error": "Ya hay una autoverificacion en curso."}), 409
        if not labs:
            return jsonify({"ok": False, "error": "Alcance desconocido."}), 400
        _AUTO.update(estado="corriendo", total=len(labs), hecho=0, resultados=[], log=[], cancelar=False, actual="")
    threading.Thread(target=_auto_hilo, args=(labs, bool(d.get("fallas"))), daemon=True).start()
    return jsonify({"ok": True, "total": len(labs)})


@app.get("/api/auto/estado")
def api_auto_estado():
    return jsonify(_AUTO)


@app.post("/api/auto/cancelar")
def api_auto_cancelar():
    _AUTO["cancelar"] = True
    return jsonify({"ok": True})


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(str(Path(__file__).parent), "autored.ico")


@app.get("/api/catalogo")
def api_catalogo():
    return jsonify(catalogo.listar())


@app.post("/api/catalogo/construir")
def api_cat_construir():
    d = request.get_json(silent=True) or {}
    lab = catalogo.obtener(d.get("id"))
    if not lab or lab["tipo"] != "laboratorio":
        return jsonify({"ok": False, "error": "Laboratorio no encontrado."}), 404
    p, deploy = lab["gen"](), bool(d.get("deploy", True))
    n = d.get("pcs")
    if isinstance(n, int) and 1 <= n <= 8:
        for nombre, gw in catalogo.con_pcs(p.d, n)[:4]:
            p.prueba(nombre, gw, tipo="PC agregado: su gateway")
    pj = json.dumps(p.d)
    try:
        v = json.loads(motor().llamar("pt_validate_plan", {"plan_json": pj}, 60))
        if not v.get("valid"):
            return jsonify({"ok": False, "error": "El motor rechazo el plan: " + "; ".join(x.get("message", "") for x in v.get("errors", []))}), 500
        cfg = motor().llamar("pt_generate_configs", {"plan_json": pj}, 60)
        dep = (motor().llamar("pt_live_deploy", {"plan_json": pj}, 300) if deploy
               else "Modo prueba: el plan se valido y se generaron las configuraciones, pero no se envio a Packet Tracer.")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    _aplicar_ips_vivas(p.d, dep)
    resumen = {"dispositivos": str(len(p.d["devices"])), "enlaces": str(len(p.d["links"])), "validacion": "PASS",
               "desplegado": deploy and bool(re.search(r"deployed", dep, re.I)) and not re.search(r"not (connected|deployed)", dep, re.I)}
    extras = []
    if resumen["desplegado"]:  # servicios que no van en el plan (DNS, HTTP...)
        for nombre, herramienta, args in p.post:
            try:
                t = motor().llamar(herramienta, args, 60)
                extras.append({"nombre": nombre, "ok": not re.search(r'"ok"\s*:\s*false|^\W*error', t, re.I), "texto": t.strip()[:200]})
            except Exception as e:
                extras.append({"nombre": nombre, "ok": False, "texto": str(e)})
    return jsonify({"ok": True, "texto": f"{dep}\n\nCLI CONFIGURATIONS\n{cfg}\n\nSUGGESTED CHECKS\n", "plan": p.d, "resumen": resumen,
                    "pruebas": p.pruebas, "extras": extras, "fallas": catalogo.fallas_meta(lab),
                    "lecturas": [{"dispositivo": a, "comando": b, "titulo": t} for a, b, t in lab["lecturas"]],
                    "nota": lab["nota"] if (not deploy or resumen["desplegado"])
                    else "No se envio a Packet Tracer: " + (dep.strip().splitlines() or ["sin respuesta"])[0]})


def _aplicar_falla(f, inyectar):
    """Inyecta o repara una falla (comandos con pt_cli y/o herramientas del motor). Devuelve la lista de errores."""
    errores = []
    for dispositivo, lineas in f["cli_on" if inyectar else "cli_off"].items():
        t = motor().llamar("pt_cli", {"device": dispositivo, "commands": ["enable", "configure terminal", *lineas, "end"], "timeout": 30}, 120)
        malo = next((ln for ln in t.splitlines() if re.search(r"ERROR|UNKNOWN COMMAND|TIMEOUT|not connected|No response", ln)), None)
        if malo:
            errores.append(f"{dispositivo}: {malo.strip()[:120]}")
    for herramienta, args in f["tools_on" if inyectar else "tools_off"]:
        t = motor().llamar(herramienta, args, 60)
        if re.search(r'"ok"\s*:\s*false|not connected', t):
            errores.append(f"{herramienta}: {t.strip()[:120]}")
    return errores


@app.post("/api/falla")
def api_falla():
    d = request.get_json(silent=True) or {}
    lab = catalogo.obtener(d.get("lab")) or {}
    f = next((x for x in lab.get("fallas", []) if x["id"] == d.get("falla")), None)
    if not f:
        return jsonify({"ok": False, "error": "Falla no encontrada."}), 404
    inyectar = d.get("accion") != "reparar"
    try:
        errores = _aplicar_falla(f, inyectar)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": not errores, "errores": errores, "pruebas": f["pruebas"] if inyectar else None})


@app.get("/api/config")
def api_config_get():
    return jsonify({"carpeta": carpeta_proyectos()})


@app.post("/api/config")
def api_config_set():
    c = str((request.get_json(silent=True) or {}).get("carpeta") or "").strip().strip('"')
    if c:
        c = os.path.abspath(os.path.expanduser(c))
        try:
            os.makedirs(c, exist_ok=True)
        except OSError as e:
            return jsonify({"ok": False, "error": f"No pude usar esa carpeta: {e}"}), 400
    antes = carpeta_proyectos()
    CFG.write_text(json.dumps({**cargar_cfg(), "carpeta": c}), encoding="utf-8")
    if c != antes:
        reiniciar_motor()
    return jsonify({"ok": True, "carpeta": c})


@app.post("/api/elegir_carpeta")
def api_elegir():
    codigo = ("import tkinter as tk;from tkinter import filedialog as f;r=tk.Tk();r.withdraw();r.attributes('-topmost',True);"
              "print(f.askdirectory(title='Carpeta para los proyectos de AutoRed') or '')")
    try:
        r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return jsonify({"ok": False, "carpeta": "", "error": str(e)})
    c = r.stdout.strip()
    return jsonify({"ok": r.returncode == 0, "carpeta": os.path.normpath(c) if c else "", "error": r.stderr.strip()[-200:]})


if __name__ == "__main__":
    motor()  # arranca el motor al iniciar, asi Packet Tracer se conecta de inmediato
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)