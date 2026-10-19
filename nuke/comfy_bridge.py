"""ComfyBridge entry point for Nuke.

Kept as a thin module so existing menu.py lines and ComfyBridge nodes
(their buttons call comfy_bridge.run(...), comfy_bridge.bake_read(...)...)
keep working. The code lives in the comfybridge package next to this file.

Install: copy comfy_bridge.py, the comfybridge folder and the
comfy_bridge_workflows folder to ~/.nuke, then in ~/.nuke/menu.py:

    import comfy_bridge
    comfy_bridge.install_menu()
"""

from comfybridge import __version__
from comfybridge.client import ComfyClient, ComfyError
from comfybridge.nuke_ui import (bake_read, create_node, download_models, install_menu,
                                 knob_changed, load_workflow_into, run, show_help,
                                 test_connection)
from comfybridge.templates import bake_template, node_template
from comfybridge.workflows import (build_prompt, load_workflow, match_layer, scan_library,
                                   slot_specs)

__all__ = ["__version__", "ComfyClient", "ComfyError", "bake_read", "create_node", "download_models", "install_menu", "knob_changed",
           "load_workflow_into", "run", "show_help", "test_connection", "bake_template",
           "node_template", "build_prompt", "load_workflow", "match_layer", "scan_library",
           "slot_specs"]
