# (c) 2025 Ricci Adams
# MIT License (or) 1-clause BSD License

# Don't evaluate type annotations at runtime
from __future__ import annotations

import sublime
import sublime_plugin
import threading
import socket, os
import enum
import json
import base64
import traceback
import itertools
import weakref

StringOrNone = object()
NumberOrNone = object()

DefaultSettings = {
    "socket_path":          "/tmp/sublime.buildsock.sock",
    "gutter_icon_adjust":   "normal",
    "issue_icon_adjust":    0,
    "details_font_face":    StringOrNone,
    "details_font_size":    NumberOrNone,
    "colorize_issue_panel": False,
    "generic_issue_scope":  "region.redish markup.issue.buildsock",
    "info_issue_scope":     "region.bluish markup.info.buildsock",
    "warning_issue_scope":  "region.yellowish markup.warning.buildsock",
    "error_issue_scope":    "region.redish markup.error.buildsock",
    "issue_panel_settings":  { }
}


class Theme(enum.IntEnum):
    LIGHT = 0,
    DARK  = 1

class DisclosureIcon(enum.IntEnum):
    NONE      = 0
    COLLAPSED = 1
    EXPANDED  = 2

class IssueType(enum.IntEnum):
    GENERIC = 0
    INFO    = 1
    WARNING = 2
    ERROR   = 3

class GutterIcon(enum.IntEnum):
    DOT      = 0,
    TRIANGLE = 1,
    OCTOGON  = 2

class GutterAdjust(enum.IntEnum):
    LOWEST  = -2
    LOWER   = -1
    NORMAL  =  0
    HIGHER  =  1
    HIGHEST =  2


def get_resource_path(path: str) -> str:
    return os.path.join("Packages/BuildSock/resources/", path)
    
def get_image_path(path: str) -> str:
    return os.path.join(get_resource_path("images"), path)
    

DisclosureImageMap = {
    ( Theme.LIGHT, DisclosureIcon.NONE      ): get_image_path("disclosure-blank.png"),
    ( Theme.LIGHT, DisclosureIcon.COLLAPSED ): get_image_path("disclosure-collapsed-light.png"),
    ( Theme.LIGHT, DisclosureIcon.EXPANDED  ): get_image_path("disclosure-expanded-light.png"),
    
    ( Theme.DARK,  DisclosureIcon.NONE      ): get_image_path("disclosure-blank.png"),
    ( Theme.DARK,  DisclosureIcon.COLLAPSED ): get_image_path("disclosure-collapsed-dark.png"),
    ( Theme.DARK,  DisclosureIcon.EXPANDED  ): get_image_path("disclosure-expanded-dark.png")
}


IssueIconMap = {
    IssueType.GENERIC: get_image_path("issue-icon-blank.png"),
    IssueType.INFO:    get_image_path("issue-icon-info.png"),
    IssueType.WARNING: get_image_path("issue-icon-warning.png"),
    IssueType.ERROR:   get_image_path("issue-icon-error.png")
}


GutterIconMap = {
    ( GutterIcon.DOT,       GutterAdjust.LOWEST  ): get_image_path("gutter-icon-dot-lowest.png"),
    ( GutterIcon.DOT,       GutterAdjust.LOWER   ): get_image_path("gutter-icon-dot-lower.png"),
    ( GutterIcon.DOT,       GutterAdjust.NORMAL  ): get_image_path("gutter-icon-dot-normal.png"),
    ( GutterIcon.DOT,       GutterAdjust.HIGHER  ): get_image_path("gutter-icon-dot-higher.png"),
    ( GutterIcon.DOT,       GutterAdjust.HIGHEST ): get_image_path("gutter-icon-dot-highest.png"),

    ( GutterIcon.TRIANGLE,  GutterAdjust.LOWEST  ): get_image_path("gutter-icon-triangle-lowest.png"),
    ( GutterIcon.TRIANGLE,  GutterAdjust.LOWER   ): get_image_path("gutter-icon-triangle-lower.png"),
    ( GutterIcon.TRIANGLE,  GutterAdjust.NORMAL  ): get_image_path("gutter-icon-triangle-normal.png"),
    ( GutterIcon.TRIANGLE,  GutterAdjust.HIGHER  ): get_image_path("gutter-icon-triangle-higher.png"),
    ( GutterIcon.TRIANGLE,  GutterAdjust.HIGHEST ): get_image_path("gutter-icon-triangle-highest.png"),

    ( GutterIcon.OCTOGON,   GutterAdjust.LOWEST  ): get_image_path("gutter-icon-octogon-lowest.png"),
    ( GutterIcon.OCTOGON,   GutterAdjust.LOWER   ): get_image_path("gutter-icon-octogon-lower.png"),
    ( GutterIcon.OCTOGON,   GutterAdjust.NORMAL  ): get_image_path("gutter-icon-octogon-normal.png"),
    ( GutterIcon.OCTOGON,   GutterAdjust.HIGHER  ): get_image_path("gutter-icon-octogon-higher.png"),
    ( GutterIcon.OCTOGON,   GutterAdjust.HIGHEST ): get_image_path("gutter-icon-octogon-highest.png")
}


class BuildSockSettings(dict):

    def __init__(self, callback: Callable[[], None]) -> None:
        self.__settings = sublime.load_settings("BuildSock.sublime-settings")
        self.callback = callback

        self.__settings.add_on_change("BuildSock", self._handle_settings_change)
        self._read_settings()


    def _read_settings(self) -> None:
        for key, default_value in DefaultSettings.items():
            value = self.__settings.get(key)
            
            if default_value == StringOrNone:
                check = lambda: type(value) == str
                default_value = None
            
            elif default_value == NumberOrNone:
                check = lambda: isinstance(value, int) or isinstance(value, float)
                default_value = None

            else:
                check = lambda: type(value) == type(default_value)
                        
            if not check():
                value = default_value

            if key == "gutter_icon_adjust":
                value = {
                    "lowest":  GutterAdjust.LOWEST,
                    "lower":   GutterAdjust.LOWER,
                    "normal":  GutterAdjust.NORMAL,
                    "higher":  GutterAdjust.HIGHER,
                    "highest": GutterAdjust.HIGHEST               
                }.get(value, GutterAdjust.NORMAL)
            
            self[key] = value

    
    def _handle_settings_change(self) -> None:
        self._read_settings()
        self.callback()
    

sSpinners = None
sBuildSockPlugin = None
sTimeouts = set()


def plugin_loaded():
    global sSpinners, sBuildSockPlugin
    
    sSpinners = json.loads(sublime.load_resource(get_resource_path("spinners.json")))
    sBuildSockPlugin = BuildSockPlugin()


def plugin_unloaded():
    cleanup_plugin()


def cleanup_plugin():
    global sBuildSockPlugin

    for weak_timeout in sTimeouts.copy():
        timeout = weak_timeout()
        if timeout: timeout.cancel()
    
    if sBuildSockPlugin:
        sBuildSockPlugin.destroy()


# Wraps sublime.set_timeout and provides a cancel() method
class Timeout:
    def __init__(self, callback: Callable, delay: int = 0) -> None:
        self.callback = callback
        sublime.set_timeout(self.__call, delay)

        self.weak_self = weakref.ref(self)
        sTimeouts.add(self.weak_self)
        
    def cancel(self):
        self.callback = None
        sTimeouts.discard(self.weak_self)
            
    def __call(self):
        if self.callback:
            self.callback()
        self.cancel()


class Project:

    def __init__(self, path: str) -> None:
        self.path = path
        self.issues = None
        self.status_message = None
        self.status_spinner = None



class Issue():

    def __init__(
        self,
        type:    IssueType,
        message: str,
        path:    Optional[str] = None, # Full path
        file:    Optional[str] = None, # Relative to project path
        line:    Optional[int] = None,
        column:  Optional[int] = None,
        details: Optional[str] = None,
        tooltip: Optional[str] = None
    ) -> None:
        self.type    = type
        self.message = message
        self.path    = path
        self.file    = file
        self.line    = line
        self.column  = column
        self.details = details
        self.tooltip = tooltip



class ViewManager:
    
    def __init__(self, view: sublime.View) -> None:
        self.view = view
        self.issues = None


    def destroy(self):
        self.erase_regions()


    def handle_settings_changed(self) -> None:
        self.update_regions()


    def set_issues(self, issues: set[Issue]) -> None: 
        if self.issues == issues: return
        self.issues = issues
        self.update_regions()


    def get_region_key(self, issue_type: IssueType) -> str:
        return f"BuildSock-{issue_type}"


    def erase_regions(self) -> None:
        for issue_type in IssueType:
            region_key = self.get_region_key(issue_type)
            self.view.erase_regions(region_key)


    def update_regions(self) -> None:
        self.erase_regions()
        if not self.issues: return

        view = self.view
        added_lines = set()
        active_region_keys = set()

        flags = (
            sublime.DRAW_NO_OUTLINE |
            sublime.DRAW_NO_FILL |
            sublime.DRAW_STIPPLED_UNDERLINE |
            sublime.NO_UNDO
        )

        for issue_type, group in itertools.groupby(self.issues, lambda i: i.type):
            gutter_icon = {
                IssueType.WARNING: GutterIcon.TRIANGLE,
                IssueType.ERROR:   GutterIcon.OCTOGON,
            }.get(issue_type, GutterIcon.DOT)

            scope_settings_key = {
                IssueType.INFO:    "info_issue_scope",
                IssueType.WARNING: "warning_issue_scope",
                IssueType.ERROR:   "error_issue_scope"            
            }.get(issue_type, "generic_issue_scope")

            region_key = self.get_region_key(issue_type)
            scope = sBuildSockPlugin.settings[scope_settings_key]
            gutter_icon_adjust = sBuildSockPlugin.settings["gutter_icon_adjust"]
            gutter_icon_path = GutterIconMap[ ( gutter_icon, gutter_icon_adjust ) ]
            
            regions = [ ]

            for issue in group:
                line = issue.line
                if line in added_lines: continue

                regions.append(sublime.Region(
                    view.text_point(line - 1, 0),
                    view.text_point(line, 0) - 1
                ))
            
            view.add_regions(region_key, regions, scope, gutter_icon_path, flags)
            

class WindowManager:

    def __init__(self, window: sublime.Window) -> None:
        self.window = window
        
        self.window.destroy_output_panel("BuildSockIssues")
        self.panel = self.window.create_output_panel("BuildSockIssues")
        self.phantom_set = sublime.PhantomSet(self.panel, "BuildSockIssues")

        self.setup_panel()

        self.phantom_dicts = [ ]
        self.image_cache = { }
        self.html_cache  = { }
        
        self.status_message = None
        self.status_spinner = None
        self.spinner_timeout = None
        self.spinner_index  = 0

        self.panel.erase_phantoms("BuildSockIssues")


    def handle_settings_changed(self) -> None:
        self.html_cache = { }
        self.setup_panel()
        self._update_phantoms()


    def setup_panel(self) -> None:
        panel = self.panel

        panel_settings = panel.settings()
        panel_settings.set("result_file_regex", "^([^:]*):([0-9]+):?([0-9]+)?:? (.*)$")
        panel_settings.set("result_line_regex", "")
        panel_settings.set("word_wrap", True)
        panel_settings.set("line_numbers", False)
        panel_settings.set("gutter", False)
        panel_settings.set("margin", 0)
        panel_settings.set("scroll_past_end", False)

        if sBuildSockPlugin.settings["colorize_issue_panel"]:
            panel.assign_syntax("Packages/BuildSock/resources/IssuePanel.sublime-syntax")
        else:
            panel.assign_syntax("Packages/Text/Plain text.tmLanguage")

        for key, value in sBuildSockPlugin.settings["issue_panel_settings"].items():
            panel_settings.set(key, value)
           
        panel.set_read_only(True)


    def _make_left_phantom_html(
        self,
        disclosure_icon: DisclosureIcon,
        issue_type: IssueType,
        show_disclosures: bool,
        show_issue_icons: bool
    ) -> str:
        cache_key = ("left", disclosure_icon, issue_type, show_disclosures, show_issue_icons)
        
        if result := self.html_cache.get(cache_key):
            return result
    
        disclosure_light_path = DisclosureImageMap[ ( Theme.LIGHT, disclosure_icon ) ]
        disclosure_dark_path  = DisclosureImageMap[ ( Theme.DARK,  disclosure_icon ) ]

        icon_path = IssueIconMap[issue_type]
            
        disclosure_light_url = self._make_data_url("image/png", disclosure_light_path)
        disclosure_dark_url  = self._make_data_url("image/png", disclosure_dark_path)
        icon_url             = self._make_data_url("image/png", icon_path)

        image_htmls = [ ]

        issue_icon_adjust = sBuildSockPlugin.settings["issue_icon_adjust"]
        line_height = self.panel.line_height()

        size = self.panel.settings().get("font_size")
        padding_top = int((line_height - size) / 2) + issue_icon_adjust
        
        if show_disclosures:
            image_htmls.append(f'<img id="light-disclosure" width="{size}" height="{size}" src="{disclosure_light_url}">')
            image_htmls.append(f'<img id="dark-disclosure"  width="{size}" height="{size}" src="{disclosure_dark_url }">')
        
        if show_issue_icons:
            image_htmls.append(f'<img width="{size}" height="{size}" src="{icon_url}">')

        return """
            <body id="build-sling-left">
                <style>
                    body { padding-top: %ipx; }
                    .dark  #light-disclosure { display: none }
                    .light #dark-disclosure  { display: none }
                </style>
                <a href="toggle:">%s</a>
            </body>
        """ % (padding_top, "".join(image_htmls))
        
        self.html_cache.set[cache_key] = result
        
        return result


    def _make_data_url(self, mime_type: str, resource_path: str) -> str:
        cache_key = (mime_type, resource_path)
        
        if result := self.image_cache.get(cache_key):
            return result

        data_bytes  = sublime.load_binary_resource(resource_path)
        data_string = base64.b64encode(data_bytes).decode("ascii")

        result = f"data:{mime_type};base64,{data_string}"
        self.image_cache[cache_key] = result
        
        return result


    def _make_details_phantom_html(self, details: str) -> None:
        font_face = sBuildSockPlugin.settings["details_font_face"]
        font_size = sBuildSockPlugin.settings["details_font_size"]
        
        font_face_css = f"font-face: {font_face};" if font_face else ""

        if font_size:
            font_size_css = f"font-size: {font_size};"
        else:
            font_size_css = f"font-size: 0.9rem;"

        return """
            <body id=show-scope>
                <style>
                    code {
                        display: block;
                        white-space: pre-wrap;
                        border-radius: 4px;
                        padding: 4px;
                        %s
                        %s
                    }
                    
                    .light code {
                        border: 1px solid #e0e0e0;
                        background-color: #f8f8f8;
                    }
                    
                    .dark code {
                        border: 1px solid #ffffff20;
                        background-color: #ffffff10;
                    }
                </style>
                <code>%s</code>
            </body>
        """ % (font_face_css, font_size_css, details)


    def _handle_phantom_toggle(self, index: int) -> None:
        d = self.phantom_dicts[index]
        d["expanded"] = not d["expanded"]
        self._update_phantoms()


    def _update_phantoms(self) -> None:
        phantoms = [ ]

        for d in self.phantom_dicts:
            if d["expanded"]:
                phantoms.append(d["left_phantoms"][1])
                phantoms.append(d["details_phantom"])            
            else:
                phantoms.append(d["left_phantoms"][0])

        self.phantom_set.update(phantoms)


    def _update_spinner(self) -> None:
        if not self.status_spinner: return

        index = self.spinner_index
        length = len(self.status_spinner)
        if index >= length: return
        
        self.window.status_message(f"{self.status_spinner[index]} {self.status_message}")
        
        timeout = 1000.0 / length
        
        self.spinner_timeout = Timeout(lambda: self._update_spinner(), timeout)
        self.spinner_index = (index + 1) % length


    def destroy(self):
        self.hide_issues()
        self.hide_status()
        
        self.window.destroy_output_panel("BuildSockIssues")

        if self.spinner_timeout:
            self.spinner_timeout.cancel()
            self.spinner_timeout = None


    def show_issues(self, project: Project) -> None:
        issues = project.issues
        panel = self.panel

        panel.set_read_only(False)
        panel.settings().set("result_base_dir", project.path)

        panel.run_command("select_all")
        panel.run_command("right_delete")

        phantom_dicts = [ ]
        i = 0
        
        show_disclosures = False
        show_issue_icons = False
        
        for issue in issues:
            if issue.details != None:
                show_disclosures = True
            if issue.type != IssueType.GENERIC:
                show_issue_icons = True

        for issue in issues:
            region = sublime.Region(panel.size())
            
            message = "{}\n".format(issue.message or "")
            if issue.file and issue.line and issue.column:
                message = f"{issue.file}:{issue.line}:{issue.column} {message}"
            elif issue.file and issue.line:
                message = f"{issue.file}:{issue.line} {message}"
            elif issue.file:
                message = f"{issue.file} {message}"
            
            panel.run_command("append", { "characters": message, "scroll_to_end": True })

            if issue.details:
                collapsed_html    = self._make_left_phantom_html(DisclosureIcon.COLLAPSED, issue.type, show_disclosures, show_issue_icons)
                collapsed_phantom = sublime.Phantom(region, collapsed_html, sublime.PhantomLayout.INLINE, lambda str, i=i: self._handle_phantom_toggle(i))

                expanded_html     = self._make_left_phantom_html(DisclosureIcon.EXPANDED, issue.type, show_disclosures, show_issue_icons)
                expanded_phantom  = sublime.Phantom(region, expanded_html, sublime.PhantomLayout.INLINE, lambda str, i=i: self._handle_phantom_toggle(i))
             
                details_html      = self._make_details_phantom_html(issue.details)
                details_phantom   = sublime.Phantom(region, details_html, sublime.PhantomLayout.BELOW)

                phantom_dict = {
                    "expanded": False,
                    "left_phantoms": ( collapsed_phantom, expanded_phantom ),
                    "details_phantom": details_phantom 
                }

            else:
                left_html = self._make_left_phantom_html(DisclosureIcon.NONE, issue.type, show_disclosures, show_issue_icons)
                left_phantom = sublime.Phantom(region, left_html, sublime.PhantomLayout.INLINE) 

                phantom_dict = {
                    "expanded": False,
                    "left_phantoms": ( left_phantom, left_phantom ),
                    "details_phantom": None
                }
                
            phantom_dicts.append(phantom_dict)
            i = i + 1

        panel.set_read_only(True)
        self.phantom_set.update([ ])
        self.panel.erase_phantoms("BuildSockIssues")

        self.phantom_dicts = phantom_dicts
        self.window.run_command("show_panel", { "panel": "output.BuildSockIssues" })

        self._update_phantoms()


    def hide_issues(self) -> None:
        self.window.run_command("hide_panel", { "panel": "output.BuildSockIssues" })


    def show_status(self,  project: Project) -> None:
        self.status_message = project.status_message
        self.status_spinner = project.status_spinner
        self.spinner_index  = 0

        if self.status_spinner:
            self._update_spinner()        
        else:
            self.window.status_message(self.status_message)


    def hide_status(self) -> None:
        self.window.status_message("")



class Listener(sublime_plugin.EventListener):

    def on_new_window(self, window: sublime.Window):
        Timeout(lambda: sBuildSockPlugin.handle_new_window(window))

    def on_pre_close_window(self, window: sublime.Window):
        Timeout(lambda: sBuildSockPlugin.handle_close_window(window))

    def on_load(self, view: View):
        Timeout(lambda: sBuildSockPlugin.handle_load(view))

    def on_close(self, view: View) -> None:
        Timeout(lambda: sBuildSockPlugin.handle_close(view))
        
    def on_exit(self):
        cleanup_plugin()


class SocketConnection:

    def __init__(self, conn, addr, callback: Callable[[any], None]) -> None:
        self.conn = conn
        self.addr = addr
        self.callback = callback
        self.stop_event = threading.Event()
        self.read_thread = threading.Thread(target=self._read_connection)
        self.read_thread.start()

    
    def stop(self) -> None:
        self.stop_event.set()

        try:
            self.conn.shutdown(socket.SHUT_RDWR)
            self.conn.close()
        except OSError:
            pass

        self.read_thread.join()


    def is_active(self) -> bool:
        return self.read_thread.is_alive()


    def _read_connection(self) -> None:
        try:
            f = self.conn.makefile()
            contents = json.loads(f.read())
            self.conn.close()

            self.callback(contents)
        except Exception as e:
            if self.stop_event.is_set():
                pass # Ignore, we are shutting down
            else:
                print("SocketConnection._read_connection() threw:", e)



class SocketServer:

    def __init__(self, socket_path: str, callback: Callable[[dict], None]) -> None:
        self.socket_path = socket_path
        self.socket = None
        self.wait_for_connection_thread = None
        self.stop_event = None
        self.callback = callback
        self.connections = set()

    
    def start(self):
        if self.socket:
            self.stop()

        self._remove_socket()

        try:
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(self.socket_path)
            self.socket.listen()

            self.stop_event = threading.Event()

            self.wait_for_connection_thread = threading.Thread(target=self._wait_for_connection)
            self.wait_for_connection_thread.start()

        except Exception as e:
            self.socket.close()
            self.socket = None

            sublime.error_message(f"BuildSock could not create a socket at '{self.socket_path}'\n\n Error: {e}")


    def stop(self):
        if not self.socket:
            return

        self.stop_event.set()

        self.socket.close()
        self.wait_for_connection_thread.join()

        for connection in self.connections:
            connection.stop()
    
        self.socket = None
        self.stop_event = None
        self.wait_for_connection_thread = None
        self.connections = set()

        self._remove_socket()


    def _remove_socket(self):
        try:
            os.remove(self.socket_path)
        except OSError:
            pass

    
    def _wait_for_connection(self) -> None:
        try:
            while not self.stop_event.is_set():
                conn, addr = self.socket.accept()
                connection = SocketConnection(conn, addr, self.callback)

                # Prune connection list
                connections = set()
                for connection in self.connections:
                    if connection.is_active():
                        connections.add(connection)

                self.connections = connections

        except ConnectionAbortedError:
            pass

        except Exception as e:
            print("SocketServer._wait_for_connection() threw:", e)



class BuildSockPlugin():

    def __init__(self) -> None:
        self.path_to_project_map   = { }
        self.window_to_manager_map = { }
        self.view_to_manager_map   = { }
        self.socket_server = None
        
        self.settings = BuildSockSettings(lambda: self.handle_settings_changed())
        self.handle_settings_changed()


    def handle_settings_changed(self) -> None:
        self.update_socket_server(self.settings["socket_path"])
        
        for manager in self.window_to_manager_map.values():
            manager.handle_settings_changed()
        
        for manager in self.view_to_manager_map.values():
            manager.handle_settings_changed()


    def update_socket_server(self, socket_path: str) -> None:
        existing_socket_path = None

        if self.socket_server:
            existing_socket_path = self.socket_server.socket_path
            
        if existing_socket_path != socket_path:
            if self.socket_server: self.socket_server.stop()
            callback = lambda s: Timeout(lambda: self.handle_json(s))
            self.socket_server = SocketServer(socket_path, callback)
            self.socket_server.start()


    def get_window_manager(self, window: sublime.Window) -> WindowManager:
        manager = self.window_to_manager_map.get(window)

        if not manager:
            manager = WindowManager(window)
            self.window_to_manager_map[window] = manager
        
        return manager


    def update_window_with_project(self, window: sublime.Window, project: Project):
        issues = project.issues
        pass


    def update_views(self, views: list[View]) -> None:
        path_to_views_map  = { }
        path_to_issues_map = { }

        for view in views:
            file_name = view.file_name()
            if not file_name: continue
                
            view_set = path_to_views_map.get(file_name) or set()
            view_set.add(view)
            path_to_views_map[file_name] = view_set

        for project in self.path_to_project_map.values():
            if project.issues:
                for issue in project.issues:
                    if not issue.path: continue
                    
                    issue_set = path_to_issues_map.get(issue.path) or set()
                    issue_set.add(issue)
                    path_to_issues_map[issue.path] = issue_set

        # Ensure a ViewManager exists for each view with issues
        for path in set(path_to_views_map.keys()) & set(path_to_issues_map.keys()):
            views  = path_to_views_map[path]
            for view in views:
                if not view in self.view_to_manager_map:
                    self.view_to_manager_map[view] = ViewManager(view)              
    
        # Update all managed views
        for view, manager in self.view_to_manager_map.items():
            issues = path_to_issues_map.get(view.file_name(), None)
            manager.set_issues(issues)


    def update_all_views(self):
        views = [ ]
        
        for window in sublime.windows():
            for view in window.views(include_transient = True):
                if not view.file_name: continue
                views.append(view)
        
        self.update_views(views)
            

    def handle_new_window(self, window: sublime.Window):
        for folder in window.folders():
            if project := self.path_to_project_map.get(folder):
                self.update_window_with_project(window, project)
                pass


    def handle_close_window(self, window: sublime.Window):
        if window in self.window_to_manager_map:
            del self.window_to_manager_map[window]


    def handle_load(self, view: sublime.View):
        self.update_views([ view ])


    def handle_close(self, view: sublime.View):
        if view in self.view_to_manager_map:
            self.view_to_manager_map[view].destroy()
            del self.view_to_manager_map[view]


    def destroy(self):
        for manager in self.window_to_manager_map.values():
            manager.destroy()

        for manager in self.view_to_manager_map.values():
            manager.destroy()

        self.path_to_project_map = { }
        self.window_to_manager_map = { } 
        
        if self.socket_server:
            self.socket_server.stop()


    def handle_json(self, in_json: dict):

        def check_type(x: Any, type: Any, default: Any = None) -> Any:
            return x if isinstance(x, type) else default

        def parse_status_spinner(in_any: any) -> Optional[list[str]]:
            if isinstance(in_any, str):
                if result := sSpinners.get(in_any):
                    return result
            elif isinstance(in_any, list):
                for s in in_any:
                    if not isinstance(s, str):
                        return None
                
                return in_any
            else:
                return None

        def parse_issue_type(in_str: str) -> IssueType:
            return {
                "info":    IssueType.INFO,
                "warning": IssueType.WARNING,
                "error":   IssueType.ERROR
            }.get(in_str, IssueType.GENERIC)

        def parse_issue(in_issue: dict, project_path: str) -> Issue:
            message = check_type(in_issue.get("message"),  str, "")
            file    = check_type(in_issue.get("file"),     str)
            line    = check_type(in_issue.get("line"),     int)
            column  = check_type(in_issue.get("column"),   int)
            details = check_type(in_issue.get("details"),  str)
            tooltip = check_type(in_issue.get("tooltip"),  str)
            type    = parse_issue_type(in_issue.get("type"))
            
            path = os.path.join(project_path, file) if file else None

            return Issue(type, message, path, file, line, column, details, tooltip)

        def parse_issues(in_issues: list[dict], project_path: str) -> list[Issue]:
            return [ parse_issue(x, project_path) for x in in_issues ]
        
        def parse_root(in_root: dict) -> None:
            project_path = check_type(in_root.get("project"), str)
            in_commands  = check_type(in_root.get("commands"), list, [ ])

            if project_path == None: return

            project = self.path_to_project_map.get(project_path)
            if not project:
                project = Project(project_path)
                self.path_to_project_map[project_path] = project
                
            current_project_path = project_path

            managers = [ ]
            needs_view_update = False
            should_clear = False

            for window in sublime.windows():
                for folder in window.folders():
                    if folder == project_path:
                        managers.append(self.get_window_manager(window))
                        break

            for in_command in in_commands:
                command_type = check_type(in_command.get("command"), str)

                if command_type == "show-issues":
                    in_issues = check_type(in_command.get("issues"), list, [ ])
                    issues = parse_issues(in_issues, project_path)

                    project.issues = issues
                    for m in managers: m.show_issues(project)

                    needs_view_update = True

                elif command_type == "hide-issues":
                    project.issues = None
                    for m in managers: m.hide_issues()

                    needs_view_update = True

                elif command_type == "show-status":
                    status_message = check_type(in_command.get("message"), str)
                    status_spinner = parse_status_spinner(in_command.get("spinner"))

                    project.status_message = status_message
                    project.status_spinner = status_spinner

                    for m in managers: m.show_status(project)

                elif command_type == "hide-status":
                    project.status_message = None
                    project.status_spinner = None

                    for m in managers: m.hide_status()

                elif command_type == "clear":
                    should_clear = True

            if should_clear:
                for m in managers:
                    m.destroy()
                       
                    if m.window in self.window_to_manager_map:
                        del self.window_to_manager_map[m.window]

                if project:
                    project.issues = None
                    self.update_all_views()
                    del self.path_to_project_map[project_path]

            if needs_view_update:
                self.update_all_views()

        root = check_type(in_json, dict, { })
        
        try:
            parse_root(root)
        except Exception:
            traceback.print_exc()

