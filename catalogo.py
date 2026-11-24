"""Catalogo de laboratorios CCNA/CCNP. Cada laboratorio genera un plan completo (topologia, IPs, protocolos y CLI)
que el motor valida y despliega. 'plantilla' = usa las plantillas del motor con parametros."""


import ipaddress
import re
import unicodedata


class P:
    """Constructor minimo de planes para el motor."""

    def __init__(self, nombre):
        self.d = {"name": nombre, "devices": [], "modules": [], "links": [], "dhcp_pools": [], "static_routes": [], "ospf_configs": [],
               "rip_configs": [], "eigrp_configs": [], "vlans": [], "access_ports": [], "trunks": [], "subinterfaces": [],
               "validations": [], "dual_stack": False, "extra_cli": {}, "errors": [], "warnings": []}
        self.cat, self.pruebas, self.post = {}, [], []

    def _add(self, n, modelo, cat, rol, x, y, **k):
        self.d["devices"].append({"name": n, "model": modelo, "category": cat, "role": rol, "x": x, "y": y, "interfaces": {}, "gateway": "", **k})
        self.cat[n] = cat
        return n

    def r(self, n, x, y, rol="core_router", m="2911"):
        return self._add(n, m, "router", rol, x, y)

    def sw(self, n, x, y, m="2960-24TT"):
        return self._add(n, m, "switch", "access_switch", x, y)

    def pc(self, n, x, y, cidr, gw, vlan=0):
        self._add(n, "PC-PT", "pc", "end_host", x, y, vlan=vlan, gateway=gw)
        self.ip(n, "FastEthernet0", cidr)
        return n

    def srv(self, n, x, y, cidr, gw):
        self._add(n, "Server-PT", "server", "server_host", x, y, gateway=gw)
        self.ip(n, "FastEthernet0", cidr)
        return n

    def ip(self, n, iface, cidr):
        next(d for d in self.d["devices"] if d["name"] == n)["interfaces"][iface] = cidr

    def enl(self, a, pa, b, pb):
        cable = "cross" if self.cat[a] == self.cat[b] and self.cat[a] in ("router", "switch") else "straight"
        self.d["links"].append({"device_a": a, "port_a": pa, "device_b": b, "port_b": pb, "cable": cable})

    def cli(self, n, *lineas):
        self.d["extra_cli"].setdefault(n, []).extend(lineas)

    def ruta(self, r, dest, mask, nh, ad=1):
        self.d["static_routes"].append({"router": r, "destination": dest, "mask": mask, "next_hop": nh, "admin_distance": ad})

    def ospf(self, r, redes, rid=""):
        self.d["ospf_configs"].append({"router": r, "process_id": 1, "router_id": rid,
                                    "networks": [{"network": n, "wildcard": w, "area": a} for n, w, a in redes]})

    def prueba(self, origen, destino, esperado=True, tipo="ping"):
        self.pruebas.append({"de": origen, "a": destino, "esperado": esperado, "tipo": tipo})

    def web(self, origen, url, contiene, tipo="web", esperado=True):
        self.pruebas.append({"de": origen, "a": url, "esperado": esperado, "tipo": tipo, "metodo": "web", "contiene": contiene})

    def estado(self, dispositivo, comando, patron, tipo, minimo=1):
        """Comprobacion de estado: ejecuta un show en el equipo y exige que la salida cumpla la expresion regular."""
        self.pruebas.append({"de": dispositivo, "a": comando, "esperado": True, "tipo": tipo, "metodo": "cli", "patron": patron, "minimo": minimo})


G = ("GigabitEthernet0/0", "GigabitEthernet0/1", "GigabitEthernet0/2")
W24, W30 = "0.0.0.255", "0.0.0.3"


def _vlans_trunk():
    p = P("vlans_trunk")
    r1, s1, s2 = p.r("R1", 600, 110), p.sw("SW1", 450, 260), p.sw("SW2", 750, 260)
    for n, x, v in (("PC1", 380, 10), ("PC2", 520, 20)):
        p.pc(n, x, 430, f"192.168.{v}.2/24", f"192.168.{v}.1", v)
    p.pc("PC3", 680, 430, "192.168.10.4/24", "192.168.10.1", 10)
    p.pc("PC4", 820, 430, "192.168.20.4/24", "192.168.20.1", 20)
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "GigabitEthernet0/2", s2, "GigabitEthernet0/1")
    for pc, sw, pt in (("PC1", s1, 1), ("PC2", s1, 2), ("PC3", s2, 1), ("PC4", s2, 2)):
        p.enl(sw, f"FastEthernet0/{pt}", pc, "FastEthernet0")
    p.d["vlans"] = [{"vlan_id": 10, "name": "VENTAS", "subnet": "192.168.10.0/24"}, {"vlan_id": 20, "name": "SOPORTE", "subnet": "192.168.20.0/24"}]
    p.d["access_ports"] = [{"switch": sw, "port": f"FastEthernet0/{pt}", "vlan_id": v} for sw, pt, v in ((s1, 1, 10), (s1, 2, 20), (s2, 1, 10), (s2, 2, 20))]
    p.d["trunks"] = [{"switch": s1, "port": "GigabitEthernet0/1", "allowed_vlans": [10, 20]}, {"switch": s1, "port": "GigabitEthernet0/2", "allowed_vlans": [10, 20]},
                     {"switch": s2, "port": "GigabitEthernet0/1", "allowed_vlans": [10, 20]}]
    p.d["subinterfaces"] = [{"router": r1, "parent_port": G[0], "vlan_id": v, "ip_cidr": f"192.168.{v}.1/24"} for v in (10, 20)]
    p.prueba("PC1", "192.168.10.1"); p.prueba("PC1", "192.168.10.4"); p.prueba("PC1", "192.168.20.2", tipo="entre VLANs"); p.prueba("PC3", "192.168.20.4", tipo="entre VLANs")
    return p


def _estaticas():
    p = P("rutas_estaticas")
    r1, r2, r3 = p.r("R1", 300, 200), p.r("R2", 600, 200), p.r("R3", 900, 200)
    s1, s3 = p.sw("SW1", 300, 340), p.sw("SW3", 900, 340)
    p.pc("PC1", 300, 480, "192.168.1.2/24", "192.168.1.1"); p.pc("PC2", 900, 480, "192.168.3.2/24", "192.168.3.1")
    p.ip(r1, G[0], "192.168.1.1/24"); p.ip(r1, G[1], "10.0.12.1/30"); p.ip(r2, G[0], "10.0.12.2/30"); p.ip(r2, G[1], "10.0.23.1/30")
    p.ip(r3, G[0], "10.0.23.2/30"); p.ip(r3, G[1], "192.168.3.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(r1, G[1], r2, G[0]); p.enl(r2, G[1], r3, G[0])
    p.enl(r3, G[1], s3, "GigabitEthernet0/1"); p.enl(s3, "FastEthernet0/1", "PC2", "FastEthernet0")
    m24, m30 = "255.255.255.0", "255.255.255.252"
    for r, d, m, nh in ((r1, "192.168.3.0", m24, "10.0.12.2"), (r1, "10.0.23.0", m30, "10.0.12.2"), (r2, "192.168.1.0", m24, "10.0.12.1"),
                        (r2, "192.168.3.0", m24, "10.0.23.2"), (r3, "192.168.1.0", m24, "10.0.23.1"), (r3, "10.0.12.0", m30, "10.0.23.1")):
        p.ruta(r, d, m, nh)
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "192.168.3.2", tipo="extremo a extremo"); p.prueba("PC2", "192.168.1.2", tipo="extremo a extremo"); p.prueba("PC1", "10.0.23.2", tipo="ruta a enlace remoto")
    return p


def _isp_nat():
    p = P("isp_nat")
    r1, isp = p.r("R1", 450, 220, "edge_router"), p.r("ISP", 750, 220, "edge_router")
    s1, s2 = p.sw("SW1", 450, 360), p.sw("SW2", 750, 360)
    p.pc("PC1", 380, 500, "192.168.10.2/24", "192.168.10.1"); p.pc("PC2", 520, 500, "192.168.10.3/24", "192.168.10.1")
    p.srv("WEB", 750, 500, "198.51.100.10/24", "198.51.100.1")
    p.ip(r1, G[0], "192.168.10.1/24"); p.ip(r1, G[1], "203.0.113.2/30"); p.ip(isp, G[0], "203.0.113.1/30"); p.ip(isp, G[1], "198.51.100.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(s1, "FastEthernet0/2", "PC2", "FastEthernet0")
    p.enl(r1, G[1], isp, G[0]); p.enl(isp, G[1], s2, "GigabitEthernet0/1"); p.enl(s2, "FastEthernet0/1", "WEB", "FastEthernet0")
    p.ruta(r1, "0.0.0.0", "0.0.0.0", "203.0.113.1")
    p.cli(r1, "access-list 1 permit 192.168.10.0 0.0.0.255", "interface GigabitEthernet0/0", "ip nat inside", "exit", "interface GigabitEthernet0/1", "ip nat outside", "exit",
          "ip nat inside source list 1 interface GigabitEthernet0/1 overload")
    p.prueba("PC1", "192.168.10.1"); p.prueba("PC1", "203.0.113.1", tipo="hacia el ISP"); p.prueba("PC1", "198.51.100.10", tipo="a Internet (NAT)"); p.prueba("PC2", "198.51.100.10", tipo="a Internet (NAT)")
    return p


def _relay():
    p = P("dhcp_relay")
    r1, r2 = p.r("R1", 400, 200), p.r("R2", 800, 200)
    s1, s2 = p.sw("SW1", 400, 340), p.sw("SW2", 800, 340)
    p.pc("PC1", 330, 480, "192.168.1.2/24", "192.168.1.1"); p.pc("PC2", 470, 480, "192.168.1.3/24", "192.168.1.1"); p.pc("PC3", 800, 480, "192.168.2.2/24", "192.168.2.1")
    p.ip(r1, G[0], "192.168.1.1/24"); p.ip(r1, G[1], "10.0.0.1/30"); p.ip(r2, G[0], "10.0.0.2/30"); p.ip(r2, G[1], "192.168.2.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(s1, "FastEthernet0/2", "PC2", "FastEthernet0")
    p.enl(r1, G[1], r2, G[0]); p.enl(r2, G[1], s2, "GigabitEthernet0/1"); p.enl(s2, "FastEthernet0/1", "PC3", "FastEthernet0")
    p.d["dhcp_pools"] = [{"router": "R2", "pool_name": "LAN_R1", "network": "192.168.1.0", "mask": "255.255.255.0", "gateway": "192.168.1.1", "dns": "8.8.8.8",
                          "excluded_start": "192.168.1.1", "excluded_end": "192.168.1.1"}]
    p.ruta(r1, "0.0.0.0", "0.0.0.0", "10.0.0.2"); p.ruta(r2, "192.168.1.0", "255.255.255.0", "10.0.0.1")
    p.cli(r1, "interface GigabitEthernet0/0", "ip helper-address 10.0.0.2", "exit")
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "10.0.0.2", tipo="hacia el servidor DHCP"); p.prueba("PC1", "192.168.2.2", tipo="entre LANs")
    return p


def _hsrp():
    p = P("hsrp")
    r1, r2, r3 = p.r("R1", 400, 160), p.r("R2", 700, 160), p.r("R3", 550, 300, "edge_router")
    s1, s2 = p.sw("SW1", 550, 60), p.sw("SW2", 550, 440)
    p.pc("PC1", 420, 60, "192.168.1.10/24", "192.168.1.1"); p.pc("PC2", 680, 60, "192.168.1.11/24", "192.168.1.1"); p.srv("WEB", 550, 540, "172.16.0.10/24", "172.16.0.1")
    p.ip(r1, G[0], "192.168.1.2/24"); p.ip(r2, G[0], "192.168.1.3/24"); p.ip(r1, G[1], "10.0.13.1/30"); p.ip(r3, G[0], "10.0.13.2/30")
    p.ip(r2, G[1], "10.0.23.1/30"); p.ip(r3, G[1], "10.0.23.2/30"); p.ip(r3, G[2], "172.16.0.1/24")
    p.enl(s1, "GigabitEthernet0/1", r1, G[0]); p.enl(s1, "GigabitEthernet0/2", r2, G[0]); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(s1, "FastEthernet0/2", "PC2", "FastEthernet0")
    p.enl(r1, G[1], r3, G[0]); p.enl(r2, G[1], r3, G[1]); p.enl(r3, G[2], s2, "GigabitEthernet0/1"); p.enl(s2, "FastEthernet0/1", "WEB", "FastEthernet0")
    p.ospf(r1, [("192.168.1.0", W24, 0), ("10.0.13.0", W30, 0)], "1.1.1.1"); p.ospf(r2, [("192.168.1.0", W24, 0), ("10.0.23.0", W30, 0)], "2.2.2.2")
    p.ospf(r3, [("10.0.13.0", W30, 0), ("10.0.23.0", W30, 0), ("172.16.0.0", W24, 0)], "3.3.3.3")
    for r, prio in ((r1, 110), (r2, 100)):
        p.cli(r, "interface GigabitEthernet0/0", "standby version 2", "standby 1 ip 192.168.1.1", f"standby 1 priority {prio}", "standby 1 preempt", "exit")
    p.prueba("PC1", "192.168.1.1", tipo="IP virtual HSRP"); p.prueba("PC1", "192.168.1.2", tipo="R1 (activo)"); p.prueba("PC1", "172.16.0.10", tipo="hacia el servidor")
    return p


def _etherchannel():
    p = P("etherchannel")
    r1, s1, s2 = p.r("R1", 600, 100), p.sw("SW1", 450, 260), p.sw("SW2", 750, 260)
    p.pc("PC1", 380, 430, "192.168.1.10/24", "192.168.1.1"); p.pc("PC2", 520, 430, "192.168.1.11/24", "192.168.1.1")
    p.pc("PC3", 680, 430, "192.168.1.12/24", "192.168.1.1"); p.pc("PC4", 820, 430, "192.168.1.13/24", "192.168.1.1")
    p.ip(r1, G[0], "192.168.1.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1")
    for sw, a, b in ((s1, "PC1", "PC2"), (s2, "PC3", "PC4")):
        p.enl(sw, "FastEthernet0/1", a, "FastEthernet0"); p.enl(sw, "FastEthernet0/2", b, "FastEthernet0")
    p.enl(s1, "FastEthernet0/23", s2, "FastEthernet0/23"); p.enl(s1, "FastEthernet0/24", s2, "FastEthernet0/24")
    for sw in (s1, s2):
        p.cli(sw, "interface range FastEthernet0/23 - 24", "switchport mode trunk", "channel-group 1 mode active", "exit", "interface Port-channel1", "switchport mode trunk", "exit")
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "192.168.1.12", tipo="por el EtherChannel"); p.prueba("PC2", "192.168.1.13", tipo="por el EtherChannel")
    return p


def _stp():
    p = P("stp_root")
    s1, s2, s3 = p.sw("SW1", 600, 140), p.sw("SW2", 400, 320), p.sw("SW3", 800, 320)
    r1 = p.r("R1", 600, 30)
    p.ip(r1, G[0], "192.168.1.1/24")
    p.pc("PC1", 600, 220, "192.168.1.10/24", "192.168.1.1"); p.pc("PC2", 400, 460, "192.168.1.11/24", "192.168.1.1"); p.pc("PC3", 800, 460, "192.168.1.12/24", "192.168.1.1")
    p.enl(r1, G[0], s1, "FastEthernet0/10")
    p.enl(s1, "GigabitEthernet0/1", s2, "GigabitEthernet0/1"); p.enl(s2, "GigabitEthernet0/2", s3, "GigabitEthernet0/1"); p.enl(s3, "GigabitEthernet0/2", s1, "GigabitEthernet0/2")
    for sw, pc in ((s1, "PC1"), (s2, "PC2"), (s3, "PC3")):
        p.enl(sw, "FastEthernet0/1", pc, "FastEthernet0")
        p.cli(sw, "spanning-tree mode rapid-pvst", "interface FastEthernet0/1", "spanning-tree portfast", "spanning-tree bpduguard enable", "exit")
    p.cli(s1, "spanning-tree vlan 1 priority 4096"); p.cli(s2, "spanning-tree vlan 1 priority 8192")
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC2", "192.168.1.12", tipo="a traves del anillo"); p.prueba("PC3", "192.168.1.10", tipo="a traves del anillo")
    return p


def _acl_web():
    p = P("acl_servidor")
    r1, s1, s2 = p.r("R1", 600, 160), p.sw("SW1", 400, 300), p.sw("SW2", 800, 300)
    p.pc("PC1", 330, 440, "192.168.10.2/24", "192.168.10.1"); p.pc("PC2", 470, 440, "192.168.10.3/24", "192.168.10.1"); p.srv("WEB", 800, 440, "192.168.20.10/24", "192.168.20.1")
    p.ip(r1, G[0], "192.168.10.1/24"); p.ip(r1, G[1], "192.168.20.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(s1, "FastEthernet0/2", "PC2", "FastEthernet0")
    p.enl(r1, G[1], s2, "GigabitEthernet0/1"); p.enl(s2, "FastEthernet0/1", "WEB", "FastEthernet0")
    p.cli(r1, "ip access-list extended SOLO_WEB", "permit tcp 192.168.10.0 0.0.0.255 host 192.168.20.10 eq 80", "deny ip 192.168.10.0 0.0.0.255 192.168.20.0 0.0.0.255",
          "permit ip any any", "exit", "interface GigabitEthernet0/0", "ip access-group SOLO_WEB in", "exit")
    p.prueba("PC1", "192.168.10.1"); p.prueba("PC1", "192.168.20.10", False, "ping bloqueado por la ACL"); p.prueba("PC2", "192.168.20.1", False, "ping bloqueado por la ACL")
    return p


def _multiarea():
    p = P("ospf_multiarea")
    r1, r2, r3 = p.r("R1", 300, 200, "branch_router"), p.r("R2", 600, 200), p.r("R3", 900, 200, "branch_router")
    s1, s3 = p.sw("SW1", 300, 340), p.sw("SW3", 900, 340)
    p.pc("PC1", 300, 480, "192.168.1.2/24", "192.168.1.1"); p.pc("PC2", 900, 480, "192.168.3.2/24", "192.168.3.1")
    p.ip(r1, G[0], "192.168.1.1/24"); p.ip(r1, G[1], "10.0.12.1/30"); p.ip(r2, G[0], "10.0.12.2/30"); p.ip(r2, G[1], "10.0.23.1/30"); p.ip(r3, G[0], "10.0.23.2/30"); p.ip(r3, G[1], "192.168.3.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(r1, G[1], r2, G[0]); p.enl(r2, G[1], r3, G[0])
    p.enl(r3, G[1], s3, "GigabitEthernet0/1"); p.enl(s3, "FastEthernet0/1", "PC2", "FastEthernet0")
    p.ospf(r1, [("192.168.1.0", W24, 1), ("10.0.12.0", W30, 1)], "1.1.1.1"); p.ospf(r2, [("10.0.12.0", W30, 1), ("10.0.23.0", W30, 0)], "2.2.2.2")
    p.ospf(r3, [("10.0.23.0", W30, 0), ("192.168.3.0", W24, 0)], "3.3.3.3")
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "192.168.3.2", tipo="entre areas (via ABR)"); p.prueba("PC2", "192.168.1.2", tipo="entre areas (via ABR)")
    return p


def _redistrib():
    p = P("redistribucion")
    r1, r2, r3 = p.r("R1", 300, 200), p.r("R2", 600, 200, "edge_router"), p.r("R3", 900, 200)
    s1, s3 = p.sw("SW1", 300, 340), p.sw("SW3", 900, 340)
    p.pc("PC1", 300, 480, "192.168.1.2/24", "192.168.1.1"); p.pc("PC2", 900, 480, "192.168.3.2/24", "192.168.3.1")
    p.ip(r1, G[0], "192.168.1.1/24"); p.ip(r1, G[1], "10.0.12.1/30"); p.ip(r2, G[0], "10.0.12.2/30"); p.ip(r2, G[1], "10.0.23.1/30"); p.ip(r3, G[0], "10.0.23.2/30"); p.ip(r3, G[1], "192.168.3.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(r1, G[1], r2, G[0]); p.enl(r2, G[1], r3, G[0])
    p.enl(r3, G[1], s3, "GigabitEthernet0/1"); p.enl(s3, "FastEthernet0/1", "PC2", "FastEthernet0")
    p.ospf(r1, [("192.168.1.0", W24, 0), ("10.0.12.0", W30, 0)], "1.1.1.1"); p.ospf(r2, [("10.0.12.0", W30, 0)], "2.2.2.2")
    p.d["eigrp_configs"] = [{"router": r2, "as_number": 100, "networks": [{"network": "10.0.23.0", "wildcard": W30}], "no_auto_summary": True},
                            {"router": r3, "as_number": 100, "networks": [{"network": "10.0.23.0", "wildcard": W30}, {"network": "192.168.3.0", "wildcard": W24}], "no_auto_summary": True}]
    p.cli(r2, "router ospf 1", "redistribute eigrp 100 subnets", "exit", "router eigrp 100", "redistribute ospf 1 metric 10000 100 255 1 1500", "exit")
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "192.168.3.2", tipo="OSPF a EIGRP (redistribucion)"); p.prueba("PC2", "192.168.1.2", tipo="EIGRP a OSPF (redistribucion)")
    return p


def _bgp():
    p = P("ebgp")
    r1, r2 = p.r("R1", 400, 200, "edge_router"), p.r("R2", 800, 200, "edge_router")
    s1, s2 = p.sw("SW1", 400, 340), p.sw("SW2", 800, 340)
    p.pc("PC1", 400, 480, "192.168.1.2/24", "192.168.1.1"); p.pc("PC2", 800, 480, "192.168.2.2/24", "192.168.2.1")
    p.ip(r1, G[0], "192.168.1.1/24"); p.ip(r1, G[1], "203.0.113.1/30"); p.ip(r2, G[0], "203.0.113.2/30"); p.ip(r2, G[1], "192.168.2.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(r1, G[1], r2, G[0])
    p.enl(r2, G[1], s2, "GigabitEthernet0/1"); p.enl(s2, "FastEthernet0/1", "PC2", "FastEthernet0")
    p.cli(r1, "router bgp 65001", "neighbor 203.0.113.2 remote-as 65002", "network 192.168.1.0 mask 255.255.255.0", "exit")
    p.cli(r2, "router bgp 65002", "neighbor 203.0.113.1 remote-as 65001", "network 192.168.2.0 mask 255.255.255.0", "exit")
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "203.0.113.2", tipo="vecino eBGP"); p.prueba("PC1", "192.168.2.2", tipo="entre sistemas autonomos"); p.prueba("PC2", "192.168.1.2", tipo="entre sistemas autonomos")
    return p


def _gre():
    p = P("gre_tunel")
    r1, r2 = p.r("R1", 400, 200, "edge_router"), p.r("R2", 800, 200, "edge_router")
    s1, s2 = p.sw("SW1", 400, 340), p.sw("SW2", 800, 340)
    p.pc("PC1", 400, 480, "192.168.1.2/24", "192.168.1.1"); p.pc("PC2", 800, 480, "192.168.2.2/24", "192.168.2.1")
    p.ip(r1, G[0], "192.168.1.1/24"); p.ip(r1, G[1], "203.0.113.1/30"); p.ip(r2, G[0], "203.0.113.2/30"); p.ip(r2, G[1], "192.168.2.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(r1, G[1], r2, G[0])
    p.enl(r2, G[1], s2, "GigabitEthernet0/1"); p.enl(s2, "FastEthernet0/1", "PC2", "FastEthernet0")
    p.cli(r1, "interface Tunnel0", "ip address 10.255.0.1 255.255.255.252", "tunnel source GigabitEthernet0/1", "tunnel destination 203.0.113.2", "no shutdown", "exit",
          "ip route 192.168.2.0 255.255.255.0 10.255.0.2")
    p.cli(r2, "interface Tunnel0", "ip address 10.255.0.2 255.255.255.252", "tunnel source GigabitEthernet0/0", "tunnel destination 203.0.113.1", "no shutdown", "exit",
          "ip route 192.168.1.0 255.255.255.0 10.255.0.1")
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "10.255.0.2", tipo="extremo del tunel GRE"); p.prueba("PC1", "192.168.2.2", tipo="LAN a LAN por el tunel"); p.prueba("PC2", "192.168.1.2", tipo="LAN a LAN por el tunel")
    return p


def _cadena(nombre):
    """R1 - R2 - R3 en linea, con una LAN en cada extremo (base de varios laboratorios)."""
    p = P(nombre)
    r1, r2, r3 = p.r("R1", 300, 200), p.r("R2", 600, 200), p.r("R3", 900, 200)
    s1, s3 = p.sw("SW1", 300, 340), p.sw("SW3", 900, 340)
    p.pc("PC1", 300, 480, "192.168.1.2/24", "192.168.1.1"); p.pc("PC2", 900, 480, "192.168.3.2/24", "192.168.3.1")
    p.ip(r1, G[0], "192.168.1.1/24"); p.ip(r1, G[1], "10.0.12.1/30"); p.ip(r2, G[0], "10.0.12.2/30"); p.ip(r2, G[1], "10.0.23.1/30")
    p.ip(r3, G[0], "10.0.23.2/30"); p.ip(r3, G[1], "192.168.3.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(r1, G[1], r2, G[0]); p.enl(r2, G[1], r3, G[0])
    p.enl(r3, G[1], s3, "GigabitEthernet0/1"); p.enl(s3, "FastEthernet0/1", "PC2", "FastEthernet0")
    return p


def _rip():
    p = _cadena("ripv2")
    for r, redes in (("R1", ["192.168.1.0", "10.0.0.0"]), ("R2", ["10.0.0.0"]), ("R3", ["10.0.0.0", "192.168.3.0"])):
        p.d["rip_configs"].append({"router": r, "version": 2, "networks": redes, "no_auto_summary": True})
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "10.0.23.2", tipo="red aprendida por RIP"); p.prueba("PC1", "192.168.3.2", tipo="extremo a extremo (RIP)"); p.prueba("PC2", "192.168.1.2", tipo="extremo a extremo (RIP)")
    return p


def _ospf_auth():
    p = _cadena("ospf_auth")
    p.ospf("R1", [("192.168.1.0", W24, 0), ("10.0.12.0", W30, 0)], "1.1.1.1"); p.ospf("R2", [("10.0.12.0", W30, 0), ("10.0.23.0", W30, 0)], "2.2.2.2")
    p.ospf("R3", [("10.0.23.0", W30, 0), ("192.168.3.0", W24, 0)], "3.3.3.3")
    for r, ifs in (("R1", (G[1],)), ("R2", (G[0], G[1])), ("R3", (G[0],))):
        for i in ifs:
            p.cli(r, f"interface {i}", "ip ospf authentication message-digest", "ip ospf message-digest-key 1 md5 Cisco123", "exit")
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "192.168.3.2", tipo="OSPF con MD5"); p.prueba("PC2", "192.168.1.2", tipo="OSPF con MD5")
    return p


def _ospf_default():
    p = P("ospf_default")
    r1, r2, isp = p.r("R1", 300, 220), p.r("R2", 600, 220, "edge_router"), p.r("ISP", 900, 220, "edge_router")
    s1, s2 = p.sw("SW1", 300, 360), p.sw("SW2", 900, 360)
    p.pc("PC1", 300, 500, "192.168.1.2/24", "192.168.1.1"); p.srv("WEB", 900, 500, "198.51.100.10/24", "198.51.100.1")
    p.ip(r1, G[0], "192.168.1.1/24"); p.ip(r1, G[1], "10.0.12.1/30"); p.ip(r2, G[0], "10.0.12.2/30"); p.ip(r2, G[1], "203.0.113.2/30")
    p.ip(isp, G[0], "203.0.113.1/30"); p.ip(isp, G[1], "198.51.100.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(r1, G[1], r2, G[0]); p.enl(r2, G[1], isp, G[0])
    p.enl(isp, G[1], s2, "GigabitEthernet0/1"); p.enl(s2, "FastEthernet0/1", "WEB", "FastEthernet0")
    p.ospf(r1, [("192.168.1.0", W24, 0), ("10.0.12.0", W30, 0)], "1.1.1.1"); p.ospf(r2, [("10.0.12.0", W30, 0)], "2.2.2.2")
    p.ruta(r2, "0.0.0.0", "0.0.0.0", "203.0.113.1"); p.ruta(isp, "192.168.1.0", "255.255.255.0", "203.0.113.2"); p.ruta(isp, "10.0.12.0", "255.255.255.252", "203.0.113.2")
    p.cli(r2, "router ospf 1", "default-information originate", "exit")
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "10.0.12.2", tipo="vecino OSPF"); p.prueba("PC1", "203.0.113.1", tipo="ruta por defecto (OSPF)"); p.prueba("PC1", "198.51.100.10", tipo="a Internet (ruta por defecto)")
    return p


def _acl_est():
    p = P("acl_estandar")
    r1, s1, s2 = p.r("R1", 600, 160), p.sw("SW1", 400, 300), p.sw("SW2", 800, 300)
    p.pc("PC1", 330, 440, "192.168.10.2/24", "192.168.10.1"); p.pc("PC2", 470, 440, "192.168.10.3/24", "192.168.10.1"); p.srv("WEB", 800, 440, "192.168.20.10/24", "192.168.20.1")
    p.ip(r1, G[0], "192.168.10.1/24"); p.ip(r1, G[1], "192.168.20.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(s1, "FastEthernet0/2", "PC2", "FastEthernet0")
    p.enl(r1, G[1], s2, "GigabitEthernet0/1"); p.enl(s2, "FastEthernet0/1", "WEB", "FastEthernet0")
    p.cli(r1, "access-list 10 deny host 192.168.10.3", "access-list 10 permit any", "interface GigabitEthernet0/1", "ip access-group 10 out", "exit")
    p.prueba("PC1", "192.168.20.10", tipo="host permitido"); p.prueba("PC2", "192.168.20.10", False, "host bloqueado por la ACL"); p.prueba("PC2", "192.168.10.1", tipo="su gateway (no afectado)")
    return p


def _svi():
    p = P("svi_l3")
    mls, s1 = p.sw("MLS1", 600, 160, "3560-24PS"), p.sw("SW1", 600, 340)
    p.pc("PC1", 520, 480, "192.168.10.2/24", "192.168.10.1", 10); p.pc("PC2", 680, 480, "192.168.20.2/24", "192.168.20.1", 20)
    p.pc("PC3", 400, 300, "192.168.10.4/24", "192.168.10.1", 10); p.pc("PC4", 800, 300, "192.168.20.4/24", "192.168.20.1", 20)
    p.enl(mls, "GigabitEthernet0/1", s1, "GigabitEthernet0/1")
    p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(s1, "FastEthernet0/2", "PC2", "FastEthernet0")
    p.enl(mls, "FastEthernet0/1", "PC3", "FastEthernet0"); p.enl(mls, "FastEthernet0/2", "PC4", "FastEthernet0")
    p.d["vlans"] = [{"vlan_id": 10, "name": "VENTAS", "subnet": "192.168.10.0/24"}, {"vlan_id": 20, "name": "SOPORTE", "subnet": "192.168.20.0/24"}]
    p.d["access_ports"] = [{"switch": sw, "port": f"FastEthernet0/{pt}", "vlan_id": v} for sw, pt, v in ((s1, 1, 10), (s1, 2, 20), (mls, 1, 10), (mls, 2, 20))]
    p.d["trunks"] = [{"switch": mls, "port": "GigabitEthernet0/1", "allowed_vlans": [10, 20]}, {"switch": s1, "port": "GigabitEthernet0/1", "allowed_vlans": [10, 20]}]
    p.cli(mls, "ip routing", "interface vlan 10", "ip address 192.168.10.1 255.255.255.0", "no shutdown", "exit", "interface vlan 20", "ip address 192.168.20.1 255.255.255.0", "no shutdown", "exit")
    p.prueba("PC1", "192.168.10.1", tipo="SVI VLAN 10"); p.prueba("PC1", "192.168.10.4", tipo="misma VLAN por el trunk"); p.prueba("PC1", "192.168.20.2", tipo="entre VLANs (SVI)"); p.prueba("PC3", "192.168.20.4", tipo="entre VLANs (SVI)")
    return p


def _dns_http():
    p = P("dns_http")
    r1, s1 = p.r("R1", 600, 140), p.sw("SW1", 600, 280)
    p.pc("PC1", 450, 430, "192.168.1.2/24", "192.168.1.1"); p.pc("PC2", 600, 430, "192.168.1.3/24", "192.168.1.1"); p.srv("SRV", 750, 430, "192.168.1.10/24", "192.168.1.1")
    p.ip(r1, G[0], "192.168.1.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(s1, "FastEthernet0/2", "PC2", "FastEthernet0")
    p.enl(s1, "FastEthernet0/3", "SRV", "FastEthernet0")
    p.post = [("Servidor DNS: www.empresa.local -> 192.168.1.10", "pt_server_dns", {"device": "SRV", "records": [DNS_REG], "enable": True}),
              ("Servidor HTTP: pagina de prueba", "pt_server_http", {"device": "SRV", "pages": {"index.html": "<html><body><h1>AutoRed: servidor web OK</h1></body></html>"}, "enable": None})]
    p.post += [(f"{pc}: servidor DNS 192.168.1.10", "pt_host_ip_config", {"device": pc, "dns": "192.168.1.10"}) for pc in ("PC1", "PC2")]
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "192.168.1.10", tipo="servidor por IP")
    p.web("PC1", "http://192.168.1.10", "AutoRed", "web por IP"); p.web("PC2", "http://www.empresa.local", "AutoRed", "web por nombre (DNS)")
    return p


def _flotante():
    p = P("ruta_flotante")
    r1, r2 = p.r("R1", 400, 220), p.r("R2", 800, 220)
    s1, s2 = p.sw("SW1", 400, 360), p.sw("SW2", 800, 360)
    p.pc("PC1", 400, 500, "192.168.1.2/24", "192.168.1.1"); p.pc("PC2", 800, 500, "192.168.2.2/24", "192.168.2.1")
    p.ip(r1, G[0], "192.168.1.1/24"); p.ip(r1, G[1], "10.0.12.1/30"); p.ip(r1, G[2], "10.0.13.1/30")
    p.ip(r2, G[0], "10.0.12.2/30"); p.ip(r2, G[1], "10.0.13.2/30"); p.ip(r2, G[2], "192.168.2.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "PC1", "FastEthernet0"); p.enl(r1, G[1], r2, G[0]); p.enl(r1, G[2], r2, G[1])
    p.enl(r2, G[2], s2, "GigabitEthernet0/1"); p.enl(s2, "FastEthernet0/1", "PC2", "FastEthernet0")
    for r, d, nh, ad in ((r1, "192.168.2.0", "10.0.12.2", 1), (r1, "192.168.2.0", "10.0.13.2", 10), (r2, "192.168.1.0", "10.0.12.1", 1), (r2, "192.168.1.0", "10.0.13.1", 10)):
        p.ruta(r, d, "255.255.255.0", nh, ad)
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "192.168.2.2", tipo="por el enlace principal"); p.prueba("PC2", "192.168.1.2", tipo="por el enlace principal")
    return p


def _nat_estatico():
    p = P("nat_estatico")
    r1, isp = p.r("R1", 450, 220, "edge_router"), p.r("ISP", 750, 220, "edge_router")
    s1, s2 = p.sw("SW1", 450, 360), p.sw("SW2", 750, 360)
    p.srv("WEB", 450, 500, "192.168.10.10/24", "192.168.10.1"); p.pc("CLIENTE", 750, 500, "198.51.100.10/24", "198.51.100.1")
    p.ip(r1, G[0], "192.168.10.1/24"); p.ip(r1, G[1], "203.0.113.2/29"); p.ip(isp, G[0], "203.0.113.1/29"); p.ip(isp, G[1], "198.51.100.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1"); p.enl(s1, "FastEthernet0/1", "WEB", "FastEthernet0"); p.enl(r1, G[1], isp, G[0])
    p.enl(isp, G[1], s2, "GigabitEthernet0/1"); p.enl(s2, "FastEthernet0/1", "CLIENTE", "FastEthernet0")
    p.ruta(r1, "0.0.0.0", "0.0.0.0", "203.0.113.1")
    p.cli(r1, "interface GigabitEthernet0/0", "ip nat inside", "exit", "interface GigabitEthernet0/1", "ip nat outside", "exit", "ip nat inside source static 192.168.10.10 203.0.113.3")
    p.prueba("CLIENTE", "203.0.113.1", tipo="hacia el ISP"); p.prueba("CLIENTE", "203.0.113.3", tipo="servidor publicado (NAT estatico)")
    p.prueba("CLIENTE", "192.168.10.10", False, "la IP privada no es alcanzable")
    return p


def _ospf_stub():
    p = _multiarea()
    p.d["name"] = "ospf_stub"
    p.cli("R1", "router ospf 1", "area 1 stub", "exit"); p.cli("R2", "router ospf 1", "area 1 stub", "exit")
    return p


DNS_REG = {"name": "www.empresa.local", "type": "A", "value": "192.168.1.10"}


def T(de, a, esperado=True, tipo="ping"):
    return {"de": de, "a": a, "esperado": esperado, "tipo": tipo}


def F(id, titulo, desc, pista, on, off, pruebas=None, espera="", tools_on=(), tools_off=()):
    return {"id": id, "titulo": titulo, "descripcion": desc, "pista": pista, "cli_on": on, "cli_off": off, "pruebas": pruebas,
            "espera": espera, "tools_on": list(tools_on), "tools_off": list(tools_off)}


FALLAS = {
    "isp_nat": [
        F("sin_nat", "Se pierde la regla de NAT", "Se borró la traducción PAT en R1: los PCs ya no salen a Internet.", "Revisa show ip nat translations y la línea 'ip nat inside source' en R1.",
          {"R1": ["no ip nat inside source list 1 interface GigabitEthernet0/1 overload"]}, {"R1": ["ip nat inside source list 1 interface GigabitEthernet0/1 overload"]},
          [T("PC1", "192.168.10.1"), T("PC1", "203.0.113.1", False, "hacia el ISP (sin NAT)"), T("PC1", "198.51.100.10", False, "a Internet (sin NAT)")]),
        F("sin_ruta", "Se borra la ruta por defecto", "R1 ya no sabe cómo llegar a Internet, aunque el NAT sigue.", "Mira show ip route en R1: falta 0.0.0.0/0.",
          {"R1": ["no ip route 0.0.0.0 0.0.0.0 203.0.113.1"]}, {"R1": ["ip route 0.0.0.0 0.0.0.0 203.0.113.1"]},
          [T("PC1", "192.168.10.1"), T("PC1", "203.0.113.1", True, "hacia el ISP (red conectada)"), T("PC1", "198.51.100.10", False, "a Internet (sin ruta)")]),
    ],
    "vlans_trunk": [F("trunk_sin_vlan20", "El trunk deja de permitir la VLAN 20", "SW1 solo deja pasar la VLAN 10 hacia el router.", "Mira show interfaces trunk en SW1.",
                      {"SW1": ["interface GigabitEthernet0/1", "switchport trunk allowed vlan 10"]}, {"SW1": ["interface GigabitEthernet0/1", "switchport trunk allowed vlan 10,20"]},
                      [T("PC1", "192.168.10.1"), T("PC2", "192.168.20.1", False, "VLAN 20 sin gateway"), T("PC1", "192.168.20.2", False, "entre VLANs")])],
    "rutas_estaticas": [F("sin_retorno", "Falta la ruta de retorno en R3", "R3 ya no conoce la LAN de PC1: el eco llega pero la respuesta no vuelve.", "Compara show ip route en R1 y en R3.",
                          {"R3": ["no ip route 192.168.1.0 255.255.255.0 10.0.23.1"]}, {"R3": ["ip route 192.168.1.0 255.255.255.0 10.0.23.1"]},
                          [T("PC1", "192.168.1.1"), T("PC1", "192.168.3.2", False, "la respuesta no vuelve"), T("PC2", "192.168.1.2", False, "R3 no conoce la LAN de PC1")])],
    "acl_estandar": [F("acl_sin_aplicar", "La ACL deja de aplicarse", "Se quitó 'ip access-group' de la interfaz: el host bloqueado vuelve a pasar.", "Usa show access-lists y show ip interface GigabitEthernet0/1 en R1.",
                       {"R1": ["interface GigabitEthernet0/1", "no ip access-group 10 out"]}, {"R1": ["interface GigabitEthernet0/1", "ip access-group 10 out"]})],
    "ospf_auth": [F("md5_distinta", "Clave MD5 distinta en R2", "R2 cambia la clave de su enlace con R1: la adyacencia OSPF cae.", "Mira show ip ospf neighbor en R1 y R2.",
                    {"R2": ["interface GigabitEthernet0/0", "ip ospf message-digest-key 1 md5 Otra123"]}, {"R2": ["interface GigabitEthernet0/0", "ip ospf message-digest-key 1 md5 Cisco123"]},
                    espera="OSPF tarda unos 40 s en dar de baja al vecino: espera y verifica.")],
    "ospf_stub": [F("stub_inconsistente", "R1 deja de ser área stub", "El indicador de área stub ya no coincide entre R1 y R2.", "Compara show ip ospf en R1 y R2.",
                    {"R1": ["router ospf 1", "no area 1 stub"]}, {"R1": ["router ospf 1", "area 1 stub"]}, espera="OSPF tarda unos 40 s en dar de baja al vecino.")],
    "ebgp": [F("as_incorrecto", "AS remoto equivocado", "R1 espera que su vecino sea el AS 65099, y es el 65002.", "Mira show ip bgp summary en R1.",
               {"R1": ["router bgp 65001", "no neighbor 203.0.113.2", "neighbor 203.0.113.2 remote-as 65099"]},
               {"R1": ["router bgp 65001", "no neighbor 203.0.113.2", "neighbor 203.0.113.2 remote-as 65002"]}, espera="BGP tarda unos segundos en reiniciar la sesión.")],
    "gre_tunel": [F("destino_incorrecto", "Destino del túnel equivocado", "R1 apunta el túnel a una IP que no existe.", "Mira show interfaces tunnel 0 y show ip interface brief en R1.",
                    {"R1": ["interface Tunnel0", "tunnel destination 203.0.113.99"]}, {"R1": ["interface Tunnel0", "tunnel destination 203.0.113.2"]})],
    "hsrp": [F("cae_r1", "Cae el router activo (R1)", "R1 apaga sus interfaces: R2 debe tomar la IP virtual.", "Mira show standby brief en R2.",
               {"R1": ["interface GigabitEthernet0/0", "shutdown", "interface GigabitEthernet0/1", "shutdown"]},
               {"R1": ["interface GigabitEthernet0/0", "no shutdown", "interface GigabitEthernet0/1", "no shutdown"]},
               [T("PC1", "192.168.1.1", True, "IP virtual (R2 activo)"), T("PC1", "192.168.1.2", False, "R1 caído"), T("PC1", "172.16.0.10", True, "servidor vía R2")],
               espera="HSRP tarda unos segundos en cambiar de router activo.")],
    "ruta_flotante": [F("cae_principal", "Cae el enlace principal", "R1 apaga el enlace principal: el tráfico debe seguir por el respaldo.", "Compara show ip route en R1 antes y después.",
                        {"R1": ["interface GigabitEthernet0/1", "shutdown"]}, {"R1": ["interface GigabitEthernet0/1", "no shutdown"]}, espera="Espera unos segundos a que cambie la ruta.")],
    "etherchannel": [F("cae_un_enlace", "Cae un enlace del canal", "SW1 apaga Fa0/23: el canal sigue con un solo enlace.", "Mira show etherchannel summary en SW1.",
                       {"SW1": ["interface FastEthernet0/23", "shutdown"]}, {"SW1": ["interface FastEthernet0/23", "no shutdown"]})],
    "stp_root": [F("cae_sw1_sw2", "Cae el enlace SW1–SW2", "STP debe reconverger por SW3.", "Mira show spanning-tree en SW3.",
                   {"SW1": ["interface GigabitEthernet0/1", "shutdown"]}, {"SW1": ["interface GigabitEthernet0/1", "no shutdown"]}, espera="Rapid-PVST reconverge en pocos segundos.")],
    "svi_l3": [F("svi20_apagada", "SVI de la VLAN 20 apagada", "El switch multicapa ya no enruta hacia la VLAN 20.", "Mira show ip interface brief en MLS1.",
                 {"MLS1": ["interface vlan 20", "shutdown"]}, {"MLS1": ["interface vlan 20", "no shutdown"]},
                 [T("PC1", "192.168.10.1", True, "SVI VLAN 10"), T("PC1", "192.168.10.4", True, "misma VLAN"), T("PC1", "192.168.20.2", False, "entre VLANs (sin SVI 20)")])],
    "redistribucion": [F("sin_redistribucion", "Falta redistribuir EIGRP en OSPF", "R1 ya no aprende la LAN de R3.", "Mira show ip route en R1.",
                         {"R2": ["router ospf 1", "no redistribute eigrp 100 subnets"]}, {"R2": ["router ospf 1", "redistribute eigrp 100 subnets"]})],
    "ospf_multiarea": [F("red_sin_anunciar", "R3 deja de anunciar su LAN", "Se borró el comando network de la LAN de R3.", "Mira show ip route ospf en R1.",
                         {"R3": ["router ospf 1", "no network 192.168.3.0 0.0.0.255 area 0"]}, {"R3": ["router ospf 1", "network 192.168.3.0 0.0.0.255 area 0"]})],
    "ripv2": [F("red_sin_anunciar", "R3 deja de anunciar su LAN", "Se borró el network de la LAN de R3 en RIP.", "Mira show ip route rip en R1.",
                {"R3": ["router rip", "no network 192.168.3.0"]}, {"R3": ["router rip", "network 192.168.3.0"]}, espera="RIP puede tardar ~30 s en reflejar el cambio.")],
    "ospf_default": [F("sin_default_originate", "R2 deja de propagar la ruta por defecto", "R1 ya no aprende cómo salir hacia el ISP.", "Mira show ip route en R1: falta O*E2.",
                       {"R2": ["router ospf 1", "no default-information originate"]}, {"R2": ["router ospf 1", "default-information originate"]})],
    "nat_estatico": [F("sin_nat_estatico", "Falta el NAT estático", "Se borró la regla que publica el servidor.", "Mira show ip nat translations en R1.",
                       {"R1": ["no ip nat inside source static 192.168.10.10 203.0.113.3"]}, {"R1": ["ip nat inside source static 192.168.10.10 203.0.113.3"]})],
    "dns_http": [F("sin_dns", "El DNS pierde el registro", "Se borró www.empresa.local del servidor DNS: solo funciona el acceso por IP.", "Prueba nslookup www.empresa.local desde un PC.", {}, {},
                   [T("PC1", "192.168.1.10", True, "servidor por IP"), {"de": "PC2", "a": "http://www.empresa.local", "esperado": False, "tipo": "web por nombre (sin DNS)", "metodo": "web", "contiene": "AutoRed"}],
                   tools_on=[("pt_server_dns", {"device": "SRV", "remove": [DNS_REG], "enable": None})], tools_off=[("pt_server_dns", {"device": "SRV", "records": [DNS_REG], "enable": True})])],
}

LECTURAS = {
    "hsrp": [("R1", "show standby brief", "HSRP en R1"), ("R2", "show standby brief", "HSRP en R2")],
    "etherchannel": [("SW1", "show etherchannel summary", "EtherChannel en SW1")],
    "ebgp": [("R1", "show ip bgp summary", "Sesión BGP en R1")],
    "gre_tunel": [("R1", "show ip interface brief", "Interfaces (túnel) en R1")],
    "dhcp_relay": [("R2", "show ip dhcp binding", "Direcciones entregadas por R2")],
    "ripv2": [("R1", "show ip route rip", "Rutas RIP en R1")],
    "svi_l3": [("MLS1", "show vlan brief", "VLAN en MLS1"), ("MLS1", "show ip route", "Rutas en MLS1")],
    "ruta_flotante": [("R1", "show ip route static", "Rutas estáticas en R1")],
    "nat_estatico": [("R1", "show ip nat translations", "NAT en R1")],
    "ospf_stub": [("R1", "show ip route ospf", "Rutas OSPF en R1 (busca O*IA)")],
    "dns_http": [("R1", "show ip interface brief", "Interfaces de R1")],
}


def _cdp():
    p = _cadena("cdp_vecinos")
    p.prueba("PC1", "192.168.1.1")
    return p


def _ospf_dr():
    p = P("ospf_dr")
    sw = p.sw("SW1", 600, 220)
    for i, x in enumerate((350, 600, 850), 1):
        r, lan = p.r(f"R{i}", x, 70), p.sw(f"SW{i + 1}", x, 400)
        p.ip(r, G[0], f"10.0.0.{i}/24"); p.ip(r, G[1], f"192.168.{i}.1/24")
        p.pc(f"PC{i}", x, 530, f"192.168.{i}.2/24", f"192.168.{i}.1")
        p.enl(r, G[0], sw, f"FastEthernet0/{i}"); p.enl(r, G[1], lan, "GigabitEthernet0/1"); p.enl(lan, "FastEthernet0/1", f"PC{i}", "FastEthernet0")
        p.ospf(r, [("10.0.0.0", W24, 0), (f"192.168.{i}.0", W24, 0)], f"{i}.{i}.{i}.{i}")
        p.cli(r, "interface GigabitEthernet0/0", f"ip ospf priority {(255, 100, 0)[i - 1]}", "exit")
    p.post = [(f"Reiniciar OSPF en R{i} (aplicar prioridades)", "pt_cli", {"device": f"R{i}", "commands": ["enable", "clear ip ospf process", "yes"], "timeout": 30})
              for i in (1, 2, 3)]
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "192.168.3.2", tipo="extremo a extremo"); p.prueba("PC2", "192.168.3.2", tipo="extremo a extremo")
    return p


def _port_sec():
    p = P("port_security")
    r1, s1 = p.r("R1", 600, 120), p.sw("SW1", 600, 270)
    p.ip(r1, G[0], "192.168.1.1/24")
    p.enl(r1, G[0], s1, "GigabitEthernet0/1")
    for i, x in enumerate((450, 600, 750), 1):
        p.pc(f"PC{i}", x, 430, f"192.168.1.{i + 1}/24", "192.168.1.1")
        p.enl(s1, f"FastEthernet0/{i}", f"PC{i}", "FastEthernet0")
    p.cli("SW1", "interface range FastEthernet0/1 - 3", "switchport mode access", "switchport port-security", "switchport port-security maximum 1",
          "switchport port-security violation restrict", "switchport port-security mac-address sticky", "exit")
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "192.168.1.3", tipo="entre PCs con port security"); p.prueba("PC3", "192.168.1.2", tipo="entre PCs con port security")
    return p


def _eigrp():
    p = _cadena("eigrp_basico")
    for r, redes in (("R1", [("192.168.1.0", W24), ("10.0.12.0", W30)]), ("R2", [("10.0.12.0", W30), ("10.0.23.0", W30)]),
                     ("R3", [("10.0.23.0", W30), ("192.168.3.0", W24)])):
        p.d["eigrp_configs"].append({"router": r, "as_number": 100, "networks": [{"network": n, "wildcard": w} for n, w in redes], "no_auto_summary": True})
    p.prueba("PC1", "192.168.1.1"); p.prueba("PC1", "192.168.3.2", tipo="extremo a extremo (EIGRP)"); p.prueba("PC2", "192.168.1.2", tipo="extremo a extremo (EIGRP)")
    return p


def _ospf_resumen():
    p = _multiarea()
    p.d["name"] = "ospf_resumen"
    p.cli("R2", "router ospf 1", "area 1 range 192.168.0.0 255.255.252.0", "exit")
    return p


# Comprobaciones de ESTADO (salida de show): (equipo, comando, expresion regular, descripcion, minimo de coincidencias)
ESTADOS = {
    "vlans_trunk": [("SW1", "show vlan brief", r"10\s+VENTAS\s+active", "VLAN 10 activa en SW1", 1)],
    "rutas_estaticas": [("R1", "show ip route", r"^S\s+192\.168\.3\.0", "R1 tiene la ruta estatica a la LAN de R3", 1)],
    "isp_nat": [("R1", "show ip nat translations", r"203\.0\.113\.2", "R1 tiene traducciones PAT activas", 1)],
    "dhcp_relay": [("R2", "show ip dhcp binding", r"192\.168\.1\.\d+", "R2 entrego direcciones a la LAN de R1", 2)],
    "hsrp": [("R1", "show standby brief", r"Active\s+local", "R1 es el router activo", 1), ("R2", "show standby brief", r"\bStandby\s+\d+\.\d+\.\d+\.\d+\s+local\b", "R2 es el router en espera", 1)],
    "etherchannel": [("SW1", "show etherchannel summary", r"Po1\(SU\)", "Port-channel 1 en uso (SU)", 1)],
    "stp_root": [("SW1", "show spanning-tree", r"This bridge is the root", "SW1 es la raiz de STP", 1)],
    "acl_servidor": [("R1", "show access-lists", r"deny\s+ip 192\.168\.10\.0 0\.0\.0\.255 192\.168\.20\.0 0\.0\.0\.255\s+\(\d+ match", "la ACL esta contando bloqueos", 1)],
    "ripv2": [("R1", "show ip route", r"^R\s+192\.168\.3\.0", "R1 aprendio la LAN de R3 por RIP", 1)],
    "ospf_default": [("R1", "show ip route", r"^O\*E2\s+0\.0\.0\.0", "R1 aprendio la ruta por defecto por OSPF", 1)],
    "acl_estandar": [("R1", "show access-lists", r"deny\s+(host\s+)?192\.168\.10\.3\s+\(\d+ match", "la ACL esta contando bloqueos", 1)],
    "svi_l3": [("MLS1", "show ip interface brief", r"Vlan10\s+192\.168\.10\.1\s+YES\s+\S+\s+up\s+up", "SVI de la VLAN 10 activa", 1)],
    "ruta_flotante": [("R1", "show ip route", r"^S\s+192\.168\.2\.0\S*\s+\[1/0\] via 10\.0\.12\.2", "ruta principal en uso", 1)],
    "nat_estatico": [("R1", "show ip nat translations", r"203\.0\.113\.3\s+192\.168\.10\.10", "NAT estatico publicado", 1)],
    "ospf_multiarea": [("R1", "show ip route", r"^O IA\s+192\.168\.3\.0", "R1 ve la LAN de R3 como ruta entre areas", 1)],
    "ospf_stub": [("R1", "show ip route", r"^O\*IA\s+0\.0\.0\.0", "R1 recibio la ruta por defecto del ABR", 1)],
    "redistribucion": [("R1", "show ip route", r"^O E2\s+192\.168\.3\.0", "R1 aprendio la LAN EIGRP por redistribucion", 1)],
    "ospf_auth": [("R2", "show ip ospf neighbor", r"FULL", "R2 tiene 2 vecinos OSPF en FULL", 2)],
    "ebgp": [("R1", "show ip bgp summary", r"^\s*203\.0\.113\.2.*\s\d+\s*$", "sesion BGP establecida", 1)],
    "gre_tunel": [("R1", "show interfaces tunnel 0", r"Tunnel0 is up, line protocol is up", "tunel GRE arriba", 1)],
    "cdp_vecinos": [("R2", "show cdp neighbors", r"\bR1\b", "R2 ve a R1 por CDP", 1), ("R2", "show cdp neighbors", r"\bR3\b", "R2 ve a R3 por CDP", 1),
                    ("R1", "show cdp neighbors", r"\bSW1\b", "R1 ve a SW1 por CDP", 1),
                    ("R2", "show ip interface brief", r"GigabitEthernet0/1\s+10\.0\.23\.1\s+YES\s+\S+\s+up\s+up", "el enlace R2-R3 esta arriba (diagnostico)", 1),
                    ("R3", "show cdp neighbors", r"\bR2\b", "R3 ve a R2 por CDP (diagnostico)", 1)],
    "ospf_dr": [("R3", "show ip ospf neighbor", r"FULL/DR\b", "R3 ve a R1 como DR", 1), ("R3", "show ip ospf neighbor", r"FULL/BDR", "R3 ve a R2 como BDR", 1)],
    "port_security": [("SW1", "show port-security interface FastEthernet0/1", r"Port Security\s*:\s*Enabled", "port security activo en Fa0/1", 1)],
    "eigrp_basico": [("R2", "show ip eigrp neighbors", r"10\.0\.12\.1", "R2 tiene a R1 como vecino EIGRP", 1), ("R2", "show ip eigrp neighbors", r"10\.0\.23\.2", "R2 tiene a R3 como vecino EIGRP", 1)],
    "ospf_resumen": [("R3", "show ip route", r"^O IA\s+192\.168\.0\.0/22", "R3 recibe la ruta resumida /22", 1)],
}

FALLAS.update({
    "cdp_vecinos": [F("sin_cdp", "R2 apaga CDP", "R2 deja de enviar y recibir anuncios CDP.", "Compara show cdp neighbors en R1 y en R2.", {"R2": ["no cdp run"]}, {"R2": ["cdp run"]})],
    "ospf_dr": [F("cae_dr", "Cae el DR (R1)", "R1 apaga su interfaz hacia el segmento: R2 debe pasar a ser DR.", "Mira show ip ospf neighbor en R3 después de ~40 s.",
                  {"R1": ["interface GigabitEthernet0/0", "shutdown"]}, {"R1": ["interface GigabitEthernet0/0", "no shutdown"]},
                  [T("PC2", "192.168.3.2", True, "R2 y R3 siguen conectados"), T("PC1", "192.168.3.2", False, "R1 quedó aislado")], espera="OSPF tarda ~40 s en reelegir al DR.")],
    "port_security": [F("sin_port_security", "Port security apagado en Fa0/1", "Se quitó la protección de ese puerto.", "Mira show port-security en SW1.",
                        {"SW1": ["interface FastEthernet0/1", "no switchport port-security"]},
                        {"SW1": ["interface FastEthernet0/1", "switchport port-security", "switchport port-security maximum 1", "switchport port-security violation restrict",
                                 "switchport port-security mac-address sticky"]})],
    "eigrp_basico": [F("red_sin_anunciar", "R3 deja de anunciar su LAN", "Se borró el network de la LAN de R3 en EIGRP.", "Mira show ip route en R1.",
                       {"R3": ["router eigrp 100", "no network 192.168.3.0 0.0.0.255"]}, {"R3": ["router eigrp 100", "network 192.168.3.0 0.0.0.255"]})],
    "ospf_resumen": [F("sin_resumen", "Se quita la sumarización del ABR", "R3 vuelve a ver la ruta específica /24 en lugar del resumen.", "Mira show ip route ospf en R3.",
                       {"R2": ["router ospf 1", "no area 1 range 192.168.0.0 255.255.252.0"]}, {"R2": ["router ospf 1", "area 1 range 192.168.0.0 255.255.252.0"]})],
})
FALLAS["ruta_flotante"][0]["pruebas"] = [T("PC1", "192.168.1.1"), T("PC1", "192.168.2.2", True, "por el enlace de respaldo"),
                                         {"de": "R1", "a": "show ip route", "esperado": True, "tipo": "ruta de respaldo en uso", "metodo": "cli",
                                          "patron": r"\[10/0\] via 10\.0\.13\.2", "minimo": 1}]
FALLAS["etherchannel"][0]["sigue"] = True  # demuestran redundancia: la red debe seguir funcionando
FALLAS["stp_root"][0]["sigue"] = True
LECTURAS.update({
    "cdp_vecinos": [("R2", "show cdp neighbors", "Vecinos CDP de R2")], "ospf_dr": [("R3", "show ip ospf neighbor", "Vecinos OSPF y DR/BDR en R3")],
    "port_security": [("SW1", "show port-security", "Resumen de port security")], "eigrp_basico": [("R2", "show ip eigrp neighbors", "Vecinos EIGRP de R2")],
    "ospf_resumen": [("R3", "show ip route ospf", "Rutas OSPF en R3")],
})


def _con_estados(gen, id):
    def f():
        p = gen()
        for dispositivo, comando, patron, tipo, minimo in ESTADOS.get(id, []):
            p.estado(dispositivo, comando, patron, tipo, minimo)
        return p
    return f


def _pl(id, nivel, cat, titulo, desc, frase, claves=()):
    return {"id": id, "nivel": nivel, "categoria": cat, "titulo": titulo, "descripcion": desc, "tipo": "plantilla", "frase": frase, "claves": claves, "nota": ""}


def _lab(id, nivel, cat, titulo, desc, gen, claves, nota=""):
    return {"id": id, "nivel": nivel, "categoria": cat, "titulo": titulo, "descripcion": desc, "tipo": "laboratorio", "gen": gen, "claves": claves, "nota": nota}


CATALOGO = [
    _pl("lan_simple", "CCNA", "Fundamentos", "LAN simple con DHCP", "Un router, un switch y 4 PCs que reciben IP por DHCP.", "oficina con 1 router y 4 pcs"),
    _pl("estrella", "CCNA", "Fundamentos", "Red en estrella (varias LAN)", "Un router central con 3 switches, cada uno con su LAN y DHCP.", "red estrella con 3 switches y 2 pcs"),
    _pl("vlan_roas", "CCNA", "Acceso", "Router on a stick (3 VLANs)", "Un router con subinterfaces 802.1Q para enrutar entre 3 VLANs.", "router on a stick con 3 vlans"),
    _pl("triangulo_ospf", "CCNA", "Enrutamiento", "Triángulo de routers con OSPF", "Tres routers en triángulo, cada uno con su LAN, enrutados por OSPF.", "triangulo de 3 routers con ospf y dhcp"),
    _pl("hub_eigrp", "CCNA", "Enrutamiento", "Hub and spoke con EIGRP", "Un router central y 3 sucursales conectadas con EIGRP.", "red estrella con 4 routers eigrp"),
    _pl("wifi", "CCNA", "Inalámbrico", "Oficina con WiFi", "LAN con punto de acceso y laptops inalámbricas.", "oficina con wifi y 2 laptops"),
    _pl("ipv6", "CCNA", "Fundamentos", "Red dual-stack IPv4/IPv6", "Varios routers con OSPF y direccionamiento IPv6 además de IPv4.", "red con 3 routers ospf e ipv6"),
    _pl("wan_nube", "CCNA", "WAN", "Sucursales con salida a la nube (WAN)", "Varias LAN y un router conectado a una nube WAN.", "red con 2 routers y salida a internet"),
    _lab("vlans_trunk", "CCNA", "Acceso", "VLANs y trunk entre dos switches", "VLAN 10 y 20 repartidas en 2 switches con trunk 802.1Q y enrutamiento entre VLANs en el router.", _vlans_trunk,
         ("trunk entre switches", "vlans en dos switches")),
    _lab("rutas_estaticas", "CCNA", "Enrutamiento", "Rutas estáticas, 3 routers en línea", "Dos LAN en los extremos unidas por R2, con todas las rutas estáticas manuales.", _estaticas, ("tres routers en linea", "rutas estaticas manuales")),
    _lab("isp_nat", "CCNA", "Servicios IP", "Salida a Internet: NAT/PAT con ISP y servidor", "LAN privada, router de borde con PAT, un ISP y un servidor web público para comprobar la salida con ping.", _isp_nat,
         ("isp con nat", "servidor de internet", "nat con isp")),
    _lab("dhcp_relay", "CCNA", "Servicios IP", "DHCP centralizado con relay", "El router R2 entrega direcciones a la LAN de R1 usando ip helper-address.", _relay, ("helper address", "dhcp relay", "dhcp centralizado")),
    _lab("hsrp", "CCNA", "Redundancia", "HSRP: puerta de enlace redundante", "Dos routers comparten la IP virtual 192.168.1.1 (R1 activo, R2 en espera) hacia un núcleo con OSPF.", _hsrp, ("hsrp", "gateway redundante", "puerta de enlace redundante"),
         "Packet Tracer puede tardar unos segundos en elegir el router activo."),
    _lab("etherchannel", "CCNA", "Acceso", "EtherChannel LACP entre switches", "Dos enlaces agrupados en un Port-channel trunk entre SW1 y SW2.", _etherchannel, ("etherchannel", "lacp", "port channel"),
         "El canal tarda ~30 s en levantar: si la verificación falla, espera y repite."),
    _lab("stp_root", "CCNA", "Acceso", "STP: root bridge, PortFast y BPDU Guard", "Tres switches en anillo con SW1 como raíz (prioridad 4096), Rapid-PVST y protección de puertos de acceso.", _stp, ("spanning tree", "root bridge", "stp", "bpdu guard"),
         "Espera ~30 s a que converja STP antes de verificar."),
    _lab("acl_servidor", "CCNA", "Seguridad", "ACL extendida: solo web hacia el servidor", "Los usuarios solo pueden llegar al servidor por el puerto 80; el ping queda bloqueado.", _acl_web, ("acl extendida", "solo web", "acl al servidor"),
         "La verificación usa ping, que la ACL bloquea a propósito; el acceso web se puede comprobar a mano desde el navegador del PC."),
    _lab("ospf_multiarea", "CCNP", "Enrutamiento", "OSPF multiárea con ABR", "R2 es el ABR entre el área 1 (R1) y el área 0 (R3).", _multiarea, ("multiarea", "multi area", "abr", "areas ospf")),
    _lab("redistribucion", "CCNP", "Enrutamiento", "Redistribución OSPF ↔ EIGRP", "R2 conecta un dominio OSPF con otro EIGRP y redistribuye en ambos sentidos.", _redistrib, ("redistribucion", "redistribuir")),
    _lab("ebgp", "CCNP", "Enrutamiento", "eBGP entre dos sistemas autónomos", "AS 65001 y AS 65002 intercambian sus LAN por eBGP.", _bgp, ("bgp", "ebgp", "sistemas autonomos")),
    _lab("gre_tunel", "CCNP", "VPN", "Túnel GRE entre sedes", "Dos sedes unidas por un túnel GRE sobre una red pública, con rutas hacia las LAN por el túnel.", _gre, ("gre", "tunel gre", "tunel")),
    _lab("ripv2", "CCNA", "Enrutamiento", "RIPv2 con 3 routers", "R1, R2 y R3 intercambian rutas con RIP versión 2 sin autosumarización.", _rip, ("ripv2", "rip v2", "rip version 2"),
         "RIP tarda ~30 s o más en converger: si la verificación falla, espera y repite."),
    _lab("ospf_default", "CCNA", "Enrutamiento", "OSPF con ruta por defecto hacia el ISP", "R2 tiene una ruta estática por defecto hacia el ISP y la propaga a R1 con default-information originate.", _ospf_default,
         ("ruta por defecto ospf", "default information", "originate")),
    _lab("acl_estandar", "CCNA", "Seguridad", "ACL estándar: bloquear un host", "Una ACL estándar en la salida hacia el servidor bloquea solo a PC2.", _acl_est, ("acl estandar", "acl estandard", "bloquear un host")),
    _lab("svi_l3", "CCNA", "Acceso", "Switch multicapa 3560: routing entre VLANs con SVI", "Un 3560 enruta entre las VLAN 10 y 20 con interfaces SVI, sin usar un router.", _svi, ("switch multicapa", "svi", "capa 3 switch", "3560")),
    _lab("ospf_auth", "CCNP", "Enrutamiento", "OSPF con autenticación MD5", "Los vecinos OSPF de R1, R2 y R3 se autentican con una clave MD5 en cada enlace.", _ospf_auth, ("autenticacion ospf", "md5 ospf", "ospf md5", "ospf con autenticacion")),
    _lab("dns_http", "CCNA", "Servicios IP", "Servicios de red: DNS y servidor web", "Un servidor con DNS (www.empresa.local) y HTTP; los PCs abren la página por IP y por nombre.", _dns_http,
         ("dns", "servidor web", "http", "nombre de dominio")),
    _lab("ruta_flotante", "CCNA", "Enrutamiento", "Rutas estáticas flotantes (enlace de respaldo)", "Dos enlaces entre R1 y R2: el principal con distancia administrativa 1 y el de respaldo con 10.", _flotante,
         ("flotante", "enlace de respaldo", "distancia administrativa", "respaldo")),
    _lab("nat_estatico", "CCNA", "Servicios IP", "NAT estático: publicar un servidor interno", "El servidor 192.168.10.10 se publica en 203.0.113.3 y un cliente de Internet lo alcanza por esa dirección.", _nat_estatico,
         ("nat estatico", "publicar un servidor", "port forwarding")),
    _lab("ospf_stub", "CCNP", "Enrutamiento", "OSPF área stub", "El área 1 es stub: R1 recibe una ruta por defecto del ABR en lugar de las rutas externas.", _ospf_stub, ("area stub", "ospf stub", "stub area", "stub")),
    _lab("cdp_vecinos", "CCNA", "Fundamentos", "CDP: descubrimiento de vecinos", "Routers y switches descubren a sus vecinos directos con CDP y la app comprueba la tabla de vecinos.", _cdp,
         ("cdp", "descubrimiento de vecinos", "vecinos cdp", "lldp")),
    _lab("ospf_dr", "CCNA", "Enrutamiento", "OSPF: elección de DR y BDR", "Tres routers en un segmento multiacceso: R1 (prioridad 255) es DR, R2 (100) es BDR y R3 (0) nunca puede serlo.", _ospf_dr,
         ("dr y bdr", "eleccion de dr", "designated router", "prioridad ospf", "multiacceso"), "Se reinician los procesos OSPF para aplicar las prioridades: espera ~1 minuto antes de verificar."),
    _lab("port_security", "CCNA", "Seguridad", "Port security en puertos de acceso", "Los puertos del switch aceptan una sola MAC (sticky) y registran las violaciones sin apagarse.", _port_sec,
         ("port security", "seguridad de puertos", "mac sticky")),
    _lab("eigrp_basico", "CCNP", "Enrutamiento", "EIGRP básico con 3 routers", "R1, R2 y R3 forman vecindad EIGRP (AS 100) y se comparten sus LAN.", _eigrp, ("eigrp", "vecinos eigrp")),
    _lab("ospf_resumen", "CCNP", "Enrutamiento", "OSPF: sumarización en el ABR", "El ABR resume la LAN del área 1 como 192.168.0.0/22 hacia el área 0.", _ospf_resumen,
         ("sumarizacion", "sumarizar", "resumen de rutas", "area range", "summary")),
]

for _e in CATALOGO:
    _e["fallas"] = FALLAS.get(_e["id"], [])
    _e["lecturas"] = LECTURAS.get(_e["id"], [])
    if _e["tipo"] == "laboratorio":
        _e["gen"] = _con_estados(_e["gen"], _e["id"])


def listar():
    salida = []
    for e in CATALOGO:
        m = {k: v for k, v in e.items() if k not in ("gen", "claves", "fallas", "lecturas")}
        if e["tipo"] == "laboratorio":
            d = e["gen"]().d
            m["param_pcs"] = not d["vlans"] and not d["dhcp_pools"]
            m["n_fallas"] = len(e["fallas"])
        salida.append(m)
    return salida


def obtener(id):
    return next((e for e in CATALOGO if e["id"] == id), None)


def buscar(texto_normalizado):
    """Sugiere un laboratorio si la frase menciona sus palabras clave (no sustituye a las plantillas)."""
    for e in CATALOGO:
        if any(c in texto_normalizado for c in e["claves"]):
            return e["id"]
    return None


_STOP = {"de", "la", "el", "los", "las", "con", "y", "en", "un", "una", "por", "entre", "al", "del", "hacia", "red", "redes", "para", "que",
         "laboratorio", "quiero", "hacer", "armar", "crear", "mas", "sin", "como"}


def _n(texto):
    t = unicodedata.normalize("NFD", texto.lower())
    return re.sub(r"[^a-z0-9 ]+", " ", "".join(ch for ch in t if unicodedata.category(ch) != "Mn"))


def sugerir(texto_normalizado, n=3):
    """Hasta n laboratorios cuyas palabras clave, titulo o descripcion coinciden con la frase (los mejores primero)."""
    palabras = {w for w in texto_normalizado.split() if len(w) > 2 and w not in _STOP}
    puntos = []
    for e in CATALOGO:
        if e["tipo"] != "laboratorio":
            continue
        base = {w for w in _n(e["titulo"] + " " + e["descripcion"]).split() if len(w) > 2 and w not in _STOP}
        puntos.append((sum(3 for k in e["claves"] if k in texto_normalizado) + len(palabras & base), e["id"]))
    return [i for p, i in sorted(puntos, reverse=True)[:n] if p >= 3]


def fallas_meta(lab):
    return [{k: f[k] for k in ("id", "titulo", "descripcion", "pista", "espera")} for f in lab.get("fallas", [])]


def con_pcs(d, n):
    """Lleva cada LAN que ya tiene PCs a n PCs (misma subred, siguientes IP libres). Devuelve [(nombre, gateway)] de los nuevos."""
    if d["vlans"] or d["dhcp_pools"]:
        return []
    dev = {x["name"]: x for x in d["devices"]}
    usados = {}
    for lk in d["links"]:
        usados.setdefault(lk["device_a"], set()).add(lk["port_a"])
        usados.setdefault(lk["device_b"], set()).add(lk["port_b"])
    ips = {ci.split("/")[0] for x in d["devices"] for ci in x["interfaces"].values()}
    num = max([int(re.sub(r"\D", "", x["name"]) or 0) for x in d["devices"] if x["category"] == "pc"] + [0])
    nuevos = []
    for sw in [x for x in d["devices"] if x["category"] == "switch"]:
        pcs = [dev[o] for lk in d["links"] for a, o in ((lk["device_a"], lk["device_b"]), (lk["device_b"], lk["device_a"]))
               if a == sw["name"] and dev[o]["category"] == "pc"]
        if not pcs or len(pcs) >= n:
            continue
        red = ipaddress.ip_interface(pcs[0]["interfaces"]["FastEthernet0"]).network
        faltan = n - len(pcs)
        for i in range(faltan):
            ip = next(str(h) for h in red.hosts() if str(h) not in ips and int(str(h).split(".")[-1]) >= 2)
            puerto = next(f"FastEthernet0/{k}" for k in range(1, 25) if f"FastEthernet0/{k}" not in usados.get(sw["name"], set()))
            num += 1
            nombre = f"PC{num}"
            d["devices"].append({"name": nombre, "model": "PC-PT", "category": "pc", "role": "end_host", "x": int(sw["x"] + (i - (faltan - 1) / 2) * 110),
                                 "y": sw["y"] + 250, "interfaces": {"FastEthernet0": f"{ip}/{red.prefixlen}"}, "gateway": pcs[0]["gateway"], "vlan": 0})
            d["links"].append({"device_a": sw["name"], "port_a": puerto, "device_b": nombre, "port_b": "FastEthernet0", "cable": "straight"})
            ips.add(ip)
            usados.setdefault(sw["name"], set()).add(puerto)
            nuevos.append((nombre, pcs[0]["gateway"]))
    return nuevos