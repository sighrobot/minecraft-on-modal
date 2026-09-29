# minecraft-on-modal

On-demand Minecraft (Java Edition) server running as a [Modal](https://modal.com) function.

Start a server when you want to play, stop it when you're done, and only pay for the time it's running. The world is saved to a [Modal Volume](https://modal.com/docs/guide/volumes) so it survives between sessions, and players connect through a [Modal tunnel](https://modal.com/docs/guide/tunnels). You don't need port forwarding or a VPS.

## How it works

- `minecraft.py` defines a Modal App with one function, `run_server`, running in a Java container ([`eclipse-temurin`](https://hub.docker.com/_/eclipse-temurin)).
- On the first run it downloads the pinned vanilla `server.jar` and accepts the EULA. Later runs restore the server directory from the volume.
- While the server runs, the world is checkpointed to the volume every 5 minutes.
- On shutdown (`modal app stop`, the session time cap, or `/stop` in-game) the server saves and syncs to the volume, then writes a `latest.tar.gz` snapshot you can download.

## Modal setup

New to Modal? Start with the [Getting Started guide](https://modal.com/docs/guide).

1. **Create an account** at [modal.com](https://modal.com/signup).
2. **Install the client** (Python 3.9+):

   ```sh
   pip install modal
   ```

3. **Authenticate** (opens a browser and saves a token to `~/.modal.toml`):

   ```sh
   modal setup
   # or: python -m modal setup
   ```

4. **Check it worked:**

   ```sh
   modal profile current
   ```

You don't have to create the volume yourself. The script creates it on first run (`create_if_missing=True`).

Useful Modal docs:

| Topic | Link |
| --- | --- |
| `modal run` CLI reference | https://modal.com/docs/reference/cli/run |
| `modal app` (logs, stop, list) | https://modal.com/docs/reference/cli/app |
| `modal volume` CLI | https://modal.com/docs/reference/cli/volume |
| Volumes | https://modal.com/docs/guide/volumes |
| Tunnels (`modal.forward`) | https://modal.com/docs/guide/tunnels |
| CPU / memory resources | https://modal.com/docs/guide/resources |
| Timeouts | https://modal.com/docs/guide/timeouts |
| Pricing | https://modal.com/pricing |

## Before your first run

Open `minecraft.py` and **add your Minecraft username to `PLAYERS`**. A fresh world has the whitelist turned on, so anyone not in that list can't join:

```python
PLAYERS = ["your_username", "a_friend"]
```

## Usage

### Start a server

```sh
modal run --detach minecraft.py
```

> **`--detach` is required.** The entrypoint uses `spawn()` and returns right away, so without `--detach` the App would shut down (and kill the server) seconds after launch. The script exits with an error if you forget it.

With custom resources:

```sh
modal run --detach minecraft.py --ram 8 --cpu 4
```

### Get the connect address

The tunnel address is printed in the app logs once the server finishes starting:

```sh
modal app logs minecraft
```

Look for a line like:

```
>>> Connect to r7.modal.host:40123
```

In Minecraft, go to **Multiplayer → Add Server** and enter that `host:port`. The address changes every session.

You can also watch the logs and the running function in the [Modal dashboard](https://modal.com/apps).

### Stop the server

```sh
modal app stop minecraft
```

This is the only way to stop it from outside the game. The server gets a grace period to run `/stop`, save and sync to the volume before the container shuts down. An op running `/stop` in-game also ends the session cleanly.

### Back up the world

Each session ends by writing a snapshot to the volume. To download it:

```sh
modal volume get minecraft-data latest.tar.gz .
```

Other volume commands:

```sh
modal volume ls minecraft-data            # list files
modal volume ls minecraft-data server     # browse the live server directory
modal volume get minecraft-data server/world ./world   # pull just the world folder
```

### Command cheat sheet

| Action | Command |
| --- | --- |
| Start (defaults: 4 GB heap, 2 CPUs) | `modal run --detach minecraft.py` |
| Start with more resources | `modal run --detach minecraft.py --ram 8 --cpu 4` |
| Find the connect address | `modal app logs minecraft` |
| List running apps | `modal app list` |
| Stop | `modal app stop minecraft` |
| Download snapshot | `modal volume get minecraft-data latest.tar.gz .` |

## Parameters

### Command-line flags

Pass these to `modal run --detach minecraft.py`:

| Flag | Default | Description |
| --- | --- | --- |
| **`--ram`** | `4` | Java heap size in GB (`-Xms`/`-Xmx`). The container gets `ram + 1.5` GB so the JVM has headroom. |
| **`--cpu`** | `2.0` | CPU cores reserved for the container. Fractional values are allowed. |

More RAM and CPU cost more per hour. See [Modal pricing](https://modal.com/pricing).

### Settings in `minecraft.py`

These are constants at the top of the script. Edit them before running.

| Setting | Default | Description |
| --- | --- | --- |
| **`APP_NAME`** | `"minecraft"` | Modal App name. Use it with `modal app logs` / `modal app stop`. |
| **`VOLUME_NAME`** | `"minecraft-data"` | Modal Volume that stores the world. Use a different name to run a separate world. |
| **`PLAYERS`** | `["sighrobot"]` | Usernames added to the whitelist at the start of every session. **Change this.** |
| **`SERVER_JAR_URL`** | pinned vanilla jar | Server jar downloaded on the first run. Find URLs at [minecraft.net/download/server](https://www.minecraft.net/en-us/download/server) or in Mojang's [version manifest](https://launchermeta.mojang.com/mc/game/version_manifest_v2.json) (`downloads.server.url`). |
| **`JAVA_IMAGE`** | `"eclipse-temurin:25-jre-noble"` | Base container image. Its Java version has to be new enough for the jar. |
| `SYNC_EVERY_S` | `300` (5 min) | How often the running world is checkpointed to the volume. This is the most you can lose if the container dies abruptly. |
| `MAX_SESSION_S` | `36000` (10 h) | Hard cap on session length, so a server you forgot about can't keep billing. The server stops cleanly 15 minutes before the cap. |
| `STOP_TIMEOUT_S` | `90` | How long to wait for `/stop` to finish saving before killing the JVM. |

> **Changing versions:** the jar is only downloaded when the volume is empty. To upgrade an existing world, replace `server/server.jar` on the volume (or start with a new `VOLUME_NAME`). **Once a newer version opens the world, you can't downgrade it.** Download a backup first.

### Server properties

Standard settings like difficulty, game mode, MOTD and view distance live in `server.properties` on the volume. To edit them:

```sh
modal volume get minecraft-data server/server.properties .
# edit server.properties
modal volume put --force minecraft-data server.properties server/server.properties
```

Changes take effect the next time you start the server.

## License

See [LICENSE](LICENSE). Running a Minecraft server means accepting the [Minecraft EULA](https://www.minecraft.net/en-us/eula). The script accepts it for you on first run.
