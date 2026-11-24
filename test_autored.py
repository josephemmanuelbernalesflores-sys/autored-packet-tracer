"""Pruebas automaticas de AutoRed. No necesitan Packet Tracer ni el motor MCP.
Ejecutar desde esta carpeta:   py -3.12 -m unittest test_autored -v"""
import ipaddress
import re
import unittest

import catalogo
from interprete import interpretar

try:
    import servidor
except ImportError:  # sin flask/mcp instalados no se prueba el servidor
    servidor = None

PUERTO = re.compile(r"^(FastEthernet0/\d{1,2}|GigabitEthernet0/\d)$")
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
LABS = [e for e in catalogo.CATALOGO if e["tipo"] == "laboratorio"]


def ips_de_cli(plan):
    """IP que solo existen en CLI libre (IP virtual de HSRP, tuneles, NAT) y son destinos validos."""
    return {ip for lineas in plan["extra_cli"].values() for ln in lineas for ip in IPV4.findall(ln)}


def ips_propias(plan):
    propias = {c.split("/")[0] for d in plan["devices"] for c in d["interfaces"].values()}
    return propias | {s["ip_cidr"].split("/")[0] for s in plan["subinterfaces"]}


class TestCatalogo(unittest.TestCase):
    def test_ids_unicos_y_campos(self):
        ids = [e["id"] for e in catalogo.CATALOGO]
        self.assertEqual(len(ids), len(set(ids)))
        for e in catalogo.CATALOGO:
            for campo in ("id", "nivel", "categoria", "titulo", "descripcion", "tipo"):
                self.assertTrue(e.get(campo), f"{e.get('id')}: falta {campo}")
            self.assertIn(e["nivel"], ("CCNA", "CCNP"))

    def test_listar_es_serializable(self):
        import json
        json.dumps(catalogo.listar())


class TestLaboratorios(unittest.TestCase):
    def test_estructura_de_cada_plan(self):
        for e in LABS:
            with self.subTest(lab=e["id"]):
                p = e["gen"]()
                d = p.d
                nombres = [x["name"] for x in d["devices"]]
                self.assertEqual(len(nombres), len(set(nombres)), "nombres de equipo repetidos")
                usados = set()
                for lk in d["links"]:
                    for dev, port in ((lk["device_a"], lk["port_a"]), (lk["device_b"], lk["port_b"])):
                        self.assertIn(dev, nombres, f"enlace a equipo inexistente {dev}")
                        self.assertTrue(PUERTO.match(port) or port == "FastEthernet0", f"puerto raro {dev}:{port}")
                        self.assertNotIn((dev, port), usados, f"puerto usado dos veces {dev}:{port}")
                        usados.add((dev, port))
                ips = [c.split("/")[0] for x in d["devices"] for c in x["interfaces"].values()]
                self.assertEqual(len(ips), len(set(ips)), "IP duplicada")
                for x in d["devices"]:
                    for c in x["interfaces"].values():
                        ipaddress.ip_interface(c)

    def test_gateways_coherentes(self):
        for e in LABS:
            with self.subTest(lab=e["id"]):
                d = e["gen"]().d
                validas = ips_propias(d) | ips_de_cli(d)
                for x in d["devices"]:
                    if x["category"] in ("pc", "server") and x.get("gateway"):
                        self.assertIn(x["gateway"], validas, f"{x['name']}: gateway {x['gateway']} no existe")
                        red = ipaddress.ip_interface(next(iter(x["interfaces"].values()))).network
                        self.assertIn(ipaddress.ip_address(x["gateway"]), red, f"{x['name']}: gateway fuera de su subred")

    def test_pruebas_validas(self):
        for e in LABS:
            with self.subTest(lab=e["id"]):
                p = e["gen"]()
                nombres = {x["name"] for x in p.d["devices"]}
                validas = ips_propias(p.d) | ips_de_cli(p.d)
                self.assertGreaterEqual(len(p.pruebas), 2, "muy pocas pruebas")
                _revisa_pruebas(self, p.pruebas, nombres, validas)

    def test_fallas_y_lecturas(self):
        for e in LABS:
            with self.subTest(lab=e["id"]):
                d = e["gen"]().d
                nombres = {x["name"] for x in d["devices"]}
                validas = ips_propias(d) | ips_de_cli(d)
                ids = [f["id"] for f in e["fallas"]]
                self.assertEqual(len(ids), len(set(ids)))
                for f in e["fallas"]:
                    for clave in ("cli_on", "cli_off"):
                        for dev, lineas in f[clave].items():
                            self.assertIn(dev, nombres, f"{f['id']}: equipo {dev} inexistente")
                            self.assertTrue(lineas)
                    self.assertEqual(bool(f["cli_on"]), bool(f["cli_off"]) , "inyectar y reparar deben ir en pareja")
                    self.assertTrue(f["cli_on"] or f["tools_on"], f"{f['id']}: no hace nada")
                    if f["pruebas"]:
                        _revisa_pruebas(self, f["pruebas"], nombres, validas)
                for dev, cmd, _ in e["lecturas"]:
                    self.assertIn(dev, nombres)
                    self.assertTrue(cmd.startswith("show"))

    def test_post_apunta_a_equipos_reales(self):
        for e in LABS:
            p = e["gen"]()
            nombres = {x["name"] for x in p.d["devices"]}
            for _, _, args in p.post:
                self.assertIn(args["device"], nombres)

    def test_pcs_por_lan(self):
        for e in LABS:
            p = e["gen"]()
            if p.d["vlans"] or p.d["dhcp_pools"]:
                self.assertEqual(catalogo.con_pcs(p.d, 4), [])
                continue
            with self.subTest(lab=e["id"]):
                nuevos = catalogo.con_pcs(p.d, 4)
                ips = [c.split("/")[0] for x in p.d["devices"] for c in x["interfaces"].values()]
                self.assertEqual(len(ips), len(set(ips)), "IP duplicada tras agregar PCs")
                usados = [(lk["device_a"], lk["port_a"]) for lk in p.d["links"]] + [(lk["device_b"], lk["port_b"]) for lk in p.d["links"]]
                self.assertEqual(len(usados), len(set(usados)), "puerto repetido tras agregar PCs")
                for nombre, gw in nuevos:
                    host = next(x for x in p.d["devices"] if x["name"] == nombre)
                    red = ipaddress.ip_interface(host["interfaces"]["FastEthernet0"]).network
                    self.assertIn(ipaddress.ip_address(gw), red)


def _revisa_pruebas(caso, pruebas, nombres, validas):
    for t in pruebas:
        caso.assertIn(t["de"], nombres, f"prueba desde equipo inexistente {t['de']}")
        metodo = t.get("metodo", "ping")
        if metodo == "ping":
            ipaddress.ip_address(t["a"])
            caso.assertIn(t["a"], validas, f"ping a {t['a']}: ningun equipo tiene esa IP")
        elif metodo == "cli":
            re.compile(t["patron"])
            caso.assertTrue(t["a"].startswith("show"))
        elif metodo == "web":
            caso.assertTrue(t["a"].startswith("http"))
            caso.assertTrue(t["contiene"])
        else:
            caso.fail(f"metodo desconocido {metodo}")


class TestInterprete(unittest.TestCase):
    def test_frases_basicas(self):
        r = interpretar("red estrella con 3 switches y 2 pcs")
        self.assertEqual((r["params"]["template"], r["params"]["switches_per_router"], r["params"]["pcs_per_lan"]), ("star", 3, 2))
        self.assertTrue(r["ok"])
        self.assertEqual(interpretar("triangulo de 3 routers con ospf")["params"]["template"], "three_router_triangle")

    def test_jerga_y_typos(self):
        self.assertEqual(interpretar("un par de compus y 3 swithces")["params"].get("pcs_per_lan"), 2)

    def test_frase_sin_sentido(self):
        self.assertFalse(interpretar("hola")["ok"])

    def test_ospf_con_un_router_se_omite(self):
        r = interpretar("oficina con 4 pcs y ospf")
        self.assertNotIn("routing", r["params"])
        self.assertTrue(r["notas"])

    def test_sugerencias_del_catalogo(self):
        self.assertIn("hsrp", interpretar("quiero hsrp con dos routers")["labs"])
        self.assertEqual(interpretar("red estrella con 3 switches y 2 pcs")["labs"], [])



HSRP_R1 = """Interface   Grp  Pri P State    Active          Standby         Virtual IP
Gi0/0       1    110 P Active   local           192.168.1.3     192.168.1.1"""
HSRP_R2 = """Interface   Grp  Pri P State    Active          Standby         Virtual IP
Gi0/0       1    100 P Standby  192.168.1.2     local           192.168.1.1"""


class TestEstados(unittest.TestCase):
    """Las expresiones de ESTADOS deben aceptar la salida correcta (formato estandar de IOS) y rechazar la incorrecta."""

    def _patron(self, lab, equipo):
        return next(re.compile(p, re.M) for d, _, p, _, _ in catalogo.ESTADOS[lab] if d == equipo)

    def test_hsrp_activo_y_en_espera(self):
        self.assertTrue(self._patron("hsrp", "R1").search(HSRP_R1))
        self.assertFalse(self._patron("hsrp", "R1").search(HSRP_R2))
        self.assertTrue(self._patron("hsrp", "R2").search(HSRP_R2))
        self.assertFalse(self._patron("hsrp", "R2").search(HSRP_R1))
        self.assertFalse(self._patron("hsrp", "R2").search(HSRP_R2.replace("Standby", "Speak  ")))


@unittest.skipIf(servidor is None, "flask/mcp no instalados")
class TestMedir(unittest.TestCase):
    PING = "Packets: Sent = 4, Received = {}, Lost = {} ({}% loss),"

    def _p(self, rec):
        return self.PING.format(rec, 4 - rec, (4 - rec) * 25)

    def _medir(self, respuestas, esperado=True):
        from unittest import mock
        it = iter(respuestas)
        with mock.patch.object(servidor, "_probar", lambda x: next(it)):
            return servidor._medir({"de": "PC1", "a": "10.0.0.1"}, esperado, lambda: None)

    def test_completo(self):
        ok, _, cal = self._medir([(True, self._p(4))])
        self.assertEqual((ok, cal), (True, "completo"))

    def test_perdida_inicial_que_se_recupera(self):
        ok, t, cal = self._medir([(True, self._p(1)), (True, self._p(4))])
        self.assertEqual((ok, cal, t), (True, "tras_perdida", self._p(4)))

    def test_primer_intento_sin_respuesta(self):
        ok, _, cal = self._medir([(False, self._p(0)), (True, self._p(4))])
        self.assertEqual((ok, cal), (True, "tras_perdida"))

    def test_perdida_que_persiste(self):
        ok, t, cal = self._medir([(True, self._p(1)), (True, self._p(2)), (True, self._p(3))])
        self.assertEqual((ok, cal), (True, "parcial"))
        self.assertEqual(t.count("--- reintento ---"), 2)

    def test_se_recupera_en_la_segunda_repeticion(self):
        ok, _, cal = self._medir([(True, self._p(1)), (True, self._p(3)), (True, self._p(4))])
        self.assertEqual((ok, cal), (True, "tras_perdida"))

    def test_sin_respuesta(self):
        ok, _, cal = self._medir([(False, self._p(0)), (False, self._p(0))])
        self.assertEqual((ok, cal), (False, ""))

    def test_bloqueo_esperado_no_reintenta(self):
        ok, _, cal = self._medir([(False, self._p(0))], esperado=False)
        self.assertEqual((ok, cal), (False, ""))

    def test_recorta_guarda_todo_lo_que_falla(self):
        self.assertEqual(len(servidor._recorta("x" * 5000, False)), 3000)
        self.assertEqual(len(servidor._recorta("x" * 5000, True)), 300)
        self.assertEqual(len(servidor._recorta("x" * 5000, True, "parcial")), 3000)


class MotorSimulado:
    """Motor falso: todo sale bien, para probar la logica de la autoverificacion sin Packet Tracer."""

    def __init__(self, despliegue="Topology deployed to Packet Tracer!"):
        self.despliegue, self.llamadas = despliegue, []

    def llamar(self, herramienta, args, timeout=0):
        self.llamadas.append(herramienta)
        return {"pt_live_deploy": self.despliegue, "pt_verify_connectivity": "Success rate is 100 percent (4/4), Received = 4",
                "pt_delete_device": "Device 'X' deleted from the topology."}.get(herramienta, "ok")


@unittest.skipIf(servidor is None, "flask/mcp no instalados")
class TestAutoverificacion(unittest.TestCase):
    def _correr(self, motor, labs, fallas):
        from unittest import mock
        servidor._AUTO.update(estado="corriendo", total=len(labs), hecho=0, resultados=[], log=[], cancelar=False, actual="")
        with mock.patch.object(servidor, "motor", lambda: motor), mock.patch("servidor.time.sleep"):
            servidor._auto_hilo(labs, fallas)
        return servidor._AUTO

    def test_ciclo_completo(self):
        labs = [catalogo.obtener("isp_nat"), catalogo.obtener("hsrp")]
        motor = MotorSimulado()
        a = self._correr(motor, labs, True)
        self.assertEqual((a["estado"], a["hecho"]), ("terminado", 2))
        for r in a["resultados"]:
            self.assertTrue(r["construccion"])
            self.assertTrue(r["pruebas"])
            self.assertTrue(r["fallas"])
        self.assertIn("pt_delete_device", motor.llamadas)

    def test_sin_packet_tracer_se_detiene(self):
        a = self._correr(MotorSimulado("Packet Tracer is not connected over any channel."), [catalogo.obtener("isp_nat"), catalogo.obtener("hsrp")], False)
        self.assertEqual((a["estado"], a["hecho"]), ("error", 1))

    def test_no_borra_si_no_construyo(self):
        motor = MotorSimulado("Not deployed: the canvas already has devices named R1")
        self._correr(motor, [catalogo.obtener("isp_nat")], False)
        self.assertNotIn("pt_delete_device", motor.llamadas)


if __name__ == "__main__":
    unittest.main()