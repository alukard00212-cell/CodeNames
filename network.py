
import json
import queue
import socket
import socketserver
import threading


PORT = 5000

# Hosts are not connected TCP clients, so they use a reserved key.
HOST_KEY = "__host__"

ROLES = ("spymaster", "operative")
TEAMS = ("red", "blue")


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
                    "message": "Invalid join request."
                })

                return

            # Get player information
            player_name = str(
                request.get("name", "Player")
            ).strip()[:20] or "Player"

            # Assign team
            with self.server.players_lock:

                # Players choose their team in the lobby.
                player_team = None
                player_role = "operative"

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
                "lobby": self.server.get_lobby()
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

        # The host is a player too (spymaster by default).
        self.host_team = "red"
        self.host_role = "spymaster"

        self.clients = {}
        self.clients_lock = threading.Lock()

        super().__init__(address, ClientHandler)

    # Get the connected player names
    def get_players(self):

        with self.players_lock:
            return list(self.players.values())

    def _client_spymaster_taken(self, team, exclude_key=None):
        """
        Return True when another client already spies for `team`.
        The caller must hold players_lock.
        """

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
        Only one spymaster is allowed per team.
        """

        if team not in TEAMS:
            return False

        if role not in ROLES:
            return False

        with self.players_lock:

            if player_key not in self.players:
                return False

            if role == "spymaster":

                if self._client_spymaster_taken(
                    team,
                    exclude_key=player_key
                ):
                    return False

                if (
                    self.host_team == team
                    and self.host_role == "spymaster"
                ):
                    return False

            self.player_teams[player_key] = team
            self.player_roles[player_key] = role

        return True

    def set_host_assignment(self, team, role):
        """
        Assign the host's own team and role.
        """

        if team not in TEAMS:
            return False

        if role not in ROLES:
            return False

        with self.players_lock:

            if (
                role == "spymaster"
                and self._client_spymaster_taken(team)
            ):
                return False

            self.host_team = team
            self.host_role = role

        return True
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

    def get_lobby(self):

        lobby = []

        with self.players_lock:

            # The host is part of the lobby, but has no TCP connection.
            lobby.append({
                "name": "HOST",
                "key": HOST_KEY,
                "team": self.host_team,
                "role": self.host_role,
                "is_host": True
            })

            for player_key, player_name in self.players.items():

                lobby.append({
                    "name": player_name,
                    "key": player_key,
                    "team": self.player_teams.get(player_key),
                    "role": self.player_roles.get(
                        player_key,
                        "operative"
                    ),
                    "is_host": False
                })

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
            "lobby": self.server.get_lobby()
        })

    def send_to_role(self, team, role, message):

        if self.server is not None:
            self.server.send_to_role(
                team,
                role,
                message
            )

    def send_to_player(self, player_key, message):

        if self.server is not None:
            self.server.send_to_player(
                player_key,
                message
            )

    def stop(self):

        if self.server is not None:

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