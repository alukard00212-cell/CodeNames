
import json
import queue
import socket
import socketserver
import threading
import time

from settings import clean_player_name


PORT = 5000

# LAN discovery runs on its own UDP port so it never collides with the
# TCP game socket. A host answers probes from any client on the same
# network; the reply carries the real TCP port and the network name.
DISCOVERY_PORT = 5001

DISCOVERY_PROBE = b"CODENAMES_DISCOVER_V1"

DISCOVERY_REPLY = b"CODENAMES_HOST_V1 "

# Hosts are not connected TCP clients, so they use a reserved key.
HOST_KEY = "__host__"

ROLES = ("spymaster", "operative")
TEAMS = ("red", "blue")

# A shared spymaster (one for both teams) is stored as team "both".
SHARED_TEAM = "both"


# =========================================================
# CLIENT HANDLER
# =========================================================

class ClientHandler(socketserver.StreamRequestHandler):


    def handle(self):

        self.send_lock = threading.Lock()

        player_name = "Player"
        player_team = None

        player_key = (
            f"{self.client_address[0]}:"
            f"{self.client_address[1]}"
        )

        joined = False

        try:

            # Receive initial JOIN request
            raw_message = self.rfile.readline()

            if not raw_message:
                return

            try:
                request = json.loads(
                    raw_message.decode("utf-8")
                )

            except (json.JSONDecodeError, UnicodeDecodeError):
                return

            if not isinstance(request, dict):
                return

            if request.get("type") != "join":

                self.send_message({
                    "type": "error",
                    "reason": "invalid_join"
                })

                return

            # Get player information
            requested_name = clean_player_name(request.get("name"))

            # Assign team
            with self.server.players_lock:

                # Two phones can keep the same saved name; make the
                # lobby readable by adding a counter ("Ali (2)").
                player_name = self.server.unique_player_name(
                    requested_name
                )

                # Players choose their team in the lobby.
                player_team = None
                player_role = None

                self.server.players[player_key] = player_name
                self.server.player_teams[player_key] = player_team
                self.server.player_roles[player_key] = player_role
            # Register connection
            with self.server.clients_lock:

                self.server.clients[player_key] = self

            joined = True

            # Welcome player
            self.send_message({
                "type": "welcome",
                "name": player_name,
                "player_key": player_key,
                "team": player_team,
                "role": player_role,
                "lobby": self.server.get_lobby(),
                "spymaster_mode": self.server.spymaster_mode,
            })

            self.server.events.put({
                "type": "player_joined",
                "name": player_name,
                "team": player_team,
                "role": player_role,
                "lobby": self.server.get_lobby()
            })

            # LISTEN FOR PLAYER MESSAGES
            for raw_message in self.rfile:

                # Always initialize the variable
                message = None

                # Decode incoming message
                try:

                    message = json.loads(
                        raw_message.decode("utf-8")
                    )

                except (json.JSONDecodeError, UnicodeDecodeError):

                    continue

                # Only accept dictionaries
                if not isinstance(message, dict):
                    continue

                # The handler's local variables are only snapshots from
                # join time, so read the current assignment from the
                # server state before forwarding the action.
                with self.server.players_lock:

                    current_team = self.server.player_teams.get(
                        player_key
                    )

                    current_role = self.server.player_roles.get(
                        player_key,
                        "operative"
                    )

                # Send valid message to game
                self.server.events.put({
                    "type": "client_message",
                    "name": player_name,
                    "player_key": player_key,
                    "team": current_team,
                    "role": current_role,
                    "data": message
                })

        except (OSError, ConnectionError) as error:

            print(
                f"Connection error for {player_name}: {error}"
            )

        finally:

            if joined:

                # Remove player
                with self.server.players_lock:

                    self.server.players.pop(player_key, None)
                    self.server.player_teams.pop(player_key, None)
                    self.server.player_roles.pop(player_key, None)

                # Remove connection
                with self.server.clients_lock:

                    self.server.clients.pop(player_key, None)

                self.server.events.put({
                    "type": "player_left",
                    "name": player_name,
                    "lobby": self.server.get_lobby()
                })

                print(
                    f"{player_name} disconnected."
                )
    # Send a message to this particular player
    def send_message(self, message):

        data = (
            json.dumps(message).encode("utf-8") + b"\n"
        )

        with self.send_lock:
            self.wfile.write(data)
            self.wfile.flush()


# =========================================================
# TCP SERVER
# =========================================================

class GameTCPServer(socketserver.ThreadingTCPServer):

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address):

        self.events = queue.Queue()

        self.players = {}
        self.players_lock = threading.Lock()

        # Player team and role information
        self.player_teams = {}
        self.player_roles = {}

        # The host is a player too. They pick a team and role in the
        # lobby; the defaults keep a fresh lobby startable in mode 2.
        self.host_name = "HOST"
        self.host_team = "red"
        self.host_role = "spymaster"

        # 1 = one shared spymaster for both teams,
        # 2 = one spymaster per team.
        self.spymaster_mode = 2

        self.clients = {}
        self.clients_lock = threading.Lock()

        super().__init__(address, ClientHandler)

    # Get the connected player names
    def get_players(self):

        with self.players_lock:
            return list(self.players.values())

    def unique_player_name(self, name):
        """Make a name unique inside the lobby. Caller holds players_lock."""

        base = clean_player_name(name)
        taken = set(self.players.values()) | {self.host_name}
        candidate = base
        counter = 2

        while candidate in taken:
            suffix = f" ({counter})"
            candidate = base[: max(1, 24 - len(suffix))] + suffix
            counter += 1

        return candidate

    def _valid_assignment(self, team, role):
        """Is this team/role pair allowed by the current spymaster mode?"""

        if team is None and role is None:
            # "No role" is always a legal way to shuffle a full lobby.
            return True

        if role not in ROLES:
            return False

        if self.spymaster_mode == 1:
            if role == "spymaster":
                return team == SHARED_TEAM

            return team in TEAMS

        return team in TEAMS

    def _spymaster_taken(self, team, exclude_key=None, exclude_host=False):
        """
        Return True when another player already spies for `team`.
        The caller must hold players_lock.
        """

        if self.spymaster_mode == 1:
            # One shared spymaster for the whole lobby.

            if not exclude_host and self.host_role == "spymaster":
                return True

            for player_key in self.players:

                if player_key == exclude_key:
                    continue

                if self.player_roles.get(player_key) == "spymaster":
                    return True

            return False

        if (
            not exclude_host
            and self.host_role == "spymaster"
            and self.host_team == team
        ):
            return True

        for player_key, player_team in self.player_teams.items():

            if player_key == exclude_key:
                continue

            if player_team != team:
                continue

            if self.player_roles.get(player_key) == "spymaster":
                return True

        return False

    def set_player_assignment(self, player_key, team, role):
        """
        Atomically assign a client to a team and a role.
        A spymaster slot is unique (per team, or shared in mode 1).
        """

        with self.players_lock:

            if player_key not in self.players:
                return False

            if not self._apply_assignment_for_client(
                player_key, team, role
            ):
                return False

            self.player_teams[player_key] = team
            self.player_roles[player_key] = role

        return True

    def _apply_assignment_for_client(self, player_key, team, role):
        if not self._valid_assignment(team, role):
            return False

        if role != "spymaster":
            return True

        return not self._spymaster_taken(
            team, exclude_key=player_key
        )

    def set_host_assignment(self, team, role):
        """
        Assign the host's own team and role (None clears the slot).
        """

        with self.players_lock:

            if not self._valid_assignment(team, role):
                return False

            if role == "spymaster" and self._spymaster_taken(
                team, exclude_host=True
            ):
                return False

            self.host_team = team
            self.host_role = role

        return True

    def set_spymaster_mode(self, mode):
        """Switch modes and clear every spymaster assignment."""

        try:
            mode = int(mode)
        except (TypeError, ValueError):
            return False

        if mode not in (1, 2):
            return False

        with self.players_lock:

            self.spymaster_mode = mode

            if (
                self.host_role == "spymaster"
                or self.host_team == SHARED_TEAM
            ):
                self.host_team = None
                self.host_role = None

            for player_key in list(self.player_roles):

                if (
                    self.player_roles.get(player_key) == "spymaster"
                    or self.player_teams.get(player_key) == SHARED_TEAM
                ):
                    self.player_teams[player_key] = None
                    self.player_roles[player_key] = None

        return True
    # Tell every client the lobby is closing, then drop their sockets
    def disconnect_all(self, message=None):

        if message is not None:
            self.broadcast(message)

        with self.clients_lock:
            handlers = list(self.clients.values())
            self.clients.clear()

        for handler in handlers:

            request = getattr(handler, "request", None)

            if request is None:
                continue

            try:
                request.shutdown(socket.SHUT_RDWR)

            except OSError:
                pass

            try:
                request.close()

            except OSError:
                pass

    # Send a message to every connected player
    def broadcast(self, message):

        with self.clients_lock:
            connected_clients = list(
                self.clients.values()
            )

        for client in connected_clients:

            try:
                client.send_message(message)

            except (OSError, ConnectionError):
                pass

    def send_to_role(self, team, role, message):

        with self.players_lock:

            target_keys = [
                player_key
                for player_key in self.players
                if self.player_teams.get(player_key) == team
                and self.player_roles.get(player_key) == role
            ]

        with self.clients_lock:

            targets = [
                self.clients.get(player_key)
                for player_key in target_keys
            ]

        for client in targets:

            if client is None:
                continue

            try:
                client.send_message(message)

            except (OSError, ConnectionError):
                pass

    def send_to_player(self, player_key, message):

        if player_key is None:
            return

        with self.clients_lock:
            client = self.clients.get(player_key)

        if client is None:
            return

        try:
            client.send_message(message)

        except (OSError, ConnectionError):
            pass

    def send_to_spymasters(self, message):
        """Send the secret key to every client acting as a spymaster."""

        with self.players_lock:

            target_keys = [
                player_key
                for player_key in self.players
                if self.player_roles.get(player_key) == "spymaster"
            ]

        with self.clients_lock:

            targets = [
                self.clients.get(player_key)
                for player_key in target_keys
            ]

        for client in targets:

            if client is None:
                continue

            try:
                client.send_message(message)

            except (OSError, ConnectionError):
                pass

    def get_lobby(self):

        lobby = []

        with self.players_lock:

            # The host is part of the lobby, but has no TCP connection.
            lobby.append({
                "name": self.host_name or "HOST",
                "key": HOST_KEY,
                "team": self.host_team,
                "role": self.host_role,
                "is_host": True,
                "is_shared": self.host_team == SHARED_TEAM,
            })

            for player_key, player_name in self.players.items():

                player_team = self.player_teams.get(player_key)

                lobby.append({
                    "name": player_name,
                    "key": player_key,
                    "team": player_team,
                    "role": self.player_roles.get(
                        player_key,
                        "operative"
                    ),
                    "is_host": False,
                    "is_shared": player_team == SHARED_TEAM,
                })

            mode = self.spymaster_mode

        for entry in lobby:
            entry["spymaster_mode"] = mode

        return lobby

# =========================================================
# GAME SERVER
# =========================================================

class GameServer:

    def __init__(self, host="0.0.0.0", port=PORT):

        self.host = host
        self.port = port

        self.server = None
        self.thread = None

    def start(self):

        self.server = GameTCPServer(
            (self.host, self.port)
        )

        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True
        )

        self.thread.start()

    def get_events(self):

        events = []

        if self.server is None:
            return events

        while True:
            try:
                events.append(
                    self.server.events.get_nowait()
                )
            except queue.Empty:
                break

        return events

    def get_players(self):

        if self.server is None:
            return []

        return self.server.get_players()

    def broadcast(self, message):

        if self.server is not None:
            self.server.broadcast(message)

    def send_lobby_update(self):

        if self.server is None:
            return

        self.server.broadcast({
            "type": "lobby_update",
            "lobby": self.server.get_lobby(),
            "spymaster_mode": self.server.spymaster_mode,
        })

    def send_to_role(self, team, role, message):

        if self.server is not None:
            self.server.send_to_role(
                team,
                role,
                message
            )

    def send_to_spymasters(self, message):

        if self.server is not None:
            self.server.send_to_spymasters(message)

    def send_to_player(self, player_key, message):

        if self.server is not None:
            self.server.send_to_player(
                player_key,
                message
            )

    def stop(self):

        if self.server is not None:

            # Guests should learn the lobby is gone instead of waiting
            # for a dead socket.
            self.server.disconnect_all({
                "type": "disconnected",
                "reason": "host_closed"
            })

            self.server.shutdown()
            self.server.server_close()

            if self.thread:
                self.thread.join(timeout=1)

            self.server = None
            self.thread = None


# =========================================================
# GAME CLIENT
# =========================================================

class GameClient:

    def __init__(self):

        self.events = queue.Queue()

        self.connection = None
        self.thread = None

        self.send_lock = threading.Lock()

    def connect(self, host, port, player_name):

        self.thread = threading.Thread(
            target=self._connect_worker,
            args=(host, port, player_name),
            daemon=True
        )

        self.thread.start()

    def _connect_worker(self, host, port, player_name):

        connection = None

        try:

            connection = socket.create_connection(
                (host, port),
                timeout=6
            )

            connection.settimeout(None)

            with self.send_lock:
                self.connection = connection

            join_message = {
                "type": "join",
                "name": player_name
            }

            self.send_message(join_message)

            with connection.makefile(
                "r",
                encoding="utf-8"
            ) as reader:

                for line in reader:

                    message = json.loads(line)

                    self.events.put(message)

            self.events.put({
                "type": "disconnected",
                "message": "Connection to host closed."
            })

        except (
            OSError,
            ConnectionError,
            json.JSONDecodeError
        ) as error:

            self.events.put({
                "type": "error",
                "message": str(error)
            })

        finally:

            with self.send_lock:

                if self.connection is connection:
                    self.connection = None

            if connection:
                try:
                    connection.close()
                except OSError:
                    pass

    # Send a message to the host
    def send_message(self, message):

        data = (
            json.dumps(message).encode("utf-8") + b"\n"
        )

        with self.send_lock:

            if self.connection is None:
                return False

            try:
                self.connection.sendall(data)
                return True

            except OSError:
                return False

    def get_events(self):

        events = []

        while True:
            try:
                events.append(
                    self.events.get_nowait()
                )
            except queue.Empty:
                break

        return events

    def close(self):

        with self.send_lock:

            connection = self.connection
            self.connection = None

            if connection:

                try:
                    connection.shutdown(
                        socket.SHUT_RDWR
                    )
                except OSError:
                    pass

                try:
                    connection.close()
                except OSError:
                    pass


# =========================================================
# FIND LOCAL IP
# =========================================================

def get_local_ip():

    connection = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    try:

        connection.connect(
            ("192.0.2.1", 80)
        )

        return connection.getsockname()[0]

    except OSError:

        return "127.0.0.1"

    finally:

        connection.close()


# =========================================================
# LAN DISCOVERY
# =========================================================

def _local_addresses():
    """Every plausible IPv4 address of this device (no Kivy import)."""

    addresses = []

    for destination in ("192.0.2.1", "8.8.8.8"):

        probe = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM
        )

        try:

            probe.settimeout(0.2)
            probe.connect((destination, 80))
            address = probe.getsockname()[0]

        except OSError:

            address = None

        finally:

            probe.close()

        if address and address != "127.0.0.1":
            if address not in addresses:
                addresses.append(address)

    return addresses


def _broadcast_targets():
    """Broadcast addresses worth probing, plus loopback for tests."""

    targets = ["255.255.255.255", "127.0.0.1"]

    for address in _local_addresses():

        octets = address.split(".")

        if len(octets) != 4:
            continue

        subnet = ".".join(octets[:3] + ["255"])

        if subnet not in targets:
            targets.append(subnet)

    return targets


class DiscoveryResponder:
    """Answer UDP probes from clients looking for a host on the LAN."""

    def __init__(self, info_provider, port=DISCOVERY_PORT):

        # `info_provider` runs on the responder thread; it must not touch
        # the UI. Returning a plain dict is enough.
        self.info_provider = info_provider
        self.port = port

        self.sock = None
        self.thread = None
        self.error = None

    def start(self):

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:

            sock.bind(("", self.port))

        except OSError as error:

            self.error = error

            try:
                sock.close()
            except OSError:
                pass

            return False

        sock.settimeout(0.8)

        self.sock = sock
        self.thread = threading.Thread(
            target=self._serve,
            daemon=True
        )
        self.thread.start()

        return True

    def _serve(self):

        while True:

            sock = self.sock

            if sock is None:
                return

            try:

                data, address = sock.recvfrom(1024)

            except socket.timeout:

                continue

            except OSError:

                return

            if data.strip() != DISCOVERY_PROBE:
                continue

            try:

                info = dict(self.info_provider() or {})

            except Exception:

                info = {}

            info["app"] = "codenames"
            info["v"] = 1

            try:

                payload = DISCOVERY_REPLY + json.dumps(info).encode(
                    "utf-8"
                )

                sock.sendto(payload, address)

            except (OSError, TypeError, ValueError):

                pass

    def stop(self):

        sock = self.sock
        self.sock = None

        if sock is None:
            return

        try:
            sock.close()
        except OSError:
            pass

        if self.thread is not None:

            try:
                self.thread.join(timeout=1)
            except RuntimeError:
                pass

            self.thread = None


def _parse_discovery_reply(data, address):
    """Turn a host reply into a dict, or None when it is not ours."""

    if not data.startswith(DISCOVERY_REPLY):
        return None

    try:

        info = json.loads(
            data[len(DISCOVERY_REPLY):].decode("utf-8")
        )

    except (json.JSONDecodeError, UnicodeDecodeError):

        return None

    if not isinstance(info, dict):
        return None

    if info.get("app") != "codenames":
        return None

    host = str(info.get("host") or address[0]).strip()

    if not host:
        return None

    try:

        port = int(info.get("port") or PORT)

    except (TypeError, ValueError):

        return None

    if not 0 < port < 65536:
        return None

    try:

        players = int(info.get("players") or 1)

    except (TypeError, ValueError):

        players = 1

    return {
        "host": host,
        "port": port,
        "ssid": str(info.get("ssid") or "").strip(),
        "host_name": str(info.get("host_name") or "").strip(),
        "kind": str(info.get("kind") or "WI-FI").strip() or "WI-FI",
        "players": max(1, players),
        "max": int(info.get("max") or 8),
        "source": address[0],
    }


def discover_hosts(timeout=1.8, port=DISCOVERY_PORT):
    """Ask the LAN for Codenames hosts and collect the replies.

    This blocks for at most `timeout` seconds, so callers should run it on
    a worker thread. Returns a list of dicts, deduplicated per endpoint.
    """

    found = {}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:

        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        sock.settimeout(0.25)

        for target in _broadcast_targets():

            try:

                sock.sendto(DISCOVERY_PROBE, (target, port))

            except OSError:

                continue

        deadline = time.monotonic() + max(0.2, float(timeout))

        while time.monotonic() < deadline:

            try:

                data, address = sock.recvfrom(2048)

            except socket.timeout:

                continue

            except OSError:

                break

            info = _parse_discovery_reply(data, address)

            if info is None:
                continue

            found[(info["host"], info["port"])] = info

    except OSError:

        pass

    finally:

        try:
            sock.close()
        except OSError:
            pass

    return sorted(
        found.values(),
        key=lambda item: (-item["players"], item["ssid"].lower())
    )