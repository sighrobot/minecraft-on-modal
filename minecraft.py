"""
Minecraft server on Modal: start on demand, persist to a Modal Volume.

Substitute APP_NAME and VOLUME_NAME below for <app> and <volume>:

  modal run --detach minecraft.py --ram <gb>   # start a session with that heap size
  modal app stop <app>                         # the only way to stop it
  modal volume get <volume> latest.tar.gz .    # pull an off-Modal copy
"""
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request

import modal

APP_NAME = "minecraft"
VOLUME_NAME = "minecraft-data"

# The server enables the whitelist itself on a fresh world, so a new volume locks
# everyone out until these are added. Re-applied every session; adding twice is a no-op.
PLAYERS = ["sighrobot"]

# Pin a specific build. Once a newer version opens the world, you can't downgrade it.
# Vanilla jar URL: minecraft.net/download/server, or downloads.server.url for the
# version you want in launchermeta.mojang.com/mc/game/version_manifest_v2.json
SERVER_JAR_URL = "https://piston-data.mojang.com/v1/objects/33680f5f2ac32864d6d7cf5e56a705fdb3e05f4c/server.jar"
JAVA_IMAGE = "eclipse-temurin:25-jre-noble"   # must be new enough for the jar pinned above
SYNC_EVERY_S = 5 * 60           # max data lost if the container dies abruptly
MAX_SESSION_S = 10 * 3600       # hard cap so a forgotten server can't bill forever
STOP_TIMEOUT_S = 90             # how long to let /stop finish saving before killing the JVM
# The exit codes rsync uses for a file that changed or vanished while it was being read.
# The server appends to its log throughout every copy, so those are routine here.
RSYNC_OK = (0, 23, 24)


class Shutdown(BaseException):
    """Raised from the SIGTERM handler so a signal unwinds through the save path."""


def _raise_shutdown(*_):
    raise Shutdown("SIGTERM")


def rsync(src: str, dst: str, *extra: str):
    r = subprocess.run(["rsync", "-a", *extra, src, dst])
    if r.returncode not in RSYNC_OK:
        raise RuntimeError(f"rsync {src} -> {dst} failed with exit code {r.returncode}")


app = modal.App(APP_NAME)
vol = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
image = modal.Image.from_registry(JAVA_IMAGE, add_python="3.12").apt_install("rsync")

VOL, LIVE = "/vol", "/srv/mc"
SAVED, SNAPSHOT = f"{VOL}/server", f"{VOL}/latest.tar.gz"


@app.function(image=image, volumes={VOL: vol}, timeout=MAX_SESSION_S)
def run_server(ram_gb: int):
    started, interrupted = time.time(), None
    os.makedirs(LIVE, exist_ok=True)

    # Restore from the volume, or set up fresh on the very first run.
    if os.path.isdir(SAVED):
        rsync(f"{SAVED}/", f"{LIVE}/")
    else:
        with urllib.request.urlopen(SERVER_JAR_URL) as r, open(f"{LIVE}/server.jar", "wb") as f:
            shutil.copyfileobj(r, f)
        with open(f"{LIVE}/eula.txt", "w") as f:
            f.write("eula=true\n")

    proc = subprocess.Popen(
        ["java", f"-Xms{ram_gb}G", f"-Xmx{ram_gb}G", "-jar", "server.jar", "nogui"],
        cwd=LIVE, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    ready, saved = threading.Event(), threading.Event()

    def read_logs():
        for line in proc.stdout:
            print(line, end="", flush=True)
            if "Done (" in line:
                ready.set()
            elif "Saved the game" in line:
                saved.set()

    threading.Thread(target=read_logs, daemon=True).start()

    def cmd(c: str):
        if proc.poll() is None:
            proc.stdin.write(c + "\n")
            proc.stdin.flush()

    def persist():
        rsync(f"{LIVE}/", f"{SAVED}/", "--delete")
        vol.commit()

    def checkpoint():
        saved.clear()
        cmd("save-off")
        cmd("save-all flush")
        saved.wait(timeout=120)
        try:
            persist()
        finally:
            cmd("save-on")

    # Modal cancels an input by raising InputCancellation in this thread; the worker may
    # also send SIGTERM. Both need to unwind through the save path below, so make the
    # signal raise rather than fire-and-forget a /stop the loop would never wait for.
    signal.signal(signal.SIGTERM, _raise_shutdown)

    while not ready.wait(timeout=1):
        if proc.poll() is not None:
            raise RuntimeError("Minecraft exited during startup, see the log above")
    for p in PLAYERS:
        cmd(f"whitelist add {p}")

    with modal.forward(25565, unencrypted=True) as tunnel:
        host, port = tunnel.tcp_socket
        print(f"\n>>> Connect to {host}:{port}\n", flush=True)

        last_sync, stopping = time.time(), False
        try:
            while proc.poll() is None:
                time.sleep(5)
                if stopping:
                    continue
                if time.time() - started > MAX_SESSION_S - 15 * 60:
                    cmd("stop")
                    stopping = True
                elif time.time() - last_sync > SYNC_EVERY_S:
                    checkpoint()
                    last_sync = time.time()
        except BaseException as e:
            # InputCancellation, KeyboardInterrupt or Shutdown. The tunnel is about to
            # close regardless; spend the remaining grace period getting the world down.
            interrupted = e
            print(f"\n>>> {type(e).__name__}: stopping the server cleanly\n", flush=True)
            cmd("stop")
            try:
                proc.wait(timeout=STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                print(">>> Server did not stop in time, killing it", flush=True)
                proc.kill()
                proc.wait(timeout=30)

    # Server has exited: final sync, then replace the single snapshot. Built under a
    # temp name and renamed, so a failure here leaves the previous snapshot intact.
    persist()
    tmp = f"{SNAPSHOT}.tmp"
    with tarfile.open(tmp, "w:gz") as t:
        t.add(LIVE, arcname="server")
    os.replace(tmp, SNAPSHOT)
    vol.commit()

    if interrupted is not None:
        raise interrupted


@app.local_entrypoint()
def main(ram: int = 4, cpu: float = 2.0):
    # spawn(), not remote(): a blocking remote() holds a client-attached input open for the
    # whole session, and disconnecting the client (Ctrl-C, closing the terminal) cancels that
    # input mid-game even though --detach keeps the App itself alive. spawn() returns
    # immediately and leaves nothing attached to cancel.
    #
    # The flip side is that --detach becomes mandatory: without it the App is ephemeral and
    # stops the moment this entrypoint returns, killing the server seconds after launch.
    if not {"-d", "--detach"}.intersection(sys.argv):
        raise SystemExit(
            "Run this with `modal run --detach minecraft.py`.\n"
            "Without --detach the App stops as soon as this entrypoint returns."
        )
    # Container gets the requested heap plus headroom for the JVM itself.
    call = run_server.with_options(memory=int((ram + 1.5) * 1024), cpu=cpu).spawn(ram)
    print(f"Started {call.object_id}. Connect address will appear in:")
    print(f"  modal app logs {app.app_id}")
