import datetime
import json
import sqlite3
import typing
from typing import Callable

from endstone import Player
from endstone.command import Command, CommandSender
from endstone.event import PlayerJoinEvent, PlayerDeathEvent, ActorDeathEvent, event_handler
from endstone.form import ActionForm, ModalForm, TextInput, Toggle, Dropdown
from endstone.plugin import Plugin
from endstone.scoreboard import Objective, DisplaySlot, Criteria

CUSTOM_OBJECTIVE_NAME = "__CUSTOM_SIDEBAR_OBJECTIVE__"


class SidebarPlugin(Plugin):
    api_version = "0.11"

    commands = {
        "sidebar": {
            "description": "Manage and toggle the sidebar display.",
            "usages": [
                "/sidebar",
                "/sidebar toggle",
                "/sidebar menu",
                "/sidebar title <title: message>",
                "/sidebar add <line: message>",
                "/sidebar set <index: int> <line: message>",
                "/sidebar remove <index: int>",
                "/sidebar list",
                "/sidebar clear",
                "/sidebar reload"
            ],
            "permissions": ["endstone_sidebar.command.sidebar"]
        }
    }

    permissions = {
        "endstone_sidebar.command.sidebar": {
            "description": "Allow players to use the /sidebar command.",
            "default": True
        },
        "endstone_sidebar.admin": {
            "description": "Allow administrators to configure the sidebar on the fly.",
            "default": "op"
        }
    }

    PLACEHOLDER_OPTIONS = [
        "None (Custom text only)",
        "Player Name ({player_name})",
        "Ping ({ping})",
        "Game Mode ({game_mode})",
        "Experience Level ({exp_level})",
        "Total Experience ({total_exp})",
        "Experience Progress ({exp_progress})",
        "X Coordinate ({x})",
        "Y Coordinate ({y})",
        "Z Coordinate ({z})",
        "Coordinates X,Y,Z ({x}, {y}, {z})",
        "Dimension Name ({dimension})",
        "Dimension ID ({dimension_id})",
        "Online Players ({online})",
        "Max Online Players ({max_online})",
        "Online/Max Ratio ({online}/{max_online})",
        "Deaths ({deaths})",
        "Player Kills PVP ({kills})",
        "Mob Kills ({mob_kills})",
        "Minecraft Version ({mc_version})",
        "Date ({date})",
        "Time ({time})",
        "Date & Time ({datetime})",
        "Year ({year})",
        "Month ({month})",
        "Day ({day})",
        "Hour ({hour})",
        "Minute ({minute})",
        "Second ({second})",
        "Player Address ({address})",
        "Player Runtime ID ({runtime_id})",
        "Player XUID ({xuid})",
        "Player UUID ({uuid})",
        "Device OS ({device_os})",
        "Locale ({locale})"
    ]

    PLACEHOLDER_MAPPING = {
        0: "",
        1: "{player_name}",
        2: "{ping}",
        3: "{game_mode}",
        4: "{exp_level}",
        5: "{total_exp}",
        6: "{exp_progress}",
        7: "{x}",
        8: "{y}",
        9: "{z}",
        10: "{x}, {y}, {z}",
        11: "{dimension}",
        12: "{dimension_id}",
        13: "{online}",
        14: "{max_online}",
        15: "{online}/{max_online}",
        16: "{deaths}",
        17: "{kills}",
        18: "{mob_kills}",
        19: "{mc_version}",
        20: "{date}",
        21: "{time}",
        22: "{datetime}",
        23: "{year}",
        24: "{month}",
        25: "{day}",
        26: "{hour}",
        27: "{minute}",
        28: "{second}",
        29: "{address}",
        30: "{runtime_id}",
        31: "{xuid}",
        32: "{uuid}",
        33: "{device_os}",
        34: "{locale}"
    }


    def __init__(self):
        super().__init__()
        self.update_period: int = 20
        self.sidebar_title: str = ""
        self.sidebar_content: list[str] = []
        self.placeholders: dict[str, Callable[[Player], str]] = {}
        self.default_enabled: bool = True
        self.preferences: dict[str, bool] = {}
        
        # SQLite Connection and stats cache
        self._conn: typing.Optional[sqlite3.Connection] = None
        self._cursor: typing.Optional[sqlite3.Cursor] = None
        self.stats_cache: dict[str, dict] = {}

    def on_enable(self) -> None:
        self.save_default_config()
        self.load_config()
        self.load_preferences()
        self.init_db()
        self.register_events(self)
        self.register_default_placeholders()
        self.server.scheduler.run_task(
            self, self.update_sidebar, delay=0, period=self.update_period
        )

    def on_disable(self) -> None:
        self.close_db()

    def init_db(self) -> None:
        self.data_folder.mkdir(parents=True, exist_ok=True)
        db_path = self.data_folder / "stats.db"
        try:
            # check_same_thread=False allows access across multiple event/scheduler threads
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._cursor = self._conn.cursor()
            self._cursor.execute("""
                CREATE TABLE IF NOT EXISTS player_stats (
                    uuid TEXT PRIMARY KEY,
                    username TEXT,
                    deaths INTEGER DEFAULT 0,
                    player_kills INTEGER DEFAULT 0,
                    mob_kills INTEGER DEFAULT 0
                )
            """)
            self._conn.commit()
        except Exception as e:
            self.logger.error(f"Failed to initialize SQLite database: {e}")

    def close_db(self) -> None:
        try:
            if self._conn:
                self._conn.close()
                self._conn = None
                self._cursor = None
        except Exception as e:
            self.logger.error(f"Failed to close SQLite database: {e}")

    def get_or_create_stats(self, player: Player) -> dict:
        uuid_str = str(player.unique_id)
        if uuid_str in self.stats_cache:
            return self.stats_cache[uuid_str]

        if not self._cursor:
            return {"deaths": 0, "player_kills": 0, "mob_kills": 0}

        try:
            self._cursor.execute(
                "SELECT deaths, player_kills, mob_kills FROM player_stats WHERE uuid = ?",
                (uuid_str,)
            )
            row = self._cursor.fetchone()
            if row:
                stats = {
                    "deaths": row[0],
                    "player_kills": row[1],
                    "mob_kills": row[2]
                }
                # Update username in db if it changed
                self._cursor.execute(
                    "UPDATE player_stats SET username = ? WHERE uuid = ?",
                    (player.name, uuid_str)
                )
                self._conn.commit()
            else:
                stats = {
                    "deaths": 0,
                    "player_kills": 0,
                    "mob_kills": 0
                }
                self._cursor.execute(
                    "INSERT INTO player_stats (uuid, username, deaths, player_kills, mob_kills) VALUES (?, ?, 0, 0, 0)",
                    (uuid_str, player.name)
                )
                self._conn.commit()
            
            self.stats_cache[uuid_str] = stats
            return stats
        except Exception as e:
            self.logger.error(f"Database error for player {player.name}: {e}")
            return {"deaths": 0, "player_kills": 0, "mob_kills": 0}

    def increment_deaths(self, player: Player) -> None:
        uuid_str = str(player.unique_id)
        stats = self.get_or_create_stats(player)
        stats["deaths"] += 1
        self.stats_cache[uuid_str] = stats
        
        if not self._cursor:
            return
        try:
            self._cursor.execute(
                "UPDATE player_stats SET deaths = ? WHERE uuid = ?",
                (stats["deaths"], uuid_str)
            )
            self._conn.commit()
        except Exception as e:
            self.logger.error(f"Failed to update deaths in db for {player.name}: {e}")

    def increment_player_kills(self, player: Player) -> None:
        uuid_str = str(player.unique_id)
        stats = self.get_or_create_stats(player)
        stats["player_kills"] += 1
        self.stats_cache[uuid_str] = stats
        
        if not self._cursor:
            return
        try:
            self._cursor.execute(
                "UPDATE player_stats SET player_kills = ? WHERE uuid = ?",
                (stats["player_kills"], uuid_str)
            )
            self._conn.commit()
        except Exception as e:
            self.logger.error(f"Failed to update player_kills in db for {player.name}: {e}")

    def increment_mob_kills(self, player: Player) -> None:
        uuid_str = str(player.unique_id)
        stats = self.get_or_create_stats(player)
        stats["mob_kills"] += 1
        self.stats_cache[uuid_str] = stats
        
        if not self._cursor:
            return
        try:
            self._cursor.execute(
                "UPDATE player_stats SET mob_kills = ? WHERE uuid = ?",
                (stats["mob_kills"], uuid_str)
            )
            self._conn.commit()
        except Exception as e:
            self.logger.error(f"Failed to update mob_kills in db for {player.name}: {e}")

    def load_preferences(self) -> None:
        pref_file = self.data_folder / "preferences.json"
        if pref_file.exists():
            try:
                with open(pref_file, "r", encoding="utf-8") as f:
                    self.preferences = json.load(f)
            except Exception as e:
                self.logger.error(f"Failed to load preferences: {e}")
                self.preferences = {}
        else:
            self.preferences = {}

    def save_preferences(self) -> None:
        self.data_folder.mkdir(parents=True, exist_ok=True)
        pref_file = self.data_folder / "preferences.json"
        try:
            with open(pref_file, "w", encoding="utf-8") as f:
                json.dump(self.preferences, f, indent=4)
        except Exception as e:
            self.logger.error(f"Failed to save preferences: {e}")

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent) -> None:
        event.player.scoreboard = self.server.create_scoreboard()
        self.get_or_create_stats(event.player)

    @event_handler
    def on_player_death(self, event: PlayerDeathEvent) -> None:
        self.increment_deaths(event.player)
        
        ds = event.damage_source
        if ds is not None and ds.damaging_actor is not None:
            if isinstance(ds.damaging_actor, Player):
                self.increment_player_kills(ds.damaging_actor)

    @event_handler
    def on_actor_death(self, event: ActorDeathEvent) -> None:
        if isinstance(event.actor, Player):
            return

        ds = event.damage_source
        if ds is not None and ds.damaging_actor is not None:
            if isinstance(ds.damaging_actor, Player):
                self.increment_mob_kills(ds.damaging_actor)

    def update_sidebar(self) -> None:
        for player in self.server.online_players:
            self.update_sidebar_for(player)

    def update_sidebar_for(self, player: Player) -> None:
        if player.scoreboard is self.server.scoreboard:
            return

        uuid_str = str(player.unique_id)
        enabled = self.preferences.get(uuid_str, self.default_enabled)
        if not enabled:
            objective = player.scoreboard.get_objective(CUSTOM_OBJECTIVE_NAME)
            if objective is not None:
                objective.unregister()
            return

        t = self.replace_placeholder(self.sidebar_title, player)

        c: list[str] = []
        for line in self.sidebar_content:
            c.append(self.replace_placeholder(line, player))

        objective: Objective = player.scoreboard.get_objective(CUSTOM_OBJECTIVE_NAME)

        if objective is not None:
            objective.unregister()

        objective = player.scoreboard.add_objective(
            CUSTOM_OBJECTIVE_NAME, Criteria.DUMMY, t
        )

        counter = 0
        for line in c:
            objective.get_score(line).value = counter
            counter += 1

        objective.set_display(DisplaySlot.SIDE_BAR)

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if command.name != "sidebar":
            return False

        # If they just type /sidebar or /sidebar menu
        if not args or args[0].lower() == "menu":
            if not isinstance(sender, Player):
                sender.send_message("§cThis command can only be executed by a player.")
                return True

            is_op = sender.is_op or sender.has_permission("endstone_sidebar.admin")
            
            # If a normal player ran /sidebar (with no args) or /sidebar menu
            if not is_op:
                # Normal players running `/sidebar` toggles it
                if not args:
                    return self.toggle_player_sidebar(sender)
                else:
                    sender.send_message("§cYou do not have permission to execute this administrative command.")
                    return True

            # Operator opening the menu GUI
            self.show_admin_menu(sender)
            return True

        action = args[0].lower()

        # Handle explicit toggle command
        if action == "toggle":
            if not isinstance(sender, Player):
                sender.send_message("§cThis command can only be executed by a player.")
                return True
            return self.toggle_player_sidebar(sender)

        # Check permissions for other admin commands
        is_operator = isinstance(sender, Player) and sender.is_op
        if not (is_operator or sender.has_permission("endstone_sidebar.admin")):
            sender.send_message("§cYou do not have permission to execute this administrative command.")
            return True

        if action == "list":
            sender.send_message(f"§e--- Sidebar Config (Title: §f{self.sidebar_title}§e) ---")
            for i, line in enumerate(self.sidebar_content, 1):
                sender.send_message(f"§b{i}. §f{line}")
            return True

        elif action == "clear":
            self.sidebar_content = []
            self.config["sidebar"]["content"] = self.sidebar_content
            self.save_config()
            sender.send_message("§aSidebar content has been cleared.")
            self.update_sidebar()
            return True

        elif action == "reload":
            self.reload_config()
            self.load_config()
            sender.send_message("§aSidebar configuration reloaded successfully.")
            self.update_sidebar()
            return True

        elif action == "title":
            if len(args) < 2:
                sender.send_message("§cUsage: /sidebar title <title: message>")
                return True
            new_title = " ".join(args[1:]).replace("&", "§")
            self.sidebar_title = new_title
            self.config["sidebar"]["title"] = new_title
            self.save_config()
            sender.send_message(f"§aSidebar title changed to: {new_title}")
            self.update_sidebar()
            return True

        elif action == "add":
            if len(args) < 2:
                sender.send_message("§cUsage: /sidebar add <line: message>")
                return True
            new_line = " ".join(args[1:]).replace("&", "§")
            self.sidebar_content.append(new_line)
            self.config["sidebar"]["content"] = self.sidebar_content
            self.save_config()
            sender.send_message(f"§aAdded line to sidebar: {new_line}")
            self.update_sidebar()
            return True

        elif action == "set":
            if len(args) < 3:
                sender.send_message("§cUsage: /sidebar set <index: int> <line: message>")
                return True
            try:
                index = int(args[1])
            except ValueError:
                sender.send_message("§cIndex must be an integer.")
                return True

            if index < 1 or index > len(self.sidebar_content):
                sender.send_message(f"§cInvalid index. Must be between 1 and {len(self.sidebar_content)}.")
                return True

            new_line = " ".join(args[2:]).replace("&", "§")
            self.sidebar_content[index - 1] = new_line
            self.config["sidebar"]["content"] = self.sidebar_content
            self.save_config()
            sender.send_message(f"§aLine {index} updated to: {new_line}")
            self.update_sidebar()
            return True

        elif action == "remove":
            if len(args) < 2:
                sender.send_message("§cUsage: /sidebar remove <index: int>")
                return True
            try:
                index = int(args[1])
            except ValueError:
                sender.send_message("§cIndex must be an integer.")
                return True

            if index < 1 or index > len(self.sidebar_content):
                sender.send_message(f"§cInvalid index. Must be between 1 and {len(self.sidebar_content)}.")
                return True

            removed_line = self.sidebar_content.pop(index - 1)
            self.config["sidebar"]["content"] = self.sidebar_content
            self.save_config()
            sender.send_message(f"§aRemoved line {index}: {removed_line}")
            self.update_sidebar()
            return True

        else:
            sender.send_message("§cUnknown subcommand. Try: toggle, menu, list, add, set, remove, clear, title, or reload.")
            return True

    def toggle_player_sidebar(self, player: Player) -> bool:
        uuid_str = str(player.unique_id)
        current_status = self.preferences.get(uuid_str, self.default_enabled)
        new_status = not current_status
        self.preferences[uuid_str] = new_status
        self.save_preferences()

        if new_status:
            player.send_message("§aSidebar has been enabled.")
            player.send_message("§aSidebar has been enabled.")
            self.update_sidebar_for(player)
        else:
            player.send_message("§cSidebar has been disabled.")
            objective = player.scoreboard.get_objective(CUSTOM_OBJECTIVE_NAME)
            if objective is not None:
                objective.unregister()
        return True

    def show_admin_menu(self, player: Player) -> None:
        form = ActionForm(
            title="§l§6OniStone Sidebar Config",
            content="Select an option below to customize the sidebar."
        )
        form.add_button("§dToggle My Sidebar")
        form.add_button("§aEdit Title")
        form.add_button("§bManage Content Lines")
        form.add_button("§eReload Config")

        def handle_menu_submit(p: Player, clicked_index: int):
            if clicked_index == 0:
                self.toggle_player_sidebar(p)
            elif clicked_index == 1:
                self.show_edit_title_form(p)
            elif clicked_index == 2:
                self.show_manage_lines_form(p)
            elif clicked_index == 3:
                self.reload_config()
                self.load_config()
                p.send_message("§aSidebar configuration reloaded successfully.")
                self.update_sidebar()

        form.on_submit = handle_menu_submit
        player.send_form(form)

    def show_edit_title_form(self, player: Player) -> None:
        form = ModalForm(title="§l§aEdit Sidebar Title")
        form.add_control(TextInput(
            label="Enter new sidebar title (supports & for color codes):",
            default_value=self.sidebar_title.replace("§", "&")
        ))

        def handle_title_submit(p: Player, data: str):
            try:
                values = json.loads(data)
                new_title = values[0].replace("&", "§")
                self.sidebar_title = new_title
                self.config["sidebar"]["title"] = new_title
                self.save_config()
                p.send_message(f"§aSidebar title successfully updated to: {new_title}")
                self.update_sidebar()
            except Exception as e:
                p.send_message(f"§cFailed to update title: {e}")

        form.on_submit = handle_title_submit
        player.send_form(form)

    def show_manage_lines_form(self, player: Player) -> None:
        form = ActionForm(
            title="§l§bManage Sidebar Lines",
            content="Select a line below to edit/remove, or add a new line."
        )
        form.add_button("§a[+] Add New Line")
        for i, line in enumerate(self.sidebar_content, 1):
            form.add_button(f"§bLine {i}: §f{line}")

        def handle_lines_submit(p: Player, clicked_index: int):
            if clicked_index == 0:
                # Add a new line using the builder flow
                self.show_add_line_form(p)
            else:
                # Edit, Reorder, or Remove existing line
                line_idx = clicked_index - 1
                current_line = self.sidebar_content[line_idx]
                
                # Build dynamic actions mapping
                buttons = []
                buttons.append(("edit", "§eEdit Line"))
                
                if line_idx > 0:
                    buttons.append(("move_up", "§6Move Up"))
                
                if line_idx < len(self.sidebar_content) - 1:
                    buttons.append(("move_down", "§dMove Down"))
                    
                buttons.append(("remove", "§cRemove Line"))
                
                choice_form = ActionForm(
                    title=f"§l§bLine {clicked_index} Options",
                    content=f"Current Content:\n{current_line}"
                )
                
                for _, label in buttons:
                    choice_form.add_button(label)

                def handle_choice_submit(p2: Player, choice_idx: int):
                    action = buttons[choice_idx][0]
                    if action == "edit":
                        self.show_edit_line_form(p2, line_idx, current_line)
                    elif action == "move_up":
                        # Swap current line with the one above it
                        self.sidebar_content[line_idx], self.sidebar_content[line_idx - 1] = \
                            self.sidebar_content[line_idx - 1], self.sidebar_content[line_idx]
                        self.config["sidebar"]["content"] = self.sidebar_content
                        self.save_config()
                        p2.send_message(f"§aMoved line {clicked_index} up.")
                        self.update_sidebar()
                        self.show_manage_lines_form(p2)
                    elif action == "move_down":
                        # Swap current line with the one below it
                        self.sidebar_content[line_idx], self.sidebar_content[line_idx + 1] = \
                            self.sidebar_content[line_idx + 1], self.sidebar_content[line_idx]
                        self.config["sidebar"]["content"] = self.sidebar_content
                        self.save_config()
                        p2.send_message(f"§aMoved line {clicked_index} down.")
                        self.update_sidebar()
                        self.show_manage_lines_form(p2)
                    elif action == "remove":
                        removed = self.sidebar_content.pop(line_idx)
                        self.config["sidebar"]["content"] = self.sidebar_content
                        self.save_config()
                        p2.send_message(f"§aSuccessfully removed line: {removed}")
                        self.update_sidebar()
                        self.show_manage_lines_form(p2)

                choice_form.on_submit = handle_choice_submit
                p.send_form(choice_form)

        form.on_submit = handle_lines_submit
        player.send_form(form)

    def show_add_line_form(self, player: Player, current_text: str = "") -> None:
        add_form = ModalForm(title="§l§aAdd New Sidebar Line")
        add_form.add_control(TextInput(
            label="Enter custom prefix or text (supports & for color codes):",
            placeholder="e.g. Mob Kills: &b",
            default_value=current_text.replace("§", "&")
        ))
        add_form.add_control(Dropdown(
            label="Select a variable to append:",
            options=self.PLACEHOLDER_OPTIONS,
            default_index=0
        ))

        def handle_add_submit(p2: Player, data: str):
            try:
                vals = json.loads(data)
                prefix = vals[0].replace("&", "§")
                dropdown_idx = int(vals[1])
                suffix = self.PLACEHOLDER_MAPPING.get(dropdown_idx, "")
                
                new_line = prefix + suffix
                self.show_add_line_confirm_form(p2, new_line)
            except Exception as e:
                p2.send_message(f"§cFailed to process form: {e}")
                self.show_manage_lines_form(p2)

        add_form.on_submit = handle_add_submit
        player.send_form(add_form)

    def show_add_line_confirm_form(self, player: Player, current_text: str) -> None:
        confirm_form = ActionForm(
            title="§l§aConfirm Sidebar Line",
            content=f"Line so far:\n§f{current_text}\n\nWould you like to save this line, or append more text/variables?"
        )
        confirm_form.add_button("§aSave Line")
        confirm_form.add_button("§bAppend More Text/Variables")
        confirm_form.add_button("§cCancel")

        def handle_confirm_submit(p2: Player, clicked_index: int):
            if clicked_index == 0:
                self.sidebar_content.append(current_text)
                self.config["sidebar"]["content"] = self.sidebar_content
                self.save_config()
                p2.send_message(f"§aSuccessfully added line: {current_text}")
                self.update_sidebar()
                self.show_manage_lines_form(p2)
            elif clicked_index == 1:
                self.show_add_line_form(p2, current_text)
            else:
                self.show_manage_lines_form(p2)

        confirm_form.on_submit = handle_confirm_submit
        player.send_form(confirm_form)

    def show_edit_line_form(self, player: Player, line_idx: int, current_text: str) -> None:
        edit_form = ModalForm(title=f"§l§eEdit Line {line_idx + 1}")
        edit_form.add_control(TextInput(
            label="Edit custom prefix or text (supports & for colors):",
            default_value=current_text.replace("§", "&")
        ))
        edit_form.add_control(Dropdown(
            label="Select a variable to append:",
            options=self.PLACEHOLDER_OPTIONS,
            default_index=0
        ))

        def handle_edit_submit(p2: Player, edit_data: str):
            try:
                vals = json.loads(edit_data)
                prefix = vals[0].replace("&", "§")
                dropdown_idx = int(vals[1])
                suffix = self.PLACEHOLDER_MAPPING.get(dropdown_idx, "")
                
                edited_line = prefix + suffix
                self.show_edit_line_confirm_form(p2, line_idx, edited_line)
            except Exception as e:
                p2.send_message(f"§cFailed to process form: {e}")
                self.show_manage_lines_form(p2)

        edit_form.on_submit = handle_edit_submit
        player.send_form(edit_form)

    def show_edit_line_confirm_form(self, player: Player, line_idx: int, current_text: str) -> None:
        confirm_form = ActionForm(
            title=f"§l§eConfirm Line {line_idx + 1}",
            content=f"Line so far:\n§f{current_text}\n\nWould you like to save these edits, or append more text/variables?"
        )
        confirm_form.add_button("§aSave Line")
        confirm_form.add_button("§bAppend More Text/Variables")
        confirm_form.add_button("§cCancel")

        def handle_confirm_submit(p2: Player, clicked_index: int):
            if clicked_index == 0:
                self.sidebar_content[line_idx] = current_text
                self.config["sidebar"]["content"] = self.sidebar_content
                self.save_config()
                p2.send_message(f"§aSuccessfully updated line {line_idx + 1}!")
                self.update_sidebar()
                self.show_manage_lines_form(p2)
            elif clicked_index == 1:
                self.show_edit_line_form(p2, line_idx, current_text)
            else:
                self.show_manage_lines_form(p2)

        confirm_form.on_submit = handle_confirm_submit
        player.send_form(confirm_form)

    def load_config(self) -> None:
        self.update_period = self.config["update_period"]
        self.sidebar_title = self.config["sidebar"]["title"]
        self.default_enabled = self.config["sidebar"].get("default_enabled", True)
        self.sidebar_content = self.config["sidebar"]["content"]

    def replace_placeholder(self, string: str, player: Player) -> str:
        args = {}
        for key, provider in self.placeholders.items():
            args[key] = str(provider(player))

        return string.format(**args)

    def register_default_placeholders(self):
        self.register_placeholder("x", lambda p: int(p.location.x))
        self.register_placeholder("y", lambda p: int(p.location.y))
        self.register_placeholder("z", lambda p: int(p.location.z))
        self.register_placeholder("player_name", lambda p: p.name)
        self.register_placeholder("dimension", lambda p: p.location.dimension.type.name.lower())
        self.register_placeholder("dimension_id", lambda p: p.location.dimension.type.value)
        self.register_placeholder("ping", lambda p: p.ping)
        self.register_placeholder("mc_version", lambda _: self.server.minecraft_version)
        self.register_placeholder("online", lambda _: len(self.server.online_players))
        self.register_placeholder("max_online", lambda _: self.server.max_players)
        self.register_placeholder("date", lambda _: datetime.datetime.today().strftime("%x"))
        self.register_placeholder("time", lambda _: datetime.datetime.today().strftime("%X"))
        self.register_placeholder("datetime", lambda _: datetime.datetime.today().strftime("%c"))
        self.register_placeholder("year", lambda _: datetime.datetime.today().strftime("%Y"))
        self.register_placeholder("month", lambda _: datetime.datetime.today().strftime("%m"))
        self.register_placeholder("day", lambda _: datetime.datetime.today().strftime("%d"))
        self.register_placeholder("hour", lambda _: datetime.datetime.today().strftime("%H"))
        self.register_placeholder("minute", lambda _: datetime.datetime.today().strftime("%M"))
        self.register_placeholder("second", lambda _: datetime.datetime.today().strftime("%S"))
        self.register_placeholder("address", lambda p: p.address)
        self.register_placeholder("runtime_id", lambda p: p.runtime_id)
        
        # Stats Placeholders
        self.register_placeholder("deaths", lambda p: self.get_or_create_stats(p)["deaths"])
        self.register_placeholder("player_kills", lambda p: self.get_or_create_stats(p)["player_kills"])
        self.register_placeholder("kills", lambda p: self.get_or_create_stats(p)["player_kills"])
        self.register_placeholder("mob_kills", lambda p: self.get_or_create_stats(p)["mob_kills"])
        
        self.register_placeholder("exp_level", lambda p: p.exp_level)
        self.register_placeholder("total_exp", lambda p: p.total_exp)
        self.register_placeholder("exp_progress", lambda p: p.exp_progress)
        self.register_placeholder("gamemode", lambda p: p.game_mode.name.lower())
        self.register_placeholder("game_mode", lambda p: p.game_mode.name.lower())
        self.register_placeholder("xuid", lambda p: p.xuid)
        self.register_placeholder("uuid", lambda p: p.unique_id)
        self.register_placeholder("device_os", lambda p: p.device_os)
        self.register_placeholder("locale", lambda p: p.locale)

    def register_placeholder(
            self, name: str, provider: Callable[[Player], typing.Any]
    ) -> None:
        self.placeholders[name] = provider
