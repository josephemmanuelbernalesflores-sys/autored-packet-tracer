"""AutoRed - programa de escritorio. Arranca el servidor y abre la interfaz en una ventana propia (sin barra de navegador).
Al cerrar la ventana, se detiene todo."""
import ctypes
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

AQUI = Path(__file__).resolve().parent
URL, PUERTO = "http://127.0.0.1:5000", 5000
NT = os.name == "nt"


def aviso(texto):
    if NT:
        ctypes.windll.user32.MessageBoxW(0, texto, "AutoRed", 0x40)
    else:
        print("AutoRed:", texto)


def navegador():
    """Edge o Chrome en modo aplicacion (una ventana sin pestanas ni barra de direcciones)."""
    if os.environ.get("AUTORED_BROWSER"):
        return os.environ["AUTORED_BROWSER"]
    rutas = [os.path.join(os.environ.get(v, ""), r) for r in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe")
             for v in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData")]  # Chrome primero: no inicia sesion ni carga extensiones solo
    return next((r for r in rutas if os.path.isfile(r)), shutil.which("msedge") or shutil.which("chrome"))


def puerto_libre():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PUERTO)) != 0


def listo():
    try:
        return urllib.request.urlopen(URL + "/api/config", timeout=1).status == 200
    except OSError:
        return False


def splash():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.overrideredirect(True)
        r.configure(bg="#0f2d4d")
        w, h = 380, 130
        r.geometry(f"{w}x{h}+{(r.winfo_screenwidth() - w) // 2}+{(r.winfo_screenheight() - h) // 3}")
        tk.Label(r, text="AutoRed", font=("Segoe UI", 22, "bold"), fg="white", bg="#0f2d4d").pack(pady=(22, 0))
        tk.Label(r, text="Iniciando el motor de Packet Tracer...", font=("Segoe UI", 10), fg="#8a9aaa", bg="#0f2d4d").pack(pady=6)
        r.update()
        return r
    except Exception:
        return None


def matar(proc):
    if proc.poll() is not None:
        return
    try:
        if NT:  # /T cierra tambien el motor MCP que arranca el servidor
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        proc.kill()


def estado_ventana():
    try:
        with urllib.request.urlopen(URL + "/api/ventana", timeout=2) as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def esperar_cierre(srv):
    """Espera a que se cierre la ventana. El navegador puede relanzarse y terminar su proceso inicial, por eso se
    usa el latido que envia la propia pagina en vez de esperar a ese proceso."""
    inicio = time.time()
    while srv.poll() is None:
        time.sleep(2)
        v = estado_ventana()
        if not v:
            continue
        if not v["visto"]:
            if time.time() - inicio > 60:
                aviso("La ventana de AutoRed no logro conectarse al servidor.\nRevisa autored.log y tu antivirus o firewall.")
                return
        elif (v["cierre"] is not None and v["cierre"] > 6) or v["silencio"] > 180:
            return


def main():
    if not puerto_libre():
        aviso("AutoRed ya esta abierto, o el puerto 5000 esta ocupado por otro programa.\nCierra la otra copia e intenta de nuevo.")
        return
    cmd = ["py", "-3.12", "servidor.py"] if NT else [sys.executable, "servidor.py"]
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if NT else {"start_new_session": True}
    with open(AQUI / "autored.log", "w", encoding="utf-8") as log:
        srv = subprocess.Popen(cmd, cwd=AQUI, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUNBUFFERED": "1"}, **extra)
        sp = splash()
        try:
            t0 = time.time()
            while not listo():
                if srv.poll() is not None or time.time() - t0 > 90:
                    if sp:
                        sp.destroy()
                    aviso(f"No se pudo iniciar el servidor.\nRevisa el archivo autored.log en:\n{AQUI}")
                    return
                if sp:
                    sp.update()
                time.sleep(0.2)
            if sp:
                sp.destroy()
            exe = navegador()
            if exe:
                perfil = AQUI / (".perfil_chrome" if "chrome" in exe.lower() else ".perfil_edge")
                subprocess.Popen([exe, f"--app={URL}", f"--user-data-dir={perfil}", "--window-size=1320,880", "--no-first-run",
                                  "--no-default-browser-check", "--disable-extensions", "--disable-sync", "--no-proxy-server",
                                  "--disable-features=msImplicitSignin,msEdgeSyncPromo"])
                esperar_cierre(srv)
            else:  # sin Edge/Chrome: navegador normal y un aviso que mantiene el programa abierto
                webbrowser.open(URL)
                aviso("AutoRed esta en ejecucion en tu navegador.\nPulsa Aceptar cuando quieras cerrarlo.")
        finally:
            matar(srv)


if __name__ == "__main__":
    main()